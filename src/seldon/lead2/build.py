"""C1/C16/C18/C23: run one plain builder from B to a captured candidate."""

import re
import shlex
from dataclasses import replace
from pathlib import Path

from .. import gitwork, inference, lead
from ..processes import reconcile_call
from ..providers import Frozen
from ..states import LEAD2_RUN, advance
from . import build_brief, chat, chunk_review
from .graph import Delta
from .views import changed_promises, delivery_name


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
    if last.get("outcome") == "done":
        for future in pending.values():
            future.result()
    before = spec.snapshot(last.get("acknowledged") or approval["subject"]["lead2_revision"])
    snapshot = spec.snapshot() if session else before
    feedback = _feedback(store, last)
    limits = [obs.result for obs in spec.observations() if obs.outcome == "not-verifiable-here"]
    done = last.get("outcome") == "done"
    if done:
        order, _ = _deliveries(store, approval)
        reviewed = {name for name, (_, _, verdict) in chunk_review.progress(store).items() if verdict}
        missing = [delivery_name(item) for item in order if delivery_name(item) not in reviewed]
        if missing:
            feedback["remaining-features"] = "Report chunk test paths and argv for: " + "; ".join(missing)
        mapped = {row["promise"] for row in spec._build_map(snapshot, last)}
        if omitted := [n["result"] for ref, n in snapshot.graph.nodes.items()
                       if n["kind"] == "promise" and ref not in mapped]:
            feedback["remaining-map"] = "Complete the actual code map for: " + "; ".join(omitted)
    with store.transaction():
        if (done and snapshot.revision == before.revision
                and spec.snapshot().revision == snapshot.revision and not feedback):
            if not _scope_question(store, spec):
                advance(store, "built", cursor.leadReturn, transitions=LEAD2_RUN)
            return
    task = _task(store, provider, last, snapshot, feedback, limits)
    if task is None:
        return
    prompt = f"Continue: {task}" if session else build_brief.brief(
        before.graph, *_deliveries(store, approval), store.read(cursor.kickoff)["words"])
    facts = {}
    if snapshot.revision != before.revision:
        facts["Accepted changes since your last acknowledgement"] = Delta(
            before.graph, snapshot.graph).describe(references=False) or ["Apply the owner's current wording."]
    if feedback:
        facts["Chunk follow-up"] = list(feedback.values())
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


def _deliveries(store, approval):
    order = store.read(approval["subject"]["plan"])["build_order"]
    demos = [delivery_name(item) for i, item in enumerate(order, 1)
             if f"delivery-{i}" in approval["demonstrations"]]
    return order, demos


def _reviews(store, provider, pool, pending):
    for ref, future in list(pending.items()):
        if future.done():
            pending.pop(ref).result()
    for ref, chunk, verdict in chunk_review.progress(store).values():
        if chunk.get("version") and not chunk["missing"] and verdict is None and ref not in pending:
            pending[ref] = pool.submit(chunk_review.review, store, provider, ref, chunk)


def _feedback(store, last):
    sent = set(last.get("feedback", []))
    feedback = {}
    for feature, (ref, chunk, verdict) in chunk_review.progress(store).items():
        key, findings = verdict or (ref, chunk.get("missing", []))
        if findings and key not in sent:
            feedback[key] = feature + "\n" + "\n".join(findings)
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
    shown = {row["outcome"] for _, _, row in store.records("lead2_demo_point")}
    new_demos = [n for n in demos if n not in shown and n.casefold() in map(str.casefold, answer["demos"])]
    for name in new_demos:
        store.record("lead2_demo_point", {"outcome": name, "candidate": candidate, "revision": revision})
    entries = {name: header for header, name in
               (entry.decode().split("\t", 1) for entry in gitwork.tree_entries(workspace, candidate))}
    code_map = _code_map(graph, entries, answer["code_map"])
    _chunks(store, graph, answer["chunks"], order, new_demos, entries, revision, candidate)
    delivered = [] if store.read(source).get("interrupted") else execution["feedback"]
    body = {"candidate": candidate, "session": session, "revision": revision,
            "acknowledged": acknowledged, "outcome": answer["outcome"], "note": answer["note"],
            "unchanged": bool(last and execution["session"]) and cursor.candidate == candidate,
            "code_map": code_map, "feedback": sorted(set(last.get("feedback", []) + delivered))}
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


def _paths(value, files):
    value = value.replace("\\", "/")
    for part in [value] if value in files else re.split(r"[,;\n]| [—–] ", value):
        text = part.strip().strip("`").removeprefix("./") + " "
        if path := max((name for name in files if text.startswith(name + " ")), key=len, default=""):
            yield path


def _commands(args, files):
    if not args:
        return []
    if " " not in args[0] or args[0] in files or Path(args[0]).is_file():
        return [args]
    try:
        return [[word.strip('"') for word in shlex.split(line, posix=False)] for line in args]
    except ValueError:
        return []


def _code_map(graph, files, rows):
    names = {node["result"].casefold(): ref for ref, node in graph.nodes.items() if node["kind"] == "promise"}
    entries = {}
    for row in rows:
        if ref := names.get(row["promise"].casefold()):
            entry = entries.setdefault(ref, {"promise": ref, "modules": [], "tests": []})
            for key in ("module", "test"):
                entry[key + "s"] = list(dict.fromkeys([*entry[key + "s"], *_paths(row[key], files)]))
    return [entry for entry in entries.values() if entry["modules"] or entry["tests"]]


def _feature(features, label):
    key = label.casefold()
    if key in features:
        return features[key]
    matches = [feat for name, feat in features.items()
               if name.startswith(key) and re.match(r"\s*[:—.–-]", name[len(key):])]
    return matches[0] if len(matches) == 1 else None


def _chunks(store, graph, chunks, order, demos, entries, revision, candidate):
    names = {delivery_name(item).casefold(): delivery_name(item) for item in order}
    rows = {name: [] for name in demos}
    for row in chunks:
        if feature := _feature(names, row["feature"]):
            rows.setdefault(feature, []).append(row)
    seen = chunk_review.progress(store)
    promises = {n["result"].casefold(): n for n in graph.nodes.values() if n["kind"] == "promise"}
    for feature, checks in rows.items():
        prior = seen.get(feature, (None, {}, None))[1]
        tests = sorted({path for row in checks for path in _paths(row["test"], entries)})
        modules = {path for row in checks for path in _paths(row["module"], entries)}
        commands = [argv for row in checks for argv in _commands(row["command"], entries) if argv]
        tests = tests or sorted(set(prior.get("tests", [])) & entries.keys())
        modules = modules or (set(prior.get("version", {})) - set(prior.get("tests", []))) & entries.keys()
        commands = commands or prior.get("commands", [])
        args = {arg.replace("\\", "/").removeprefix("./").rstrip("/")
                for argv in commands for arg in argv[1:]}
        tests = sorted(set(tests) | (args & entries.keys()))
        missing = ([] if tests and commands else ["Name or add the planned tests with paths and commands."])
        if not modules and not missing:
            missing = ["Name the implemented code paths for the feature."]
        watched = modules | set(tests)
        version = {path: entries[path] for path in sorted(watched & entries.keys())}
        if missing and missing == prior.get("missing"):
            continue
        if not missing and not prior.get("missing") and candidate == prior.get("candidate"):
            continue
        if (version, missing) != (prior.get("version"), prior.get("missing")):
            results = dict.fromkeys(row["promise"].casefold() for row in checks)
            store.record("lead2_chunk", {"feature": feature, "revision": revision, "candidate": candidate,
                "promises": [promises[p] for p in results if p in promises],
                "commands": commands, "tests": tests, "version": version, "missing": missing})
