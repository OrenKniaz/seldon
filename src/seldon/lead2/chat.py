"""C8/C20: present fresh Lead choices through the existing bound chat records."""

import time
from dataclasses import asdict, replace
from pathlib import Path

from .. import decisions
from ..processes import UncertainCleanup, reconcile_call
from ..providers import role_for
from ..states import LEAD2_BUILT as BUILT
from ..states import LEAD2_MODE as MODE
from ..states import LEAD2_READY as READY
from .store import SpecStore
from .views import render_person

ROUND = "round"
SIZE = "size"
TECHNICAL = "technical"
ANYTHING_ELSE = "Anything else we missed that you'd like to include?"
APPROVE = "approve"
REVISE = "revise"
RELAY = ("Show the proposal and options. Relay exact choices and words with a fresh replyId reused on retry. "
         "Relay approval as Approve, with the exact condition if qualified.")


def manually_paused(store):
    changes = store.records("lead2_pause")
    return bool(changes and changes[-1][2]["paused"])


def set_paused(store, value):
    with store.transaction():
        if manually_paused(store) != value:
            store.record("lead2_pause", {"paused": value})


def state(store, cursor, run, active, submitted=False, full_spec=False):
    result = {"mode": MODE, "reason": cursor.failure.partition(";")[0].split("\n", 1)[0],
              "run": str(Path(run).resolve()), "status": "RUNNING" if active else "PAUSED",
              "state": cursor.state, "controller": "running" if active else "idle",
              "units": {}, "outcome": None, "waitingFor": None}
    metrics = {row["attempt"]: row for _, _, row in store.records("lead2_call_metric")}
    result["calls"] = [metrics.get(ref, {"attempt": ref, "kind": row["operation"],
                                       "elapsedSeconds": None, "freshPlusOutputTokens": None})
                       for ref, _, row in store.records("attempt")]
    if cursor.state in (READY, BUILT):
        approval = store.read(cursor.B)
        subject = approval["subject"]
        plan = store.read(subject["plan"])
        result.update(approvedSpec={
            "revision": subject["lead2_revision"], "approval": cursor.B, "scripted": approval["scripted"],
            "buildOrder": plan["build_order"], "accessNeeds": plan.get("access", []),
            "demonstrations": approval["demonstrations"],
        })
        spec = SpecStore(store)
        result["build"] = spec.build_status()
        result["build"]["unverified"] = [asdict(obs) for obs in spec.observations()
                                         if obs.outcome == "not-verifiable-here"]
    if cursor.state == BUILT:
        result.update(status="BUILT", next="The builder returned the candidate and its code map.")
    elif cursor.state == "STOPPED":
        outcomes = store.records("outcome")
        result.update(status="STOPPED", next="Use start with a new folder for your next dream.",
                      outcome=outcomes[-1][2] if outcomes else None)
    elif cursor.decision and not submitted:
        item = store.read(cursor.decision)
        ask = item["ask"]
        other = {"id": "other", "label": "Other", "means": "Your own words"}
        groups = [{**group, "options": [*group["options"], other]} for group in ask["groups"]]
        result["waitingFor"] = {"request": cursor.decision, "gate": item["gate"], "subject": item["subject"],
                                "ask": {**ask, "groups": groups}}
        more = (" Use state with full_spec=true for the full spec." if item["gate"] in {"scope", "B"} else "")
        if not active and not manually_paused(store):
            result["status"] = "WAITING"
        result["next"] = f"{RELAY} Use answer with request {cursor.decision}.{more}"
    else:
        if active:
            _show_attempt(store, cursor, result)
            result["next"] = "Use pause to pause this run."
        else:
            try:
                reconcile_call(store)
                result["next"] = "Use resume to continue this run."
            except UncertainCleanup:
                result["next"] = "Wait for the previous planner attempt to finish cleaning up."
    if full_spec:
        revision = store.read(cursor.decision)["subject"]["lead2_revision"] if cursor.decision else None
        result["spec"] = render_person(SpecStore(store), revision=revision, detailed=True)
        if cursor.leadPlan:
            result["buildOrder"] = store.read(cursor.leadPlan)["build_order"]
    return result


def _show_attempt(store, cursor, result):
    actor = ("builder" if cursor.state == READY else
             "reviewer" if cursor.state == "L2-REVIEW" and not cursor.requestedChange else "planner")
    if cursor.state in ("ALIGN", "A"):
        round_number = 1 + sum(r["gate"] == "A" for _, _, r in store.records("decision_request"))
        result["reason"] = f"The planner is working on round {round_number}."
    else:
        result["reason"] = f"The {actor} is working."
    ended = {row["attempt"] for kind in ("attempt_end", "lead2_call_metric", "provider_failure")
             for _, _, row in store.records(kind)}
    latest = {body["operation"]: (ref, body) for ref, _, body in store.records("attempt")}
    row = max((body for ref, body in latest.values() if ref not in ended),
              key=lambda body: body["startedAt"], default=None)
    if not row:
        return
    elapsed = max(0, int(time.time() - row["startedAt"]))
    duration = f"{elapsed // 60} min" if elapsed >= 60 else f"{elapsed} s"
    number = row.get("number", 1)
    role = role_for(row["operation"], {})
    result["attempt"] = {"number": number, "elapsedSeconds": elapsed,
                         "role": "builder" if role == "build" else role}
    if "silenceWindowSeconds" in row:
        result["attempt"]["silenceWindowSeconds"] = row["silenceWindowSeconds"]
    result["reason"] += f" Attempt {number}, {duration} elapsed."


def answer(request, words, reply_id, selections, refusal):
    if not reply_id or (words is None and not selections):
        raise refusal(f"Use answer request {request} with a replyId and words or selections")
    return {"request": request, "replyId": reply_id,
            **({"words": words} if words is not None else {}),
            **({"selections": selections} if selections else {})}


def group(ref, question, options):
    return {"id": ref, "question": question, "options": options, "default": options[0]["id"]}


def options(*items):
    return [{"id": ref, "label": label, "means": means} for ref, label, means in items]


def ask_technical(store):
    choices = options(("plain", "No, keep it in business terms", "Recommend and explain the stack."),
                      ("technical", "Yes, show me the technical choices",
                       "Offer stack choices with defaults."))
    request(store, "Kickoff", 0, [group(TECHNICAL, "Are you technical?", choices)],
            proposal="", summary="")


def product_groups(questions):
    grouped = {}
    for row in questions:
        for role in ("recommended", "alternative"):
            label, means = row[role].strip(), row[f"{role}_means"]
            if label and label.casefold() != "other":
                picks = grouped.setdefault(row["question"].strip(), {})
                picks.setdefault(label.casefold(), (label, means))
    return [group(f"q{i}", question, options(*((f"o{j}", *option)
             for j, option in enumerate(picks.values(), 1))))
            for i, (question, picks) in enumerate(grouped.items(), 1)]


def closing_group():
    return group("anything-else", ANYTHING_ELSE, options(("nothing", "Nothing else", "Continue.")))


def gate_group(gate, *, recommend=True):
    approved = {
        "A": ("Lock this vision at A", "Accept the displayed vision and any unanswered defaults."),
        "B": ("Approve the spec at B",
              "Accept the spec, build order, chosen demonstrations and any displayed testing limits."),
        "scope": ("Accept this change", "Update the agreed scope as shown."),
    }
    approved_label, approved_means = approved[gate]
    revise = ("Keep shaping this", "Use Other or pick a product option to say what needs to change.")
    choices = options((APPROVE, approved_label, approved_means), (REVISE, *revise))
    return group(ROUND, "How should we continue?", choices if recommend else choices[::-1])


def request(store, gate, revision, groups, *, proposal, summary, **subject):
    decisions.request(store, gate, {"lead2_revision": revision, **subject},
                      ask={"proposal": proposal, "delta": summary, "groups": groups, "retained": []})


def read_reply(store, user, ref, item):
    reply = user({"request": ref, **item})
    if not isinstance(reply, dict) or reply.get("request") != ref or not reply.get("replyId"):
        raise decisions.Waiting()
    if any(r["request"] == ref and r.get("replyId") == reply["replyId"]
           for _, _, r in store.records("user_words")):
        raise decisions.Waiting()
    ask = item["ask"]
    groups = {g["id"]: g for g in ask["groups"]}
    words = reply.get("words")
    try:
        if words is not None and (not isinstance(words, str) or not words.strip()):
            raise ValueError("Use the person's nonempty words.")
        if reply.get("selections"):
            picked = decisions.picked_options(ask["groups"], reply["selections"])
        elif words:
            picked = {ROUND: {"optionId": "other", "text": words}}
        else:
            raise ValueError("Relay the person's selection or words.")
    except ValueError as error:
        with store.transaction():
            store.record("user_words", {**reply, "words": words or "", "origin": item["origin"],
                                        "rejected": True})
            store.cursor(replace(store.cursor(), failure=str(error)))
        raise decisions.Waiting() from error
    choices = []
    for key, selection in picked.items():
        choice = selection["optionId"]
        question = groups.get(key, {"question": "What should change?", "options": []})
        option = next((o for o in question["options"] if o["id"] == choice), None)
        choices.append({"group": key, "question": question["question"], **selection,
                        "label": option["label"] if option else "Other",
                        "meaning": option["means"] if option else selection["text"]})
    material = [c for c in choices if c["group"] != ROUND or c["optionId"] == "other"]
    return reply, words or "\n".join(c["text"] or c["label"] for c in (material or choices)), choices
