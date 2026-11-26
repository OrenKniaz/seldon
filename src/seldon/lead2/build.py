"""C1/C16/C18/C23: run one plain builder from B to a captured candidate."""

import re
from dataclasses import replace
from pathlib import Path

from .. import gitwork, inference, lead
from ..processes import reconcile_call
from ..providers import Frozen
from ..states import LEAD2_RUN, advance
from . import build_brief, chat
from .graph import Delta
from .views import changed_promises, delivery_name

DELIVERY_REF = re.compile(r"\bdelivery-[0-9]+\b", re.IGNORECASE)


def step(store, spec, provider, user):
    cursor = store.cursor()
    if cursor.decision:
        return _answer(store, spec, user, cursor.decision)
    if _scope_question(store, spec):
        return
    approval = store.read(cursor.B)
    workspace = Path(cursor.scratch) / "builder"
    if not workspace.exists():
        gitwork.git(store.root.parent, "worktree", "add", "--detach", str(workspace), cursor.candidate)
    last = store.read(cursor.leadReturn) if cursor.leadReturn else {}
    session, recovered = _recover(store, spec, last)
    if recovered:
        return
    before = spec.snapshot(last.get("acknowledged") or spec.pins()["B"])
    snapshot = spec.snapshot()
    repairs = {ref: row for ref, _, row in store.records("lead2_walk_repair")
               if ref not in last.get("feedback", [])}
    feedback = {ref: row["direction"] for ref, row in repairs.items()}
    offered = {row["demo"] for _, _, row in store.records("lead2_walk_target")}
    points = store.records("lead2_demo_point")
    demo = next((ref for ref, _, _ in points if ref not in offered), None)
    if demo and not repairs:
        return _walk(store, store.read(demo).get("return", cursor.leadReturn), demo)
    if (last.get("outcome") == "done" and not feedback
            and store.read(approval["subject"]["plan"]).get("build_size") == "large"
            and not store.records("lead2_end_review")):
        return _end_review(store, provider, snapshot, last)
    with store.transaction():
        if (last.get("outcome") == "done" and snapshot.revision == before.revision
                and spec.snapshot().revision == snapshot.revision and not feedback):
            if not _scope_question(store, spec):
                _walk(store, cursor.leadReturn, demo, last.get("repairs", []))
            return
    task = _task(store, provider, last, snapshot)
    if task is None:
        return
    order, demos = _deliveries(store, approval)
    next_demo = next((ref for ref in demos if ref not in {row.get("delivery") for _, _, row in points}), None)
    prompt = (f"Continue: {task}" if session else
              build_brief.brief(snapshot.graph, order, demos, store.read(cursor.kickoff)["words"]))
    facts = {"Delivery references": order, "Chosen demo references": demos,
             "Next return": (f"Return partial at {next_demo}, with that ID in demos, before building later work."
                             if next_demo else "Return done when the product and local checks are complete.")}
    if repairs:
        facts["Repair records"] = {ref: {**row, "findings": {key: store.read(key) for key in row["defects"]}}
                                   for ref, row in repairs.items()}
    if snapshot.revision != before.revision:
        facts["Accepted changes since your last acknowledgement"] = Delta(
            before.graph, snapshot.graph).describe(references=True) or ["Apply the owner's current wording."]
    if repairs:
        facts["Story IDs"] = {ref: node["story"] for ref, node in snapshot.graph.nodes.items()
                              if node["kind"] == "story"}
        facts["Recheck"] = "Return all story IDs affected by your changes in touched. Use an empty list if none."
    question = build_brief.question("WORK", build_brief.WORK, prompt, **facts)
    question["execution"] = {"cwd": str(workspace), "session": session, "lead": cursor.B,
                             "revision": snapshot.revision, "feedback": list(feedback)}
    try:
        inference.call(store, provider, question, normalise=build_brief.normalise,
                       consume=lambda ref, answer: _returned(store, spec, ref, answer, session))
    except Frozen as error:
        if not error.cleanup_verified:
            raise
        attempt, _ = lead.attempts(store, cursor.B)[-1]
        answer = build_brief.normalise({"outcome": "blocked", "note": str(error)}, build_brief.WORK)
        with store.transaction():
            source = store.record("answer", {"attempt": attempt, "answer": answer, "interrupted": True})
            _returned(store, spec, source, answer, error.session or session)
            store.record("attempt_end", {"attempt": attempt, "result": source})


def _walk(store, delivery, demo, repair_refs=()):
    request = {"delivery": delivery, "demo": demo}
    repairs = [store.read(ref) for ref in repair_refs]
    if repairs and "prior" in repairs[-1]:
        result = store.read(delivery)
        request.update(prior=repairs[-1]["prior"],
                       stories=sorted(set(result["touched"]) | {s for row in repairs for s in row["stories"]}),
                       items=sorted({ref for row in repairs for ref in row["defects"]}))
    with store.transaction():
        target = store.record("lead2_walk_target", request)
        advance(store, "walk", target, transitions=LEAD2_RUN)


def _deliveries(store, approval):
    order = store.read(approval["subject"]["plan"])["build_order"]
    order = {f"delivery-{i}": delivery_name(item) for i, item in enumerate(order, 1)}
    demos = [ref for ref in order if ref in approval["demonstrations"]]
    return order, demos


def _end_review(store, provider, snapshot, last):
    workspace = gitwork.admit(store.root.parent, store.cursor().scratch, last["candidate"])
    question = build_brief.question("LEAD2-CHUNK", build_brief.END_REVIEW,
        "Review the integrated build once for concrete source, quality or documentation gaps. "
        "Return short release-note findings. Leave the product unchanged.",
        Candidate=last["candidate"], Stories=[node["story"] for node in snapshot.graph.nodes.values()
                                               if node["kind"] == "story"])
    evidence = Path(store.cursor().scratch) / "end-review"
    evidence.mkdir(exist_ok=True)
    question["execution"] = {"walk": workspace, "evidence": str(evidence)}
    try:
        inference.call(store, provider, question, consume=lambda source, answer:
                       store.record("lead2_end_review", {"source": source, "notes": answer["notes"]}))
    finally:
        reconcile_call(store)
        gitwork.git(store.root.parent, "worktree", "remove", "--force", workspace)


def _scope_question(store, spec):
    head = spec.snapshot()
    declined = {row["proposal"] for _, _, row in store.records("lead2_scope_declined")}
    proposals = (p for p in spec.proposals() if p.parent == head.revision and p.revision not in declined)
    if proposal := next(proposals, None):
        chat.request(store, "scope", proposal.revision, [chat.gate_group("scope")],
                     proposal="\n".join(changed_promises(head.graph, proposal.graph, detailed=True)),
                     summary="Change the agreed scope during the build")
    return proposal is not None


def _answer(store, spec, user, ref):
    item = store.read(ref)
    reply, words, choices = chat.read_reply(store, user, ref, item)
    revision = item["subject"]["lead2_revision"]
    with store.transaction():
        store.cursor(replace(store.cursor(), decision=None, failure=""))
        raw = store.record("user_words", {**reply, "words": words, "origin": item["origin"]})
        if item["gate"] == "Build question":
            store.record("lead2_build_direction", {"source": item["subject"]["source"], "reply": raw})
        elif (spec.snapshot(revision).parent == spec.snapshot().revision and not reply.get("words")
              and not any(c["text"] for c in choices)
              and any(c["group"] == chat.ROUND and c["optionId"] == chat.APPROVE for c in choices)):
            setup = store.read(store.cursor().kickoff)["setup"]
            approval = store.record("approval", {"request": ref, "subject": item["subject"],
                "userWords": raw, "effect": "approve", "scripted": setup.get("scriptedUser", False)})
            spec.bind_decision("scope", revision, approval)
        else:
            store.record("lead2_scope_declined", {"proposal": revision, "reply": raw})


def _task(store, provider, last, snapshot):
    if not last or last["outcome"] == "done":
        return "finish the remaining work"
    if last["outcome"] == "partial" and not last["unchanged"]:
        return last["note"]
    source = store.cursor().leadReturn
    if advice := next((row for _, _, row in reversed(store.records("lead2_build_direction"))
                       if row["source"] == source), None):
        return store.read(advice["reply"])["words"] if "reply" in advice else advice["direction"]
    facts = {"Builder report": last["note"], "B access": store.read(store.read(store.cursor().B)["subject"]["plan"]).get("access", []),
             "Accepted spec": [node for node in snapshot.graph.nodes.values()]}
    task = ("Answer or change approach within scope. Reserve unavailable B checks for later proof. "
            "Ask with concrete choices only when an owner decision is needed to build.")
    question = build_brief.question("LEAD2-STEER", build_brief.STEER, task, **facts)

    def save(_, answer):
        groups = chat.product_groups(answer["questions"])
        if groups:
            chat.request(store, "Build question", snapshot.revision, groups,
                         proposal=last["note"], summary=answer["direction"], source=source)
        else:
            store.record("lead2_build_direction", {"source": source, "direction": answer["direction"]})

    _, answer = inference.call(store, provider, question, normalise=build_brief.normalise, consume=save)
    return None if store.cursor().decision else answer["direction"]


def _recover(store, spec, last):
    attempts = lead.attempts(store, store.cursor().B)
    if not attempts or any(row["attempt"] == attempts[-1][0] for _, _, row in store.records("attempt_end")):
        return last.get("session"), False
    ref, attempt = attempts[-1]
    reconcile_call(store, ref)
    folder = next((Path(row["folder"]) for _, _, row in store.records("call_started")
                   if row["attempt"] == ref), None)
    with store.transaction():
        result, session = (lead.recover_return(store, ref, attempt["question"], folder, build_brief.normalise)
                           if folder else (None, None))
        if result:
            _returned(store, spec, result, store.read(result)["answer"], session)
            store.record("attempt_end", {"attempt": ref, "result": result})
    return session or last.get("session"), bool(result)


def _returned(store, spec, source, answer, session):
    cursor = store.cursor()
    last = store.read(cursor.leadReturn) if cursor.leadReturn else {}
    attempt = store.read(source)["attempt"]
    execution = store.read(store.read(attempt)["question"])["execution"]
    session = next((row["session"] for _, _, row in reversed(store.records("call_session"))
                    if row["attempt"] == attempt), session)
    revision = execution["revision"]
    graph = spec.snapshot(revision).graph
    workspace = execution["cwd"]
    candidate = _capture(workspace)
    acknowledged = (revision if answer["acknowledged"] else
                    last.get("acknowledged") or spec.pins()["B"])
    order, demos = _deliveries(store, store.read(cursor.B))
    shown = {row.get("delivery", row["outcome"]) for _, _, row in store.records("lead2_demo_point")}
    reported_demos = {ref for value in answer["demos"] for ref in _delivery_refs(value)}
    new_demos = [ref for ref in demos
                 if ref not in shown and order[ref] not in shown and ref in reported_demos]
    entries = {entry.decode().split("\t", 1)[1] for entry in gitwork.tree_entries(workspace, candidate)
               if entry.startswith((b"100644 blob ", b"100755 blob "))}
    code_map, errors = _code_map(graph, entries, answer["code_map"])
    story_ids = {ref for ref, node in graph.nodes.items() if node["kind"] == "story"}
    touched = set(answer["touched"])
    if invalid := touched - story_ids:
        inference.correct(store, source, answer,
                          ["Use exact story IDs in touched: " + ", ".join(sorted(invalid))])
        touched = story_ids
    if answer.get("correction_note"):
        errors.append(answer["correction_note"])
    delivered = [] if store.read(source).get("interrupted") else execution["feedback"]
    body = {"candidate": candidate, "session": session, "revision": revision,
            "acknowledged": acknowledged, "outcome": answer["outcome"], "note": answer["note"],
            "unchanged": bool(last and execution["session"]) and cursor.candidate == candidate,
            "code_map": code_map, "advice": errors,
            "repairs": delivered or (last.get("repairs", []) if last.get("outcome") != "done" else []),
            "touched": sorted(touched | (set(last.get("touched", [])) if last.get("outcome") != "done" else set())),
            "feedback": sorted(set(last.get("feedback", []) + delivered))}
    ref = store.record("lead2_return", body)
    for demo in new_demos:
        store.record("lead2_demo_point", {"delivery": demo, "outcome": order[demo], "return": ref})
    store.cursor(replace(cursor, leadReturn=ref, candidate=candidate, failure=""))


def _capture(workspace):
    head = gitwork.git(workspace, "rev-parse", "HEAD")
    gitwork.git(workspace, "add", "-A")
    tree = gitwork.git(workspace, "write-tree")
    if tree == gitwork.git(workspace, "rev-parse", f"{head}^{{tree}}"):
        return head
    candidate = gitwork.commit(workspace, tree, head)
    gitwork.git(workspace, "update-ref", "HEAD", candidate, head)
    return candidate


def _code_map(graph, files, rows):
    aliases = build_brief.promise_refs(graph)
    promises = set(aliases.values())
    entries, errors = {}, []
    for row in rows:
        label = row["promise"]
        ref = aliases.get(label, label)
        if ref not in promises:
            errors.append(f"Map row {label!r}: give one shown promise ID for those files.")
            continue
        entry = entries.setdefault(ref, {"promise": ref, "modules": [], "tests": []})
        for key in ("modules", "tests"):
            for raw in row[key]:
                path = raw.replace("\\", "/").removeprefix("./")
                if path not in files:
                    errors.append(f"{ref} {key}: replace {raw!r} with a tracked repository-relative path.")
                elif path not in entry[key]:
                    entry[key].append(path)
    return list(entries.values()), errors


def _delivery_refs(value):
    return DELIVERY_REF.findall(value.partition(":")[0].casefold())
