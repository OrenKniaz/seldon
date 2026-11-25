"""C1/C8/C23/C27: fresh Lead from kickoff through the reviewed, approved spec."""

from dataclasses import replace
from functools import partial

from .. import decisions, inference
from ..answers import ConsumptionFailure
from ..processes import reconcile_call
from ..questions import AnswerRejected
from ..states import LEAD2_RUN, TERMINAL
from ..states import advance as advance_run
from . import chat, prompts, review_options
from .contracts import normalise_edits, typed_reply
from .graph import review_issues
from .store import SpecStore
from .views import b_changes, b_summary, changed_promises, delivery_name, planning_facts, planning_view

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
    elif cursor.state == "ALIGN" and not cursor.vision and not store.records("lead2_owner"):
        chat.ask_technical(store)
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
    requested = cursor.requestedChange
    if requested:
        kind = "LEAD2-SPEC"
    proposal, quoted_replies, refs = planning_facts(snapshot.graph, kickoff)
    size = next((item["size"] for _, _, item in reversed(store.records("approval")) if "size" in item), None)
    facts = {"Earlier replies": quoted_replies, "Owner's chosen size": size}
    if owners := store.records("lead2_owner"):
        facts["Owner's technical preference"] = owners[-1][2]["preference"]
    if kind == "LEAD2-ALIGN" and cursor.vision:
        facts["Previous questions and options"] = store.read(cursor.vision)["answer"]["questions"]
    elif cursor.leadPlan and kind != "LEAD2-ALIGN":
        plan = store.read(cursor.leadPlan)
        facts["Proposed build order"] = plan["build_order"]
        facts["Proposed access needs"] = plan.get("access")
    if kind == "REVIEW":
        facts["Missing proof links"] = review_issues(snapshot.graph)
    if requested:
        reply = store.read(store.read(requested)["userWords"])
        if cursor.state == "L2-SPEC":
            facts["qualification"] = reply["words"]
        else:
            facts["Resolve only this review reply"] = reply["words"]
            facts["Review choices offered"] = store.read(reply["request"])["ask"]["groups"]
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
    reviewing = cursor.state == "L2-REVIEW" and not cursor.requestedChange
    edits, ignored = normalise_edits(snapshot.graph, answer, references)
    if ignored:
        store.record("lead2_normalisation", {"answer": answer_ref, "ignoredEdits": ignored})
    updated = snapshot
    if edits:
        batch = {"revision": snapshot.revision, "reason": answer["reason"].strip() or "Plan the product",
                 "mode": "substantive", "edits": edits}
        result = spec.apply(batch, author="reviewer" if reviewing else "planner")
        references = {**references, **{name.lstrip("$"): ref for name, ref in result.names.items()}}
        updated = spec.snapshot(result.revision)
    plan_ref = cursor.leadPlan if cursor.state != "ALIGN" else None
    previous = store.read(plan_ref) if plan_ref else {}
    plan = {key: answer.get(key) or previous.get(key, []) for key in ("build_order", "access")}
    if plan["build_order"] and any(value != previous.get(key, []) for key, value in plan.items()):
        plan_ref = store.record("lead2_plan", {**plan, "source": answer_ref})
    store.cursor(replace(cursor, leadPlan=plan_ref, failure=""))
    options = review_options.offer(store, updated, answer, references) if reviewing else None
    if cursor.state == "ALIGN":
        _offer_a(store, updated, answer_ref, answer, size)
    elif not updated.accepted:
        pending = {"review": answer_ref, "options": options} if reviewing else {}
        chat.request(store, "scope", updated.revision, [chat.gate_group("scope")],
                     proposal="\n".join(changed_promises(snapshot.graph, updated.graph, detailed=True)),
                     summary="Changes to the agreed scope", **pending)
    elif reviewing:
        _finish_review(store, snapshot.graph, updated, answer_ref, options)
    else:
        _finish_plan(store, answer_ref)


def _finish_plan(store, source):
    cursor = store.cursor()
    event = ("reviewed" if cursor.state == "L2-REVIEW" else
             "resolved" if cursor.requestedChange else "planned")
    advance(store, event, source, requestedChange=None)


def _finish_review(store, before, updated, answer_ref, options, groups=None):
    if options:
        chat.request(store, "Review choices", updated.revision, groups or store.read(options)["groups"],
                     proposal="", summary=store.read(answer_ref)["answer"]["findings"],
                     review=answer_ref, options=options)
        return
    gaps = review_issues(updated.graph)
    if not store.cursor().leadPlan:
        raise ValueError("The plan is missing its build order.")
    used_extra = any(row["event"] == "trace-progress" for _, _, row in store.records("transition"))
    progress = not used_extra and gaps and len(gaps) < len(review_issues(before))
    advance(store, "trace-progress" if progress else "reviewed", answer_ref, review=answer_ref)


def _offer_a(store, snapshot, source, answer, chosen_size):
    proposal = planning_view(snapshot.graph)
    if not proposal:
        raise ValueError("Describe at least one story before asking the owner to lock the vision.")
    size = chosen_size or answer["size"]
    groups = chat.product_groups(answer["questions"])
    if chosen_size is None:
        others = ((key, key.capitalize(), why) for key, why in prompts.SIZES.items() if key != size)
        groups.append(chat.group(chat.SIZE, "How large is the dream?",
                                 chat.options((size, size.capitalize(), answer["size_reason"]), *others)))
    groups.extend((chat.gate_group("A", recommend=answer["aligned"]), chat.closing_group()))
    advance(store, "aligned", source, vision=source)
    chat.request(store, "A", snapshot.revision, groups, proposal=proposal,
                 summary=answer["reason"], planner_aligned=answer["aligned"], size=size)


def _offer_b(store, spec, cursor):
    snapshot = spec.snapshot()
    plan = store.read(cursor.leadPlan)
    skip, show = chat.options(("skip", "Skip demonstration", "Keep this in the build order."),
                              ("show", "Show me this working", "Demonstrate this delivery."))
    groups = [chat.group(f"delivery-{i}", f"Demonstrate this delivery? {delivery_name(delivery)}",
                         [show, skip] if delivery["recommend_demo"] else [skip, show])
              for i, delivery in enumerate(plan["build_order"], 1)]
    offers = reversed(store.records("decision_request"))
    prior = next((r["subject"] for _, _, r in offers if r["gate"] == "B"), None)
    changes = b_changes(spec.snapshot(prior["lead2_revision"]), snapshot,
                        store.read(prior["plan"]), plan) if prior else ""
    chat.request(store, "B", snapshot.revision, [*groups, chat.gate_group("B")],
                 proposal=b_summary(snapshot.graph, plan, groups, {}, changes=changes),
                 summary=changes or "Reviewed spec",
                 plan=cursor.leadPlan, review=cursor.review)


def _answer(store, spec, user, cursor):
    ref = cursor.decision
    item = store.read(ref)
    reply, words, choices = chat.read_reply(store, user, ref, item)
    subject, gate, ask = item["subject"], item["gate"], item["ask"]
    revision = subject["lead2_revision"]
    picked = {c["group"]: c for c in [*ask["retained"], *choices]}
    explicit = picked.get(chat.ROUND, {}).get("optionId") == chat.APPROVE
    review = subject.get("options") and gate == "Review choices"
    notes = list(dict.fromkeys(filter(None, [reply.get("words"), *(c["text"] for c in choices)])))
    if not review:
        notes.extend(f"{c['question']} {c['label']} — {c['meaning']}" for c in choices
                     if c["group"].startswith("q") and not c["text"])
    size = picked.get(chat.SIZE, {}).get("optionId")
    changes = bool(notes) or size in prompts.SIZES and size != subject.get("size")
    approve = explicit and not changes
    effect = "approve" if approve or review else "direct"
    with store.transaction():
        expected = spec.snapshot(revision).parent if gate == "scope" else revision
        if store.cursor().decision != ref or spec.snapshot().revision != expected:
            raise ValueError("The proposal changed. Reopen the current proposal before recording a reply.")
        raw = store.record("user_words", {**reply, "words": words, "origin": item["origin"],
                                         "effect": effect if changes or approve else "continue"})
        store.cursor(replace(cursor, decision=None, failure=""))
        if gate == "Kickoff":
            store.record("lead2_owner", {"preference": words, "reply": raw})
            return
        if not changes and (gate == "B" and chat.ROUND not in picked
                            or review and any(group["id"] not in picked for group in ask["groups"])):
            if gate == "B":
                delta = ask["delta"] if ask["delta"] != "Reviewed spec" else ""
                ask = {**ask, "proposal": b_summary(spec.snapshot(revision).graph,
                                                   store.read(subject["plan"]), ask["groups"], picked,
                                                   changes=delta)}
            decisions.request(store, gate, subject, ask={**ask, "retained": list(picked.values())})
            return
        receipt = {"request": ref, "subject": subject, "words": raw, "userWords": raw,
                   "scripted": store.read(cursor.kickoff)["setup"].get("scriptedUser", False),
                   "effect": effect}
        if gate == "A":
            receipt["size"] = size if size in prompts.SIZES else subject["size"]
        if approve and gate == "B":
            receipt["demonstrations"] = [g["id"] for g in ask["groups"]
                                         if picked.get(g["id"], {}).get("optionId", g["default"]) == "show"]
        approval = store.record("approval", receipt)
        if review:
            revision = spec.apply_review_options(revision, approval)
            if notes:
                _add_words(spec, revision, "\n".join(notes), "Record the owner's review reply")
                store.cursor(replace(store.cursor(), requestedChange=approval, review=subject["review"]))
            else:
                advance(store, "reviewed", approval, review=subject["review"])
        elif approve:
            spec.bind_decision(gate, revision, approval)
            if gate == "scope":
                if reviewed := subject.get("review"):
                    _finish_review(store, spec.snapshot(expected).graph, spec.snapshot(revision),
                                   reviewed, subject.get("options"), subject.get("review_groups"))
                else:
                    _finish_plan(store, approval)
            elif gate == "A":
                folded = (receipt["size"] in {"micro", "small"} and cursor.leadPlan
                          and not review_issues(spec.snapshot().graph))
                advance(store, "approve-spec" if folded else "approve", approval, A=approval,
                        leadPlan=cursor.leadPlan if folded else None)
            else:
                advance(store, "approve", approval, B=approval)
        else:
            if notes:
                _add_words(spec, expected, "\n".join(notes), "Record the owner's reply")
            if gate == "scope" and cursor.requestedChange:
                _finish_plan(store, approval)
            else:
                requested = approval if gate == "B" and explicit else None
                advance(store, "revise", approval, requestedChange=requested)
