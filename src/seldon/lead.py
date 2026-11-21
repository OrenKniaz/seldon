"""C1/C8/C15/C27/C28: approve a spec, then build and independently prove each milestone."""

import json
import os
from dataclasses import replace
from pathlib import Path

from . import architecture, decisions, gitwork, inference, judgment, plan, proof, questions, recovery
from .providers import ProviderFailure, recorded
from .schema import REFS, TEXT, answer_schema, array, choice, obj, validate
from .states import advance
from .store import encode

CHECK = obj(purpose=TEXT, command=array(TEXT, 1),
            expectedEvidence={**TEXT, "description": "What the command asserts about the product."},
            expectedExit={"type": "integer", "enum": [0]})
MILESTONE = obj(outcome=TEXT, promises=array(obj(vision=TEXT, action=TEXT, result=TEXT), 1),
                spec=obj(what=TEXT, how=TEXT, test=TEXT), brief=TEXT)
PLAN = obj(answer=obj(milestones=array(MILESTONE, 1)))
RECEIPT = obj(answer=obj(
    outcome=choice("done", "question", "plan correction", "proof fault"),
    summary=TEXT, checks=array(obj(vision=TEXT, **CHECK["properties"])),
    checkPaths=REFS, walkthrough={"anyOf": [CHECK, obj()]}, question={"type": "string"}))
WRITE_RULES = (
    "Write the product, semantic checks and try-it guide in the assigned workspace. Preserve accepted "
    "behavior and earlier witnesses. You may use provider-native sub-agents and skills within this "
    "assignment; wait for them to finish. Keep Seldon's store, decisions and proof untouched. Do not "
    "commit or change global settings. For each promise, return one unattended check with its command, "
    "what it asserts and expectedExit 0. Check the full approved result, including whether any "
    "diagnostic conveys the required meaning. A missing product must fail its check. Reuse the same "
    "witness across promises and the walkthrough where it covers their actions and results. Keep "
    "the guide to runnable steps the walkthrough exercises, including any shell commands or status "
    "inspection it asks the reader to perform. Return the walkthrough in the same format, or an "
    "empty object if unavailable. Use headless browsers. List all witness files in checkPaths. "
    "Return done when the work is written. Seldon runs every check. Put doubts in summary. "
    "Leave question empty unless you need an answer you cannot find yourself. "
    + plan.GUIDE_WALKTHROUGH
)
EXAMPLE_CHECK = {"purpose": "Greet a named visitor", "command": ["python", "checks/greet.py"],
                 "expectedEvidence": "The process prints Hello, Ada!", "expectedExit": 0}
EXAMPLE_BRIEF = (
    "Give a visitor a greeting they can personalize. A first-time user should be able to run the "
    "command from a new checkout with the available Python interpreter. They pass a name and see "
    "Hello, Ada! when they pass Ada. Preserve that exact punctuation. Keep the argument handling "
    "and printing easy to understand, and let a missing name produce a helpful usage message. "
    "Exercise the command as a visitor would, by launching the real program and checking what it "
    "prints. Keep a short guide with the commands and expected outputs so someone can try it on their "
    "machine. The check should detect a changed greeting on a later milestone. Return what someone "
    "can now do, the command that tests it, and a walkthrough."
)


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


def propose(store, provider, cursor):
    previous = milestones(store, cursor)
    completed = previous[:cursor.wave - 1]
    promises = commitments(store, cursor)
    open_promises = {key: value for key, value in promises.items()
                     if not any(key in obligations(m, promises) for m in completed)}
    example = {"outcome": "Greet a named visitor", "promises": [
                   {"vision": next(iter(open_promises or promises)), "action": "Pass Ada as the name",
                    "result": "See Hello, Ada! on standard output."}],
               "spec": {"what": "A greeting names the visitor", "how": "Read the name from a CLI argument",
                        "test": "Observe the full greeting and punctuation for the supplied name."},
               "brief": EXAMPLE_BRIEF}
    facts = {**user_words(store, cursor), "Accepted vision": store.read(cursor.vision)["answer"],
             "Open promises": open_promises, "Completed milestones": completed,
             "Remaining proposal": previous[len(completed):],
             "Runtime": questions.runtime(store.read(cursor.kickoff)["setup"])}
    if cursor.failure:
        facts["Exact failure"] = cursor.failure
    if cursor.requestedChange:
        facts["Owner direction"] = store.read(cursor.requestedChange)["words"]
    if cursor.review:
        facts["Previous review"] = store.read(cursor.review)["answer"]
    prompt = (
        "Plan the remaining milestones from these approved promises and owner words. A micro dream usually "
        "needs one milestone. Keep staging the owner asked for. Reference the supplied promise handles. "
        "For each promise state the person's action and the observable result in words. Give a spec of "
        "what, how and what proof must observe. The planner supplies no code, tests or commands to run. "
        "Write a 120â€“400 word mission brief as a tech lead to a strong engineer. Preserve the person's "
        "exact wording, including any invocation they specified as part of the product. Assign the "
        "real tests and try-it guide to the same builder."
    )

    def consume(source, answer):
        ref = store.record("milestone_plan", {"source": source, "vision": cursor.vision,
            "parent": cursor.leadPlan, "milestones": completed + answer["milestones"]})
        advance(store, "planned", ref, leadPlan=ref, B=None, requestedChange=None)

    inference.call(store, provider, questions.question("MILESTONES", prompt, facts, PLAN,
                   [{"milestones": [example]}]), consume=consume, binding=cursor.A)


def review(store, provider, cursor):
    if cursor.requestedChange:
        advance(store, "repair-plan", cursor.requestedChange)
        return
    facts = {**user_words(store, cursor), "Accepted promises": commitments(store, cursor),
             "Milestones": milestones(store, cursor)}
    question = questions.question("REVIEW",
        "Does this milestone plan cover the locked dream, with specific what, how and observable results? "
        "Check the owner's exact constraints, staging, milestone briefs and walkthrough obligation. "
        "Name any material correction.", facts, answer_schema("REVIEW"),
        [{"verdict": "adequate", "gaps": [], "why": "The named greeting is specified and exercised."}])

    def consume(source, answer):
        providers = store.read(cursor.kickoff)["setup"].get("providers", {})
        reviewer, planner = (providers.get(role, {}) for role in ("reviewer", "planner"))
        tier = ("cross-provider review" if reviewer.get("provider") != planner.get("provider") else
                "fresh same-provider review")
        ref = store.record("plan_review", {"revision": cursor.leadPlan, "source": source,
            "answer": answer, "notJudged": {}, "reviewTier": tier})
        named = [p["vision"] if isinstance(p, dict) else p
                 for item in facts["Milestones"] for p in item["promises"]]
        if set(named) != facts["Accepted promises"].keys() or len(named) != len(set(named)):
            failure = f"Assign each accepted promise exactly once. Planned: {named}."
        elif answer["verdict"] != "adequate":
            failure = "; ".join([gap["correction"] for gap in answer.get("gaps", [])]
                                or answer.get("neededFacts", []) or [answer["why"]])
        else:
            advance(store, "reviewed", ref, review=ref, repairs=0)
            return
        key = "lead-plan:" + str(cursor.A)
        if recovery.failed(store, ref, key=key, shape=failure):
            decisions.owner_question(store, "Planner", failure)
        else:
            advance(store, "repair-plan", ref, review=ref, failure=failure)

    inference.call(store, provider, question, consume=consume, binding=cursor.leadPlan)


def render_builder(question):
    facts = question["facts"]
    brief = facts["Milestone brief"]
    context = questions.labelled({key: value for key, value in facts.items() if key != "Milestone brief"})
    return "\n\n".join((brief, WRITE_RULES, context, "Return schema\n" + encode(question["schema"]),
                        "Example receipt\n" + encode(question["examples"][0]))), WRITE_RULES


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
    except (RuntimeError, ValueError):
        return None
    return store.record("answer", {"attempt": attempt, "kind": "WORK", "answer": raw["answer"],
                                   "recovered": True})


def build(store, provider, cursor):
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
            facts["Previous checkpoint"] = store.read(cursor.productVerdict)
        if cursor.failure:
            facts["Exact failure"] = cursor.failure
        if cursor.requestedChange:
            facts["Owner direction"] = store.read(cursor.requestedChange)["words"]
        example = {"outcome": "done", "summary": "A visitor can greet Ada.", "question": "",
                   "checks": [{**EXAMPLE_CHECK, "vision": next(iter(facts["Promises"]))}],
                   "checkPaths": ["checks/greet.py", "docs/try-it/walkthrough.py"],
                   "walkthrough": {"purpose": "Replay the guide",
                                   "command": ["python", "docs/try-it/walkthrough.py"],
                                   "expectedEvidence": "The reader sees Hello, Ada!", "expectedExit": 0}}
        question = questions.question("WORK", WRITE_RULES, facts, RECEIPT, [example],
            execution={"cwd": assignment["workspace"], "session": session, "lead": cursor.leadBuild,
                       "turn": cursor.repairs})

        def consume(source, _):
            store.cursor(replace(store.cursor(), leadReturn=source))

        try:
            inference.call(store, provider, question, consume=consume, binding=cursor.leadBuild)
        except (questions.AnswerRejected, ProviderFailure) as error:
            if isinstance(error, ProviderFailure) and not error.cleanup_verified:
                raise
            if isinstance(error, ProviderFailure) and not error.transient:
                raise proof.UncertainCleanup(str(error)) from error
            fail(store, store.cursor(), str(error), "Seldon" if isinstance(error, ProviderFailure) else "Builder",
                 retry="Retry builder" if isinstance(error, ProviderFailure) else None)
            return
        cursor = store.cursor()
    answer = store.read(cursor.leadReturn)["answer"]
    question = answer.get("question", "").strip()
    if question or not answer.get("checks"):
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
    # A candidate's tree is stable across snapshots, sessions and reworded receipts.
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
    receipt = store.read(cursor.leadReturn)["answer"]
    worktree, base = assignment["workspace"], assignment["base"]
    items = milestones(store, cursor)
    vision = commitments(store, cursor)
    subjects = {key: value for item in items[:cursor.wave] for key, value in obligations(item, vision).items()}
    current = obligations(items[cursor.wave - 1], vision)
    previous = store.read(cursor.productVerdict) if cursor.productVerdict else {}
    candidate = None
    try:
        _, candidate, changed = gitwork.snapshots(worktree, base, ["**"], [], cursor.scratch)
        checks, new, walks = proof.milestone_checks(store, previous, cursor.wave, current, subjects, receipt, candidate)
    except ValueError as error:
        fail(store, cursor, str(error), candidate=candidate)
        return
    declarations = checks | new
    red_refs = proof.observe(store, project, base, declarations, "milestone-base", walkthroughs=walks)
    green = proof.observe(store, project, candidate, declarations, "milestone", walkthroughs=walks)
    observations = red_refs + green
    if failed := proof.failures(store, green):
        runner_fault = any(not o["executionStarted"] or o["timedOut"] for o in failed)
        fail(store, cursor, proof.explain(failed), "Seldon" if runner_fault else "Builder",
             candidate=candidate, evidence=observations, retry="Retry proof" if runner_fault else None)
        return
    new_promises = [key for key, check in new.items() if key not in walks and not check.get("replacement")]
    judge, fixes, missing = judgment.milestone(store, provider, base, candidate, subjects, declarations,
                                              observations, walks, new_promises, receipt["summary"])
    if fixes or missing:
        fail(store, cursor, "\n".join(fixes or missing), "Builder" if fixes else "Seldon",
             candidate=candidate, evidence=[judge, *observations],
             retry="Run proof after the evidence is fixed" if missing and not fixes else None)
        return
    checks.update({k: v for k, v in new.items() if "replacement" not in v})
    gitwork.unchanged(project, base)
    with store.transaction():
        verdict = store.record("milestone_proof", {"milestone": cursor.wave, "plan": cursor.leadPlan,
            "commit": candidate, "assignment": cursor.leadBuild, "return": cursor.leadReturn,
            "changed": changed, "checks": checks, "walkthroughs": walks, "red": red_refs,
            "observations": green, "executedChecks": list(declarations), "judge": judge,
            "previous": cursor.productVerdict})
        intent = store.record("publication", {"milestone": cursor.wave, "before": base,
            "commit": candidate, "verdict": verdict, "observations": green})
        store.cursor(replace(store.cursor(), publication=intent))
    gitwork.publish(project, store)


def eligible(store):
    cursor = store.cursor()
    checkin = store.read(cursor.checkin)["subject"] if cursor.checkin else {}
    current = store.read(cursor.productVerdict) if cursor.productVerdict else {}
    observed = {row["check"] for row in (store.read(ref) for ref in cursor.finalObservations)
                if row["commit"] == cursor.candidate}
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
            "milestones": milestones(store, cursor), "review": store.read(cursor.review)})
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
                "walkthrough": receipt["walkthrough"],
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
            "guide": plan.GUIDE_PATH, "observations": questions.observed(store, cursor.finalObservations)})
    return store.cursor().state
