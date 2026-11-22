"""C1/C8/C15/C27/C28: approve a spec, then build and independently prove each milestone."""

import json
import os
from dataclasses import replace
from pathlib import Path

from . import architecture, decisions, features, gitwork, inference, judgment, plan, proof, questions, recovery
from .providers import ProviderFailure, recorded
from .schema import REFS, TEXT, array, choice, lead_plan_schema, obj, validate
from .states import advance
from .store import encode

RECEIPT = obj(answer=obj(
    outcome=choice("done", "question", "plan correction", "proof fault"),
    summary=TEXT, checks=array(obj(promises=array(TEXT, 1), command=array(TEXT, 1))),
    checkPaths=REFS, question={"type": "string"}))
WRITE_RULES = (
    "Write the product, semantic checks and try-it guide in the assigned workspace. Preserve accepted "
    "behavior and earlier witnesses. You may use provider-native sub-agents and skills within this "
    "assignment; wait for them to finish. Keep Seldon's store, decisions and proof untouched. Do not "
    "commit or change global settings. Return unattended commands with handles for every promise "
    "they cover, including tryItGuide for the walkthrough. Use headless browsers. List all witness files "
    "in checkPaths. Return done when written. Put doubts in summary and leave question empty unless "
    "you need an answer. " + plan.GUIDE_WALKTHROUGH
)
BOUND_RULES = ("Write checks against exactly the approved action, result and notMetIf. "
               "Put walkthrough scratch, fresh copies, dependencies and generated build outputs "
               "in temporary directories outside the product tree. Use the OS temp directory (TMPDIR/TEMP). ")


def kickoff_mode(selected):
    value = os.getenv("SELDON_MODE", "") if selected is None else selected
    return "lead" if value.strip().casefold() == "lead" else "orchestrator"


def enabled(store):
    return store.read(store.cursor().kickoff).get("mode") == "lead"


def milestones(store, cursor):
    return store.read(cursor.leadPlan)["milestones"] if cursor.leadPlan else []


def progress(store):
    cursor = store.cursor()
    return {"mode": "lead", "milestone": cursor.wave, "milestones": len(milestones(store, cursor))}


def commitments(store, cursor):
    return architecture.commitments(questions.vision_body(store.read(cursor.vision)["answer"]))


def obligations(item, vision):
    return {p["vision"]: p for p in item["promises"] if isinstance(p, dict)} | {
        p: vision.get(p) for p in item["promises"] if isinstance(p, str)}


def user_words(store, cursor):
    return {"Dream": store.read(cursor.kickoff)["words"],
            "Owner replies": [b["words"] for _, _, b in store.records("user_words")
                              if isinstance(b["words"], str) and not b.get("rejected")]}


def spec_lines(item, number):
    return {
        f"M{number} outcome": (item, "outcome"),
        f"M{number} design": (item["spec"], "how"),
        **{f"{p['vision']} {field}": (p, field) for p in item["promises"] if isinstance(p, dict)
           for field in ("action", "result", "notMetIf") if field in p},
    }


def render_spec(item, number, context, promises):
    return questions.labelled({
        **{line: body[key] for line, (body, key) in spec_lines(item, number).items()},
        "Accepted promises": {key: promises[key] for key in obligations(item, promises) if key in promises},
        **context,
    })


def spec_context(store, cursor):
    words = user_words(store, cursor)
    vision = questions.vision_body(store.read(cursor.vision)["answer"])
    return {**words, "Owner replies": "\n\n".join(words["Owner replies"]),
            **{f"Accepted constraint {c['handle']}": c["value"] for c in vision.get("constraints", [])},
            "Try-it guide": plan.GUIDE_WALKTHROUGH}


def propose(store, provider, cursor):
    previous = milestones(store, cursor)
    bound = features.enabled("leadBoundProof")
    amend = bound and (cursor.failure or "").startswith("Amend B proof: ")
    completed = [] if amend else previous[:cursor.wave - 1]
    promises = commitments(store, cursor)
    assigned = {key for m in completed for key in obligations(m, promises)}
    open_promises = {key: value for key, value in promises.items() if key not in assigned}
    example = {"outcome": "Greet a named visitor", "promises": [
                   {"vision": next(iter(open_promises or promises)), "action": "Pass Ada as the name",
                    "result": "See Hello, Ada! on standard output.",
                    **({"notMetIf": "The greeting omits Ada or its exclamation mark."} if bound else {})}],
               "design": "Read the name from a CLI argument."}
    context = spec_context(store, cursor)
    facts = {**context, "Accepted vision": store.read(cursor.vision)["answer"],
             "Open promises": open_promises, "Milestone outline": [m["outcome"] for m in previous],
             "Runtime": questions.runtime(store.read(cursor.kickoff)["setup"])}
    if cursor.failure:
        facts["Exact failure"] = cursor.failure
    if cursor.requestedChange:
        facts["Owner direction"] = store.read(cursor.requestedChange)["words"]
    prompt = (
        "Plan the remaining milestones from these approved promises and owner words. A micro dream usually "
        "needs one milestone. Keep staging the owner asked for. Reference the supplied promise handles. "
        "State the outcome and material design choices. Assign each open promise once with its action and "
        "observable result. Seldon includes the owner's exact words and guide obligation in the spec. "
        + ("State one or two wrong behaviours per promise in notMetIf. " if bound else "")
        + ("Preserve milestone order and ownership while addressing the named proof issue." if amend else "")
    )

    def consume(source, answer):
        remaining = dict(open_promises or promises)
        items = completed.copy()
        for proposed in answer["milestones"]:
            unique = [p for p in proposed["promises"] if remaining.pop(p["vision"], None) is not None]
            if unique:
                item = {"outcome": proposed["outcome"], "promises": unique,
                        "spec": {"how": proposed["design"], "test": None, "rendered": True}}
                item["brief"] = render_spec(item, len(items) + 1, context, promises)
                items.append(item)
        ref = store.record("milestone_plan", {"source": source, "vision": cursor.vision,
            "parent": cursor.leadPlan, "milestones": items})
        advance(store, "planned", ref, leadPlan=ref, B=None, requestedChange=None, failure="")

    question = questions.question("MILESTONES", prompt, facts, lead_plan_schema(bound, open_promises or promises),
                                  [{"milestones": [example]}])
    inference.call(store, provider, question, consume=consume, binding=cursor.A)


def review(store, provider, cursor):
    if cursor.requestedChange:
        advance(store, "repair-plan", cursor.requestedChange)
        return
    if amend_missing(store, cursor):
        return
    items, vision = milestones(store, cursor), commitments(store, cursor)
    lines = {line: target for i, item in enumerate(items, 1) if i >= cursor.wave
             for line, target in spec_lines(item, i).items()}
    missing = {key: vision[key] for key in vision if not any(key in obligations(m, vision) for m in items)}
    schema = obj(answer=obj(amendments=array(obj(target=choice(*lines), replacement=TEXT)),
                            unresolved=REFS, why=TEXT))
    question = questions.question("REVIEW",
        "Review the builder's spec against the accepted promises, owner constraints and staging. "
        "For a material gap that fits an editable line, give its handle and full replacement text. "
        "Use unresolved for gaps requiring a coverage or staging decision. Follow the supplied constraints "
        "and guide obligation by reference without restating them in your amendments.",
        {"Builder spec": "\n\n".join(m["brief"] for m in items), "Unassigned promises": missing},
        schema, [{"amendments": [], "unresolved": [], "why": "The named greeting is specified and exercised."}])

    def consume(source, answer):
        providers = store.read(cursor.kickoff)["setup"].get("providers", {})
        reviewer, planner = (providers.get(role, {}) for role in ("reviewer", "planner"))
        tier = ("cross-provider review" if reviewer.get("provider") != planner.get("provider") else
                "fresh same-provider review")
        revision = cursor.leadPlan
        for edit in answer["amendments"]:
            body, field = lines[edit["target"]]
            body[field] = edit["replacement"]
        if answer["amendments"]:
            context = spec_context(store, cursor)
            for i, item in enumerate(items, 1):
                if i >= cursor.wave:
                    item["brief"] = render_spec(item, i, context, vision)
            revision = store.record("milestone_plan", {"source": source, "vision": cursor.vision,
                "parent": revision, "milestones": items})
        deferred = {key: {"reason": "No milestone assigns this accepted promise."} for key in missing}
        if answer["unresolved"]:
            deferred["plan"] = {"reason": "\n".join(answer["unresolved"])}
        ref = store.record("plan_review", {"revision": revision, "source": source, "answer": answer,
            "notJudged": deferred, "reviewTier": tier})
        advance(store, "reviewed", ref, leadPlan=revision, review=ref, repairs=0)

    inference.call(store, provider, question, consume=consume, binding=cursor.leadPlan)


def amend_missing(store, cursor):
    if features.enabled("leadBoundProof") and (missing := [key for item in milestones(store, cursor)
            for key, p in obligations(item, {}).items() if not (p or {}).get("notMetIf", "").strip()]):
        return advance(store, "repair-plan", cursor.leadPlan, B=None, leadBuild=None, leadReturn=None,
                       failure="Amend B proof: Add notMetIf to " + ", ".join(missing))


def render_builder(question):
    facts = question["facts"]
    brief = facts["Milestone brief"]
    rules = question["prompt"]
    rendered = facts.get("Approved spec", {}).get("rendered", False)
    projected = {"Approved spec", "Promises", "Owner words", "Guide path"} if rendered else set()
    context = questions.labelled({k: v for k, v in facts.items() if k != "Milestone brief" and k not in projected})
    if rendered:
        rules = rules.removesuffix(plan.GUIDE_WALKTHROUGH).rstrip()
    return "\n\n".join((brief, rules, context, "Return schema\n" + encode(question["schema"]),
                        "Example receipt\n" + encode(question["examples"][0]))), rules


def attempts(store, assignment, turn=None):
    return [(ref, body) for ref, _, body in store.records("attempt")
            for execution in [store.read(body["question"]).get("execution", {})]
            if execution.get("lead") == assignment and (turn is None or execution["turn"] == turn)]


def builder_session(store, calls):
    ids = {ref for ref, _ in calls}
    return next((b["session"] for _, _, b in reversed(store.records("call_session"))
                 if b["attempt"] in ids), None)


def recover_return(store, attempt, question):
    """Only a completed recording can recover a return after controller interruption."""
    folder = next((Path(b["folder"]) for _, _, b in store.records("call_started")
                   if b["attempt"] == attempt), None)
    if folder is None:
        return None
    receipt = folder / "result.json"
    if not receipt.is_file():
        raise proof.UncertainCleanup("Builder cleanup is unverified. Resume when its recording is complete.")
    result = json.loads(receipt.read_text("utf-8"))
    if not result["cleanupVerified"]:
        raise proof.UncertainCleanup("Builder cleanup is unverified. Inspect " + str(receipt))
    output = (folder / "stdout.bin").read_text("utf-8", errors="replace")
    native = recorded(folder)
    if session := native.session(output):
        store.record("call_session", {"attempt": attempt, "session": session})
    if result["timedOut"]:
        return None
    try:
        raw = json.loads(native.answer(output))
        validate(raw, store.read(question)["schema"])
    except RuntimeError:
        return None
    except ValueError as error:
        return store.record("invalid_lead_receipt", {"answer": {}, "error": str(error)})
    return store.record("answer", {"attempt": attempt, "kind": "WORK", "answer": raw["answer"],
                                   "recovered": True})


def build(store, provider, cursor):
    if amend_missing(store, cursor):
        return
    project = store.root.parent
    approval = store.read(cursor.B) if cursor.B else {}
    if (approval.get("subject", {}).get("plan") != cursor.leadPlan
            or approval["subject"]["vision"] != cursor.vision):
        raise ValueError("A builder requires the owner's B for this plan and spec")
    item = milestones(store, cursor)[cursor.wave - 1]
    if not cursor.leadBuild:
        gitwork.unchanged(project, cursor.candidate)
        workspace = gitwork.admit(project, cursor.scratch, cursor.candidate)
        with store.transaction():
            ref = store.record("lead_assignment", {"milestone": cursor.wave, "plan": cursor.leadPlan,
                "B": cursor.B, "base": cursor.candidate, "workspace": workspace, "brief": item["brief"]})
            cursor = store.cursor(replace(cursor, leadBuild=ref))
    assignment = store.read(cursor.leadBuild)
    calls = attempts(store, cursor.leadBuild)
    current = attempts(store, cursor.leadBuild, cursor.repairs)
    if current and not cursor.leadReturn:
        ref, last = current[-1]
        if returned := recover_return(store, ref, last["question"]):
            cursor = store.cursor(replace(cursor, leadReturn=returned))
    if not cursor.leadReturn:
        session = builder_session(store, calls)
        if current and not session and any(b["attempt"] == current[-1][0]
                                          for _, _, b in store.records("call_started")):
            raise proof.UncertainCleanup("The builder has no resumable session in its recording.")
        spec = item["spec"]
        facts = {"Milestone brief": assignment["brief"],
                 "Approved spec": spec if isinstance(spec["test"], str) else {**spec, "test": "See promises."},
                 "Promises": obligations(item, commitments(store, cursor)),
                 "Owner words": user_words(store, cursor), "Write scope": assignment["workspace"],
                 "Guide path": plan.GUIDE_PATH}
        if cursor.productVerdict:
            previous = store.read(cursor.productVerdict)
            facts["Previous checkpoint"] = previous
            facts["How the previous milestone felt"] = store.read(previous["judge"]).get("feedback", [])
        if cursor.failure:
            facts["Exact failure"] = cursor.failure
        if cursor.requestedChange:
            facts["Owner direction"] = store.read(cursor.requestedChange)["words"]
        path = "docs/try-it/walkthrough.py"
        example = {"outcome": "done", "summary": "A visitor can greet Ada.", "question": "", "checkPaths": [path],
                   "checks": [{"command": ["python", path], "promises": [next(iter(facts["Promises"])), "tryItGuide"]}]}
        question = questions.question("WORK", (BOUND_RULES if features.enabled("leadBoundProof") else
            "Check the full approved result and diagnostic meaning. A missing product must fail its check. ")
            + WRITE_RULES, facts, RECEIPT, [example],
            execution={"cwd": assignment["workspace"], "session": session, "lead": cursor.leadBuild,
                       "turn": cursor.repairs})

        try:
            inference.call(store, provider, question, binding=cursor.leadBuild, retries=0,
                           consume=lambda ref, _: store.cursor(replace(store.cursor(), leadReturn=ref)))
        except questions.AnswerRejected as error:
            ref = store.record("invalid_lead_receipt", {"answer": error.answer or {}, "error": str(error)})
            store.cursor(replace(store.cursor(), leadReturn=ref))
        except ProviderFailure as error:
            if not error.cleanup_verified:
                raise
            if not error.transient:
                raise proof.UncertainCleanup(str(error)) from error
            fail(store, store.cursor(), str(error), "Seldon", retry="Retry builder")
            return
        cursor = store.cursor()
    returned = store.read(cursor.leadReturn)
    answer = returned["answer"]
    if "error" in returned:
        advance(store, "returned", cursor.leadReturn, requestedChange=None)
        return
    question = answer.get("question", "").strip()
    if question or not answer.get("checks") and answer.get("outcome") in ("question", "plan correction", "proof fault"):
        owner = "Planner" if question else {
            "question": "Planner", "plan correction": "Planner", "proof fault": "Seldon",
        }.get(answer["outcome"], "Builder")
        _, candidate, _ = gitwork.snapshots(assignment["workspace"], assignment["base"], ["**"], [], cursor.scratch)
        fail(store, cursor, question or answer["summary"], owner, candidate=candidate,
             replan=not question and answer["outcome"] == "plan correction")
        return
    advance(store, "returned", cursor.leadReturn, requestedChange=None)


def fail(store, cursor, failure, owner="Builder", *, candidate=None, evidence=(),
         replan=False, retry=None):
    root = store.root.parent
    tree = gitwork.git(root, "rev-parse", f"{candidate}^{{tree}}") if candidate else None
    with store.transaction():
        cause = store.record("lead_failure", {"milestone": cursor.wave, "assignment": cursor.leadBuild,
            "return": cursor.leadReturn, "failure": failure, "candidate": candidate, "tree": tree,
            "owner": owner, "evidence": evidence})
        if owner == "Seldon" and cursor.state == "PROVE":
            store.cursor(replace(cursor, failure=failure))
            return decisions.owner_question(store, owner, failure, retry=retry)
        updates = {"leadReturn": None, "repairs": cursor.repairs + 1, "failure": failure,
                   "requestedChange": None}
        if replan and not recovery.failed(store, cause, key=f"lead-spec:{cursor.wave}",
                                        shape=[tree, milestones(store, cursor)[cursor.wave - 1]["promises"]]):
            advance(store, "repair-plan", cause, **{**updates, "leadBuild": None, "B": None})
            return
        if cursor.state == "PROVE":
            advance(store, "repair-build", cause, **updates)
        else:
            store.cursor(replace(cursor, **updates))
        if replan:
            decisions.owner_question(store, owner, failure)
        else:
            decisions.route_repair(store, cause, owner, tree, failure, retry)


def prove(store, provider, cursor):
    project = store.root.parent
    assignment = store.read(cursor.leadBuild)
    base = assignment["base"]
    items, vision = milestones(store, cursor), commitments(store, cursor)
    current = obligations(items[cursor.wave - 1], vision)
    subjects = {key: p for item in items[:cursor.wave] for key, p in obligations(item, vision).items()}
    previous = store.read(cursor.productVerdict) if cursor.productVerdict else {}
    _, candidate, changed = gitwork.snapshots(assignment["workspace"], base, ["**"], [], cursor.scratch)
    tree = gitwork.git(project, "rev-parse", f"{candidate}^{{tree}}")
    snap = next((row for _, _, row in reversed(store.records("lead_candidate"))
                 if (row["assignment"], row["tree"]) == (cursor.leadBuild, tree)), None)
    if snap:
        candidate = snap["commit"]
    else:
        store.record("lead_candidate", {"assignment": cursor.leadBuild, "tree": tree, "commit": candidate})
    returned = store.read(cursor.leadReturn)
    receipt = returned["answer"]
    try:
        if "error" in returned:
            raise ValueError(returned["error"])
        checks = proof.milestone_checks(store, previous, cursor.wave, current, receipt, candidate)
    except ValueError as error:
        if returned.get("kind") == "RECEIPT":
            fail(store, cursor, str(error), "Seldon", candidate=candidate)
            return
        work_question = store.read(attempts(store, cursor.leadBuild)[-1][1]["question"])
        question = questions.question("RECEIPT",
            "Correct the receipt for this frozen candidate. Reference existing witness files and commands.",
            {"Error": str(error), "Return": receipt, "New promises": current, "Candidate": candidate,
             "Candidate files": gitwork.paths(project, candidate)}, RECEIPT,
            [work_question["examples"][0]["answer"]])

        inference.call(store, provider, question, binding=candidate,
                       consume=lambda ref, _: store.cursor(replace(store.cursor(), leadReturn=ref)))
        return

    green = proof.observe(store, project, candidate, checks, "milestone", candidate=candidate)
    judge, verdict = judgment.milestone(store, provider, base, candidate, subjects, current, green)
    amendments, fixes = verdict["amendments"], verdict["fixes"]
    if amendments or fixes:
        return fail(store, cursor, ("Amend B proof: " if amendments else "") + "\n".join(amendments or fixes),
                    "Planner" if amendments else "Builder", candidate=candidate, evidence=[judge, *green],
                    replan=bool(amendments))
    if failed := proof.failures(store, green):
        fail(store, cursor, proof.explain(failed), "Seldon", candidate=candidate, evidence=[judge, *green])
        return
    gitwork.unchanged(project, base)
    with store.transaction():
        verdict = store.record("milestone_proof", {"milestone": cursor.wave, "plan": cursor.leadPlan,
            "commit": candidate, "assignment": cursor.leadBuild, "return": cursor.leadReturn,
            "changed": changed, "checks": checks,
            "observations": green, "judge": judge,
            "previous": cursor.productVerdict})
        intent = store.record("publication", {"milestone": cursor.wave, "before": base,
            "commit": candidate, "verdict": verdict, "observations": green})
        store.cursor(replace(store.cursor(), publication=intent))
    gitwork.publish(project, store)


def eligible(store):
    cursor = store.cursor()
    checkin = store.read(cursor.checkin)["subject"] if cursor.checkin else {}
    current = store.read(cursor.productVerdict) if cursor.productVerdict else {}
    observed = {check for row in map(store.read, cursor.finalObservations)
                if row["commit"] == cursor.candidate for check in row.get("checks", [row["check"]])}
    if (not cursor.B or cursor.wave != len(milestones(store, cursor))
            or current.get("plan") != cursor.leadPlan or current.get("commit") != cursor.candidate
            or checkin.get("proof") != cursor.productVerdict
            or observed != set(current.get("executedChecks", current["checks"]))
            or proof.failures(store, cursor.finalObservations)):
        raise ValueError("C requires current cumulative proof and the final milestone check-in")
    gitwork.unchanged(store.root.parent, cursor.candidate)


def step(store, provider, user):
    cursor = store.cursor()
    state = cursor.state
    if cursor.decision:
        decisions.decide(store, provider, user)
    elif state == "MILESTONES":
        propose(store, provider, cursor)
    elif state == "LEAD-REVIEW":
        review(store, provider, cursor)
    elif state == "B":
        decisions.request(store, "B", {"plan": cursor.leadPlan, "vision": cursor.vision,
            "milestones": milestones(store, cursor), "review": store.read(cursor.review),
            **({"Whole-product proof": judgment.LEAD_BINDINGS} if features.enabled("leadBoundProof") else {})})
    elif state == "BUILD":
        build(store, provider, cursor)
    elif state == "PROVE":
        prove(store, provider, cursor)
    elif state == "CHECK-IN":
        item = milestones(store, cursor)
        receipt = store.read(cursor.leadReturn)["answer"]
        with store.transaction():
            decisions.request(store, state, {"commit": cursor.candidate, "milestone": cursor.wave,
                "proof": cursor.productVerdict, "built": receipt["summary"],
                "guard": judgment.walk_feedback(store, cursor.productVerdict),
                "next milestone": item[cursor.wave]["outcome"] if cursor.wave < len(item) else None})
            if cursor.waived:
                advance(store, "approve", store.cursor().checkin)
    elif state == "LEAD-NEXT":
        if cursor.wave < len(milestones(store, cursor)):
            advance(store, "next-milestone", cursor.checkin, wave=cursor.wave + 1, repairs=0,
                    leadBuild=None, leadReturn=None)
        else:
            advance(store, "last-milestone", cursor.checkin)
    elif state == "C":
        eligible(store)
        decisions.request(store, "C", {"commit": cursor.candidate, "proof": cursor.productVerdict,
            "guide": plan.GUIDE_PATH, "observations": questions.observed(store, cursor.finalObservations),
            "guard": judgment.walk_feedback(store, cursor.productVerdict),
            "unverified": judgment.not_judged(store)})
    return store.cursor().state
