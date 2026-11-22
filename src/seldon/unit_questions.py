"""Orchestrator planning and unit question templates."""

from concurrent.futures import ThreadPoolExecutor

from . import architecture, features, fixups, inference, gitwork, plan, proof, questions
from .answers import index
from .questions import AVAILABLE_TOOLS, CRAFT, DEFECT_HANDOFF, images, labelled, observed, question, runtime, take, tiny_round_example, user_words
from .schema import answer_schema, vision_body


JUDGMENT_LEEWAY = (
    "Unrequested improvements that support the accepted experience and keep its meaning, constraints and "
    "commitments are fine; reject one only by citing the accepted obligation or material consequence "
    "it breaks. Judge meaning, not wording: a gap is behaviour or quality a user would miss. "
    "Proof this machine cannot give (cutting the network, another OS, real devices) is not a gap "
    "when the product and its observations plainly meet the meaning; name the limit in why. "
    "must-fix means a user would miss accepted behaviour or quality; nit means polish a user would "
    "hardly notice; pre-existing means not caused by this unit's change. "
    "A small requirement left unproven in an otherwise delivered app is a nit note at C.")

SOLE_WORK = (
    "Deliver the accepted product within your scope. Use real user paths to check each requirement's "
    "observable behavior. The walkthrough can also serve as a requirement check. Declare the commands "
    "with the requirement handles they prove and list check files and helpers in checkPaths. "
    "Keep existing declarations unless updating or retiring one by handle. Save visual evidence in "
    "SELDON_EVIDENCE_DIR. Seldon runs the declared checks; run locally as needed to build or debug.")

WORK_RETURN = (
    "Return question with the specific question in the question field when a missing fact or "
    "decision prevents completing your work. Return done when the assigned work is complete. "
    "If an exact runtime needed only for verification is unavailable, exercise the supported paths, "
    "keep the exact step in the guide, and record its name and observed=false with the reason in the "
    "walkthrough evidence. Report that limit in why."
)
LEGACY_WORK_RETURN = (
    "If a missing fact or a decision about meaning prevents completion, return question with the "
    "specific question in summary. Otherwise return done."
)
OPERATION_WALKTHROUGH = (
    "For each accepted operation with start/stop or progress, observe one run through the documented "
    "user interface in the integrated walkthrough. Start it, read state while active, stop it and read "
    "the resulting state (including saved state if documented). For progress-only operations read the "
    "terminal state after completion. Write a JSON array in SELDON_EVIDENCE_DIR/operation-lifecycle.json "
    "with one object per operation: operation, linked requirement or wave commitment handles, "
    "documentation reference, observed "
    "(true), and the actual start, whileRunning, stop (if applicable) and after responses or visible state. "
    "If the interface is undocumented or a step cannot be observed, record observed=false and the "
    "reason. Keep these observations even when the outcome differs from expectations.")

PROMPTS = {
    "ARCHITECT": 'What areas, agreements and wave goals deliver the accepted vision? '
                 'Follow the Planning approach or Steering policy when supplied. Preserve the user’s '
                 'requested checkpoints and use owned product paths with room for checks. '
                 'Without a supplied policy, give each area work one builder can finish alongside the '
                 'others using the contract text. '
                 'Put into contracts everything two areas must agree on. Wave 1 is the thinnest path that '
                 'runs end to end when the horizon has multiple waves. Assign '
                 'commitments to waves; write in full only the contracts the next wave needs, '
                 'leaving later contracts as a handle and one line. Give every commitment to an area '
                 'and give areas disjoint path scopes. With built results, name only what changes; '
                 'a change to accepted scope returns scope for the user.\n'
                 'waves: the full horizon, completed waves included; when steering, remaining waves '
                 'alone also work, and an empty list keeps the horizon.\n'
                 'areas/contracts: handles merge with existing entries when steering; empty lists keep them.',
    "DETAIL": 'What features, requirements and units deliver this wave in this area? Each requirement '
              'has one plain-words acceptance sentence. Give each session-sized unit disjoint ownership, '
              'requirements and dependencies. Subdivide if needed or use contract_gap for a missing agreement. '
              'Normally the area is one unit. Use more only when they can be built at the same '
              "time. Name a dependency only when a unit cannot be written until another unit's files exist.\n"
              "edits: requirement features refer to feature targetHandle values in this answer.\n"
              "units: requirements use local requirement handles; dependencies use one-based unit positions.",
    "REVIEW": "Do the planned requirements and responsibilities cover the accepted commitments? "
              "Name a missing accepted promise and its correction. " + JUDGMENT_LEEWAY,
    "WORK": "Deliver this whole unit inside its scope. Write and run your own checks for the "
            "acceptance sentences; declare each check's command, plus checkPaths "
            "including helpers needed without the product. Code observes RED without product and GREEN "
            "with it. Save visual evidence "
            "in SELDON_EVIDENCE_DIR.\n"
            "checkPaths: paths of checks and helpers inside your scope; Seldon uses them to run checks without product.\n"
            "checks: declare commands with the Requirements handles each proves. Existing checks keep running "
            "when omitted. Name an Existing checks handle in update to revise its declaration, or return "
            '{"retire":"handle"} to stop running it.\n'
            "discoveries: Seldon sends each correction to its named owner, then reruns your checks. " + DEFECT_HANDOFF,
    "JUDGE": "Judge only the supplied acceptance sentences by their meaning: delivered, exercised by "
             "a meaningful check with the supplied execution observations, and no stub replacing that behaviour? "
             "A gap quotes the acceptance sentence it breaks; other suggestions are notes. "
             "Its owners: this unit when it can fix the gap in its scope, or a contract when this "
             "unit and that agreement disagree and the contract text must change. "
             + JUDGMENT_LEEWAY,
    "FLOW": "Does this feature's observed user flow meet each requirement across its contracts? "
            "Name only responsible correction owners for each gap; name a crossed contract "
            "when its agreement needs correction. Owners may also be units or contracts outside "
            "this feature. Judge the observations and screenshots. " + JUDGMENT_LEEWAY,
    "PRODUCT": "Do all these area criteria hold in the delivered experience? Each gap names its criterion "
               "as target and its responsible unit or contract owners. "
               "Inspect the traced final evidence. A UI that works but is plain or forgettable "
               "does not meet an accepted distinctive visual direction. " + JUDGMENT_LEEWAY,
}


def build(kind, facts, targets=(), execution=None, images=(), owners=(), *, grown=False,
          sole_work=False, context=None, planner_brief=False, guide_brief=False):
    if kind in ("ARCHITECT", "DETAIL", "WORK") and not (kind == "WORK" and execution.get("session") and facts.get("Exact failure")):
        facts = {**facts, "Available tools and skills": AVAILABLE_TOOLS}
    example = {
        "ARCHITECT": lambda: example_architecture(grown),
        "DETAIL": lambda: example_detail(grown, planner_brief, guide_brief),
        "REVIEW": lambda: {"verdict": "adequate", "gaps": []},
        "WORK": lambda: {"outcome": "done", "summary": "Implemented and checked the acceptance",
                 "checkPaths": [], "checks": [], "discoveries": []},
    }.get(kind, lambda: {"verdict": "satisfied", "gaps": []})()
    example["why"] = ("The responsibilities, agreements and waves cover the accepted promises"
                      if kind == "REVIEW" and facts.get("Review subject") == "architecture" else
                      "The observed interaction and check sources support this answer")
    prompt = SOLE_WORK if sole_work else PROMPTS[kind]
    if kind == "WORK":
        prompt += "\n" + WORK_RETURN
    if kind == "DETAIL" and ("Planning approach" in facts or "Steering policy" in facts):
        prompt += ("\nKeep the requested stages, and implement only the current wave's commitments. "
                   "When the area fits one session, give one unit the work and its checks. "
                   "Subdivide for a concrete ownership seam. Preserve the feature-to-requirement trace. "
                   "The compiled plan assigns the try-it guide and its runnable walkthrough.")
    if kind == "DETAIL" and "Contracts" in facts and not facts["Contracts"]:
        prompt += ("\nKeep unit instructions focused on the product and its real-path acceptance. "
                   "Leave check implementation and local verification order to the builder.")
    if grown and kind == "DETAIL":
        prompt += ("\nCite the Accepted commitments that each feature implements. Plan only the work "
                   "due here in this wave. Seldon supplies linked exact values for execution and review.")
    continuing = kind == "DETAIL" and "Continuing owner" in facts
    if continuing:
        prompt += "\n" + questions.CONTINUING_ASSIGNMENT
        del example["units"][0]["scope"]
    if grown and kind == "ARCHITECT":
        prompt += ("\nUse the supplied commitment handles across the full wave horizon. Start with a "
                   "working slice of the highest-value outcomes and preserve the exact constraints.")
    if kind == "ARCHITECT" and "Next wave" in facts:
        prompt = questions.checkin_wave_task(facts["Next wave"])
        example = {"areas": [], "contracts": [], "decisions": [],
                   "waves": [{"goal": "Let visitors save the greeting to read later",
                              "commitments": example["waves"][0]["commitments"][:1]}],
                   "why": "The existing owner can extend the greeting outcome with saving."}
    return question(kind, prompt, facts, answer_schema(kind, targets, owners, grown=grown,
                    planner_brief=planner_brief, guide_brief=guide_brief,
                    continuing=continuing),
                    [example], targets, execution, images, context=context)


def example_architecture(grown=False):
    if grown:
        goals = list(questions.commitments(tiny_round_example()["vision"]))
        return {"areas": [{"handle": "cli", "responsibility": "Greet the visitor", "scope": ["greeting/**"],
                           "commitments": goals}], "contracts": [], "decisions": [],
                "waves": [{"goal": "A visitor receives a readable greeting", "commitments": goals}]}
    return {"areas": [
        {"handle": "api", "responsibility": "Serve greetings",
         "scope": ["api/**"], "commitments": ["scopeIn:1"]},
        {"handle": "page", "responsibility": "Display greetings",
         "scope": ["page/**"], "commitments": ["quality:1"]}],
        "contracts": [{"handle": "greeting", "kind": "api", "provider": "api", "consumers": ["page"],
            "text": 'GET /api/greeting?name=<URL-encoded name>: 200 JSON {"message":"Hello, <name>!"}; '
                    'empty/missing name: 400 JSON {"error":"Name required"}. Page / submits a labelled '
                    'name input and displays message or error in its result region. Same origin; no auth.'}],
        "decisions": [], "waves": [{"goal": "Deliver the greeting API and its page",
                                    "commitments": ["scopeIn:1", "quality:1"]}]}


BRIEF_EXAMPLE = (
    'People staffing the equipment desk said, "I need to know who has a key before I close up." '
    "Give them a way to record a key checkout from the desk screen and see the borrower's name when "
    "they reopen it. The tricky bit is when two people try to lend the same key. Keep the first checkout "
    "and tell the second person who already has it. The returns screen and staff sign-in have their own "
    "owners. Keep the checkout rule in one place, use names from the desk, and give each step room to read "
    "at a normal line length. Show this from the desk screen with two open sessions, then reopen the "
    "record and check that the first borrower still appears.")


def example_detail(grown=False, planner_brief=False, guide_brief=False):
    edits = [
        {"kind": "feature", "targetHandle": "new:f", "typedBody": {
            "story": "Receive a greeting", "acceptance": "The name is greeted",
            **({"commitments": ["scopeIn:greeting"]} if grown else {})}},
        {"kind": "requirement", "targetHandle": "new:r", "typedBody": {
            "acceptance": "Running with a name prints a greeting containing that name.", "features": ["new:f"]}},
    ]
    unit = {"scope": ["greeting/**"], "requirements": ["new:r"], "dependencies": [],
            "instructions": "Implement the greeting"}
    if planner_brief:
        feature, requirement = (e["typedBody"] for e in edits)
        feature.update(story="Lend a key from the desk", acceptance="Reopen a key checkout")
        if grown:
            feature["commitments"] = ["scopeIn:key-checkout"]
        requirement["acceptance"] = "Reopening a checkout shows the first borrower's name."
        unit.update(scope=["key-desk/**"], instructions="Deliver key checkout", brief=BRIEF_EXAMPLE)
    return {"verdict": "planned", "edits": edits, "units": [unit], **({"guideBrief": "Help a new desk worker try a key checkout from a clean "
                               "start and reopen it to see who has the key. Walk through the documented "
                               "steps and check the name they see."}
                              if guide_brief else {})}


def architect_brief(vision, words, gap=None, previous=None, built=None):
    vision = vision_body(vision)
    facts = {"Approved vision": vision,
             "User's words": words,
             "Commitment handles": questions.commitments(vision),
             "Ownership": "Each area owns disjoint paths, with room for its own checks. "
                          "`checks/kit` is supplied by Seldon in every worktree; nobody owns or builds it.",
             "Contracts": "Agreements are shared text, available before implementation. Only explicit "
                          "file dependencies order execution; mutual contracts are allowed."}
    if gap:
        facts["Architecture correction"] = gap
        facts["Previous architecture"] = previous
    if built is not None:
        facts.update(built)
    return build("ARCHITECT", facts, grown="stories" in vision)


def detail_brief(store, outline, area, *, guide_brief=False, may_fold_guide=False):
    cursor = store.cursor()
    vision = vision_body(store.read(cursor.vision)["answer"])
    participants = [area["handle"], *area.get("ancestors", [])]
    contracts = {c["handle"]: c for c in outline["contracts"]
                 if c["provider"] in participants or set(participants).intersection(c["consumers"])}
    listing = gitwork.git(store.root.parent, "ls-tree", "-r", "--name-only", cursor.candidate).splitlines()
    files = [p for p in listing if gitwork.matches(p, area["scope"])]
    goals = questions.commitments(vision)
    active = set(outline["waves"][cursor.wave - 1]["commitments"])
    grown = "stories" in vision
    area_goals = set(area["commitments"])
    commitments = [r for r in area["commitments"] if r in active]
    if grown:
        # A changed agreement can bring an area into DETAIL without a wave commitment.
        commitments = [r for r in goals if r in (area_goals & active or area_goals)]
    document = store.plan(cursor.revision)
    existing, completed = {}, []
    for ref, unit in plan.select(document, "unit").items():
        if unit["area"] == area["handle"] and ref != "guide":
            row = store.unit(ref)
            existing[ref] = {"scope": unit["scope"], "acceptance": [
                document["nodes"][r]["body"]["acceptance"] for r in unit["requirements"]],
                "last worker summary": store.read(row.report)["answer"]["summary"]
                if row and row.report else ""}
            if row and row.state == "DONE" and row.wave < cursor.wave:
                completed.append(ref)
    setup = store.read(cursor.kickoff)["setup"]
    facts = {"User's words": user_words(store),
             "Approved scope": {"scopeIn": [goals[r] for r in commitments
                                           if not r.startswith("quality:")],
                               "scopeOut": vision["scopeOut"], "quality": [goals[r]
                                   for r in area["commitments"] if r.startswith("quality:")]},
             "Accepted commitments": {r: goals[r] for r in commitments},
             "Area": {k: area[k] for k in ("handle", "responsibility", "scope")},
             "Current wave": outline["waves"][cursor.wave - 1],
             "What exists here": existing,
             "Contracts": {r: {"kind": c["kind"], "text": c["text"],
                              "side": "provider" if c["provider"] in participants else "consumer"}
                           for r, c in contracts.items()},
             "Decisions": [{k: d[k] for k in ("choice", "reason")} for d in outline["decisions"]
                           if area["handle"] in d["areas"]],
             "Product in this area": {"files": files},
             "Runtime": runtime(setup)}
    if len(completed) == 1:
        facts["Continuing owner"] = completed[0]
    if guide := plan.guide_owner(document):
        scope = plan.select(document, "unit")[guide]["scope"]
        assignment = ("Include walkthrough behavior in this owner's assignment without restating these paths."
                      if guide in completed else
                      "Seldon keeps walkthrough work with this owner. Leave it out of product assignments.")
        facts["Walkthrough ownership"] = {"owner": guide, "paths": [p for p in scope if p in questions.GUIDE_SCOPE],
                                         "assignment": assignment}
    if cursor.wave > 1 and isinstance(words := plan.prior_checkin(store)["User check-in"], str):
        facts["User check-in"] = words
    if setup.get("sizeFirst"):
        facts.update(architecture.planning_approach(setup, vision, continuing=cursor.wave > 1))
    if grown:
        facts["Approved scope"].pop("scopeIn")
        relevant = [*commitments, *(r for r in area["commitments"] if r.startswith("quality:"))]
        constraints = plan.applicable_constraints(vision, relevant)
        facts["Applicable constraints"] = {c["handle"]: c for c in constraints}
    planner_brief = features.enabled("plannerBrief")
    guide_brief = planner_brief and guide_brief
    if planner_brief:
        facts["Writing the brief"] = (
            "Write each unit's brief in 120–400 words, like a tech lead talking to a strong engineer. "
            "Say who needs the work and why, using their own words. Describe what done looks like to "
            "them, the tricky part, what belongs to another owner or is out of scope, and one real user "
            "path that proves it works. Say what readable, maintainable code looks like for this work: "
            "normal line lengths and one home for each behavior. Give the engineer the intent and room "
            "to make ordinary implementation choices. "
            "The sample answer includes an example brief.")
        if may_fold_guide:
            facts["Writing the brief"] += ("\n\nIf one unit owns the whole outcome, also brief it on the "
                                          "fresh-checkout walkthrough. " + questions.GUIDE_WALKTHROUGH)
        if guide_brief:
            facts["Separate walkthrough"] = (
                "Write guideBrief in the same voice for the separate walkthrough owner. Cover this "
                "wave's outcome from a new reader's point of view. " + plan.GUIDE_WORK)
            facts["Walkthrough outcomes"] = {r: goals[r] for r in outline["waves"][cursor.wave - 1]["commitments"]
                                             if not r.startswith("quality:")}
    return build("DETAIL", facts, grown=grown, context={"audience": vision["users"]},
                 planner_brief=planner_brief, guide_brief=guide_brief)


WORK_RECEIPT = {
    "outcome": "done", "summary": "A reader can save a preference and see it after reopening the app. "
                                  "I exercised that path locally.",
    "checkPaths": ["checks/preferences.py"],
    "checks": [{"purpose": "Reopen a saved preference", "command": ["python", "checks/preferences.py"],
                "expectedEvidence": "The reopened app shows the value the reader saved.",
                "requirements": ["remember-preference"]}],
    "discoveries": [], "why": "Reopening exercises the stored value across two app sessions.",
}
WORK_OUTPUT = (
    "After the work, return one JSON receipt. Summarize what a person can now do, what you exercised "
    "locally and what remains for integration. For each new check give its command as an argument "
    "array, purpose, expected evidence and requirement handles. In checkPaths include the scoped check "
    "files and helpers needed to run without the product. Seldon observes the check on the baseline "
    "and on the delivered product. Describe only results you observed.\n\n"
    "Omitted existing checks keep running. To revise one, use its handle in update with its new declaration. "
    "To retire one, give its handle in retire. List actual cross-scope corrections in discoveries.")
SOURCE_REVIEW = (
    "Follow a promised action from its entry point to the result the person receives. Read the relevant "
    "assertion and its baseline and delivered observations. Explain what the check can distinguish, "
    "including a stub or a check that supplies its own answer. Code records which command ran and on which "
    "revision.\n\nCode quality. Could the next engineer safely change this path? Inspect a concrete symbol "
    "for understandable names, control flow, ownership of decisions and visible failure handling. Give a "
    "short source-based judgment in why, with the file or symbol and its consequence. An improvement that "
    "preserves the accepted meaning is welcome. A style preference belongs in why. A blocking gap needs "
    "an accepted obligation and a material consequence.")
def work_output(question):
    variants = question["schema"]["properties"]["answer"]["anyOf"]
    returns = WORK_RETURN if any("question" in variant["properties"] for variant in variants) else LEGACY_WORK_RETURN
    return WORK_OUTPUT + " " + returns


def reviewed_work(question, facts, context):
    checks = question["schema"]["properties"]["answer"]["anyOf"][0]["properties"]["checks"]["items"]
    output, receipt = work_output(question), WORK_RECEIPT
    if "anyOf" not in checks:  # Retained WORK calls predate check links and edits.
        output = ("After the work, return one JSON receipt. Summarize what a person can now do, what you "
                  "exercised locally and what remains for integration. For each check give its command as "
                  "an argument array, purpose and expected evidence. In checkPaths include its scoped files "
                  "and helpers. Describe only observed results. List actual cross-scope corrections in "
                  "discoveries. For missing facts or decisions return question with the question in summary.")
        receipt = {**receipt, "checks": [{k: v for k, v in receipt["checks"][0].items()
                                          if k in checks["properties"]}]}
    # Historical questions lack renderContext. Correction owners is the recorded ownership map.
    owners = facts.get("Correction owners", {})
    product_owners = [owner for owner, scope in owners.items()
                      if isinstance(scope, list) and owner != "guide"]
    sole = context.get("soleOwner", len(product_owners) == 1 and "guide" not in owners)
    guide = context.get("guideRole")
    if guide is None and ("Try-it guide" in facts or "Guide path" in facts):
        guide = "owned" if sole or "Try-it guide" in facts else "separate"
    audience = context.get("audience") or facts.get("Readers")
    wave = context.get("wave", {})
    if question["execution"].get("session") and facts.get("Exact failure"):
        mission = "Continue the existing work. Settle the remaining problem within the assigned write scope."
        opening = take(facts, "Exact failure", "Write scope")
        return mission, opening, "", output, {"answer": receipt}

    mission = ("Build the current wave's accepted outcome for the people below. Here is what matters "
               "to them in their own words." if sole else
               "Help the people below achieve the accepted feature outcome. Deliver the assigned part "
               "of that experience. Here is the user's reason for wanting it.")
    if guide == "separate":
        mission = "Help the reader try this wave's outcome from a fresh checkout."
    craft = ("Write code a senior engineer would be comfortable handing to the next maintainer. "
             "Give operations names that explain their purpose. Keep the normal path easy to follow "
             "and failure handling visible. Reuse the project's conventions. A reader should be able to "
             "find where a behavior lives and change it in one place." if sole else
             "Write code a senior engineer can trace, debug and change. Use names from the product, "
             "give each decision one clear home, and keep error paths legible. Make your side of each "
             "public agreement usable by the next owner.")
    craft += (" Prove the failure paths and edge cases the requirements imply, such as invalid input, "
              "empty states, limits and conflicts.")
    craft = "\n\n".join(filter(None, (craft, labelled(take(facts, "Quality bar",
                                                         "Design guidance used by UI workers")))))
    user_words = take(facts, "User's words")
    if user_words:
        user_words["Exact product wording"] = (
            "Use the person's exact words, labels, commands, field names and numbers verbatim in the product. "
            "Apply craft to everything around them.")
    opening = {**({"Accepted audience": audience} if audience else {}),
               **user_words,
               **({"Current wave": wave} if wave else {}),
               **take(facts, "User check-in", "This wave's commitments", "Unit",
                      "Applicable exact constraints"),
               **({"Accepted stories and results": context["stories"]} if context.get("stories") else {}),
               **({"Applicable exact constraints": context["constraints"]}
                  if context.get("constraints") else {})}
    if audience:
        facts.pop("Readers", None)
    task = ("You own the complete experience, from its accepted entry points through the promised results. "
            "Start it as the user would and exercise the promised result. Choose the simplest "
            "implementation that supports the accepted behavior and its real failure cases." if sole else
            "Deliver your part through its real entry point inside the listed write scope. Follow the "
            "provided and consumed contracts. Use the paths actually present here. Declare checks that "
            "need a peer's missing files for the integrated run. Run what you can here and say what awaits "
            "integration. "
            "Leave out-of-scope defects with their listed owner or planner destination, keep the check "
            "faithful and return done when your own work is complete.")
    if guide:
        for key in ("Try-it guide", "Guide path"):
            facts.pop(key, None)
        if guide == "separate":
            craft = ""
            if opening.get("Unit", "").startswith(plan.GUIDE_WORK + "\nThis wave: ") and wave:
                opening.pop("Unit")  # The wave goal and current guide brief already carry this assignment.
            task = ("Write from this wave's public agreements. Where a peer's UI is unavailable, describe "
                    "supported interactions and leave unseen form details to its owner. "
                    "Declare the walkthrough for integrated WAVE-VERIFY.")
        task += ("\n\nHelp the reader try this wave's outcome from a fresh checkout. In docs/try-it.md give "
                 "the starting directory, inputs or how to create them, exact steps for each accepted "
                 "surface, one runnable example per CLI command where applicable, and the result to look "
                 "for. Declare a walkthrough whose command names its file under docs/try-it/. Use clean "
                 "temporary data, execute those same steps where the required files are available, "
                 "assert the promised result and stop what you start.")
    task += ("\n\nUse the acceptance sentences to choose checks that exercise a real user path and would "
             "detect the loss of the promised behavior. A walkthrough may cover several requirements. "
             "Inspect an actual UI result where relevant and save visual evidence in SELDON_EVIDENCE_DIR.")
    if "Execution facts" in facts:
        facts["Execution facts"] = {k: v for k, v in facts["Execution facts"].items() if k != "Execution"}
    return mission, opening, "\n\n".join(filter(None, (craft, task))), output, {"answer": receipt}


def reviewed_judge(question, facts, context):
    mission = ("Would the intended user get the assigned outcome from this implementation? "
               "Use the delivered source and checks, recorded observations and attached images.")
    opening = {**({"Accepted audience": context["audience"]} if context.get("audience") else {}),
               **({"Accepted stories and results": context["stories"]} if context.get("stories") else {}),
               **take(facts, "Unit responsibility", "Applicable exact constraints")}
    example = {"answer": {"verdict": "uncertain", "neededFacts": [
        "What value appears after closing and reopening the app?"],
        "why": "The check reads from the session that did the write. Settings.save has a clear persistence "
               "boundary, but this observation cannot settle the promise about reopening."}}
    task = ("Decide whether this unit delivers the supplied acceptance sentences. For a gap cite the "
            "affected sentence, what a person would miss, the smallest sufficient correction and the "
            "owner that can make it. Name a contract owner when the agreement itself must change.")
    if "findings" in question["schema"]["properties"]["answer"].get("properties", {}):
        task = ("On this delivered revision give a finding for every supplied subject under its exact "
                "handle, including the feature, requirements, quality and guide subjects shown. Put the "
                + ("code-quality assessment in codeQuality. " if "codeQuality" in facts["Subjects"]
                   else "code-quality assessment in sourceAcceptance. ") +
                "For tryItGuide, compare the actual "
                "documented steps with the fresh-checkout walkthrough and its observed results. "
                "Give each finding its own reason.")
        findings = dict(question["examples"][0]["answer"]["findings"])
        findings["sourceAcceptance"] = {**findings["sourceAcceptance"],
            "why": "The restart check reaches Settings.load through the public command. One function reads "
                   "the stored value, so a maintainer can find and change that decision."}
        example = {"answer": {"findings": findings, "why": "The guide needs another observation."}}
    output = "Return the verdict and reasons as one JSON object using the supplied targets and owners."
    return mission, opening, "\n\n".join((SOURCE_REVIEW, task, questions.SEVERITY_GUIDANCE)), output, example


def planner_work(question, brief, instruction, image_directions):
    from .store import encode

    facts = dict(question["facts"])
    context = question.get("renderContext", {})
    words = facts.pop("User's words", None) or context.get("guideWords", {})
    appendix = [words["Dream"], *words.get("Requested change", [])] if words else []
    for key in ("Unit", "Readers", "This wave's commitments", "Quality bar",
                "Design guidance used by UI workers", "Try-it guide"):
        facts.pop(key, None)
    structure = take(facts, "Write scope", "User check-in", "Applicable exact constraints",
                     "Contract authority", "Contracts provided",
                     "Contracts consumed", "Area contracts", "Planned contracts")
    if "Execution facts" in facts:
        facts["Execution facts"] = {k: v for k, v in facts["Execution facts"].items() if k != "Execution"}
    if constraints := context.get("constraints"):
        facts["Applicable exact constraints"] = constraints
    return "\n\n".join(filter(None, (
        brief, labelled(structure),
        "Call instruction\n" + instruction if instruction else "",
        labelled(facts), image_directions.strip(),
        "Save visual evidence in SELDON_EVIDENCE_DIR when the proof uses a visual surface.",
        work_output(question),
        "Illustrative receipt\n" + encode({"answer": WORK_RECEIPT}),
        "Person's exact words\n" + "\n\n".join(appendix) if appendix else "",
        "Answer schema\n" + encode(question["schema"]))))


def requirement_facts(document, refs):
    return {r: {k: v for k, v in document["nodes"][r]["body"].items() if k != "features"} for r in refs}


def agreement_facts(document, ref):
    contracts = architecture.contracts_for(document, ref)
    return {"Contract authority": "Contract nodes override copies in requirement prose. "
                                  "Planned contracts are area agreements before unit binding.",
            **{label: {r: {k: c[k] for k in ("kind", "text", "provider")} for r, c in contracts.items()
                    if architecture.endpoint(ref, c) == side}
            for label, side in (("Contracts provided", "provider"), ("Contracts consumed", "consumer"),
                                ("Area contracts", None))
            if any(architecture.endpoint(ref, c) == side for c in contracts.values())},
            **({"Planned contracts": planned} if (planned := planned_agreements(document, ref, contracts))
               else {})}


def planned_agreements(document, ref, bound):
    """Include area agreements whose unit bindings are not in this projection yet."""
    if ref == "guide":
        return {}
    area = plan.select(document, "unit")[ref]["area"]
    outline = document.get("architecture", {})
    participants = {area, *index(outline.get("areas", [])).get(area, {}).get("ancestors", [])}
    agreements = outline.get("contracts", [])
    providers = participants | {c["provider"] for c in agreements
                                if participants.intersection(c["consumers"])}
    contracts = plan.select(document, "contract")
    return {r: {k: contracts.get(r, c)[k] for k in ("kind", "text", "provider")}
            for c in agreements if (r := c["handle"]) not in bound and c["provider"] in providers}


def repair_facts(store, assignment):
    refs = [r for g in assignment["gaps"] for r in
            store.read(g["source"]).get("observations", [g["source"]]) if "evidenceFiles" in store.read(r)]
    return {"Correction observations": observed(store, refs)}, refs


def work_brief(store, ref):
    cursor = store.cursor()
    unit = store.unit(ref)
    document = plan.execution(store)
    contract = plan.select(document, "unit")[ref]
    sole = plan.sole_owner(document) == ref
    setup = store.read(cursor.kickoff)["setup"]
    waves = document.get("architecture", {}).get("waves", [])
    context = plan.prompt_context(document, plan.acceptance(document, ref), area=contract["area"])
    context.update(soleOwner=sole, guideRole="separate" if ref == "guide" else
                   "owned" if plan.guide_owner(document) == ref else "",
                   wave={"number": unit.wave, "goal": waves[unit.wave - 1]["goal"]} if waves else {})
    constraints = context.pop("constraints")
    facts = {"User's words": user_words(store),
             "Unit": contract["instructions"], "Write scope": contract["scope"],
             **({"Applicable exact constraints": constraints} if constraints else {}),
             "Requirements": requirement_facts(document, contract["requirements"]),
             "Existing checks": plan.assigned(document)[ref],
             "Execution facts": execution_facts(store, ref, sole=sole),
             "Quality bar": [c["criterion"] for c in plan.select(document, "criterion").values()]}
    if unit.wave == cursor.wave > 1 and isinstance(words := plan.prior_checkin(store)["User check-in"], str):
        facts["User check-in"] = words
    # A sole owner builds the whole experience, page included; never gate its craft on criterion wording.
    if len(plan.select(document, "unit")) == 1 or any(
            "visual direction" in c["criterion"].lower() for c in plan.select(document, "criterion").values()
            if c["area"] == contract["area"]):
        facts["Design guidance used by UI workers"] = CRAFT
    refs = []
    if assignment := fixups.active(store, ref):
        repair, refs = repair_facts(store, assignment)
        if any(g["kind"] != "check" for g in assignment["gaps"]):
            facts.update(repair)
    elif ref != "guide" and not unit.thread and not unit.failure:
        facts["Exercise an acceptance path"] = (
            "Use the assigned requirements and declared checks to choose one concrete path a person relies on. "
            "Identify the action and the condition it must handle. Exercise it from its real entry point "
            "before returning and observe the result caused by that action. For Stop or concurrent work, "
            "first observe the operation running, then assert its terminal state and promised result.")
    if ref == "guide":
        # The walkthrough proves this wave's promises only (M3 rule 10), never the whole dream.
        vision = document["nodes"]["vision"]["body"]
        goals = questions.commitments(vision)
        wave = waves[unit.wave - 1]["commitments"]
        del facts["User's words"]
        facts.update({"Guide path": questions.GUIDE_PATH, "Readers": vision["users"],
                      "This wave's commitments": {r: goals[r] for r in wave if not r.startswith("quality:")},
                      "Quality bar": [goals[r]["criterion"] for r in wave if r.startswith("quality:")]})
        if constraints := plan.applicable_constraints(vision, wave):
            facts["Applicable exact constraints"] = constraints
    if unit.failure:
        facts["Exact failure"] = unit.failure
        facts["Read current patch"] = ["git", "diff", "--binary", unit.base]
    facts["Correction owners"] = owners(document)
    if unit.thread and unit.failure:
        facts = {k: facts[k] for k in ("Exact failure", "Write scope", "Correction owners", "Existing checks",
                                     "Correction observations") if k in facts}
    facts["Runtime"] = runtime(setup)
    assumptions = [d["choice"] for d in plan.select(document, "decision").values()
                   if d["origin"] == "environment" and (ref == "guide" or contract["area"] in d["areas"])]
    if assumptions:
        facts["Runtime assumptions"] = assumptions
    if ref != "guide" and plan.guide_owner(document) == ref:
        facts["Try-it guide"] = questions.GUIDE_WALKTHROUGH
    if plan.guide_owner(document) == ref and features.enabled("judgeEvidence"):
        facts["Operation lifecycle walkthrough"] = OPERATION_WALKTHROUGH
    if not sole:
        facts.update(agreement_facts(document, ref))
    if (brief := contract.get("brief")) and brief.strip():
        context["plannerBrief"] = brief
        if ref == "guide":
            context["guideWords"] = user_words(store)
    return build("WORK", facts, execution={"cwd": unit.worktree, "session": unit.thread},
                 images=images(store, refs), owners=facts["Correction owners"], sole_work=sole,
                 context=context)


def owners(document):
    """Units own their scopes; work no unit of this wave holds goes to the planner."""
    units = plan.select(document, "unit")
    built = {u["area"] for u in units.values()}
    planner = "No unit yet: Seldon's planner gives this work an owner"
    return {**{r: u["scope"] for r, u in units.items()},
            **{r: {"scope": a["scope"], "owner": planner}
               for r, a in plan.select(document, "area").items() if r not in built},
            "none": "No listed scope holds the fix: " + planner}


def execution_facts(store, ref, *, sole=False):
    document, unit = plan.execution(store), store.unit(ref)
    units = plan.select(document, "unit")
    peers = {ref, *units[ref]["dependencies"]}
    scopes = [p for u in peers for p in units[u]["scope"]]
    files = [p for p in gitwork.git(store.root.parent, "ls-tree", "-r", "--name-only", unit.base).splitlines()
             if gitwork.matches(p, scopes)]
    facts = {"Base": unit.base, "Worktree": unit.worktree, "Paths present": files}
    if sole:
        return facts
    return {**facts,
        "Units": {u: {"scope": units[u]["scope"], "state": store.unit(u).state,
                       "commit": store.unit(u).integrated} for u in peers},
        "Proof owner": "WAVE-VERIFY" if (wave := plan.wave_requirements(document, ref)) or units[ref]["handed"] or ref == "guide" else ref,
        "Wave requirements": wave,
        "Execution": "Your peers are building their units right now from the same contracts; their files "
            "are not in this worktree. Build to the contract text. Run what you can here. A check that needs "
            "a peer's files fails here, and that is expected: write it, declare it, say so in your summary "
            "and return done. Seldon runs every check on the integrated product and comes back to you only "
            "if one fails there."}


def judge_brief(store, ref, commit=None, observations=None):
    unit = store.unit(ref)
    document = plan.execution(store)
    contract = plan.select(document, "unit")[ref]
    refs = ([r for r in unit.observations if store.read(r)["purpose"] in ("T", "wave-start")]
            + list(observations) if observations is not None else list(unit.observations))
    refs = list(dict.fromkeys(refs))
    root = store.root.parent if commit else unit.worktree
    commit = commit or unit.P
    required = plan.acceptance(document, ref)
    requirements = requirement_facts(document, required)
    context = plan.prompt_context(document, required, area=contract["area"])
    sources = gitwork.blobs(root, commit, [*contract["scope"], *contract["checkPaths"]])
    checks, product, binaries = {}, {}, {}
    for path, content in sources.items():
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            binaries[path] = len(content)
            continue
        if gitwork.matches(path, contract["checkPaths"]):
            checks[path] = text
        elif gitwork.matches(path, contract["scope"]):
            product[path] = text
    facts = {"Unit responsibility": contract["instructions"],
             "Requirements": requirements, "Checks": plan.assigned(document)[ref],
             "Check sources": checks,
             "Binary files (bytes)": binaries,
             "Product files": product,
             "Observations": (observed(store, refs) if observations is None
                              else proof.account(store, refs, commit))}
    if constraints := context.pop("constraints"):
        facts["Applicable exact constraints"] = constraints
    facts.update(agreement_facts(document, ref))
    return build("JUDGE", facts, requirements, images=images(store, refs),
                 owners=[ref, *architecture.contracts_for(document, ref)], context=context)


def batch(store, provider, pending, check=lambda answer, question: None,
          consume=lambda subject, source, answer: None, field="answer"):
    """Record independent replies on one candidate; only the controller applies their outcomes."""
    cursor = store.cursor()
    bindings = {s: [cursor.candidate, cursor.revision, q["kind"], s] for s, q in pending.items()}
    recorded = {tuple(b["binding"]): (r, b["answer"]) for r, _, b in store.records("answer")
                if b.get("binding")}

    def ask(subject):
        try:
            question = pending[subject]
            retained = recorded.get(tuple(bindings[subject]))
            return inference.call(store, provider, question, check=lambda a: check(a, question),
                binding=bindings[subject], consume=lambda r, a: consume(subject, r, a), field=field,
                retained=retained[0] if retained else None)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=store.read(cursor.kickoff)["capacity"]) as pool:
        replies = dict(zip(pending, pool.map(ask, pending), strict=True))
    latest = store.cursor()
    if (latest.candidate, latest.revision) != (cursor.candidate, cursor.revision):
        raise ValueError("Final replies require their original candidate and plan")
    return replies
