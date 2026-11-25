"""C1/C8/C23/C27: fresh Lead from kickoff through the reviewed, approved spec."""

from dataclasses import replace
from functools import partial

from .. import decisions, inference
from ..answers import ConsumptionFailure
from ..processes import reconcile_call
from ..questions import AnswerRejected
from ..states import LEAD2_RUN, TERMINAL
from ..states import advance as advance_run
from . import chat, prompts
from .contracts import normalise_edits, typed_reply
from .graph import review_issues
from .store import SpecStore
from .views import b_summary, planning_facts, planning_view, scope_summary

advance = partial(advance_run, transitions=LEAD2_RUN)


class UnusableAnswer(ValueError):
    pass


def step(store, provider, user):
    cursor = store.cursor()
    if cursor.state in TERMINAL:
        return cursor.state
    spec = SpecStore(store)
    if cursor.decision:
        _answer(store, spec, user, cursor)
    elif cursor.state == "B":
        _offer_b(store, spec, cursor)
    else:
        _plan(store, spec, provider, cursor)
    return store.cursor().state


def _plan(store, spec, provider, cursor):
    reconcile_call(store)
    snapshot = spec.snapshot()
    kickoff = store.read(cursor.kickoff)["words"]
    if not snapshot.revision:
        _add_words(spec, snapshot.revision, kickoff, "Record the initial brief")
        snapshot = spec.snapshot()
    kind = {"ALIGN": "LEAD2-ALIGN", "L2-SPEC": "LEAD2-SPEC", "L2-REVIEW": "REVIEW"}[cursor.state]
    proposal, quoted_replies, refs = planning_facts(snapshot.graph, kickoff)
    size = next((item["size"] for _, _, item in reversed(store.records("approval")) if "size" in item), None)
    facts = {"Earlier replies": quoted_replies, "Owner's chosen size": size}
    if kind == "LEAD2-ALIGN" and cursor.vision:
        facts["Previous questions and options"] = store.read(cursor.vision)["answer"]["questions"]
    elif cursor.leadPlan and kind != "LEAD2-ALIGN":
        facts["Proposed build order"] = store.read(cursor.leadPlan)["build_order"]
    if kind == "REVIEW":
        facts["Missing proof links"] = review_issues(snapshot.graph)
    question = prompts.question(kind, kickoff, proposal, **{k: v for k, v in facts.items() if v})
    try:
        inference.call(store, provider, question, normalise=typed_reply,
                       consume=lambda ref, answer: _propose(store, spec, snapshot, ref, answer, size, refs))
    except (AnswerRejected, ConsumptionFailure) as error:
        reason = (str(error).partition("; ")[2] if isinstance(error, AnswerRejected)
                  else str(error.__cause__ or error))
        store.record("lead2_unusable_answer", {"reason": reason})
        raise UnusableAnswer(f"Planning paused. {reason}") from error


def _add_words(spec, revision, words, reason):
    spec.apply({"revision": revision, "reason": reason, "mode": "substantive",
                "edits": [{"op": "add", "name": None, "node": {"kind": "dream", "words": words}}]},
               author="owner")


def _propose(store, spec, snapshot, answer_ref, answer, size, references):
    cursor = store.cursor()
    edits, ignored = normalise_edits(snapshot.graph, answer, references)
    if ignored:
        store.record("lead2_normalisation", {"answer": answer_ref, "ignoredEdits": ignored})
    updated = snapshot
    if edits:
        batch = {"revision": snapshot.revision, "reason": answer["reason"].strip() or "Plan the product",
                 "mode": "substantive", "edits": edits}
        result = spec.apply(batch, author="reviewer" if cursor.state == "L2-REVIEW" else "planner")
        updated = spec.snapshot(result.revision)
    plan = cursor.leadPlan if cursor.state == "L2-REVIEW" else None
    if order := answer["build_order"]:
        plan = store.record("lead2_plan", {"build_order": order, "source": answer_ref})
    store.cursor(replace(cursor, leadPlan=plan, failure=""))
    if cursor.state == "ALIGN":
        _offer_a(store, updated, answer_ref, answer, size)
    elif not updated.accepted:
        chat.request(store, "scope", updated.revision, [chat.gate_group("scope")],
                     proposal=scope_summary(snapshot, updated), summary="Changes to the agreed scope",
                     **({"review": answer_ref} if cursor.state == "L2-REVIEW" else {}))
    elif cursor.state == "L2-SPEC":
        advance(store, "planned", answer_ref)
    else:
        _finish_review(store, snapshot.graph, updated, answer_ref, answer)


def _finish_review(store, before, updated, answer_ref, answer):
    if groups := chat.product_groups(answer["questions"]):
        chat.request(store, "Review choices", updated.revision, groups,
                     proposal=planning_view(updated.graph), summary=answer["findings"])
        return
    gaps = review_issues(updated.graph)
    if not store.cursor().leadPlan:
        raise ValueError("The plan is missing its build order.")
    progress = gaps and len(gaps) < len(review_issues(before))
    advance(store, "trace-progress" if progress else "reviewed", answer_ref, review=answer_ref)


def _offer_a(store, snapshot, source, answer, chosen_size):
    proposal = planning_view(snapshot.graph)
    if not proposal:
        raise ValueError("Describe at least one story before asking the owner to lock the vision.")
    size = chosen_size or answer["size"]
    sizes = [size, *(key for key in prompts.SIZES if key != size)]
    choices = chat.options(*((key, key.capitalize(), prompts.SIZES[key].capitalize()) for key in sizes))
    choices[0]["means"] = answer["size_reason"] if size == answer["size"] else "You chose this size."
    groups = [*chat.product_groups(answer["questions"]),
              chat.group(chat.SIZE, "How large is the dream?", choices),
              chat.gate_group("A", recommend=answer["aligned"])]
    advance(store, "aligned", source, vision=source)
    chat.request(store, "A", snapshot.revision, groups, proposal=proposal,
                 summary=answer["reason"], planner_aligned=answer["aligned"], size=size)


def _offer_b(store, spec, cursor):
    snapshot = spec.snapshot()
    order = store.read(cursor.leadPlan)["build_order"]
    groups = []
    for index, delivery in enumerate(order, 1):
        outcome = delivery["outcome"]
        choices = chat.options(("skip", "Skip demonstration", "Keep this in the build order."),
                               ("show", "Show me this working", "Demonstrate this delivery."))
        if delivery["recommend_demo"]:
            choices.reverse()
        groups.append(chat.group(f"delivery-{index}", f"Demonstrate this delivery? {outcome}", choices,
                                 default="skip"))
    revised = any(r["gate"] == "B" for _, _, r in store.records("decision_request"))
    chat.request(store, "B", snapshot.revision, [*groups, chat.gate_group("B")],
                 proposal=b_summary(snapshot.graph, order, {}, revised=revised), summary="Reviewed spec",
                 plan=cursor.leadPlan, review=cursor.review, revised=revised)


def _answer(store, spec, user, cursor):
    ref = cursor.decision
    item = store.read(ref)
    reply, words, choices = chat.read_reply(store, user, ref, item)
    subject, gate = item["subject"], item["gate"]
    revision = subject["lead2_revision"]
    by_group = {c["group"]: c for c in choices}
    explicit = by_group.get(chat.ROUND, {}).get("optionId") == chat.APPROVE
    changes = bool(reply.get("words")) or _changes(item["ask"], choices, gate)
    approve = explicit and not changes
    effect = "approve" if approve else "direct"
    with store.transaction():
        expected = spec.snapshot(revision).parent if gate == "scope" else revision
        if store.cursor().decision != ref or spec.snapshot().revision != expected:
            raise ValueError("The proposal changed. Reopen the current proposal before recording a reply.")
        raw = store.record("user_words", {**reply, "words": words, "origin": item["origin"],
                                         "effect": effect if changes or approve else "continue"})
        store.cursor(replace(cursor, decision=None, failure=""))
        if gate == "B" and not explicit and not changes and chat.ROUND not in by_group:
            ask = item["ask"]
            retained = {c["group"]: c for c in ask["retained"]} | by_group
            order = store.read(subject["plan"])["build_order"]
            proposal = b_summary(spec.snapshot(revision).graph, order, retained,
                                 revised=subject.get("revised", False))
            decisions.request(store, "B", subject, ask={**ask, "proposal": proposal,
                                                        "retained": list(retained.values())})
            return
        receipt = {"request": ref, "subject": subject, "words": raw, "userWords": raw,
                   "scripted": store.read(cursor.kickoff)["setup"].get("scriptedUser", False),
                   "effect": effect}
        if gate == "A":
            receipt["size"] = by_group.get(chat.SIZE, {}).get("optionId", subject["size"])
            if receipt["size"] not in prompts.SIZES:
                receipt["size"] = subject["size"]
        if approve and gate == "B":
            picked = {c["group"]: c for c in item["ask"]["retained"]} | by_group
            receipt["demonstrations"] = [g["id"] for g in item["ask"]["groups"]
                                         if picked.get(g["id"], {}).get("optionId") == "show"]
        approval = store.record("approval", receipt)
        if approve:
            spec.bind_decision(gate, revision, approval)
            if gate == "scope":
                if cursor.state == "L2-SPEC":
                    advance(store, "planned", approval)
                elif reviewed := subject.get("review"):
                    _finish_review(store, spec.snapshot(expected).graph, spec.snapshot(revision),
                                   reviewed, store.read(reviewed)["answer"])
            elif gate == "A":
                folded = (receipt["size"] in {"micro", "small"} and cursor.leadPlan
                          and not review_issues(spec.snapshot().graph))
                advance(store, "approve-spec" if folded else "approve", approval, A=approval,
                        leadPlan=cursor.leadPlan if folded else None)
            else:
                advance(store, "approve", approval, B=approval)
        else:
            notes = [reply["words"]] if reply.get("words") else []
            for choice in choices:
                if choice["text"] and choice["text"] not in notes:
                    notes.append(choice["text"])
                elif choice["group"].startswith("q") and not choice["text"]:
                    notes.append(f"{choice['question']} {choice['label']} — {choice['meaning']}")
            if notes:
                _add_words(spec, expected, "\n".join(notes), "Record the owner's reply")
            advance(store, "revise", approval)


def _changes(ask, choices, gate):
    defaults = {group["id"]: group["default"] for group in ask["groups"]}
    return any(c["optionId"] == "other" or c["group"].startswith("q") or
               (gate == "A" and c["group"] == chat.SIZE and c["optionId"] != defaults[chat.SIZE])
               for c in choices)


