"""C1/C16/C18/C23: run one plain builder from B to a captured candidate."""

import re
from dataclasses import replace
from pathlib import Path

from .. import gitwork, inference, lead
from ..processes import reconcile_call
from ..providers import Frozen
from ..states import LEAD2_RUN, advance
from . import build_brief, chat, chunk_review
from .graph import Delta
from .views import changed_promises, delivery_name

DELIVERY_REF = re.compile(r"\bdelivery-[0-9]+\b", re.IGNORECASE)


def step(store, spec, provider, user, pool, pending):
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
    _reviews(store, provider, pool, pending)
    before = spec.snapshot(last.get("acknowledged") or approval["subject"]["lead2_revision"])
    snapshot = spec.snapshot()
    feedback = _feedback(store, last)
    repairs = {ref: row["direction"] for ref, _, row in store.records("lead2_walk_repair")
               if ref not in last.get("feedback", [])}
    feedback.update(repairs)
    offered = {row["demo"] for _, _, row in store.records("lead2_walk_target")}
    demo = next((ref for ref, _, _ in store.records("lead2_demo_point") if ref not in offered), None)
    if demo and not pending and not feedback:
        delivery = store.read(demo)
        ref = next((ref for ref, _, row in reversed(store.records("lead2_return"))
                    if row["code_map"] and row["candidate"] == delivery["candidate"]
                    and row["revision"] == delivery["revision"]),
                   cursor.leadReturn)
        return _walk(store, ref, demo)
    limits = [obs.result for obs in spec.observations() if obs.outcome == "not-verifiable-here"]
    if last.get("outcome") == "done" and not feedback and pending:
        next(iter(pending.values())).result()
        return
    with store.transaction():
        if (last.get("outcome") == "done" and snapshot.revision == before.revision
                and spec.snapshot().revision == snapshot.revision and not feedback):
            if not _scope_question(store, spec):
                _walk(store, cursor.leadReturn, demo)
            return
    task = _task(store, provider, last, snapshot, feedback, limits)
    if task is None:
        return
    order, demos = _deliveries(store, approval)
    prompt = (f"Continue: {task}" if session else
              build_brief.brief(snapshot.graph, order, demos, store.read(cursor.kickoff)["words"]))
    facts = {"Delivery references": order, "Chosen demo references": demos,
             "Delivery": "Return done when the implementation and locally runnable checks are complete."}
    if snapshot.revision != before.revision:
        facts["Accepted changes since your last acknowledgement"] = Delta(
            before.graph, snapshot.graph).describe(references=True) or ["Apply the owner's current wording."]
    if feedback:
        facts["Review follow-up"] = list(feedback.values())
    if limits:
        facts["Owner direction for acceptance"] = [
            "Keep building. Return done once the implementation and locally runnable checks are complete.",
            "The following checks remain unverified until C. Do not wait for them or change the agreed spec.",
            *limits,
        ]
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


def _walk(store, delivery, demo):
    with store.transaction():
        target = store.record("lead2_walk_target", {"delivery": delivery, "demo": demo})
        advance(store, "walk", target, transitions=LEAD2_RUN)


def _deliveries(store, approval):
    order = store.read(approval["subject"]["plan"])["build_order"]
    order = {f"delivery-{i}": delivery_name(item) for i, item in enumerate(order, 1)}
    demos = [ref for ref in order if ref in approval["demonstrations"]]
    return order, demos


def _reviews(store, provider, pool, pending):
    for ref, future in list(pending.items()):
        if future.done():
            pending.pop(ref).result()
    for ref, chunk, verdict in chunk_review.progress(store).values():
        if chunk.get("delivery") and verdict is None and ref not in pending:
            pending[ref] = pool.submit(chunk_review.review, store, provider, ref, chunk)


def _feedback(store, last):
    sent = set(last.get("feedback", []))
    feedback = {}
    for _, chunk, verdict in chunk_review.progress(store).values():
        if verdict:
            key, assessment = verdict
            if assessment["findings"] and key not in sent:
                feedback[key] = chunk["feature"] + "\n" + "\n".join(assessment["findings"])
    return feedback


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


def _task(store, provider, last, snapshot, feedback, limits):
    if not last or last["outcome"] == "done":
        return "finish the remaining work"
    if last["outcome"] == "partial" and not last["unchanged"]:
        return last["note"]
    source = store.cursor().leadReturn
    if advice := next((row for _, _, row in reversed(store.records("lead2_build_direction"))
                       if row["source"] == source), None):
        return store.read(advice["reply"])["words"] if "reply" in advice else advice["direction"]
    facts = {"Builder report": last["note"], "Chunk findings": list(feedback.values()),
             "B access": store.read(store.read(store.cursor().B)["subject"]["plan"]).get("access", []),
             "Checks unverified until C": limits,
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
    for ref in new_demos:
        store.record("lead2_demo_point", {"delivery": ref, "outcome": order[ref],
                                        "candidate": candidate, "revision": revision})
    entries = {entry.decode().split("\t", 1)[1] for entry in gitwork.tree_entries(workspace, candidate)
               if entry.startswith((b"100644 blob ", b"100755 blob "))}
    code_map = _code_map(graph, entries, answer["code_map"])
    _chunks(store, answer["chunks"], order, revision, candidate)
    delivered = [] if store.read(source).get("interrupted") else execution["feedback"]
    body = {"candidate": candidate, "session": session, "revision": revision,
            "acknowledged": acknowledged, "outcome": answer["outcome"], "note": answer["note"],
            "unchanged": bool(last and execution["session"]) and cursor.candidate == candidate,
            "code_map": code_map,
            "feedback": sorted(set(last.get("feedback", []) + delivered))}
    ref = store.record("lead2_return", body)
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
    entries = {}
    for row in rows:
        ref = row["promise"]
        if graph.nodes.get(ref, {}).get("kind") != "promise":
            continue
        entry = entries.setdefault(ref, {"promise": ref, "modules": [], "tests": []})
        for key in ("modules", "tests"):
            paths = (path.replace("\\", "/").removeprefix("./") for path in row[key])
            entry[key] = list(dict.fromkeys([*entry[key], *(path for path in paths if path in files)]))
    return [entry for entry in entries.values() if entry["modules"] or entry["tests"]]


def _delivery_refs(value):
    return DELIVERY_REF.findall(value.partition(":")[0].casefold())


def _chunks(store, chunks, order, revision, candidate):
    completed = {}
    for row in chunks:
        for ref in _delivery_refs(row["delivery"]):
            if ref in order and row["command"] and row["command"] not in completed.setdefault(ref, []):
                completed[ref].append(row["command"])
    seen = chunk_review.progress(store)
    for ref, commands in completed.items():
        prior = seen.get(ref, (None, {}, None))[1]
        if candidate != prior.get("candidate") or commands != prior.get("commands"):
            store.record("lead2_chunk", {"delivery": ref, "feature": order[ref], "revision": revision,
                                         "candidate": candidate, "commands": commands})
