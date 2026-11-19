"""C3/C4/I10; R2/R8/R12: human-readable briefs bounded by one unit or one feature."""

import json
import re

from . import architecture, fixups, gitwork, observations, plan
from .answers import Mismatch
from .schema import ALIGN_ROUND, answer_schema, batch_decision, obj, validate, vision_body
from . import features

# Owner decision: ALIGN improves craft within scope, with observable quality and a distinctive UI direction.
ALIGN_DESIGN = (
    "Improve the dream toward the most beautiful, simplest, most delightful version of what was asked, "
    "within scope. Beauty means craft, not extra requirements. Every quality criterion must be observable "
    "with the supplied runtime. A product with a user interface states one distinctive visual direction "
    "as a quality criterion.")

# Owner decision: retain visual craft guidance; removing the avoid list regressed measured page quality.
CRAFT = (
    "For a UI, follow the accepted visual direction with deliberate typography, spacing, contrast, "
    "clear language and purposeful accessible interaction. Inspect the real result. "
    "A UI must look designed by someone with a point of view, following the accepted visual direction. "
    "Avoid the default AI look: a system-font stack as the whole typography, a centered column on off-white, "
    "a generic pill button, purple gradients, cards in cards, gray text on color, bounce easing. "
    "Instead: deliberate type with real hierarchy from fonts available offline; a confident palette with "
    "strong contrast; generous rhythm in spacing; motion that is brief, purposeful and honors "
    "reduced-motion; "
    "words a person would actually say. No TODOs, no partial implementations. "
    "A finished page opens with a clean browser console: give it an icon (for example "
    "<link rel=\"icon\" href=\"data:,\">) and serve every file it requests.")

# Owner decision: accepted-experience leeway applies to unit and final judgments.
JUDGMENT_LEEWAY = (
    "Unrequested improvements that support the accepted experience and keep its meaning, constraints and "
    "commitments are fine; reject one only by citing the accepted obligation or material consequence "
    "it breaks. Judge meaning, not wording: a gap is behaviour or quality a user would miss. "
    "Proof this machine cannot give (cutting the network, another OS, real devices) is not a gap "
    "when the product and its observations plainly meet the meaning; name the limit in why. "
    "must-fix means a user would miss accepted behaviour or quality; nit means polish a user would "
    "hardly notice; pre-existing means not caused by this unit's change. "
    "A small requirement left unproven in an otherwise delivered app is a nit note at C.")

AVAILABLE_TOOLS = (
    "Available tools and skills: checks/kit/browser.cjs exports browser(url, async ({page, shot, errors}) "
    "=> { /* exercise the UI; await shot(name) saves evidence */ }); "
    "checks/kit/serve.cjs exports serve(argv, async ({url, port, dataDir}) => { /* exercise product */ }); "
    "argv substitutes {port} and {data}. In a Node check: const browser = require('./kit/browser.cjs'); "
    "const serve = require('./kit/serve.cjs'); await serve(['node','app/server.js'], "
    "async ({url}) => browser(url, async ({page, shot}) => { await page.goto(url); await shot('ready'); })); "
    "Run with node checks/example.cjs. Adjust require paths relative to your scoped check file.")

DEFECT_HANDOFF = (
    "A defect in a file outside your write scope is its owner's to fix: leave the file alone, "
    "keep your check honest, name the owner and the correction in discoveries and return done. "
    "Seldon delivers it and reruns your checks on the integrated product.")

SOLE_WORK = (
    "Deliver the accepted product within your scope. Use real user paths to check each requirement's "
    "observable behavior. The walkthrough can also serve as a requirement check. Declare the commands "
    "with the requirement handles they prove and list check files and helpers in checkPaths. "
    "Keep existing declarations unless updating or retiring one by handle. Save visual evidence in "
    "SELDON_EVIDENCE_DIR. Seldon runs the declared checks; run locally as needed to build or debug.")

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

SIZE_ESTIMATE = (
    "Estimate how long one capable plain agent would need to build and verify the grown scope and quality "
    "bar with the same starting repo, tools and access. Micro means minutes in one session: a single-file "
    "CLI, a local web app with a small API, SQLite and an importer, or a standard-library signal-processing "
    "bench with a page, CLI and local storage server. Small means days. Medium means weeks with decisions "
    "to carry across sessions, as in a multi-service product with auth and migrations. Big means months. "
    "A technical-sounding domain or several local interfaces alone do not imply more effort. In sizeWhy "
    "state the closest comparable work or its absence, expected effort, and what would make it bigger. "
    "Follow an Owner size override and explain its planning consequences. "
    "Keep explicitly requested delivery stages and exact constraints at every size.")

PROMPTS = {
    "ALIGN": "What improved dream should we agree to? Recommend size and an observable quality bar. "
             "Keep every exact value the user stated verbatim in the scopeIn item it belongs to; "
             "improving never paraphrases an exact value. "
             "Name 1-3 alternatives the user may prefer to this vision: smaller, bigger, or another "
             "answer to what their words leave open. "
             + ALIGN_DESIGN,
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
              'has one plain-words acceptance sentence. Assign disjoint scopes, requirements and dependencies to '
              'session-sized units. Subdivide if needed; a missing agreement returns a contract_gap to the '
              'planner. Normally the area is one unit. Use more only when they can be built at the same '
              "time. Name a dependency only when a unit cannot be written until another unit's files exist.\n"
              "edits: requirement features refer to feature targetHandle values in this answer.\n"
              "units: requirements use local requirement handles; dependencies use one-based unit positions.",
    "REVIEW": "Do the planned requirements and responsibilities cover the accepted commitments? "
              "Name a missing accepted promise and its correction. " + JUDGMENT_LEEWAY,
    "COACH": "Answer this worker's question using the accepted requirements, contracts and tools. "
             "Give practical guidance so the worker can continue within its scope. "
             "Answer yes or no first when the question allows it. "
             "If the vision, decisions and results do not settle it, return ask: one question, "
             "2-4 options, your recommendation first. " + DEFECT_HANDOFF,
    "DECIDE": "Which displayed option do the user's verbatim words choose? Other for their own "
              "direction, Stop to end the dream, Unclear when you cannot tell. When Waive is available, "
              "use it only when the user approves continuing and says later check-ins need no answer.",
    "WORK": "Deliver this whole unit inside its scope. Write and run your own checks for the "
            "acceptance sentences; declare each check's command, plus checkPaths "
            "including helpers needed without the product. Code observes RED without product and GREEN "
            "with it. Return a question in summary for missing facts or requirement meaning. Save visual evidence "
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


def question(kind, prompt, facts, schema, examples, targets=(), execution=None, images=(), *, context=None):
    for example in examples:
        validate({"answer": example}, schema)
    return {"kind": kind, "prompt": prompt, "facts": facts, "targets": list(targets), "schema": schema,
            "examples": [{"answer": example} for example in examples],
            "execution": execution or {}, "images": list(images),
            **({"renderContext": context} if context else {})}


def build(kind, facts, targets=(), execution=None, images=(), owners=(), *, grown=False,
          coach_contracts=False, sole_work=False, context=None):
    if kind in ("ARCHITECT", "DETAIL", "WORK", "COACH") and not (kind == "WORK" and execution.get("session") and facts.get("Exact failure")):
        facts = {**facts, "Available tools and skills": AVAILABLE_TOOLS}
    if kind == "ALIGN":
        facts = {**facts, "Design guidance used by UI workers": CRAFT}
    example = {
        "ALIGN": {"size": "tiny", "understanding": "A personal greeting", "users": ["CLI user"],
                  "scopeIn": ["Greet a supplied name"], "scopeOut": [], "improvements": [],
                  "quality": [{"criterion": "Clear output", "verification": "Run with a name"}],
                  "alternatives": [{"label": "Greet everyone", "means": "Use a greeting without a name"}]},
        "ARCHITECT": example_architecture(grown), "DETAIL": example_detail(grown),
        "REVIEW": {"verdict": "adequate", "gaps": []},
        "DECIDE": {"choice": next(iter(targets), "Unclear")},
        "COACH": {"guidance": "Use the declared contract and write a check of the acceptance sentence."},
        "WORK": {"outcome": "done", "summary": "Implemented and checked the acceptance",
                 "checkPaths": [], "checks": [], "discoveries": []},
        "JUDGE": {"verdict": "satisfied", "gaps": []},
        "PRODUCT": {"verdict": "satisfied", "gaps": []},
        "FLOW": {"verdict": "satisfied", "gaps": []},
    }[kind]
    example["why"] = ("The responsibilities, agreements and waves cover the accepted promises"
                      if kind == "REVIEW" and facts.get("Review subject") == "architecture" else
                      "The observed interaction and check sources support this answer")
    prompt = SOLE_WORK if sole_work else PROMPTS[kind]
    if kind == "DETAIL" and ("Planning approach" in facts or "Steering policy" in facts):
        prompt += ("\nKeep the requested stages, and implement only the current wave's commitments. "
                   "When the area fits one session, give one unit the work and its checks. "
                   "Subdivide for a concrete ownership seam. Preserve the feature-to-requirement trace. "
                   "The compiled plan assigns the try-it guide and its runnable walkthrough.")
    if kind == "DETAIL" and "Contracts" in facts and not facts["Contracts"]:
        prompt += ("\nKeep unit instructions focused on the product and its real-path acceptance. "
                   "Leave check implementation and local verification order to the builder.")
    if grown and kind == "DETAIL":
        prompt += ("\nCite the Accepted commitments that each feature implements. Carry the applicable "
                   "constraints into requirements, and plan only the work due here in this wave.")
    if grown and kind == "ARCHITECT":
        prompt += ("\nUse the supplied commitment handles across the full wave horizon. Start with a "
                   "working slice of the highest-value outcomes and preserve the exact constraints.")
    if coach_contracts:
        prompt += ("\nUse the contract response for questions about peer agreements. Answer with the "
                   "applicable plan text. If the text is missing, incomplete, disputed, or the previous "
                   "guidance did not resolve it, describe the gap and what the planner must settle. "
                   "Use ask for decisions that require the user.")
    return question(kind, prompt, facts, answer_schema(kind, targets, owners, grown=grown,
                    coach_contracts=coach_contracts),
                    [example], targets, execution, images, context=context)


def align_round_brief(facts, *, sources, size_first=False):
    """Build one ALIGN round. Sources maps recorded handles to their exact user words."""
    return question("ALIGN",
        "What product would you put in front of this person now? Name who makes what decision, "
        "what could mislead them, and what they would need tomorrow to repeat it. Rank the stories "
        "by value. Keep a story's handle as it moves or changes so its earlier choices stay traceable. "
        "Keep exact required values once, linked to their source and affected outcomes. Runtime "
        "inventory tells you what is available. Carry values into scope only when the user required them. "
        "Cite the supplied Sources for choices that introduced or changed a story, criterion or exclusion. "
        "Keep earlier citations while they explain the current outcome. "
        "Ask together only the choices whose answers materially change the proposed product. "
        "Name the affected commitment or scope, show the proposed default first and explain what each "
        "option changes for the user. Give two to four concrete options; Seldon supplies Other. "
        "Give the changed outcome and why. Recommend done when the displayed defaults settle the product. "
        + ALIGN_DESIGN + ("\n" + SIZE_ESTIMATE if size_first else ""),
        {**facts, "Sources": sources, "Design guidance used by UI workers": CRAFT},
        obj(answer=ALIGN_ROUND), [tiny_round_example(), consequential_round_example()],
        context={"sizeFirst": size_first})


def check_align_round(answer, sources):
    vision = vision_body(answer)
    constraints = architecture.index(vision["constraints"], "vision.constraints")
    for constraint in constraints.values():
        source = constraint["source"]
        words, value = sources.get(source, ""), constraint["value"]
        if value in words:
            continue
        pattern = r"\s+".join(map(re.escape, value.split()))
        match = re.search(pattern, words) if pattern else None
        if not match:
            raise Mismatch("vision.constraints", f"Constraint {value!r} is absent from source "
                           f"{source!r}. Copy an exact required value from the supplied Sources.")
        constraint["value"] = match.group()
    goals = architecture.commitments(vision)
    for entry in [*goals.values(), *vision["scopeOut"]]:
        if unknown := set(entry.get("constraints", [])) - constraints.keys():
            raise Mismatch("vision.constraints", f"Unknown constraint handles {sorted(unknown)}. "
                           f"Use these declared handles: {list(constraints)}.")
        if unknown := set(entry["sources"]) - sources.keys():
            raise Mismatch("vision.sources", f"Unknown source handles {sorted(unknown)}. "
                           f"Use these supplied handles: {list(sources)}.")
    subjects = {"vision", "size", "scopeOut", *goals, *constraints}
    for ask in answer["questions"]:
        if ask["affects"] not in subjects:
            raise Mismatch("questions.affects", f"{ask['question']!r} cites {ask['affects']!r}. "
                           f"Use an affected part of this proposal: {sorted(subjects)}.")


def batch_decide_brief(groups, words):
    """Map verbatim chat to request-bound group IDs and actual option IDs in one DECIDE."""
    example = {"selections": {group["id"]: {"optionId": "unclear", "text": ""} for group in groups},
               "why": "The words do not settle these choices"}
    return question("DECIDE",
        "What selections do the user's exact words make in the displayed groups? "
        "Resolve each unambiguous choice using its option ID. Use 'other' with the exact quoted "
        "direction, or 'unclear' for an unanswered or ambiguous group. Leave text empty except for 'other'. "
        "Only an explicit answer to the round decision can select done.",
        {"Displayed groups": groups, "User's verbatim words": words},
        obj(answer=batch_decision(groups)), [example])


def check_batch_decide(answer, words):
    for group, selection in answer["selections"].items():
        text = selection["text"]
        if selection["optionId"] == "other":
            if text.strip() and text in words:
                continue
            raise Mismatch("selections", f"Other for {group!r} contains {text!r}. "
                           "Copy the exact direction from the user's words, or use Unclear.")
        if text:
            raise Mismatch("selections", f"Selection {selection['optionId']!r} for {group!r} "
                           "needs empty text. Copy the user's words only for Other.")


def tiny_round_example():
    return {"vision": {"understanding": "Greet the supplied name", "size": "tiny",
        "sizeWhy": "A single-file CLI takes one plain agent minutes", "users": ["CLI user"], "scopeOut": [],
        "stories": [{"handle": "greeting", "feature": "Personal greeting", "story": "Greet a visitor by name",
                     "outcome": "The reply contains the supplied name", "constraints": [], "sources": []}],
        "constraints": [], "quality": [{"handle": "clear", "criterion": "Readable greeting",
            "verification": "Run with a name and read the greeting", "constraints": [], "sources": []}]},
        "changed": "The supplied name appears in the greeting.", "questions": [],
        "recommendation": "done", "why": "One clear interaction covers the dream"}


def consequential_round_example():
    return {"vision": {"understanding": "Compare two noise filters on repeatable input", "size": "tiny",
        "sizeWhy": "Like a local simulation bench, one agent can build and verify it in minutes using shared "
                   "local data; adding live hardware calibration would increase the effort.",
        "users": ["Audio engineer choosing a filter"], "constraints": [{"handle": "launch",
            "value": "python server.py --port PORT --data DIR", "meaning": "Start the local bench",
            "source": "kickoff"}], "scopeOut": [{"outcome": "Hardware playback", "sources": []}],
        "stories": [{"handle": "compare", "feature": "Filter comparison",
            "story": "Choose a filter after comparing two runs of yesterday's reference input",
            "outcome": "The engineer sees the band differences and which measurements span unequal intervals",
            "constraints": ["launch"], "sources": []},
            {"handle": "inspect", "feature": "Saved runs",
             "story": "An engineer can reopen a stopped run and see what was measured",
             "outcome": "A saved run shows its settings and measured interval",
             "constraints": ["launch"], "sources": ["example-reply"]}],
        "quality": [{"handle": "repeatable", "criterion": "Repeatable band readings",
            "verification": "Reopen a saved input with identical settings and compare the readings",
            "constraints": [], "sources": []}]},
        "changed": "A stopped run retains the measured interval so a colleague can inspect it.",
        "questions": [{"affects": "scopeIn:compare", "question": "How may stopped runs be compared?",
            "options": [{"label": "Inspect only", "means": "Only complete runs contribute a band comparison"},
                        {"label": "Compare shared interval",
                         "means": "Compare overlapping measurements and mark the result partial"}]}],
        "recommendation": "revise", "why": "A partial comparison trades measurement time for confidence"}


def example_plan():
    return {"edits": [
        {"kind": "feature", "targetHandle": "new:f", "typedBody": {
            "story": "Receive a greeting", "acceptance": "The name is greeted"}},
        {"kind": "requirement", "targetHandle": "new:r", "typedBody": {
            "acceptance": "Running with a name prints a greeting containing that name.",
            "features": ["new:f"]}},
        {"kind": "area", "targetHandle": "new:a", "typedBody": {
            "responsibility": "Deliver greetings", "scope": ["greeting/**"]}}],
        "units": [{"scope": ["greeting/**"],
                   "requirements": ["new:r"], "dependencies": [], "instructions": "Implement the greeting",
                   "area": "new:a"}]}


def example_architecture(grown=False):
    if grown:
        goals = list(architecture.commitments(tiny_round_example()["vision"]))
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


def example_detail(grown=False):
    example = example_plan()
    if grown:
        example["edits"][0]["typedBody"]["commitments"] = ["scopeIn:greeting"]
    return {"verdict": "planned", "edits": [e for e in example["edits"] if e["kind"] != "area"],
            "units": [{k: v for k, v in u.items() if k != "area"} for u in example["units"]]}


def user_words(store):
    cursor = store.cursor()
    answer = store.read(cursor.vision) if cursor.vision else None
    if answer and "scopeIn" in answer["answer"]:
        attempt = store.read(answer["attempt"])
        dream = store.read(attempt["question"])["facts"]["Dream"]
    else:
        dream = store.read(cursor.kickoff)["words"]
    changes = [store.read(b["words"])["words"] for _, _, b in store.records("approval")
               if b.get("choice") == "revise" or "step" in b["subject"]]
    return {"Dream": dream, **({"Requested change": changes} if changes else {})}


def architect_brief(vision, words, gap=None, previous=None, built=None):
    vision = vision_body(vision)
    facts = {"Approved vision": vision,
             "User's words": words,
             "Commitment handles": architecture.commitments(vision),
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


def detail_brief(store, outline, area):
    cursor = store.cursor()
    vision = vision_body(store.read(cursor.vision)["answer"])
    participants = [area["handle"], *area.get("ancestors", [])]
    contracts = {c["handle"]: c for c in outline["contracts"]
                 if c["provider"] in participants or set(participants).intersection(c["consumers"])}
    listing = gitwork.git(store.root.parent, "ls-tree", "-r", "--name-only", cursor.candidate).splitlines()
    files = [p for p in listing if plan.matches(p, area["scope"])]
    goals = architecture.commitments(vision)
    active = set(outline["waves"][cursor.wave - 1]["commitments"])
    grown = "stories" in vision
    area_goals = set(area["commitments"])
    commitments = [r for r in area["commitments"] if r in active]
    if grown:
        # A changed agreement can bring an area into DETAIL without a wave commitment.
        commitments = [r for r in goals if r in (area_goals & active or area_goals)]
    document = store.plan(cursor.revision)
    existing = {}
    for ref, unit in plan.select(document, "unit").items():
        if unit["area"] == area["handle"] and ref != "guide":
            row = store.unit(ref)
            existing[ref] = {"scope": unit["scope"], "acceptance": [
                document["nodes"][r]["body"]["acceptance"] for r in unit["requirements"]],
                "last worker summary": store.read(row.report)["answer"]["summary"]
                if row and row.report else ""}
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
    if setup.get("sizeFirst"):
        facts.update(architecture.planning_approach(setup, vision, continuing=cursor.wave > 1))
    if grown:
        facts["Approved scope"].pop("scopeIn")
        attached = {handle for goal in goals.values() for handle in goal["constraints"]}
        relevant = {handle for r in commitments for handle in goals[r]["constraints"]}
        facts["Applicable constraints"] = {c["handle"]: c for c in vision["constraints"]
                                           if c["handle"] in relevant or c["handle"] not in attached}
    return build("DETAIL", facts, grown=grown, context={"audience": vision["users"]})


def labelled(facts):
    return "\n\n".join(label + "\n" + (value if isinstance(value, str) else
                          json.dumps(value, ensure_ascii=False, indent=2)) for label, value in facts.items())


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
    "To retire one, give its handle in retire. List actual cross-scope corrections in discoveries. "
    "If a missing fact or a decision about meaning prevents completion, return question with the specific "
    "question in summary. Otherwise return done.")
SOURCE_REVIEW = (
    "Follow a promised action from its entry point to the result the person receives. Read the relevant "
    "assertion and its baseline and delivered observations. Explain what the check can distinguish, "
    "including a stub or a check that supplies its own answer. Code records which command ran and on which "
    "revision.\n\nCode quality. Could the next engineer safely change this path? Inspect a concrete symbol "
    "for understandable names, control flow, ownership of decisions and visible failure handling. Give a "
    "short source-based judgment in why, with the file or symbol and its consequence. An improvement that "
    "preserves the accepted meaning is welcome. A style preference belongs in why. A blocking gap needs "
    "an accepted obligation and a material consequence.")
SEVERITY_GUIDANCE = (
    "Use must-fix for missing accepted behavior or quality, nit for minor polish or a small residual proof "
    "limit in an otherwise delivered experience, and pre-existing for a defect the unit did not introduce. "
    "When essential evidence is missing, use uncertain and name the smallest observation that would decide "
    "it. State machine or device limits in why when the available evidence already establishes the meaning.")


def take(facts, *keys):
    return {key: facts.pop(key) for key in keys if key in facts}


def reviewed_work(question, facts, context):
    checks = question["schema"]["properties"]["answer"]["anyOf"][0]["properties"]["checks"]["items"]
    output, receipt = WORK_OUTPUT, WORK_RECEIPT
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
               **take(facts, "This wave's commitments", "Unit"),
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
               **take(facts, "Unit responsibility")}
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
    return mission, opening, "\n\n".join((SOURCE_REVIEW, task, SEVERITY_GUIDANCE)), output, example


def reviewed_decision(question, facts, context):
    kind = question["kind"]
    example = question["examples"][0]
    opening = {"Accepted audience": context["audience"]} if context.get("audience") else {}
    task = ""
    output = "Return one JSON receipt with the decision and its reason."
    if kind == "ALIGN":
        follow_up = "Previous proposal" in facts
        grown = "vision" in question["schema"]["properties"]["answer"].get("properties", {})
        if grown:
            mission = ("Here is what the person chose. What changed for them since the last proposal?"
                       if follow_up else
                       "Who would use this, what action or decision matters most to them, and what would "
                       "a noticeably better version let them do?")
            opening.update(take(facts, "Selected choices", "Requested change", "Sources"))
            if "Sources" in opening and facts.get("Dream") == opening["Sources"].get("kickoff"):
                facts.pop("Dream")
            opening.update(take(facts, "Dream"))
            task = ("Rank stories by value. Carry forward settled stories, handles and citations. Attach "
                    "exact required values to their supplied source and affected outcomes. Give each "
                    "quality promise something observable, including a distinctive direction for a UI. "
                    "Ask together only choices that materially change the product. Give two to four "
                    "concrete options with consequences, recommendation first. Seldon supplies Other. "
                    "Recommend done when settled. "
                    "The user decides gate A. For an exact correction, address the rejected value and "
                    "its source.")
            if context.get("sizeFirst", SIZE_ESTIMATE in question["prompt"]):
                task += ("\n\n" + SIZE_ESTIMATE + " Display micro/small/medium/big and use the schema values "
                         "tiny/small/medium/large in the receipt.")
            if follow_up:
                example = question["examples"][-1]
        else:
            mission = "What improved experience can this person agree to?"
            opening.update(take(facts, "Dream", "Requested change"))
            task = ("Preserve exact values in their scope items. Name observable quality, recommend size "
                    "and show one to three genuinely different alternatives where the words leave room. "
                    + ALIGN_DESIGN)
    elif kind == "ARCHITECT":
        if "Finished wave" in facts:
            mission = "What must change in the unfinished work to deliver the accepted outcome?"
            opening.update(take(facts, "Finished wave", "User check-in"))
            task = ("Preserve completed waves and owners. Describe only changed agreements, responsibilities "
                    "or remaining waves. Return scope for a change requiring the user's decision.")
            output += (" List the full wave horizon or only the remaining waves. An empty waves list keeps "
                       "the horizon. Areas and contracts merge by handle, and empty lists keep them.")
        else:
            mission = "What is the first runnable delivery for this user, and who owns the work?"
            vision = facts.get("Approved vision", {})
            if vision.get("users"):
                opening["Accepted audience"] = vision["users"]
            goals = facts.get("Commitment handles", {})
            if goals:
                first = next((key for key in goals if key.startswith("scopeIn:")), next(iter(goals)))
                # The approved vision supplies ordering, even when replay loads a sorted JSON record.
                if vision.get("stories"):
                    first = "scopeIn:" + vision["stories"][0]["handle"]
                if first in goals:
                    opening["First accepted outcome"] = {first: goals[first]}
            task = ("Honor the accepted stages and size. Give one capable builder a coherent area when it "
                    "fits. Split at independent responsibilities and write the agreements that enable "
                    "concurrent work. Keep scopes disjoint with room for product and checks. Keep every "
                    "commitment and exact constraint on the horizon. Detail next-wave contracts and leave "
                    "later ones as handles and a sentence. In a multi-wave plan start with a runnable slice.")
    elif kind == "DETAIL":
        mission = "What should the user be able to do through this area in the current wave?"
        opening.update(take(facts, "Current wave", "Area", "Applicable constraints"))
        task = ("Describe the features and give each requirement one observable acceptance sentence. Trace "
                "commitments and exact constraints. Give the work and its checks to one session-sized owner "
                "unless another unit can build an independent piece now. Reserve dependencies for actual "
                "file prerequisites. Return contract_gap for a missing agreement or subdivide at a real boundary.")
        output += (" Cite Accepted commitments when the schema asks for them. Feature links use feature "
                   "targetHandle values. Unit requirements use local requirement handles and dependencies "
                   "use one-based unit positions.")
    elif kind == "REVIEW":
        mission = ("If the builders delivered exactly this plan, "
                   "which accepted promise could the user still miss?")
        opening.update(take(facts, "Review subject", "Area", "Accepted commitments"))
        task = ("Trace each relevant commitment to a responsibility, an agreement where needed and an "
                "observable acceptance sentence. For a material omission name the promise and the smallest "
                "plan correction. Judge the supplied plan from plan facts.")
        example = {"answer": {"verdict": "gap", "gaps": [{"target": "remember-preference",
            "correction": ("Assign persistence to one owner and require the saved value "
                           "to appear after restart."),
            "severity": "must-fix"}], "why": "Saving within one session leaves reopening unassigned."}}
    elif kind == "COACH":
        mission = "What can this worker do next inside their scope?"
        opening.update(take(facts, "Unresolved work"))
        task = ("Answer yes or no first when possible. Resolve the question from accepted requirements, "
                "decisions, the actual runtime and contract text. Escalate unresolved user choices with "
                "two to four concrete options, recommendation first.")
        if any("contract" in branch.get("properties", {})
               for branch in question["schema"]["properties"]["answer"]["anyOf"]):
            task += (" For a peer agreement, return its applicable text or identify the gap "
                     "the planner must settle when the text is missing, incomplete or disputed.")
        example = {"answer": {"guidance": "Yes. Reload through the documented command from the product root.",
                             "why": "The recorded command accepts the saved profile path."}}
    elif kind == "FLOW":
        mission = "Follow the feature story from the person's first action to the accepted result."
        opening.update(take(facts, "Feature"))
        task = ("What do the observations and screenshots show at each handoff? Use the traced requirements, "
                "crossed contracts, baseline and final observations and source judgments. Identify the "
                "actual repair owner, including a unit or agreement outside this feature where responsible. "
                "Use uncertain for a missing decisive observation.")
    elif kind == "PRODUCT":
        mission = "Does this delivered experience meet the named criterion for its intended audience?"
        opening.update(take(facts, "Criteria"))
        task = ("Use final observations and attached evidence to describe what the person sees or does. "
                "For a visual criterion inspect the accepted visual direction.")
        if context.get("productMode") == "commitment" or "Feature verdicts" in facts:
            task += " Trace the join between feature results for the cross-area promise."
        if context.get("productMode") == "guide" or "Guide file" in facts:
            task += " Compare the delivered instructions with the observed clean-start journey."
        task += " Use the supplied criterion and responsible owner for any correction. " + SEVERITY_GUIDANCE
    else:  # DECIDE has either displayed options or a group-specific schema.
        mission = "Which displayed option does the person's exact wording settle?"
        opening.update(take(facts, "User's verbatim words", "User's words", "Decision", "Displayed subject",
                            "Displayed groups", "Displayed options"))
        if "Displayed groups" in opening:
            task = ("Map each group to its own displayed option ID. Use other with the person's exact words "
                    "for their own direction and leave text empty for every other selection. Mark unanswered "
                    "or ambiguous groups unclear. Select done only on an explicit round decision.")
        else:
            task = ("Use Other for their own direction, Stop for an explicit stop and Unclear for ambiguity. "
                    "Waive requires permission to continue and to skip answers at later check-ins, "
                    "and is available only when offered.")
            example = {"answer": {"choice": "Unclear", "why": "The words leave the displayed choice open."}}
    if kind in ("FLOW", "PRODUCT"):
        example = {"answer": {"verdict": "uncertain", "neededFacts": ["The value after reopening the app."],
                             "why": "The delivered observation ends before the reader reopens the app."}}
    if kind in ("ARCHITECT", "DETAIL", "COACH"):
        facts.pop("Available tools and skills", None)  # Leave the builder tutorial with WORK.
    return mission, opening, task, output, example


def reviewed_brief(question, stdin_instruction, image_directions):
    """Render the reviewed arm from recorded facts and presentation context only."""
    from .store import encode

    facts = dict(question["facts"])
    kind = question["kind"]
    context = question.get("renderContext", {})
    if kind == "WORK":
        mission, opening, task, output, example = reviewed_work(question, facts, context)
    elif kind == "JUDGE":
        mission, opening, task, output, example = reviewed_judge(question, facts, context)
    else:
        mission, opening, task, output, example = reviewed_decision(question, facts, context)
    validate(example, question["schema"])
    if repairs := take(facts, "Exact corrections", "Exact failure"):
        opening.update(repairs)
    keys = ("Write scope", "Requirements", "Subjects", "Contract authority", "Contracts provided",
            "Contracts consumed", "Area contracts", "Planned contracts", "Decisions", "Runtime",
            "Execution facts", "Existing checks", "Correction owners", "Crossed contracts",
            "Wave-start observations", "Observations", "Final observations", "Check artifacts")
    evidence = {**take(facts, *keys), **dict(sorted(facts.items()))}
    label = ("Assignment facts" if kind == "WORK" else
             "Evidence" if kind in ("JUDGE", "FLOW", "PRODUCT") else "Decision facts")
    caption = ("Illustration from another assignment. Use the paths, handles and observations from yours."
               if kind == "WORK" else "Illustrative receipt. Use the supplied facts and handles.")
    return "\n\n".join(filter(None, (
        mission,
        "Call instruction\n" + stdin_instruction if stdin_instruction else "",
        labelled(opening), task,
        label + "\n\n" + labelled(evidence) if evidence else "",
        image_directions.strip(), output, caption + "\n" + encode(example),
        "Answer schema\n" + encode(question["schema"]))))


def runtime(setup):
    return {k: setup[k] for k in ("python", "node", "nodeVersion") if k in setup}


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
    participants = {area, *architecture.index(outline.get("areas", [])).get(area, {}).get("ancestors", [])}
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
    unit = store.unit(ref)
    document = plan.execution(store)
    contract = plan.select(document, "unit")[ref]
    sole = plan.sole_owner(document) == ref
    setup = store.read(store.cursor().kickoff)["setup"]
    facts = {"User's words": user_words(store),
             "Unit": contract["instructions"], "Write scope": contract["scope"],
             "Requirements": requirement_facts(document, contract["requirements"]),
             "Existing checks": plan.assigned(document)[ref],
             "Execution facts": execution_facts(store, ref, sole=sole),
             "Quality bar": [c["criterion"] for c in plan.select(document, "criterion").values()]}
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
    if ref == "guide":
        # The walkthrough proves this wave's promises only (M3 rule 10), never the whole dream.
        vision = document["nodes"]["vision"]["body"]
        goals = architecture.commitments(vision)
        wave = document["architecture"]["waves"][unit.wave - 1]["commitments"]
        del facts["User's words"]
        facts.update({"Guide path": plan.GUIDE_PATH, "Readers": vision["users"],
                      "This wave's commitments": {r: goals[r] for r in wave if not r.startswith("quality:")},
                      "Quality bar": [goals[r]["criterion"] for r in wave if r.startswith("quality:")]})
    if unit.failure:
        facts["Exact failure"] = unit.failure
        facts["Read current patch"] = ["git", "diff", "--binary", unit.base]
    facts["Correction owners"] = owners(document)
    if unit.thread and unit.failure:
        facts = {k: facts[k] for k in ("Exact failure", "Write scope", "Correction owners", "Existing checks",
                                     "Correction observations") if k in facts}
    facts["Runtime"] = runtime(setup)
    if ref != "guide" and plan.guide_owner(document) == ref:
        facts["Try-it guide"] = plan.GUIDE_WALKTHROUGH
    if plan.guide_owner(document) == ref and features.enabled("judgeEvidence"):
        facts["Operation lifecycle walkthrough"] = OPERATION_WALKTHROUGH
    if not sole:
        facts.update(agreement_facts(document, ref))
    wave = document.get("architecture", {}).get("waves", [])
    context = {**plan.prompt_context(document, plan.acceptance(document, ref)), "soleOwner": sole,
               "guideRole": "separate" if ref == "guide" else
                            "owned" if plan.guide_owner(document) == ref else "",
               "wave": {"number": unit.wave, "goal": wave[unit.wave - 1]["goal"]} if wave else {}}
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
             if plan.matches(p, scopes)]
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


def observed(store, refs):
    return [{**{k: b[k] for k in ("commit", "check", "requirements", "purpose", "command",
                                 "exitCode", "stdout", "stderr")}, **observations.judge_fields(b)}
            for b in (store.read(ref) for ref in refs)]


def images(store, refs):
    from pathlib import Path
    return list(dict.fromkeys(p for ref in refs for p in store.read(ref)["evidenceFiles"]
                             if Path(p).read_bytes().startswith((b"\x89PNG", b"\xff\xd8"))))


def judge_brief(store, ref, commit=None, observations=None):
    unit = store.unit(ref)
    document = plan.execution(store)
    contract = plan.select(document, "unit")[ref]
    refs = ([r for r in unit.observations if store.read(r)["purpose"] in ("T", "wave-start")]
            + list(observations) if observations is not None else list(unit.observations))
    refs = list(dict.fromkeys(refs))
    root = store.root.parent if commit else unit.worktree
    commit = commit or unit.P
    requirements = requirement_facts(document, plan.acceptance(document, ref))
    sources = gitwork.blobs(root, commit, [*contract["scope"], *contract["checkPaths"]])
    checks, product, binaries = {}, {}, {}
    for path, content in sources.items():
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            binaries[path] = len(content)
            continue
        if plan.matches(path, contract["checkPaths"]):
            checks[path] = text
        elif plan.matches(path, contract["scope"]):
            product[path] = text
    facts = {"Unit responsibility": contract["instructions"],
             "Requirements": requirements, "Checks": plan.assigned(document)[ref],
             "Check sources": checks,
             "Binary files (bytes)": binaries,
             "Product files": product,
             "Observations": observed(store, refs)}
    facts.update(agreement_facts(document, ref))
    return build("JUDGE", facts, requirements, images=images(store, refs),
                 owners=[ref, *architecture.contracts_for(document, ref)],
                 context=plan.prompt_context(document, requirements))


class AnswerRejected(ValueError):
    def __init__(self, message, answer):
        super().__init__(message)
        self.answer = answer


def ask(provider, question, record, retries=2):
    original = question
    rejected = []
    for attempt in range(retries + 1):
        raw = provider(question)
        errors = []
        answer = None
        try:
            decoded = json.loads(raw)
            validate(decoded, question["schema"])
            answer = decoded["answer"]
        except (ValueError, KeyError) as error:
            errors = str(error).splitlines()
        record({"rawAnswer": raw, "errors": errors})
        if not errors:
            return answer
        rejected.append({"Rejected answer": raw, "Errors": errors})
        if attempt == retries:
            raise AnswerRejected(f"{original['kind']}: {retries} exact-fix retries exhausted; "
                                 + "; ".join(errors), answer)
        question = {**original, "facts": {**original["facts"], "Exact corrections": rejected}}


def must_fix(answer):
    return [g for g in answer["gaps"] if features.blocking(g)]


def satisfied(answer):
    return answer.get("verdict") in ("satisfied", "gap") and not must_fix(answer)
