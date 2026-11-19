"""C1/C8/C15/C27/C28: approve a spec, then build and independently prove each milestone."""

import json
import os
from dataclasses import replace
from pathlib import Path

from . import architecture, decisions, gitwork, inference, observations, plan, proof, questions, recovery
from .providers import ProviderFailure, recorded
from .schema import REFS, TEXT, answer_schema, array, choice, folded_judge_schema, obj, validate
from .states import advance
from .store import encode

CHECK = obj(purpose=TEXT, command=array(TEXT, 1), expectedEvidence=TEXT)
MILESTONE = obj(outcome=TEXT, promises=array(TEXT, 1), spec=obj(what=TEXT, how=TEXT, test=array(CHECK, 1)),
                brief=TEXT)
PLAN = obj(answer=obj(milestones=array(MILESTONE, 1)))
RECEIPT = obj(answer=obj(outcome=choice("done", "question"), summary=TEXT, checks=array(CHECK),
                         checkPaths=REFS, walkthrough=CHECK))
WRITE_RULES = (
    "Write only in the assigned product workspace. Preserve accepted behavior. Write semantic checks and "
    "a try-it guide. You may use provider-native sub-agents and skills within this assignment; wait for "
    "them to finish before returning. Keep Seldon's store, decisions and proof untouched. Do not commit "
    "or change global settings. Your receipt offers a candidate for independent proof."
)
EXAMPLE_CHECK = {"purpose": "Greet a named visitor", "command": ["python", "checks/greet.py"],
                 "expectedEvidence": "The process prints Hello, Ada!"}
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


def user_words(store, cursor):
    return {"Dream": store.read(cursor.kickoff)["words"],
            "Owner replies": [b["words"] for _, _, b in store.records("user_words")
                              if isinstance(b["words"], str) and not b.get("rejected")]}


def propose(store, provider, cursor):
    previous = milestones(store, cursor)
    completed = previous[:cursor.wave - 1]
    promises = commitments(store, cursor)
    open_promises = {key: value for key, value in promises.items()
                     if not any(key in m["promises"] for m in completed)}
    example = {"outcome": "Greet a named visitor", "promises": [next(iter(open_promises or promises))],
               "spec": {"what": "A greeting names the visitor", "how": "Read the name from a CLI argument",
                        "test": [EXAMPLE_CHECK]},
               "brief": EXAMPLE_BRIEF}
    facts = {**user_words(store, cursor), "Accepted vision": store.read(cursor.vision)["answer"],
             "Open promises": open_promises, "Completed milestones": completed,
             "Remaining proposal": previous[len(completed):],
             "Runtime": questions.runtime(store.read(cursor.kickoff)["setup"])}
    if cursor.requestedChange:
        facts["Owner direction"] = store.read(cursor.requestedChange)["words"]
    if cursor.review:
        facts["Previous review"] = store.read(cursor.review)["answer"]
    prompt = (
        "Plan the remaining milestones from these approved promises and owner words. A micro dream usually "
        "needs one milestone. Keep staging the owner asked for. Reference the supplied promise handles. "
        "For each milestone give its usable outcome and a spec of what, how and test. Give a runnable "
        "command and expected evidence for each promised behavior so Seldon can replay it on later builds. "
        "Prefer an independent assertion in the command when it can directly exercise the product. "
        "Write a 120–400 word mission brief as a tech lead to a strong engineer. Preserve the person's "
        "exact wording where it matters. Assign the real tests and try-it guide to the same builder."
    )

    def consume(source, answer):
        proposed = answer["milestones"]
        remaining = set(open_promises)
        normalized = []
        for item in proposed:
            assigned = list(dict.fromkeys(p for p in item["promises"] if p in promises))
            remaining.difference_update(assigned)
            normalized.append({**item, "promises": assigned})
        normalized[-1]["promises"].extend(key for key in promises if key in remaining)
        ref = store.record("milestone_plan", {"source": source, "vision": cursor.vision,
            "parent": cursor.leadPlan, "milestones": completed + normalized})
        advance(store, "planned", ref, leadPlan=ref, B=None, requestedChange=None)

    inference.call(store, provider, questions.question("MILESTONES", prompt, facts, PLAN,
                   [{"milestones": [example]}]), consume=consume, binding=cursor.A)


def ask_failure(store, gate, failure):
    decisions.request(store, gate, {"step": gate, "failure": failure}, ask={
        "question": "How should Seldon continue?\n" + failure,
        "options": [decisions.option("Try a different approach", "Describe how to proceed", "direct"),
                    decisions.option("Pause", "Keep the current state for later", "pause")]})


def review(store, provider, cursor):
    if cursor.requestedChange:
        advance(store, "repair-plan", cursor.requestedChange)
        return
    facts = {**user_words(store, cursor), "Accepted promises": commitments(store, cursor),
             "Milestones": milestones(store, cursor)}
    question = questions.question("REVIEW",
        "Does this milestone plan cover the locked dream, with specific what, how and runnable tests? "
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
        if answer["verdict"] != "adequate":
            failure = "; ".join([gap["correction"] for gap in answer.get("gaps", [])]
                                or answer.get("neededFacts", []) or [answer["why"]])
            key = "lead-plan:" + str(cursor.A)
            if recovery.failed(store, ref, key=key, shape=failure):
                ask_failure(store, "LEAD-REVIEW", failure)
            else:
                advance(store, "repair-plan", ref, review=ref)
        else:
            advance(store, "reviewed", ref, review=ref, repairs=0)

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
        if cursor.repairs and not cursor.requestedChange and (cursor.repairs > 1 or not session):
            subject = decisions.coach(store, provider, {"step": "BUILD", "tried": [cursor.failure],
                "failure": cursor.failure}, frame=True)
            if subject:
                decisions.request(store, "BUILD", subject)
            else:
                ask_failure(store, "BUILD", cursor.failure)
            return
        if current and not session and any(b["attempt"] == current[-1][0]
                                          for _, _, b in store.records("call_started")):
            raise proof.UncertainCleanup("The builder has no resumable session in its recording.")
        facts = {"Milestone brief": assignment["brief"], "Approved spec": item["spec"],
                 "Owner words": user_words(store, cursor), "Write scope": assignment["workspace"],
                 "Guide path": plan.GUIDE_PATH}
        if cursor.productVerdict:
            facts["Previous checkpoint"] = store.read(cursor.productVerdict)
        if cursor.failure:
            facts["Exact failure"] = cursor.failure
        if cursor.requestedChange:
            facts["Owner direction"] = store.read(cursor.requestedChange)["words"]
        example = {"outcome": "done", "summary": "A visitor can greet Ada.", "checks": [EXAMPLE_CHECK],
                   "checkPaths": ["checks/greet.py"], "walkthrough": EXAMPLE_CHECK}
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
            fail(store, store.cursor(), str(error))
            return
        cursor = store.cursor()
    answer = store.read(cursor.leadReturn)["answer"]
    if answer["outcome"] == "question":
        fail(store, cursor, answer["summary"])
        return
    advance(store, "returned", cursor.leadReturn, failure="", requestedChange=None)


def fail(store, cursor, failure):
    with store.transaction():
        cause = store.record("lead_failure", {"milestone": cursor.wave, "assignment": cursor.leadBuild,
            "return": cursor.leadReturn, "failure": failure})
        updates = {"leadReturn": None, "repairs": cursor.repairs + 1, "failure": failure,
                   "requestedChange": None}
        if cursor.state == "PROVE":
            advance(store, "repair-build", cause, **updates)
        else:
            store.cursor(replace(cursor, **updates))


def check_set(previous, cursor, item, receipt):
    declarations = dict(previous.get("checks", {}))
    prefix = f"milestone-{cursor.wave}"
    for index, check in enumerate([*item["spec"]["test"], *receipt["checks"]], 1):
        if not any(c["command"] == check["command"] and c["requirements"] == item["promises"]
                   for c in declarations.values()):
            declarations[f"{prefix}-check-{index}"] = {**check, "requirements": item["promises"]}
    walk = f"{prefix}-walkthrough"
    command = receipt["walkthrough"]["command"]
    matching = next((ref for ref, check in declarations.items() if check["command"] == command
                     and check["requirements"] == item["promises"]), None)
    if matching:
        walk = matching
    else:
        declarations[walk] = {**receipt["walkthrough"], "requirements": item["promises"]}
    return declarations, list(dict.fromkeys([*previous.get("walkthroughs", []), walk]))


def judge(store, provider, cursor, candidate, checks, refs, checked, walkthroughs):
    project = store.root.parent
    names = [p for p in gitwork.paths(project, candidate) if not p.startswith("checks/kit/")]
    files = {name: content.decode("utf-8", errors="replace") for name, content in
             gitwork.blobs(project, candidate, ["**"], named=names).items()}
    delivered = {key for check in checks.values() for key in check["requirements"]}
    subjects = {key: value for key, value in commitments(store, cursor).items() if key in delivered}
    subjects.update(tryItGuide="The documented try-it steps work from a fresh checkout.",
                    sourceAcceptance="Real implementation with no stubs, mocks or missing behavior.",
                    codeQuality="Main implementation is readable and safe to change.")
    facts = {"Candidate": candidate, "Subjects": subjects, "Product files": files,
             "Code shape (measured)": observations.code_shape(files), "Checks": checks,
             "Walkthrough checks": walkthroughs, "Observations": questions.observed(store, refs)}
    sample = {key: {"verdict": "satisfied", "gaps": [], "why": "The check exercises the delivered action."}
              for key in subjects}
    question = questions.question("JUDGE",
        "Judge each promised behavior and quality criterion against the real observations, the source and "
        "the guide. Confirm tests fail for the right reason and cover the actual user path. For code quality "
        "use the measured shape and name concrete maintainability defects. Report missing evidence as "
        "uncertainty and return exact corrections for material gaps.", facts,
        folded_judge_schema(subjects, "builder"), [{"findings": sample, "why": "Evidence was reviewed."}],
        subjects, images=questions.images(store, checked))
    source, answer = inference.call(store, provider, question, binding=candidate)
    failures = [gap["correction"] for f in answer["findings"].values() if "gaps" in f
                for gap in questions.must_fix(f)]
    failures += [f["why"] + " " + "; ".join(f["neededFacts"]) for f in answer["findings"].values()
                 if f["verdict"] == "uncertain"]
    if not files.get(plan.GUIDE_PATH, "").strip():
        failures.append(f"Deliver the runnable try-it guide at {plan.GUIDE_PATH}.")
    return source, failures


def prove(store, provider, cursor):
    project = store.root.parent
    assignment = store.read(cursor.leadBuild)
    receipt = store.read(cursor.leadReturn)["answer"]
    worktree, base = assignment["workspace"], assignment["base"]
    item = milestones(store, cursor)[cursor.wave - 1]
    previous = store.read(cursor.productVerdict) if cursor.productVerdict else {}
    checks, walks = check_set(previous, cursor, item, receipt)
    try:
        red, candidate, changed = gitwork.snapshots(
            worktree, base, ["**"], receipt["checkPaths"], cursor.scratch)
    except ValueError as error:
        fail(store, cursor, str(error))
        return
    green = proof.observe(store, project, candidate, checks, "milestone", walkthroughs=walks)
    if failed := proof.failures(store, green):
        fail(store, cursor, f"Candidate {candidate} failed integrated proof: " + proof.explain(failed))
        return
    new = {ref: check for ref, check in checks.items() if ref not in previous.get("checks", {})}
    red_refs = proof.observe(store, project, red, new, "milestone-red")
    judgment, gaps = judge(store, provider, cursor, candidate, checks, red_refs + green, green, walks)
    if gaps:
        fail(store, cursor, f"Candidate {candidate}: " + "\n".join(gaps))
        return
    gitwork.unchanged(project, base)
    with store.transaction():
        verdict = store.record("milestone_proof", {"milestone": cursor.wave, "plan": cursor.leadPlan,
            "commit": candidate, "assignment": cursor.leadBuild, "return": cursor.leadReturn,
            "changed": changed, "checks": checks, "walkthroughs": walks, "red": red_refs,
            "observations": green, "judge": judgment, "previous": cursor.productVerdict})
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
            or observed != set(current["checks"]) or proof.failures(store, cursor.finalObservations)):
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
