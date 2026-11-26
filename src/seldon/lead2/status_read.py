"""C9/C20: project saved Fresh Lead events and spec revisions for the status pane."""

import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from textwrap import shorten

from ..states import LEAD2_BUILT, LEAD2_READY, LEAD2_WALK, TERMINAL
from .build import _deliveries, delivery_progress
from .evidence import promise_status
from .store import SpecStore
from .timing import time_and_cost
from .walk_record import current_walk, read_walk, release_notes

BUILD_STAGES = {LEAD2_WALK: 7, "C": 8, "ACCEPTED": 8, "SCRIPTED_C": 8}
STAGE = {"NEW": 0, "ALIGN": 1, "A": 1, "L2-STACK": 2, "L2-SPEC": 3, "L2-REVIEW": 4,
         "B": 5, LEAD2_READY: 6, LEAD2_BUILT: 6, **BUILD_STAGES}
ACTIVITY = ["Your idea", "Asking round {round}", "Agreeing the stack", "Writing the plan",
            "Review round {review}", "Preparing your approval"]
EVENTS = ("transition", "attempt", "answer", "attempt_end", "decision_request", "user_words",
          "lead2_demo_point", "lead2_return", "lead2_walk", "lead2_walk_repair")
HOME_STAGES = ("Your idea", "Align the idea", "Agree the stack", "Write the plan", "Review & fix",
               "You approve", "Build", "Judge walks it", "Your OK")
INTRO = r"\b(?:I['’]m|I am|My name is) ([A-Z][\w’'-]*(?: [A-Z][\w’'-]*)*)\b"


def _frame(state, revision, rounds, reviews, gate="", detail=""):
    stage = 2 if gate.casefold() == "stack" else STAGE[state]
    activity = ACTIVITY[stage].format(round=rounds, review=max(1, reviews)) if stage < 6 else ""
    if gate:
        detail = {"A": f"Align · round {rounds}", "B": "Approve the plan at B",
                  "C": "Try this build and answer in chat"}.get(gate, gate)
    return dict(stage=stage, revision=revision, rounds=rounds, reviews=reviews,
                activity="Waiting on you" if gate else detail or activity,
                detail=detail if gate else "", approved=False)


def _build_frame(state, order, last, started, progress):
    chunk = next((order[ref] for ref, value in progress.items() if value in ("building", "planned")),
                 next(reversed(order.values()), ""))
    complete = state == LEAD2_BUILT
    activity = ("Build complete" if complete else "Ready to build" if not started else
                "Checking deliveries" if last.get("outcome") == "done" else "Building")
    return dict(chunk=chunk, features=[(order[ref], value) for ref, value in progress.items()],
                activity=activity, built=complete)


def _timeline(store, spec, revisions):
    state, gate, revision, rounds, reviews = "NEW", "", 0, 1, 0
    frames = [_frame(state, revision, rounds, reviews)]
    history = deque((ref, datetime.fromisoformat(when).timestamp()) for ref, when in revisions)
    records = sorted((int(ref[1:]), kind, body) for kind in EVENTS for ref, _, body in store.records(kind))
    finished = {body["result"]: body["finishedAt"] for _, kind, body in records
                if kind == "attempt_end" and "finishedAt" in body}
    order, last, stems, shown = {}, {}, {}, set()
    started, walking, repair = False, None, False
    judgments = {}

    def frame(detail=""):
        projected = _frame(state, revision, rounds, reviews, gate, detail)
        if state in (LEAD2_READY, LEAD2_BUILT) or state in BUILD_STAGES:
            progress = delivery_progress(order, last, shown, state, started)
            build = _build_frame(state, order, last, started, progress)
            projected["deliveryTrace"] = {ref: value for delivery, value in progress.items()
                                          for ref in stems.get(delivery, ())}
            if gate:
                build["activity"] = projected["activity"]
            elif state in BUILD_STAGES:
                build["activity"] = detail or HOME_STAGES[STAGE[state]]
            elif repair:
                build["activity"] = "Fixing walk findings"
            projected.update(build)
        if walking or state == LEAD2_WALK:
            candidate = last.get("candidate")
            rows = _judge_rows(walking, state, repair, candidate, revision)
            if walking and walking["candidate"] == candidate and walking["revision"] != revision:
                key = revision, candidate
                if key not in judgments:
                    judgments[key] = promise_status(spec, revision, candidate)
                rows = {ref: judgments[key].get(ref, value) for ref, value in rows.items()}
            projected["judgment"] = rows
        return projected

    for number, kind, event in records:
        detail = ""
        # A revision written by the answer belongs before the transition it caused.
        before = finished.get(event.get("cause"), event.get("startedAt", event.get("finishedAt", 0)))
        subject = event.get("subject", {}).get("lead2_revision", 0) or 0
        while history and (history[0][1] <= before or history[0][0] <= subject):
            revision, _ = history.popleft()
            frames.append(frame())

        if kind == "transition" and event["unit"] is None:
            if state == "A" and event["next"] == "ALIGN":
                rounds += 1
            if state == "B" and event["next"] == "L2-SPEC":
                reviews = 0
            gate = ""
            state = event["next"] if event["next"] in STAGE else state
            if state == LEAD2_WALK and event["input"] == LEAD2_READY:
                repair = False
            if state == LEAD2_READY and event["input"] == "B":
                revision = spec.pins()["B"]
                while history and history[0][0] <= revision:
                    history.popleft()
                approval = store.read(event["cause"])
                order, _ = _deliveries(store, approval)
                plan = store.read(approval["subject"]["plan"])["build_order"]
                graph = spec.snapshot(revision).graph
                stems = {ref: {p for story in row.get("stories", ()) for p in graph.follow(story)
                               if graph.nodes.get(p, {}).get("kind") == "promise"}
                         for ref, row in zip(order, plan)}
                frames.append({**_frame("B", revision, rounds, reviews),
                               "activity": "Plan approved at B", "approved": True})
        elif kind == "decision_request":
            gate = event["gate"]
        elif kind == "user_words" and not event.get("rejected"):
            gate = ""
        elif kind == "attempt" and event["operation"] == "REVIEW" and not event.get("retryOf"):
            reviews += 1
        elif kind == "attempt" and state == "L2-REVIEW" and event["operation"] == "LEAD2-SPEC":
            detail = f"Review round {reviews} · applying your choice"
        elif kind == "attempt" and state == LEAD2_READY and event["operation"] == "WORK":
            started = True
        elif kind == "lead2_demo_point":
            shown.add(event.get("delivery"))
        elif kind == "lead2_return":
            last = event
        elif kind == "lead2_walk":
            walking = read_walk(store, f"r{number}", event)
        elif kind == "lead2_walk_repair":
            repair = True
        projected = frame(detail)
        if projected != frames[-1]:
            frames.append(projected)
        if kind == "answer" and state == "L2-STACK" and (choices := event["answer"].get("stack")):
            frames.append({**_frame("L2-STACK", revision, rounds, reviews),
                           "activity": "Stack recommendations recorded",
                           "detail": " · ".join(choice["decision"] for choice in choices)})
    while history:
        revision, _ = history.popleft()
        frames.append(frame())
    return state, frames


def _judge_rows(run, state, repair, candidate, revision):
    if not run:
        return {"*": "under review"} if state == LEAD2_WALK else {}
    if "results" in run:
        states = {"works": "walked",
                  "broken": "fixing" if repair else "failed", "can't-test-here": "not-verifiable-here"}
        stale = (candidate and run["candidate"] != candidate) or run["revision"] != revision
        return {promise: "stale" if stale else states["broken" if story in run.get("blocked_stories", ())
                                                     else row["outcome"]]
                for story, row in run["results"].items() for promise in run["stories"][story]["promises"]}
    if (candidate and run["candidate"] != candidate) or run["revision"] != revision:
        return {row["promise"]: "stale" for row in run["promises"]}
    statuses = {"works": "accepted" if state == "ACCEPTED" else "proven-current",
                "broken": "fixing" if repair else "failed", "can't-test-here": "not-verifiable-here"}
    return {row["promise"]: ("under review" if row["outcome"] == "works" and not row.get("traceable", True)
                             else statuses.get(row["outcome"], "under review")) for row in run["promises"]}


def _label(text):
    clause = re.split(r"[.!?;:](?:\s|$)|\s[—–]\s|,\s", text.strip(), maxsplit=1)[0]
    words = (clause if len(clause.split()) >= 3 else text).split()
    return shorten(" ".join(words[:8]), width=60, placeholder="…")


def _cell(node, primary, *fields):
    return {"label": _label(node.get("title") or node[primary]),
            "text": "\n\n".join(node[key] for key in (primary, *fields) if node[key])}


def _idea(words, graph=None):
    heading, _, rest = words.partition("\n")
    named = heading.startswith("# ")
    summary = heading[2:] if named else _label(words)
    match = re.search(INTRO, rest if named else words)
    name = match[1] if match else ""
    owner = f"{name} · Dream owner" if name else "Dream owner"
    if name and graph is not None:
        owner = next((node["role"].partition(":")[0] for node in graph.nodes.values()
                      if node["kind"] == "person" and re.search(rf"\b{re.escape(name)}\b", node["role"])),
                     owner)
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
            promise=ref,
            group=story_nodes[0][0] if story_nodes else "",
            feature=_cell(node, "result", "action", "not_met_if"),
            stories=[{**_cell(item, "story"), "priority": item.get("priority", "must")}
                     for _, item in story_nodes],
            checks=[_cell(item, "action", "expected") for key, item in graph.nodes.items()
                    if key in children and item["kind"] == "check"],
            walks=[_cell(item, "scenario", "interruption") for key, item in graph.nodes.items()
                   if key in children and item["kind"] == "walk"],
            modules=[_cell(item, "decision", "reason", "words") for key, item in graph.nodes.items()
                     if key in children and item["kind"] == "design"],
            status=statuses[ref],
        ))
    return rows


@dataclass(frozen=True)
class HomeSummary:
    name: str
    owner: str
    idea: str
    stage: int
    label: str
    chip: str
    when: float | None

    @property
    def earlier(self):
        return self.chip in ("Built", "Done", "Stopped", "Missing")


def _home_idea(words):
    idea = _idea(words)
    body = words.partition("\n")[2].strip() if words.startswith("# ") else words
    body = re.sub(r"\A(?:#{1,6} [^\n]*\n\s*)+", "", body)
    owner = idea["owner"]
    if match := re.match(INTRO, body):
        # Keep the owner's stated role, when their introduction supplies one.
        role = re.match(r",\s*(?:(?:a|an|the)\s+([^,.!?\n]+)|and I manage\s+([^,.!?\n]+))",
                        body[match.end():])
        if role:
            owner = f"{match[1]} · {role[1] or 'manages ' + role[2]}"
        body = re.split(r"[.!?]\s+", body, maxsplit=1)[-1]
    return idea["label"], owner, shorten(body, width=140, placeholder="…")


def summary(store, when):
    kickoff, state, failure, gate, paused, previous, reviews = store.db.execute("""
        WITH current AS (SELECT cursor ->> 'state' AS state, cursor ->> 'failure' AS failure,
                                cursor ->> 'decision' AS decision, cursor ->> 'kickoff' AS kickoff FROM run),
             since AS (SELECT COALESCE(MAX(id), 0) AS id FROM record WHERE kind = 'transition'
                        AND body ->> 'input' = 'B' AND body ->> 'next' = 'L2-SPEC')
        SELECT idea.body, state, failure, request.body ->> 'gate',
               (SELECT body ->> 'paused' FROM record WHERE kind = 'lead2_pause' ORDER BY id DESC LIMIT 1),
               CASE WHEN state = 'STOPPED' THEN
                 (SELECT body ->> 'input' FROM record
                  WHERE kind = 'transition' AND body ->> 'next' = 'STOPPED' ORDER BY id DESC LIMIT 1) END,
               CASE WHEN state = 'L2-REVIEW' THEN
                 (SELECT COUNT(*) FROM record, since WHERE record.id > since.id AND kind = 'attempt'
                  AND body ->> 'operation' = 'REVIEW' AND NOT COALESCE(body ->> 'retryOf', '')) END
        FROM current JOIN record AS idea ON idea.id = CAST(substr(kickoff, 2) AS INTEGER)
        LEFT JOIN record AS request ON request.id = CAST(substr(decision, 2) AS INTEGER)
    """).fetchone()
    kickoff = json.loads(kickoff)
    if kickoff.get("mode") != "lead2":
        raise ValueError("Expected a Fresh Lead run")
    phase = previous or state
    stage = 2 if (gate or "").casefold() == "stack" else STAGE.get(phase, BUILD_STAGES.get(phase, 0))
    label = HOME_STAGES[stage]
    build = {}
    if phase in (LEAD2_READY, LEAD2_BUILT):
        cursor = store.cursor()
        last = store.read(cursor.leadReturn) if cursor.leadReturn else {}
        order, _ = _deliveries(store, store.read(cursor.B))
        shown = {row.get("delivery") for _, _, row in store.records("lead2_demo_point")}
        build = _build_frame(phase, order, last, True, delivery_progress(order, last, shown, phase, True))
        if build["chunk"]:
            label += f" · {build['chunk']}"
    if reviews:
        label += f" · round {reviews}"
    if state in TERMINAL:
        chip = "Stopped" if state == "STOPPED" else "Done"
        if state == "STOPPED":
            label = f"Stopped at {label}"
        elif state == "ACCEPTED":
            label = "Done · accepted by you"
        elif state == "SCRIPTED_C":
            label = "Done · scripted acceptance"
        elif state == LEAD2_BUILT:
            chip = "Built"
    elif gate:
        chip = "Waiting on you"
        if gate == "B":
            label = "Waiting on you · approve the plan"
    elif paused or failure:
        chip = "Paused"
    else:
        chip = (build["activity"] if build.get("activity") in ("Fixing", "Review failed") else
                "Walking" if phase == LEAD2_WALK else "Building" if stage >= 6 else "Planning")
    return HomeSummary(*_home_idea(kickoff["words"].strip()), stage, label, chip, when)


def last_activity(database):
    modified = database.stat().st_mtime
    try:
        wal = database.with_suffix(".sqlite3-wal").stat()
        if wal.st_size:
            modified = max(modified, wal.st_mtime)
    except FileNotFoundError:
        pass
    return modified


def status(store):
    spec = SpecStore(store, readonly=True)
    revisions = spec.revisions()
    state, frames = _timeline(store, spec, revisions)
    cursor = store.cursor()
    decision = store.read(cursor.decision) if cursor.decision else {}
    revision = decision.get("subject", {}).get("lead2_revision")
    if revision is None:
        revision = spec.snapshot().revision if revisions else 0
    counts = frames[-1]
    build_stage = state in (LEAD2_READY, LEAD2_BUILT) or state in BUILD_STAGES
    live = ({**counts, "revision": revision} if build_stage else
            _frame(cursor.state if cursor.state in STAGE else state, revision,
                   counts["rounds"], counts["reviews"], decision.get("gate", "")))
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
    graph = spec.snapshot(revision).graph
    idea = _idea(words, graph)
    run = current_walk(store, revision)
    return {"idea": idea, "frames": frames, "timeCost": time_and_cost(store),
            "releaseNotes": release_notes(store, run) if run else None,
            "trace": {ref: _rows(spec, ref, cursor.candidate) for ref in {f["revision"] for f in frames}}}
