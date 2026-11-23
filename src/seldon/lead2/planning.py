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
from .contracts import normalise_edits, spec_edits, typed_reply, vision_edits
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
    replies = [r for _, _, r in store.records("user_words") if r.get("effect") == "direct"]
    graph_words = {n["words"] for n in snapshot.graph.nodes.values() if n["kind"] == "dream"}
    choices = [c for reply in replies for c in reply["choices"]]
    facts = {"Earlier replies": quoted_replies,
             "Follow-up words": "\n\n".join(r["words"] for r in replies if r["words"] not in graph_words),
             "Chosen details": "\n".join(c["meaning"] for c in choices if c["group"].startswith("q")
                                         and not c["text"])}
    if cursor.leadPlan and kind != "LEAD2-ALIGN":
        facts["Proposed build order"] = store.read(cursor.leadPlan)["build_order"]
    if kind == "REVIEW":
        facts["Missing proof links"] = review_issues(snapshot.graph)
    size = next((c["optionId"] for c in reversed(choices)
                 if c["group"] == chat.SIZE and c["optionId"] in prompts.SIZES), None)
    facts["Owner's chosen size"] = size
    last = store.records()[-1]
    if last[1] == "lead2_unusable_answer":
        issue = last[2]["reason"]
        for alias, ref in refs.items():
            issue = issue.replace(ref, alias)
        facts["Unresolved proposal issue"] = issue
    question = prompts.question(kind, kickoff, proposal, **{k: v for k, v in facts.items() if v})
    try:
        inference.call(store, provider, question, normalise=typed_reply,
                       consume=lambda ref, answer: _propose(store, spec, snapshot, ref, answer, size, refs))
    except (AnswerRejected, ConsumptionFailure) as error:
        store.record("lead2_unusable_answer", {"reason": str(error.__cause__ or error)})
        raise UnusableAnswer("The planner returned an unusable answer. The proposal is unchanged.") from error


def _add_words(spec, revision, words, reason):
    spec.apply({"revision": revision, "reason": reason, "mode": "substantive",
                "edits": [{"op": "add", "name": None, "node": {"kind": "dream", "words": words}}]},
               author="owner")


def _propose(store, spec, snapshot, answer_ref, answer, size, references):
    cursor = store.cursor()
    graph = snapshot.graph
    raw = vision_edits(graph, answer, references) if cursor.state == "ALIGN" else spec_edits(answer["edits"])
    edits = normalise_edits(graph, raw, references)
    if ignored := len(raw) - len(edits):
        store.record("lead2_normalisation", {"answer": answer_ref, "ignoredEdits": ignored})
    updated = snapshot
    if edits:
        batch = {"revision": snapshot.revision, "reason": answer["reason"].strip() or "Plan the product",
                 "mode": "substantive", "edits": edits}
        result = spec.apply(batch, author="reviewer" if cursor.state == "L2-REVIEW" else "planner")
        updated = spec.snapshot(result.revision)
    plan = cursor.leadPlan if cursor.state == "L2-REVIEW" else None
    if order := answer.get("build_order"):
        plan = store.record("lead2_plan", {"build_order": order, "source": answer_ref})
    store.cursor(replace(cursor, leadPlan=plan, failure=""))
    if cursor.state == "ALIGN":
        _offer_a(store, updated, answer_ref, answer, size)
    elif not updated.accepted:
        chat.request(store, "scope", updated.revision, [chat.gate_group("scope")],
                     proposal=scope_summary(spec, snapshot, updated), summary="Changes to the agreed scope")
    elif cursor.state == "L2-SPEC":
        advance(store, "planned", answer_ref)
    elif groups := chat.product_groups(answer["questions"]):
        chat.request(store, "Review choices", updated.revision, groups,
                     proposal=planning_view(updated.graph, detailed=True), summary=answer["findings"])
    else:
        if issues := review_issues(updated.graph):
            raise ValueError("\n".join(issues))
        advance(store, "reviewed", answer_ref, review=answer_ref)


def _offer_a(store, snapshot, source, answer, chosen_size):
    proposal = planning_view(snapshot.graph, detailed=True)
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
    chat.request(store, "B", snapshot.revision, [*groups, chat.gate_group("B")],
                 proposal=b_summary(snapshot.graph, order, {}), summary="Reviewed spec",
                 plan=cursor.leadPlan, review=cursor.review)


def _answer(store, spec, user, cursor):
    ref = cursor.decision
    item = store.read(ref)
    reply, words, choices = chat.read_reply(store, user, ref, item)
    subject, gate = item["subject"], item["gate"]
    revision = subject["lead2_revision"]
    by_group = {c["group"]: c for c in choices}
    explicit = by_group.get(chat.ROUND, {}).get("optionId") == chat.APPROVE
    changes = _changes(item["ask"], choices, gate)
    approve = explicit and not changes
    effect = "approve" if approve else "direct"
    with store.transaction():
        expected = spec.snapshot(revision).parent if gate == "scope" else revision
        if store.cursor().decision != ref or spec.snapshot().revision != expected:
            raise ValueError("The proposal changed. Reopen the current proposal before recording a reply.")
        raw = store.record("user_words", {**reply, "words": words, "origin": item["origin"],
                                         "choices": choices,
                                         "effect": effect if changes or approve else "continue"})
        store.cursor(replace(cursor, decision=None, failure=""))
        if gate == "B" and not explicit and not changes and chat.ROUND not in by_group:
            ask = item["ask"]
            retained = {c["group"]: c for c in ask["retained"]} | by_group
            order = store.read(subject["plan"])["build_order"]
            proposal = b_summary(spec.snapshot(revision).graph, order, retained)
            decisions.request(store, "B", subject, ask={**ask, "proposal": proposal,
                                                        "retained": list(retained.values())})
            return
        receipt = {"request": ref, "subject": subject, "words": raw, "userWords": raw,
                   "scripted": store.read(cursor.kickoff)["setup"].get("scriptedUser", False),
                   "effect": effect}
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
            elif gate == "A":
                advance(store, "approve", approval, A=approval, leadPlan=None)
            else:
                advance(store, "approve", approval, B=approval)
        else:
            if gate == "A" and changes:
                _add_words(spec, revision, words, "Record the owner's alignment answers")
            advance(store, "revise", approval)


def _changes(ask, choices, gate):
    defaults = {group["id"]: group["default"] for group in ask["groups"]}
    return any(c["optionId"] == "other" or c["group"].startswith("q") or
               (gate == "A" and c["group"] == chat.SIZE and c["optionId"] != defaults[chat.SIZE])
               for c in choices)


