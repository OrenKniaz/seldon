"""C9/C20: project saved Fresh Lead events and spec revisions for the status pane."""

import re
from collections import deque
from contextlib import closing
from datetime import datetime
from textwrap import shorten

from ..store import Store
from .evidence import promise_status
from .store import SpecStore

STAGE = {"NEW": 0, "ALIGN": 1, "A": 1, "L2-STACK": 2,
         "L2-SPEC": 3, "L2-REVIEW": 4, "B": 5, "L2-READY": 5}
ACTIVITY = ["Your idea", "Asking round {round}", "Agreeing the stack", "Writing the plan",
            "Review {review} of 3", "Preparing your approval"]
EVENTS = ("transition", "attempt", "answer", "attempt_end", "decision_request", "user_words")


def _frame(state, revision, rounds, reviews, gate="", detail=""):
    stage = 2 if gate.casefold() == "stack" else STAGE[state]
    activity = ACTIVITY[stage].format(round=rounds, review=max(1, reviews))
    if state == "L2-READY":
        detail = "Plan approved at B"
    elif gate:
        detail = {"A": f"Align · round {rounds}", "B": "Approve the plan at B"}.get(gate, gate)
    return dict(stage=stage, revision=revision, rounds=rounds, reviews=reviews,
                activity="Waiting on you" if gate else detail or activity,
                detail=detail if gate else "", approved=state == "L2-READY")


def _timeline(store, revisions):
    state, gate, revision, rounds, reviews = "NEW", "", 0, 1, 0
    frames = [_frame(state, revision, rounds, reviews)]
    history = deque((ref, datetime.fromisoformat(when).timestamp()) for ref, when in revisions)
    records = sorted((int(ref[1:]), kind, body) for kind in EVENTS for ref, _, body in store.records(kind))
    finished = {body["result"]: body["finishedAt"] for _, kind, body in records if kind == "attempt_end"}

    for _, kind, event in records:
        detail = ""
        # A revision written by the answer belongs before the transition it caused.
        before = finished.get(event.get("cause"), event.get("startedAt", event.get("finishedAt", 0)))
        subject = event.get("subject", {}).get("lead2_revision", 0) or 0
        while history and (history[0][1] <= before or history[0][0] <= subject):
            revision, _ = history.popleft()
            frames.append(_frame(state, revision, rounds, reviews, gate))

        if kind == "transition" and event["unit"] is None:
            if state == "A" and event["next"] == "ALIGN":
                rounds += 1
            if state == "B" and event["next"] == "L2-SPEC":
                reviews = 0
            gate = ""
            state = event["next"] if event["next"] in STAGE else state
            if event["event"] == "trace-progress":
                detail = f"Review {reviews} of 3 · fixing trace gaps"
        elif kind == "decision_request":
            gate = event["gate"]
        elif kind == "user_words" and not event.get("rejected"):
            gate = ""
        elif kind == "attempt" and event["operation"] == "REVIEW" and not event.get("retryOf"):
            reviews += 1
        elif kind == "attempt" and state == "L2-REVIEW" and event["operation"] == "LEAD2-SPEC":
            detail = f"Review {reviews} of 3 · applying your choice"
        frame = _frame(state, revision, rounds, reviews, gate, detail)
        if frame != frames[-1]:
            frames.append(frame)
        if kind == "answer" and (choices := event["answer"].get("stack")):
            frames.append({**_frame("L2-STACK", revision, rounds, reviews),
                           "activity": "Stack recommendations recorded",
                           "detail": " · ".join(choice["decision"] for choice in choices)})
    for revision, _ in history:
        frames.append(_frame(state, revision, rounds, reviews, gate))
    return state, frames


def _label(text):
    clause = re.split(r"[.!?;:](?:\s|$)|\s[—–]\s|,\s", text.strip(), maxsplit=1)[0]
    words = (clause if len(clause.split()) >= 3 else text).split()
    return shorten(" ".join(words[:8]), width=60, placeholder="…")


def _cell(node, primary, *fields):
    return {"label": _label(node.get("title") or node[primary]),
            "text": "\n\n".join(node[key] for key in (primary, *fields) if node[key])}


def _idea(words, graph):
    heading, _, rest = words.partition("\n")
    named = heading.startswith("# ")
    summary = heading[2:] if named else _label(words)
    match = re.search(r"\b(?:I['’]m|I am|My name is) ([A-Z][\w’'-]*(?: [A-Z][\w’'-]*)*)\b",
                      rest if named else words)
    name = match[1] if match else ""
    owner = "Dream owner"
    if name:
        owner = next((node["role"].partition(":")[0] for node in graph.nodes.values()
                      if node["kind"] == "person" and re.search(rf"\b{re.escape(name)}\b", node["role"])),
                     f"{name} · Dream owner")
    return {"label": summary, "owner": owner, "text": words}


def _rows(spec, revision, commit):
    if not revision:
        return []
    graph = spec.snapshot(revision).graph
    statuses = promise_status(spec, revision, commit)
    rows = []
    for ref, node in graph.nodes.items():
        if ref not in statuses:
            continue
        stories = graph.follow(ref, upstream=True)
        children = graph.follow(ref)
        story_nodes = [(key, item) for key, item in graph.nodes.items()
                       if key in stories and item["kind"] == "story"]
        rows.append(dict(
            group=story_nodes[0][0] if story_nodes else "",
            feature=_cell(node, "result", "action", "not_met_if"),
            stories=[_cell(item, "story") for _, item in story_nodes],
            checks=[_cell(item, "action", "expected") for key, item in graph.nodes.items()
                    if key in children and item["kind"] == "check"],
            walks=[_cell(item, "scenario", "interruption") for key, item in graph.nodes.items()
                   if key in children and item["kind"] == "walk"],
            modules=[_cell(item, "decision", "reason", "words") for key, item in graph.nodes.items()
                     if key in children and item["kind"] == "design"],
            status=statuses[ref],
        ))
    return rows


def status(project):
    with closing(Store(project, readonly=True)) as store:
        store.db.execute("BEGIN")
        spec = SpecStore(store, readonly=True)
        revisions = spec.revisions()
        state, frames = _timeline(store, revisions)
        cursor = store.cursor()
        decision = store.read(cursor.decision) if cursor.decision else {}
        revision = decision.get("subject", {}).get("lead2_revision")
        if revision is None:
            revision = spec.snapshot().revision if revisions else 0
        counts = frames[-1]
        live = _frame(cursor.state if cursor.state in STAGE else state, revision,
                      counts["rounds"], counts["reviews"], decision.get("gate", ""))
        if cursor.state == "STOPPED" or not decision and (cursor.failure or (
            (pauses := store.records("lead2_pause")) and pauses[-1][2]["paused"]
        )):
            live.update(activity="Stopped" if cursor.state == "STOPPED" else "Paused",
                        detail=cursor.failure.partition(";")[0] or live["detail"])
        elif live["stage"] == 4 and not decision and frames[-1]["revision"] == revision:
            live = frames[-1]
        if frames[-1] != live:
            frames.append(live)
        words = store.read(cursor.kickoff)["words"].strip()
        idea = _idea(words, spec.snapshot(revision).graph)
        return {"idea": idea, "frames": frames,
                "trace": {ref: _rows(spec, ref, cursor.candidate) for ref in {f["revision"] for f in frames}}}
