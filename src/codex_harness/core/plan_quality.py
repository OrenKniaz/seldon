"""Deterministic, read-only planner checklist.

SQL reads: nodes (existence only), clarifications, assumptions, events (consent/question timing),
revisions (frozen snapshot), sqlite_master, and optional review_runs/review_findings.
Reviewer coverage comes from the latest successful run per provider for the current manifest;
open findings block across all rounds.
Resolved findings and reasoned rejections count as addressed. Unknown layouts fail explicitly.
Parallel width means the largest topological level, as specified by the checklist,
not the general graph-theoretic maximum antichain.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from codex_harness.core.graph import PlanError, load_plan, topological_order
from codex_harness.core.plan_store import PHASES, PlanStore, PlanStoreError
from codex_harness.core.reviewer_evidence import single_provider_notice
from codex_harness.core.spine import Spine, SpineError, compile_manifest
from codex_harness.core.spine_render import render_markdown

QUESTIONS = (
    (
        "expanded-and-aligned",
        "Did we expand the idea, and ask enough questions to align?",
        "expand",
    ),
    (
        "mapping",
        "Is the idea mapped correctly into features, stories and requirements?",
        "requirements",
    ),
    ("first-moment-proof", "First user moment from a clean install", "requirements"),
    ("spine", "Is there a clear spine from vision to tasks to proof?", "plan"),
    ("concurrency-ownership", "Are beads spread for concurrency and ownership?", "plan"),
    ("cross-bead-contract", "Are cross-bead interfaces exercised by consumer proofs?", "plan"),
    ("worker-readiness", "Can cold-start workers implement and verify their packets?", "plan"),
    ("roundtable", "Did the roundtable run, and was it helpful?", "roundtable"),
    ("audit", "Did the current manifest pass its test audit?", "roundtable"),
    ("proof-level", "Were the tests verified against a reference implementation?", "roundtable"),
    ("guesswork", "Is the process efficient, or full of guesswork?", "publish"),
    ("repeatable", "Is it repeatable and deterministic?", "plan"),
    ("published-context", "Is the plan published with correct context for each worker?", "publish"),
)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _roundtable(store: PlanStore) -> tuple[str, dict, str]:
    from codex_harness.core.roundtable import advisory_finding_ids

    exists = store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_runs'"
    ).fetchone()
    if not exists:
        return "fail", {"tableExists": False}, "Run and record the roundtable."
    columns = {row[1] for row in store.connection.execute("PRAGMA table_info(review_runs)")}
    if (
        not {"id", "round_id", "plan_revision", "reviewer", "provider", "exit_code", "finished_at"}
        <= columns
    ):
        return "fail", {"columns": sorted(columns)}, "Record complete reviewer runs."
    revision = store.manifest_revision()
    latest = store.connection.execute(
        "SELECT round_id, plan_revision FROM review_runs WHERE plan_revision=? "
        "ORDER BY finished_at DESC, id DESC LIMIT 1",
        (revision,),
    ).fetchone()
    stale = latest is None
    if stale:
        latest = store.connection.execute(
            "SELECT round_id, plan_revision FROM review_runs "
            "ORDER BY finished_at DESC, id DESC LIMIT 1"
        ).fetchone()
    if latest is None:
        return "fail", {"roundId": None}, "Run and record the roundtable."
    runs = store.successful_reviews(revision)
    if stale and any(row.get("carriedForward") for row in runs):
        stale = False
        latest = next(row for row in runs if row.get("carriedForward"))
    attempted = {
        row[0]
        for row in store.connection.execute(
            "SELECT DISTINCT provider FROM review_runs WHERE plan_revision=?", (revision,)
        )
    }
    columns = {row[1] for row in store.connection.execute("PRAGMA table_info(review_findings)")}
    if not {"id", "status", "resolution"} <= columns:
        return "fail", {"columns": sorted(columns)}, "Record finding status and resolution."
    query = "SELECT id, status, resolution FROM review_findings"
    if "stage" in columns:
        query += " WHERE stage != 'readiness'"
    rows = store.connection.execute(query + " ORDER BY id").fetchall()
    reviewers = sorted({row["reviewer"] for row in runs if row["reviewer"]})
    providers = sorted({row["provider"] for row in runs if row["provider"]})
    advisory = advisory_finding_ids(store)
    unresolved = [
        row["id"] for row in rows if row["status"] == "open" and row["id"] not in advisory
    ]
    addressed = sum(
        row["status"] == "resolved"
        or row["status"] == "rejected"
        and bool((row["resolution"] or "").strip())
        for row in rows
    )
    evidence = {
        "roundId": latest["round_id"],
        "reviewedRevision": latest["plan_revision"],
        "currentRevision": revision,
        "stale": stale,
        "reviewers": reviewers,
        "reviewerCount": len(reviewers),
        "providers": providers,
        "providerCount": len(providers),
        "resolved": sum(row["status"] == "resolved" for row in rows),
        "addressed": addressed,
        "open": len(unresolved),
        "openFindingIds": unresolved,
        "advisoryFindingIds": sorted(advisory),
    }
    policy = store.plan_review_policy_state(revision)
    if policy["active"]:
        evidence["reviewPolicy"] = policy
        if not policy["eligible"]:
            return (
                "fail",
                evidence,
                "Prospective review policy held: "
                + ", ".join(policy["reasons"])
                + ". Repair required work; retry only within existing authorization. "
                "Targeted assessment/observed-separation producers are unavailable; "
                "author claims cannot certify closure.",
            )
    labeled = {
        record["roundId"]
        for (payload,) in store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='roundtable-completed'"
        )
        if (record := json.loads(payload)).get("reviewIndependence") == "single-provider"
    }
    single = (
        len(reviewers) >= 2
        and len(providers) == 1
        and all(row["round_id"] in labeled for row in runs)
    )
    evidence.update({"reviewIndependence": "single-provider"} if single else {})
    passed = len(reviewers) >= 2 and (len(providers) >= 2 or single) and not unresolved
    advice = "" if passed else "Resolve all open findings."
    if single:
        advice = single_provider_notice({"reviewers": runs}) + " " + advice
    elif len(providers) == 1:
        advice = (
            "Only one provider is available in the successful review set; "
            "cross-subscription evidence is missing. " + advice
        )
    failed_providers = attempted - set(providers)
    if policy["active"]:
        # Only roles explicitly declared optional before attempts receive this
        # disclosure. No legacy/default failure is silently made optional.
        # Role metadata does not waive the existing quality threshold: one
        # successful provider plus an attempted failed provider still fails.
        if policy["optionalFailures"]:
            advice = (
                "Reduced assurance: optional reviews not passed: "
                + ", ".join(policy["optionalFailures"])
                + ". "
                + advice
            )
    if failed_providers:
        advice = (
            "Missing successful review from "
            + ", ".join(sorted(failed_providers))
            + "; inspect its failure artifact and retry that reviewer."
        )
    if not providers:
        advice = "No successful review for the current manifest revision; run roundtable."
    if stale:
        advice = "plan changed since the last review; re-run the roundtable"
        if unresolved:
            advice += "; resolve all open findings."
    return (
        "warn"
        if passed and policy["active"] and policy["optionalFailures"]
        else "pass"
        if passed
        else "fail"
        if not providers or failed_providers
        else "warn",
        evidence,
        advice,
    )


def quality_report(store: PlanStore) -> dict:
    """Evaluate applicable checks without changing the store or its exports."""
    state = store.status()
    phase = PHASES.index(state["phase"])
    # An unopened understanding card has no valid Spine yet.
    has_nodes = bool(store.connection.execute("SELECT 1 FROM nodes LIMIT 1").fetchone())
    spine = store.spine() if has_nodes else None
    checks = []
    for check_id, question, first_phase in QUESTIONS:
        check = dict(id=check_id, question=question, status="not-yet", evidence={}, advice="")
        checks.append(check)
        if phase < PHASES.index(first_phase):
            continue
        if spine is None:
            check.update(status="fail", advice="Create the vision and its planning spine.")
            continue
        status, evidence, advice = _evaluate(check_id, store, spine, state)
        check.update(status=status, evidence=evidence, advice=advice)
    columns = {row[1] for row in store.connection.execute("PRAGMA table_info(review_findings)")}
    deferred = (
        [
            row[0]
            for row in store.connection.execute(
                "SELECT id FROM review_findings WHERE status='deferred' ORDER BY id"
            )
        ]
        if {"id", "status"} <= columns
        else []
    )
    deferred += [item["id"] for item in store.acceptance_findings() if item["status"] == "deferred"]
    checks.append(
        dict(
            id="deferred-findings",
            question="Are known gaps deferred?",
            status="warn"
            if deferred
            else "pass"
            if phase >= PHASES.index("roundtable")
            else "not-yet",
            evidence={"count": len(deferred), "ids": deferred},
            advice=(
                "deferred findings are known gaps; "
                "add them to the next plan revision or accept them explicitly"
            )
            if deferred
            else "",
        )
    )
    for check in checks:
        executed = check["evidence"].get("acceptanceRunId") is not None
        check["evidenceStatus"] = "executed" if executed else "planned"
        check["claim"] = (
            ("proved" if executed else "planned") if check["status"] == "pass" else check["status"]
        )
    weights = {"pass": 1, "warn": 0.5, "fail": 0}
    values = [weights[check["status"]] for check in checks if check["status"] in weights]
    score = round(100 * sum(values) / len(values)) if values else 0
    counts = {
        name: sum(check["status"] == name for check in checks) for name in (*weights, "not-yet")
    }
    return {
        "planRevision": state["plan_revision"],
        "manifestRevision": state["manifest_revision"],
        "score": score,
        "checks": checks,
        "summary": ", ".join(f"{count} {name}" for name, count in counts.items()),
    }


def first_moment_proof(spine: Spine) -> tuple[str, dict, str]:
    moment = spine.node(spine.ids("vision")[0])["fields"].get("first_user_moment", "")
    if isinstance(moment, dict):
        moment = moment.get("title", "")
    linked, clean = [], []
    for key in spine.ids("test"):
        test = spine.node(key)
        if test["fields"].get("level") != "e2e":
            continue
        candidates = [test] + [
            spine.node(parent)
            for parent in spine.ancestors(key)
            if spine.node(parent)["kind"] == "requirement"
        ]
        matches = [
            node
            for node in candidates
            if node["fields"].get("firstMoment") is True
            or isinstance(moment, str)
            and moment.strip()
            and moment.casefold() in node["text"].casefold()
        ]
        if matches:
            linked.append(key)
            if test["fields"].get("cleanState") is True or any(
                node["fields"].get("cleanState") is True for node in matches
            ):
                clean.append(key)
    conflicts = [
        key
        for key in spine.ids("requirement")
        if spine.node(key)["fields"].get("kind") == "edge-case"
        and all(word in spine.node(key)["text"].casefold() for word in ("missing", "error"))
    ]
    warning = "Missing-file error requirements may contradict first use; verify cleanState."
    evidence = {
        "linkedE2e": linked,
        "cleanE2e": clean,
        "warnings": [warning] if conflicts and not clean else [],
        "edgeCaseIds": conflicts,
    }
    return (
        "pass" if clean else "fail",
        evidence,
        "" if clean else "add an e2e scenario of the first user moment from a clean install",
    )


def _evaluate(check_id: str, store: PlanStore, spine: Spine, state: dict) -> tuple[str, dict, str]:
    if spine.semantic and check_id in {"first-moment-proof", "cross-bead-contract"}:
        manifest = compile_manifest(spine)
        return (
            "warn" if check_id == "first-moment-proof" else "pass",
            {
                "scenarioIds": [s["id"] for s in manifest["semantic"]["scenarios"]["scenarios"]],
                "scope": "planned data; controller execution pending",
            },
            "First-moment and qualitative acceptance remain unproved."
            if check_id == "first-moment-proof"
            else "",
        )
    if check_id == "first-moment-proof":
        status, evidence, advice = first_moment_proof(spine)
        scenario = next(
            (s for s in store.acceptance_scenarios() if s["id"] == "first-moment"), None
        )
        if scenario is not None:
            planned = scenario.get("executable") is True and scenario.get("status") == "planned"
            status = "pass" if planned else "fail"
            advice = (
                ""
                if planned
                else "Supply steps and expectations for the current first-moment scenario."
            )
            evidence["scenarioStatus"] = scenario.get("status")
            result = next(
                (s for s in state["acceptance"]["scenarios"] if s["id"] == "first-moment"), {}
            )
            evidence["acceptanceSubject"] = result.get("lastResultSubject")
            if result.get("lastResultSubject") == "oracle":
                evidence["note"] = result.get("lastResultNote")
            if (
                planned
                and result.get("lastResult") == "pass"
                and result.get("lastResultSubject") == "product"
            ):
                evidence["acceptanceRunId"] = result["runId"]
        return status, evidence, advice
    if check_id == "cross-bead-contract":
        return _cross_bead_contract(store, spine)
    if check_id == "worker-readiness":
        readiness = state["readiness"]
        evidence = {**readiness, "eligibleBeadCount": len(readiness["eligibleBeads"])}
        pending = f"{readiness['pendingProducerCount']} beads pending producers"
        return readiness["status"], evidence, " ".join(filter(None, [readiness["advice"], pending]))
    if check_id == "expanded-and-aligned":
        from codex_harness.core.approvals import require_approval

        try:
            approvals = [require_approval(store, "A")["id"]]
        except PlanStoreError:
            approvals = []
        questions = store.connection.execute(
            "SELECT id, status FROM clarifications ORDER BY id"
        ).fetchall()
        fields = spine.node(spine.ids("vision")[0])["fields"]
        missing = [
            key
            for key in ("users", "first_user_moment", "scope_in", "scope_out")
            if not fields.get(key) or isinstance(fields[key], str) and not fields[key].strip()
        ]
        unanswered = [row["id"] for row in questions if row["status"] == "open"]
        locked = PHASES.index(state["phase"]) >= PHASES.index("vision-lock")
        failed = not approvals or bool(missing) or (locked and bool(unanswered))
        return (
            "fail" if failed else "pass",
            {
                "approvalIds": approvals,
                "clarificationsAsked": len(questions),
                "unansweredIds": unanswered,
                "missingFields": missing,
            },
            "Complete understanding; harness_prepare_chat_confirmation gate=vision-lock shows A. "
            "Record the actual reply; answer open questions with harness_answer."
            if failed
            else "",
        )
    if check_id == "mapping":
        features, stories, requirements = (
            spine.ids(kind) for kind in ("feature", "story", "requirement")
        )
        no_story = [key for key in features if not set(spine.children(key)) & set(stories)]
        no_requirement = [
            key for key in stories if not set(spine.children(key)) & set(requirements)
        ]
        unlinked = [key for key in requirements if not set(spine.ancestors(key)) & set(features)]
        edge_cases = [
            key
            for key in requirements
            if spine.node(key)["fields"].get("kind") == "edge-case"
            or re.search(
                r"edge|error|invalid|reject|exception|failure",
                spine.node(key)["title"] + " " + spine.node(key)["text"],
                re.I,
            )
        ]
        failed = bool(no_story or no_requirement or unlinked or not features or not requirements)
        return (
            "fail" if failed else "pass" if edge_cases else "warn",
            {
                "featuresWithoutStories": no_story,
                "storiesWithoutRequirements": no_requirement,
                "requirementsWithoutFeature": unlinked,
                "edgeCaseIds": edge_cases,
            },
            "Link every feature, story and requirement and specify at least one edge/error case."
            if failed or not edge_cases
            else "",
        )
    if check_id == "spine":
        gaps = spine.coverage_gaps()
        empty = not spine.ids("bead") or not spine.ids("requirement")
        return (
            "fail" if gaps or empty else "pass",
            {"gaps": gaps, "emptyPlan": empty},
            "Link vision, requirements, beads and tests until coverage has no gaps."
            if gaps or empty
            else "",
        )
    if check_id == "roundtable":
        return _roundtable(store)
    if check_id == "audit":
        if spine.semantic:
            return (
                "pass",
                {
                    "applicable": False,
                    "reason": "semantic-v1: controller proves scenarios during execution",
                },
                "",
            )
        revision = state["manifest_revision"]
        audit = store.passing_audit(revision)
        return (
            "pass" if audit else "fail",
            {"manifestRevision": revision, "audit": audit},
            ""
            if audit
            else "No passing audit for the current manifest revision; run audit --oracle oracle.",
        )
    if check_id == "proof-level":
        if spine.semantic:
            return (
                "warn",
                {"proofLevel": None},
                "Contracts RED is history; Validation PASS is required.",
            )
        level = state["proofLevel"]
        return (
            "warn" if level == "prospective" else "pass" if level == "oracle-verified" else "fail",
            {"proofLevel": level},
            "workers' integrated validation and clean-state acceptance are the first real proof; "
            "review tests carefully"
            if level == "prospective"
            else ""
            if level
            else "Run a current-manifest test audit.",
        )
    if check_id == "guesswork":
        evidence = {
            table: [
                dict(row)
                for row in store.connection.execute(
                    f"SELECT id, {text} FROM {table} WHERE status='open' ORDER BY id"
                )
            ]
            for table, text in (("assumptions", "text"), ("clarifications", "question"))
        }
        opened = any(evidence.values())
        return (
            "warn" if opened else "pass",
            evidence,
            "Resolve the listed assumptions and clarifications." if opened else "",
        )
    if check_id == "published-context":
        revision = state["published_revision"]
        if not revision:
            return "fail", {"publishedRevision": None}, "Publish a frozen plan revision."
        row = store.connection.execute(
            "SELECT snapshot_json FROM revisions WHERE plan_revision=?", (revision,)
        ).fetchone()
        snapshot = Spine(json.loads(row[0]))
        manifest = store.published_manifest(revision)
        invalid = []
        for key, bead in sorted(manifest["beads"].items()):
            refs = bead.get("requirementRefs", [])
            if (
                not refs
                or not bead.get("ownedFiles")
                or (not snapshot.semantic and not bead.get("proofModule", "").strip())
                or any(dep not in manifest["beads"] for dep in bead.get("dependsOn", []))
                or any(
                    ref.get("id") not in snapshot.ids("requirement")
                    or ref.get("revision") != snapshot.revision(ref["id"])
                    for ref in refs
                )
            ):
                invalid.append(key)
        valid = bool(manifest["beads"]) and not invalid and manifest["planRevision"] == revision
        return (
            "pass" if valid else "fail",
            {"publishedRevision": revision, "invalidBeadIds": invalid},
            "" if valid else "Republish complete, revision-bound worker context.",
        )
    try:
        manifest = compile_manifest(spine)
        if check_id == "repeatable":
            second = compile_manifest(spine)
            equal = (
                manifest["planRevision"] == second["planRevision"]
                and _json(manifest) == _json(second)
                and render_markdown(spine) == render_markdown(spine)
            )
            return (
                "pass" if equal else "fail",
                {"identical": equal},
                "" if equal else "Remove nondeterminism from compilation and rendering.",
            )
        # Reuse the canonical ownership/path/dependency validator, including overlaps.
        with TemporaryDirectory(prefix="plan-quality-") as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(_json(manifest), encoding="utf-8")
            plan = load_plan(path)
        levels: dict[str, int] = {}
        for key in topological_order(plan):
            levels[key] = 1 + max((levels[dep] for dep in plan.beads[key].depends_on), default=-1)
        width = max(
            (list(levels.values()).count(level) for level in set(levels.values())), default=0
        )
        owners: dict[str, set[str]] = {}
        for key, bead in plan.beads.items():
            for name in bead.owned_files:
                owners.setdefault(name.replace("\\", "/"), set()).add(key)
        overlaps = {name: sorted(ids) for name, ids in sorted(owners.items()) if len(ids) > 1}
        status = (
            "fail"
            if (overlaps and not spine.semantic) or not plan.beads
            else "warn"
            if width == 1 and len(plan.beads) > 2
            else "pass"
        )
        return (
            status,
            {"parallelWidth": width, "beadCount": len(plan.beads), "overlaps": overlaps},
            "Give each file one owner and parallelize independent beads."
            if status != "pass"
            else "",
        )
    except (SpineError, PlanError) as error:
        return "fail", {"errorCode": error.code}, str(error)


def render_quality_card(report: dict) -> str:
    """Render one line per check and the first three actionable pieces of advice."""
    icons = {"pass": "✅", "warn": "⚠️", "fail": "❌", "not-yet": "○"}
    lines = [f"Planner quality: {report['score']}/100"]
    lines.extend(
        f"{icons[check['status']]} {check['question']}: {check['status']} "
        f"({check['claim']}; evidence: {check['evidenceStatus']})"
        + "".join(f" Warning: {warning}" for warning in check["evidence"].get("warnings", []))
        for check in report["checks"]
    )
    advice = [check["advice"] for check in report["checks"] if check["advice"]]
    lines.extend(f"- {item}" for item in advice[:3])
    return "\n".join(lines) + "\n"


def _cross_bead_contract(store: PlanStore, spine: Spine) -> tuple[str, dict, str]:
    """Inspect protected consumer proofs without importing or executing them."""
    gaps, checked = [], []
    beads = {key: spine.node(key) for key in spine.ids("bead")}
    verification = spine.node(spine.ids("vision")[0])["fields"].get("verification")
    for key in (*spine.ids("requirement"), *spine.ids("decision")):
        contract = spine.node(key)
        linked = {bead for bead in beads if key in spine.ancestors(bead)}
        pairs = [
            (producer, consumer)
            for consumer in sorted(linked)
            for producer in beads[consumer]["fields"].get("dependsOn", [])
            if producer in linked
            and beads[producer]["fields"].get("owner", producer)
            != beads[consumer]["fields"].get("owner", consumer)
        ]
        for producer, consumer in pairs:
            pair = {"contract": key, "producer": producer, "consumer": consumer}
            checked.append(pair)
            if contract["fields"].get("contractTest") or any(
                spine.node(ref)["kind"] == "requirement"
                and spine.node(ref)["fields"].get("contractTest")
                for ref in spine.ancestors(key)
            ):
                continue
            if verification is not None and verification["runner"] == "command":
                from codex_harness.core.runners import resolve_runner
                from codex_harness.core.spine import requirement_file_sources

                runner = resolve_runner({"verification": verification})
                template = store.workspace / "template"
                sources = requirement_file_sources(template, [runner.test_dir])
                proof = beads[consumer]["fields"].get("proofModule", "")
                source = sources.get(proof)
                owned = {
                    (template / name).resolve() for name in beads[producer]["fields"]["ownedFiles"]
                }
                imported = set()
                if source is not None:
                    text = source.read_text(encoding="utf-8-sig")
                    for pattern in runner.import_patterns or ():
                        for match in re.finditer(pattern, text):
                            specifier = match[1]
                            if not specifier.startswith(("./", "../")):
                                continue
                            target = source.parent / specifier
                            candidates = [
                                target,
                                *(Path(str(target) + ext) for ext in (".ts", ".js")),
                            ]
                            candidates.extend(target / ("index" + ext) for ext in (".ts", ".js"))
                            if target.suffix == ".js":
                                candidates.append(target.with_suffix(".ts"))
                            imported.update(path.resolve() for path in candidates)
                if not owned.intersection(imported):
                    gaps.append(pair)
                continue
            modules = set()
            for name in beads[producer]["fields"].get("ownedFiles", []):
                path = name.replace("\\", "/")
                if path.startswith("src/"):
                    path = path[4:]
                if path.endswith(".py"):
                    modules.add(path[:-3].removesuffix("/__init__").replace("/", "."))
            proof = beads[consumer]["fields"].get("proofModule", "")
            # show_node owns protected-path discovery and confinement. Decision
            # contracts use their linked requirements' test sources.
            requirements = (
                [key]
                if contract["kind"] == "requirement"
                else [
                    ref
                    for ref in spine.ancestors(consumer)
                    if spine.node(ref)["kind"] == "requirement"
                ]
            )
            files = {
                file["path"] for ref in requirements for file in store.show_node(ref)["testFiles"]
            }
            imported = set()
            for name in sorted(files):
                path = Path(name)
                dotted = name[:-3].replace("/", ".")
                if proof not in (name, path.name, path.stem, dotted):
                    continue
                tree = ast.parse(
                    (store.workspace / "template" / path).read_text(encoding="utf-8-sig")
                )
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        prefix = dotted.split(".")[: -node.level] if node.level else []
                        base = ".".join([*prefix, *([node.module] if node.module else [])])
                        imported.add(base)
                        imported.update(f"{base}.{alias.name}".lstrip(".") for alias in node.names)
            if not modules.intersection(imported):
                gaps.append(pair)
    return (
        "warn" if gaps else "pass",
        {"checked": checked, "gaps": gaps},
        "pin the interface in the requirement and add a real producer→consumer proof "
        "owned by the consumer; make the consumer depend on the producer"
        if gaps
        else "",
    )
