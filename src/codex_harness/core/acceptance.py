"""Clean first-use execution and revision-bound vision acceptance.

Vision fields may contain structured first_user_moment/scope_in items with
given, steps and expect. String items need matching fields.acceptance entries
before they can execute; prose is never silently converted into a passing test.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from uuid import uuid4

from codex_harness.core import roundtable
from codex_harness.core.plan_store import PlanStoreError, _now
from codex_harness.core.spine_render import render_markdown

QUESTION = "Does this product achieve the locked vision for a real first-time user? List gaps."


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def derive_scenarios(spine) -> list[dict]:
    fields = spine.node(spine.ids("vision")[0])["fields"]
    scope = fields.get("scope_in", [])
    if isinstance(scope, str):
        scope = [item.strip() for item in re.split(r"[,\n]", scope) if item.strip()]
    items = [("first-moment", fields.get("first_user_moment", ""))]
    items.extend((f"scope-{index}", item) for index, item in enumerate(scope, 1))
    explicit = {item["id"]: item for item in fields.get("acceptance", [])}
    for key in spine.ids("acceptance"):
        node = spine.node(key)
        explicit[key] = {"id": key, "title": node["title"], **node["fields"]}
    scenarios = []
    for key, item in items:
        body = item if isinstance(item, dict) else {"title": item}
        scenario = {
            "id": key,
            "title": body.get("title", key),
            "given": "A clean install with no user data",
            "steps": [],
            "expect": {},
        }
        scenario.update(body)
        scenario.update(explicit.pop(key, {}))
        scenarios.append(scenario)
    scenarios.extend(explicit.values())
    return scenarios


def _product_git_head(repo: Path) -> str | None:
    """Use Git only when the declared product is its checkout's root.

    Git discovers ancestor repositories from plain directories too. Their
    HEAD and ignore rules cannot stand in for this product's working files.
    A linked worktree's .git file is supported by the same root check.
    """
    top = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top.returncode != 0:
        if (repo / ".git").exists() or (repo / ".git").is_symlink():
            raise ValueError(f"Cannot determine product Git scope: {repo}")
        return None
    if not top.stdout.strip() or Path(top.stdout.strip()).resolve() != repo:
        return None
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return head.stdout.strip() if head.returncode == 0 else None


def integrated_revision(repo) -> str:
    """Bind evidence to HEAD and actual working files, including untracked code."""
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise ValueError(f"Repository directory does not exist: {repo}")
    head = _product_git_head(repo)
    if head is not None:
        listing = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            capture_output=True,
            check=True,
        )
        paths = sorted(set(listing.stdout.decode("utf-8").split("\0")) - {""})
    else:
        paths = sorted(
            path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file()
        )
    ignored = {".git", ".codex-harness", "__pycache__", ".pytest_cache", ".ruff_cache"}
    files = []
    for name in paths:
        path = repo / name
        if not ignored.intersection(Path(name).parts):
            files.append(
                (name, hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
            )
    return _hash(
        {
            "algorithm": "tree-bytes-v2",
            "head": head,
            "files": files,
        }
    )


def _integrated_revision_v1(repo) -> str:
    """Bind evidence to HEAD and actual working files, including untracked code."""
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise ValueError(f"Repository directory does not exist: {repo}")
    head = _product_git_head(repo)
    if head is not None:
        changes = subprocess.run(
            ["git", "-C", str(repo), "diff", "HEAD", "--binary", "--no-ext-diff", "--", "."],
            capture_output=True,
            check=True,
        )
        listing = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            capture_output=True,
            check=True,
        )
        paths = sorted(set(listing.stdout.decode("utf-8").split("\0")) - {""})
    else:
        changes = None
        paths = sorted(
            path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file()
        )
    ignored = {".git", ".codex-harness", "__pycache__", ".pytest_cache", ".ruff_cache"}
    files = []
    for name in paths:
        path = repo / name
        if not ignored.intersection(Path(name).parts):
            files.append(
                (name, hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
            )
    return _hash(
        {
            "head": head,
            "diff": hashlib.sha256(changes.stdout).hexdigest() if changes else None,
            "files": files,
        }
    )


def _matches(actual: dict, expect: dict) -> bool:
    if not isinstance(expect, dict) or not expect:
        return False
    if set(expect) - {"exit", "exitCode", "stdout", "stderr", "stdoutRegex", "stderrRegex"}:
        raise ValueError("Unknown acceptance expectation")
    if actual["exit"] != expect.get("exit", expect.get("exitCode", 0)):
        return False
    for stream in ("stdout", "stderr"):
        if stream in expect and expect[stream] not in actual[stream]:
            return False
        if stream + "Regex" in expect and not re.search(expect[stream + "Regex"], actual[stream]):
            return False
    return True


def run_scenarios(repo, scenarios, env=None) -> list[dict]:
    """Run trusted planner commands, with isolated cwd/HOME/data for each scenario.

    {repo}, {home}, {data} and {python} placeholders are available. Environment
    entries ending in _DATA/_DATA_DIR/_DATA_HOME are redirected into fresh data.
    Controlled variables: HOME, USERPROFILE, HOMEDRIVE, HOMEPATH, APPDATA,
    LOCALAPPDATA, XDG_DATA_HOME, XDG_CONFIG_HOME, XDG_CACHE_HOME, XDG_STATE_HOME,
    XDG_RUNTIME_DIR, TMP, TEMP, TMPDIR, PYTHONPATH and PYTHONNOUSERSITE.
    Each step may be a shell string or {command, expect}; final expect applies
    to the last step. No intermediate nonzero exit is silently ignored.
    """
    import sys

    repo = Path(repo).resolve()
    results = []
    for scenario in scenarios:
        result = {"id": scenario["id"], "title": scenario["title"], "steps": [], "passed": False}
        results.append(result)
        # A killed Windows process tree can hold the work dir briefly after
        # taskkill returns; leftover temp files must not fail the scenario.
        with TemporaryDirectory(
            prefix="harness-acceptance-", ignore_cleanup_errors=True
        ) as directory:
            root = Path(directory)
            home, data, cwd = (root / name for name in ("home", "data", "work"))
            for path in (home, data, cwd):
                path.mkdir()
            process_env = {**os.environ, **(env or {})}
            for key in list(process_env):
                if key.endswith(("_DATA", "_DATA_DIR", "_DATA_HOME")):
                    process_env[key] = str(data / key.lower())
            process_env.update(
                HOME=str(home),
                USERPROFILE=str(home),
                APPDATA=str(data / "roaming"),
                LOCALAPPDATA=str(data / "local"),
                XDG_DATA_HOME=str(data),
                XDG_CONFIG_HOME=str(home / ".config"),
                XDG_CACHE_HOME=str(home / ".cache"),
                XDG_STATE_HOME=str(data / "state"),
                XDG_RUNTIME_DIR=str(root / "runtime"),
                TMP=str(root),
                TEMP=str(root),
                TMPDIR=str(root),
                PYTHONPATH=str(repo),
                PYTHONNOUSERSITE="1",
            )
            process_env.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive) :])
            tokens = {
                "repo": str(repo),
                "home": str(home),
                "data": str(data),
                "python": sys.executable,
            }
            steps = scenario.get("steps", [])
            if not steps:
                result["error"] = "No executable steps supplied for this vision promise"
                continue
            for index, step in enumerate(steps):
                command = step if isinstance(step, str) else step["command"]
                for name, value in tokens.items():
                    command = command.replace("{" + name + "}", value)
                expect = (step.get("expect") if isinstance(step, dict) else None) or (
                    scenario.get("expect", {}) if index == len(steps) - 1 else {"exit": 0}
                )
                timed_out = False
                # Files avoid pipe backpressure and inherited-pipe waits. Only bounded
                # output is read into memory, even when the command is very noisy.
                with TemporaryFile() as stdout, TemporaryFile() as stderr:
                    process = subprocess.Popen(
                        command,
                        shell=True,
                        cwd=cwd,
                        env=process_env,
                        stdout=stdout,
                        stderr=stderr,
                        **(
                            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                            if os.name == "nt"
                            else {"start_new_session": True}
                        ),
                    )
                    try:
                        process.communicate(timeout=60)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        if os.name == "nt":
                            subprocess.run(
                                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                                capture_output=True,
                                timeout=1.9,
                                check=False,
                            )
                        else:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        process.communicate(timeout=0.05)
                    record = {
                        "command": command,
                        "stdout": _bounded_output(stdout),
                        "stderr": _bounded_output(stderr, "\nStep timed out" if timed_out else ""),
                        "exit": None if timed_out else process.returncode,
                    }
                    record["passed"] = not timed_out and _matches(record, expect)
                result["steps"].append(record)
                if not record["passed"]:
                    break
            result["passed"] = len(result["steps"]) == len(steps) and all(
                item["passed"] for item in result["steps"]
            )
    return results


def _bounded_output(stream, suffix="") -> str:
    limit = 64 * 1024
    marker = b"\n[truncated]\n"
    stream.seek(0)
    value = stream.read(limit + 1) + suffix.encode("utf-8")
    if len(value) > limit:
        value = value[: limit - len(marker)] + marker
    return value.decode("utf-8", errors="ignore")


def run_subject(run) -> str:
    """Legacy product evidence is recognized only at a dispatch integrated repo."""
    if "subject" in run:
        return run["subject"]
    return "product" if (Path(run["repo"]).parent / "evidence" / "run.json").is_file() else "oracle"


def run_acceptance(store, repo, env=None, *, subject=None) -> dict:
    if subject not in ("product", "oracle"):
        raise PlanStoreError(
            "acceptance-subject-required",
            "Specify the acceptance subject. Next: harness_accept_run repo=<integrated-repo> "
            "subject=product OR harness_accept_run repo=<reference-repo> subject=oracle",
        )
    spine = store.locked_acceptance_spine()
    scenarios = store.acceptance_scenarios()
    if not scenarios:
        store.save_acceptance_scenarios(derive_scenarios(spine))
        scenarios = store.acceptance_scenarios()
    revision = integrated_revision(repo)
    plan_revision = store._revision()
    results = run_scenarios(repo, scenarios, env)
    with store._transaction():
        if (
            integrated_revision(repo) != revision
            or store._revision() != plan_revision
            or store.acceptance_scenarios() != scenarios
        ):
            raise PlanStoreError("acceptance-revision-changed", "Rerun on the integrated revision")
        result = {
            "id": uuid4().hex,
            "repo": str(Path(repo).resolve()),
            "subject": subject,
            "revision": revision,
            "revisionAlgorithm": "tree-bytes-v2",
            "planRevision": plan_revision,
            "scenarioRevision": _hash(scenarios),
            "results": results,
            "passed": bool(results) and all(r["passed"] for r in results),
        }
        store.connection.execute(
            "INSERT INTO acceptance_runs VALUES (?, ?)", (result["id"], json.dumps(result))
        )
        store._event("acceptance-run", result)
    return result


def _latest_run(store) -> dict | None:
    row = store.connection.execute(
        "SELECT result_json FROM acceptance_runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def _raw_scenarios(store) -> list[dict]:
    return [
        json.loads(row[0])
        for row in store.connection.execute(
            "SELECT scenario_json FROM acceptance_scenarios ORDER BY id"
        )
    ]


def run_invalid_reason(store, run) -> str | None:
    try:
        store.locked_acceptance_spine()
    except PlanStoreError:
        return "vision-unlocked"
    if run is None:
        return "acceptance-scenarios-required"
    if run["planRevision"] != store._revision():
        return "plan-changed"
    raw = _raw_scenarios(store)
    legacy_unbound = any("nodeRevisions" not in item for item in raw)
    scenarios = store.acceptance_scenarios()
    if legacy_unbound:
        if run["scenarioRevision"] != _hash(raw) or any(
            item.get("status") == "stale" for item in scenarios if "nodeRevisions" in item
        ):
            return "scenarios-changed"
    elif run["scenarioRevision"] != _hash(scenarios):
        return "scenarios-changed"
    if not Path(run["repo"]).is_dir():
        return "product-changed"
    algorithm = run.get("revisionAlgorithm", "git-diff-v1")
    if algorithm == "git-diff-v1":
        if run["revision"] != _integrated_revision_v1(run["repo"]):
            return "product-changed"
        return "legacy-unbound" if legacy_unbound else "revision-algorithm-upgraded"
    if algorithm != "tree-bytes-v2":
        return "revision-algorithm-unsupported"
    if run["revision"] != integrated_revision(run["repo"]):
        return "product-changed"
    return "legacy-unbound" if legacy_unbound else None


def current_run(store) -> dict | None:
    run = _latest_run(store)
    return run if run_invalid_reason(store, run) is None else None


INVALID_MESSAGES = {
    "acceptance-product-required": (
        "Scenarios verified against the reference implementation (dry run); "
        "product acceptance is required."
    ),
    "revision-algorithm-upgraded": (
        "The revision algorithm was upgraded; the v1 product revision still matches."
    ),
    "product-changed": "The product revision has changed or its repository is unavailable.",
    "legacy-unbound": (
        "The saved legacy scenarios lack node revision bindings; their raw hash still matches "
        "the acceptance run. Rebind to back-fill bindings and rerun all scenarios."
    ),
    "plan-changed": "The plan has changed since the acceptance run.",
    "scenarios-changed": "The acceptance scenarios have changed since the run.",
    "vision-unlocked": "The current vision is not locked.",
    "revision-algorithm-unsupported": "The recorded revision algorithm is unsupported.",
    "acceptance-scenarios-required": "All current acceptance scenarios must pass.",
    "acceptance-approval-required": "Acceptance approval is required for the current run.",
    "acceptance-review-required": "An acceptance review is required for the current run.",
    "acceptance-findings-open": "Acceptance findings remain open.",
}


def acceptance_next(reason, repo=None) -> str:
    if reason in ("revision-algorithm-upgraded", "legacy-unbound"):
        return f"harness_accept_rebind repo={json.dumps(repo or '<integrated-repo>')}"
    if reason == "vision-unlocked":
        return "harness_record_confirmation gate=vision-lock statement=<actual-words>"
    if reason == "scenarios-changed":
        return (
            "Review and save the current scenarios with harness_accept_scenarios, "
            "then harness_accept_run repo=<integrated-repo> subject=product."
        )
    if reason == "acceptance-findings-open":
        return "Resolve open acceptance findings with harness_resolve_finding."
    if reason == "acceptance-review-required":
        return "harness_accept_review repo=<integrated-repo>"
    if reason == "acceptance-approval-required":
        return "harness_accept statement=<actual-words> by=<user-or-proxy:name>"
    return (
        "harness_accept_run repo=<integrated-repo> subject=product; "
        "obtain a fresh acceptance review and approval."
    )


def _refuse(reason, repo=None):
    raise PlanStoreError(
        reason, INVALID_MESSAGES[reason] + " Next: " + acceptance_next(reason, repo)
    )


def rebind_acceptance(store, repo, env=None) -> dict:
    """Rerun v1 evidence without changing the authorship of prior decisions."""
    with store._transaction():
        if store.phase() != "accept":
            raise PlanStoreError("acceptance-phase-required", "Next: Advance to accept first")
        old_run = _latest_run(store)
        if old_run is not None and run_subject(old_run) != "product":
            _refuse("acceptance-product-required")
        reason = run_invalid_reason(store, old_run)
        if reason not in ("revision-algorithm-upgraded", "legacy-unbound"):
            if reason is None:
                raise PlanStoreError(
                    "acceptance-rebind-not-required", "Next: Check current acceptance status"
                )
            _refuse(reason, old_run["repo"] if old_run else None)
        if Path(repo).resolve() != Path(old_run["repo"]).resolve():
            _refuse("product-changed")
        if old_run["revision"] != _integrated_revision_v1(repo):
            _refuse("product-changed")
        row = store.connection.execute(
            "SELECT result_json FROM acceptance_approvals ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        approval = json.loads(row[0]) if row else None
        if not approval or approval["runId"] != old_run["id"]:
            _refuse("acceptance-approval-required")
        if not old_run["passed"]:
            _refuse("acceptance-scenarios-required")
        if any(item["status"] == "open" for item in store.acceptance_findings()):
            _refuse("acceptance-findings-open")
        reviews = [
            json.loads(row[0])
            for row in store.connection.execute("SELECT result_json FROM acceptance_reviews")
            if json.loads(row[0])["runId"] == old_run["id"]
        ]
        if not reviews:
            _refuse("acceptance-review-required")

        if reason == "legacy-unbound":
            spine = store.locked_acceptance_spine()
            revisions = {
                row["id"]: row["revision"]
                for row in store.connection.execute("SELECT id, revision FROM nodes")
            }
            at = _now()
            for scenario in _raw_scenarios(store):
                if "nodeRevisions" in scenario:
                    continue
                refs = set(scenario.get("nodeIds", [])) | set(spine.ids("vision"))
                if refs - revisions.keys():
                    _refuse("scenarios-changed")
                steps = scenario["steps"]
                executable = bool(steps) and bool(
                    scenario["expect"] or isinstance(steps[-1], dict) and steps[-1].get("expect")
                )
                scenario.update(
                    nodeRevisions={key: revisions[key] for key in sorted(refs)},
                    nodeRevisionsBackfilled=True,
                    nodeRevisionsBackfilledAt=at,
                    by="harness",
                    status="planned" if executable else "draft",
                    executable=executable,
                )
                store.connection.execute(
                    "UPDATE acceptance_scenarios SET scenario_json=? WHERE id=?",
                    (json.dumps(scenario), scenario["id"]),
                )
            store._event(
                "acceptance-scenario-bindings-backfilled",
                {"reboundFrom": old_run["id"], "at": at, "by": "harness"},
            )
        # The old run remains immutable; this preimage checks the migrated scenario
        # set after execution while still requiring its original v1 product digest.
        old_binding = {**old_run, "scenarioRevision": _hash(store.acceptance_scenarios())}

    try:
        run = run_acceptance(store, repo, env, subject="product")
    except PlanStoreError as error:
        if error.code == "acceptance-revision-changed":
            reason = run_invalid_reason(store, old_binding)
            if reason != "revision-algorithm-upgraded":
                _refuse(reason or "product-changed")
        raise
    with store._transaction():
        # Check the old binding again after execution, under the write reservation.
        if store.phase() != "accept":
            raise PlanStoreError("acceptance-phase-required", "Next: Advance to accept first")
        reason = run_invalid_reason(store, old_binding)
        if reason != "revision-algorithm-upgraded":
            _refuse(reason or "product-changed")
        reason = run_invalid_reason(store, run)
        if reason:
            _refuse(reason)
        if _latest_run(store) != run:
            raise PlanStoreError(
                "acceptance-revision-changed", "Next: Rerun acceptance on the current revision"
            )
        if not run["passed"]:
            _refuse("acceptance-scenarios-required")
        if any(item["status"] == "open" for item in store.acceptance_findings()):
            _refuse("acceptance-findings-open")
        latest_approval = store.connection.execute(
            "SELECT result_json FROM acceptance_approvals ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if not latest_approval or json.loads(latest_approval[0]) != approval:
            _refuse("acceptance-approval-required")
        provenance = {
            "reboundFrom": old_run["id"],
            "reason": "revision algorithm upgraded; product unchanged",
            "by": "harness",
        }
        rebound_reviews = []
        for review in reviews:
            rebound = {
                "id": uuid4().hex,
                "stage": "acceptance",
                "runId": run["id"],
                "originalReviewId": review["id"],
                **provenance,
            }
            store.connection.execute(
                "INSERT INTO acceptance_reviews VALUES (?, ?)", (rebound["id"], json.dumps(rebound))
            )
            rebound_reviews.append(rebound)
        # `by` always identifies the approver. The harness actor is explicit in
        # the rebind provenance, never impersonating a fresh user/proxy decision.
        rebound_approval = {
            **approval,
            "id": uuid4().hex,
            "runId": run["id"],
            "originalApprovalId": approval["id"],
            "reboundFrom": old_run["id"],
            "reason": provenance["reason"],
            "rebind": provenance,
        }
        store.connection.execute(
            "INSERT INTO acceptance_approvals VALUES (?, ?)",
            (rebound_approval["id"], json.dumps(rebound_approval)),
        )
        result = {
            "run": run,
            "reviews": rebound_reviews,
            "approval": rebound_approval,
            **provenance,
        }
        store._event("acceptance-rebound", result)
    return result


def acceptance_review(store, repo, *, reviewers=None, runner=None, token_budgets=None) -> dict:
    spine = store.locked_acceptance_spine()
    run = current_run(store)
    if run is None or Path(run["repo"]) != Path(repo).resolve():
        raise PlanStoreError("acceptance-run-required", "Run scenarios on the current revision")
    selected = roundtable.default_reviewers() if reviewers is None else list(reviewers)
    if len({item["provider"] for item in selected}) < 2:
        raise ValueError("Acceptance review requires cross-subscription reviewers")
    included_header = (
        "## Packet contents\n\n"
        "Included: locked vision, plan context, protected tests and their template-local "
        "Python imports, current acceptance scenarios and recorded scenario results. "
        "Shards include the named feature slices; oversized bodies are explicitly marked. "
        "Not included: worker implementation, external imports, repository access or "
        "independent live product review.\n\n"
    )
    packet = (
        included_header
        + QUESTION
        + "\nReview only this packet; return a JSON array with node_id, severity "
        "(blocker, major, minor or note), finding, suggestion (empty array when no gaps).\n"
        + json.dumps(
            {
                "lockedVision": spine.node(spine.ids("vision")[0]),
                "scenarioResults": run,
                "renderedDocs": render_markdown(store.spine()),
            },
            ensure_ascii=False,
        )
    )
    packet += "\n" + roundtable._scenario_section(store.acceptance_scenarios())
    if (store.workspace / "template").is_dir():
        packet += "\n" + roundtable._test_bodies(store)
    result = {
        "id": uuid4().hex,
        "stage": "acceptance",
        "runId": run["id"],
        "reviewers": [],
        "isMocked": runner is not None,
    }
    pending = []
    for reviewer in selected:
        provider = reviewer["provider"]
        executable = provider if runner is not None else roundtable._executable(provider, None)
        if not executable:
            raise ValueError(f"Reviewer executable unavailable: {provider}")
        record = dict(reviewer)
        merged = {}
        for shard_record, items in roundtable._review_dossier(
            store,
            result,
            reviewer,
            record,
            executable=executable,
            runner=runner,
            timeout=900,
            stop_on_invalid=True,
            token_budget=(token_budgets or {}).get(provider),
            packet=packet,
            included_header=included_header,
            context=QUESTION
            + "\n"
            + json.dumps(
                {"lockedVision": spine.node(spine.ids("vision")[0]), "scenarioResults": run},
                ensure_ascii=False,
            ),
        ):
            if shard_record["invalid"]:
                # Persist diagnostic evidence (already saved before cleanup), but
                # never admit a failed shard as successful acceptance review.
                result["reviewers"].append(record)
                store._event("acceptance-review-failed", result)
                raise ValueError(
                    f"Acceptance reviewer {reviewer['name']} exited {shard_record['exitCode']}: "
                    + shard_record["rejected"][0]["reason"]
                )
            for item in items:
                roundtable._merge_finding(
                    merged, {**item, **roundtable._binding_fields(shard_record)}
                )
        record["outOfPacketTools"] = list(
            dict.fromkeys(
                tool for shard in record["shardDetails"] for tool in shard["outOfPacketTools"]
            )
        )
        record["packetBound"] = not record["outOfPacketTools"]
        for item in merged.values():
            pending.append(
                {
                    **item,
                    "id": "AF-" + uuid4().hex,
                    "stage": "acceptance",
                    "reviewer": reviewer["name"],
                    "provider": provider,
                    "status": "open",
                    "resolution": "",
                    "runId": run["id"],
                }
            )
        result["reviewers"].append(record)
    result["outOfPacketTools"] = sorted(
        {tool for item in result["reviewers"] for tool in item["outOfPacketTools"]}
    )
    result["packetBound"] = all(item["packetBound"] for item in result["reviewers"])
    with store._transaction():
        if current_run(store) != run:
            raise PlanStoreError("acceptance-revision-changed", "Rerun acceptance review")
        for finding in pending:
            store.connection.execute(
                "INSERT INTO acceptance_findings VALUES (?, ?)",
                (finding["id"], json.dumps(finding)),
            )
        store.connection.execute(
            "INSERT INTO acceptance_reviews VALUES (?, ?)", (result["id"], json.dumps(result))
        )
        store._event("acceptance-reviewed", result)
    return {**result, "findings": pending}


def acceptance_status(store) -> dict:
    previous_run = _latest_run(store)
    invalid_reason = run_invalid_reason(store, previous_run)
    run = previous_run if invalid_reason is None else None
    findings = store.acceptance_findings()
    approval = store.connection.execute(
        "SELECT result_json FROM acceptance_approvals ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    approval = json.loads(approval[0]) if approval else None
    reviews = [
        json.loads(row[0])
        for row in store.connection.execute(
            "SELECT result_json FROM acceptance_reviews ORDER BY rowid"
        )
    ]
    if invalid_reason is None:
        if run_subject(run) != "product":
            invalid_reason = "acceptance-product-required"
        elif not run["passed"]:
            invalid_reason = "acceptance-scenarios-required"
        elif any(item["status"] == "open" for item in findings):
            invalid_reason = "acceptance-findings-open"
        elif not any(review["runId"] == run["id"] for review in reviews):
            invalid_reason = "acceptance-review-required"
        elif not approval or approval["runId"] != run["id"]:
            invalid_reason = "acceptance-approval-required"
    valid = invalid_reason is None
    previous = store.connection.execute("SELECT 1 FROM acceptance_runs LIMIT 1").fetchone()
    results = {item["id"]: item for item in run["results"]} if run else {}
    scenarios = [
        dict(
            id=item["id"],
            title=item["title"],
            steps=item["steps"],
            lastResult=("pass" if results[item["id"]]["passed"] else "fail")
            if item["id"] in results
            else "not-run (stale)"
            if previous
            else "not-run",
            lastResultSubject=run_subject(run) if item["id"] in results else None,
            lastResultNote=(
                "scenarios verified against the reference implementation (dry run)"
                if run_subject(run) == "oracle" and results[item["id"]]["passed"]
                else f"{run_subject(run)} scenario result"
            )
            if item["id"] in results
            else None,
            runId=run["id"] if item["id"] in results else None,
            stepResults=results[item["id"]]["steps"] if item["id"] in results else [],
            error=results[item["id"]].get("error") if item["id"] in results else None,
        )
        for item in store.acceptance_scenarios()
    ]
    return {
        "scenarios": scenarios,
        "lastRunSubject": run_subject(previous_run) if previous_run else None,
        "findings": findings,
        "approvedBy": approval["by"] if valid else None,
        "isProxy": approval["isProxy"] if valid else None,
        "accepted": valid,
        "invalidReason": invalid_reason,
        "invalidMessage": INVALID_MESSAGES.get(invalid_reason),
        "repo": previous_run["repo"] if previous_run else None,
        "next": acceptance_next(invalid_reason, previous_run["repo"] if previous_run else None)
        if invalid_reason
        else "harness_plan_status",
        "approval": approval,
        "reviews": reviews,
        "approvalOrigin": "recorded-unverified" if approval else None,
        "humanOriginVerified": False,
        "recordedUserAccepted": valid and not approval["isProxy"],
        # Deprecated alias for one release; this does not verify human origin.
        "realUserAccepted": valid and not approval["isProxy"],
    }
