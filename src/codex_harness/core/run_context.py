"""Fresh semantic packets from canonical facts; no provider session is resumed."""

import json
import sys


def direction_content(record):
    return dict(meaning={k:v for k,v in record["meaning"].items() if k not in {"reply","rationale"}},rationale=record["rationale"])


def recovery_context(store, run):
    """Retain observed attempts by admitted work, including unentered successors."""
    from pathlib import Path
    from codex_harness.core import planner_jobs as jobs, planner_recovery as recovery, approvals
    from codex_harness.core.record_io import read_record_json
    context = dict(jobId=run.get("jobId"), observation=run.get("observation"), publicationRevision=run.get("publicationRevision"))
    if not run.get("jobId"):
        return context
    status = jobs.worker_job_status(store.workspace, run["jobId"])
    resolved = recovery.disposition(store, run["jobId"]) or {}
    proof = resolved.get("proof",{})
    context.update(job={k:v for k,v in status.items() if k not in {"run","options"}},
                   recovery={k:v for k,v in resolved.items() if k != "proof"})
    context["affectedNodeIds"] = proof.get("faultBeads",[])
    if status.get("run"):
        record = read_record_json(jobs._inside(jobs._directory(store.workspace,run["jobId"]), Path(status["run"]["recordPath"])))
        context["affectedNodeIds"] = context["affectedNodeIds"] or list(record["beads"])
        context["attempts"] = {key:[{**{k:v for k,v in bead.items() if k in {"status","blocker","workerReport","requirementRefs"}},
            "attempt":{k:v for k,v in (bead.get("attempt") or {}).items() if k in {"status","exitCode","startedAt","finishedAt","changedPaths","requirementRefs"}}}
            for bead in [*record["beads"][key].get("superseded",[]),record["beads"][key]]] for key in record["beads"]}
    work = {key:o["workId"] for key,o in status.get("terminal",{}).get("outcomes",{}).items()}
    affected = set(context["affectedNodeIds"]) | context.get("attempts",{}).keys() | work.keys()
    for event_id, raw in store.connection.execute("SELECT id,payload_json FROM events WHERE kind=? ORDER BY id DESC", (recovery.RESOLVED,)):
        prior = json.loads(raw)
        if (prior["jobId"],prior.get("segmentId")) == (run["jobId"],status.get("terminal",{}).get("segmentId")): continue
        for key, identifier in prior["proof"].get("faultWork",{}).items():
            if work.get(key) != identifier: continue
            source = dict(eventId=event_id,kind=recovery.RESOLVED,jobId=prior["jobId"],segmentId=prior["segmentId"],workId=identifier,historical=True)
            outcome = dict(workId=identifier,workStatus=prior["proof"]["faultOutcomes"][key])
            context.setdefault("attempts",{}).setdefault(key,[]).append(dict(blocker=outcome["workStatus"],source=source))
            terminal = context["job"]["terminal"]
            context["job"]["terminal"] = dict(terminal,retainedWorkObservations=[*terminal.get("retainedWorkObservations",[]),dict(source,outcome=outcome)])
    directions = {}
    for r in approvals.records(store,"judgment-recorded"):
        if (r["question"] == "loop-direction" and r.get("meaning")
            and (r["meaning"].get("option") in {"repair","replan","research"} or r["meaning"].get("amendments"))
            and any(s["payload"].get("context",{}).get("jobId") == run["jobId"] or affected.intersection(s["payload"].get("context",{}).get("affectedNodeIds",[])) for s in r["sourceRefs"])):
            directions.setdefault(json.dumps(direction_content(r),sort_keys=True),dict(id=r["id"],meaning=r["meaning"],rationale=r["rationale"]))
    context["priorDirections"] = list(reversed(list(directions.values())[:3]))
    return context


def relevant_ids(store, family, purpose, context, judgment=None):
    """Select the question's dependency slice, including its upstream meaning."""
    spine = store.spine()
    visions = set(spine.ids("vision"))
    if purpose in {"understanding", "planning-size", "vision-expansion", "vision-intent"}:
        keys = {n["id"] for n in store.list_nodes("feature") if n["fields"].get("baseline")}
        for key in list(keys):
            baseline = spine.node(key)["fields"]["baseline"]
            keys.update(ref["id"] for name in ("codeRefs", "testRefs", "resultRefs") for ref in baseline[name])
    elif purpose in {"finding-dispositions", "finding-disposition", "finding-equivalence"}:
        members = context.get("members", [])
        if judgment:
            members = next((r["payload"].get("facts", {}).get("members", []) for r in judgment["sourceRefs"] if r["payload"].get("facts", {}).get("members")), members)
        keys = {m.get("node_id") for m in members} & set(spine.ids())
    elif family in {"author", "repair"}:
        # A complete plan is one meaningful slice. Runtime history and kickoff
        # selection nodes are not authored by the planner.
        keys = {n["id"] for n in store.list_nodes() if n["kind"] in {
            "feature", "story", "requirement", "architecture", "design", "contract", "test", "bead", "research", "metric", "stop-condition", "amendment"
        }}
        keys.update(ref["id"] for key in list(keys) for ref in spine.node(key)["fields"].get("decisionRefs", []))
    elif family == "direction":
        keys = (set(context.get("affectedNodeIds", [])) | context.get("attempts", {}).keys()) & set(spine.ids())
    elif judgment:
        keys = {r["id"] for r in judgment["subjectRefs"] + judgment["evidenceRefs"]}
        observation = next((r["payload"] for r in judgment["sourceRefs"] if r["kind"] == "candidate-observation"), None)
        if observation:
            from codex_harness.core.spine import scenario_nodes
            aliases = scenario_nodes(spine)
            keys = set()
            for result in observation["proof"]["results"]:
                identity = result["id"]
                keys.update([identity] if identity in spine.ids() else aliases.get(identity, []))
    else:
        keys = set(context.get("affectedNodeIds", [])) & set(spine.ids())
    for key in list(keys):
        keys.update(spine.ancestors(key))
    return sorted((keys | visions) - {n["id"] for n in store.list_nodes("decision") if n["fields"].get("schema") == "dot-reviewer-selection-v1"})


def evidence_facts(record):
    """The dossier already contains its packet; do not prefill it a second time."""
    result = []
    for ref in record["sourceRefs"]:
        if ref["kind"] == "loop-question":
            continue
        payload = dict(ref["payload"])
        if "facts" in payload:
            payload["facts"] = {k:v for k,v in payload["facts"].items() if k not in {"packet","question"}}
        result.append(payload)
    return result


def repository_facts(workspace):
    """Bounded local source/test observations, using the existing path/read rules."""
    import os
    from pathlib import Path
    from codex_harness.core.acceptance_evidence import SOURCE_SUFFIXES
    from codex_harness.core.semantic_schema import path as confined_path
    from codex_harness.core.semantic_runner import read, ObservationUnavailable
    root, pending, paths, visited = Path(workspace).resolve(), [Path(workspace).resolve()], [], 0
    while pending and visited < 512:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                visited += 1
                if visited > 512:
                    break
                item = Path(entry.path)
                if entry.name.startswith(".") or item.is_symlink() or item.is_junction():
                    continue
                item = confined_path(root,item.relative_to(root).as_posix())
                if item.is_dir():
                    if item.name not in {"node_modules","__pycache__","venv","dist","build"}:
                        pending.append(item)
                elif item.suffix.lower() in SOURCE_SUFFIXES or item.name in {"README.md","pyproject.toml","package.json"}:
                    paths.append(item)
    truncated, budget, records = bool(pending) or visited >= 512, 65536, []
    for item in sorted(paths,key=lambda p:(p.name not in {"README.md","pyproject.toml","package.json"},len(p.parts),str(p)))[:128]:
        item = confined_path(root,item.relative_to(root).as_posix())
        entry = dict(path=item.relative_to(root).as_posix(),bytes=item.stat().st_size)
        try:
            if entry["bytes"] > min(budget,16384):
                raise ObservationUnavailable("Outside packet content budget; obtain focused evidence before judging behavior")
            raw = read(item,min(budget,16384))
            entry["content"] = raw.decode("utf-8")
            budget -= len(raw)
        except (ObservationUnavailable,UnicodeDecodeError) as error:
            entry["contentStatus"] = str(error)
        records.append(entry)
    return dict(root=str(root),files=records,discoveryTruncated=truncated,
        omittedFiles=None if truncated else max(0,len(paths)-len(records)),
        evidenceLimit="Inventory/source are observations, not proof of behavior; omitted context requires focused evidence")


def packet(store, pending):
    from codex_harness.core.run_loop import cursor

    run = cursor(store)
    record = store.judgment(pending["requestId"], candidate=pending["candidate"])
    # Source payloads carry actual observations, review members and displayed
    # consent subjects. A digest or a locator alone cannot inform a tool-free judge.
    sources = evidence_facts(record)
    dialogue = [json.loads(r[0]) for r in store.connection.execute(
        "SELECT payload_json FROM events WHERE kind='chat-message' ORDER BY id DESC LIMIT 12")]
    selected = relevant_ids(store, pending["family"], pending["purpose"], pending.get("context", {}), record)
    spine = store.spine()
    nodes = [spine.node(key) for key in selected]
    for source in sources:
        facts = source.get("facts", {})
        if "currentNodes" in facts:
            facts["currentNodes"] = [n for n in facts["currentNodes"] if n["id"] in selected]
        if isinstance(facts.get("baseline"), dict) and "nodes" in facts["baseline"]:
            facts["baseline"] = dict(facts["baseline"], nodes=[n for n in facts["baseline"]["nodes"] if n["id"] in selected])
    from codex_harness.core.project_memory import ProjectMemoryStore
    memory = ProjectMemoryStore(store.workspace)
    cited_memory = [dict(id=e.id, text=e.text, provenance=e.provenance.__dict__) for e in memory.confirmed_slice(limit=8, max_chars=4000)] if memory.path.exists() else []
    return dict(
        question=pending["purpose"], family=pending["family"],
        answerSchema=pending["answerSchema"], examples=pending.get("examples", []),
        exactFix=record["error"],
        facts=dict(
            setup=run["setup"], vision=[n for n in nodes if n["kind"] == "vision"],
            executionSetup=dict(pythonExecutable=sys.executable, pythonVersion=sys.version,
                descriptorForms="Use exactly one candidate-owned entry or dotted module; the other is null. "
                "entry uses an absolute installed runtime executable (or null for a directly executable entry); "
                "module uses controller Python and must name a candidate package, never unittest or another standard-library module. "
                "argv holds arguments only; testRoots names candidate-owned test directories. "
                "These are observed controller facts, not proof of a requested product runtime version.")
                if pending["purpose"] in {"plan-outline", "trace"} else None,
            acceptedVision=json.loads(store._meta()["locked_vision_json"]) if store._meta()["locked_vision_json"] else None,
            graph=[n for n in nodes if n["kind"] != "vision"],
            referenceInventory=[{k:n[k] for k in ("id","kind","title")} for n in spine.to_dict()["nodes"] if n["id"] not in selected] if pending["purpose"] == "trace" else [],
            repository=repository_facts(store.workspace) if pending["family"] == "author" else None,
            dialogue=list(reversed(dialogue)), sources=sources,
            assumptions=[dict(r) for r in store.connection.execute("SELECT * FROM assumptions")],
            openQuestions=[dict(r) for r in store.connection.execute("SELECT * FROM clarifications")],
            context=pending.get("context", {}),
            authoringFailure=record.get("diagnostics"),
            rejectedResponse=json.loads(store.connection.execute("SELECT payload_json FROM events WHERE id=?", (record["rejectedResponseEventId"],)).fetchone()[0]) if record.get("rejectedResponseEventId") else None,
            memory=cited_memory, observation=run.get("observation") if pending["family"] == "author" and "observation" not in pending.get("context",{}) else None, displayedSubject=run.get("userRequest"),
        ),
    )


def review_packet(reviewer, packet, *, exact_fix=None):
    from tempfile import gettempdir
    from pathlib import Path
    from codex_harness.core.run_schemas import question_spec
    packet, raw_requests = packet.split("\n## Bound review requests\n", 1)
    record = json.loads(raw_requests)[reviewer["name"]]
    acceptance = any(r["payload"].get("facts", {}).get("reviewBinding", {}).get("runId") for r in record["sourceRefs"])
    answer_schema, examples = question_spec("assess", "full-dream" if acceptance else "plan-review")
    from codex_harness.core.run_questions import acceptance_inventory
    inventory = acceptance_inventory(record) if acceptance else {}
    schema_path = Path(gettempdir()) / ("seldon-review-schema-" + record["id"] + ".json")
    packet += "\n## Review question\n" + json.dumps(dict(
        question=("Does this integrated candidate deliver the accepted dream?" if acceptance else
                  "Before B, can this plan deliver the whole accepted dream? Judge coverage, feasibility and planned verification. "
                  "Implementation and execution evidence belong after Build; do not request them or return inconclusive because they are absent.")
                 + " State review depth and its consequence-based reason.",
        answerSchema=answer_schema, **inventory,
        evidence=evidence_facts(record),
        exactFix=exact_fix or record["error"],
        example=examples[0]), ensure_ascii=False)
    return packet, answer_schema, schema_path
