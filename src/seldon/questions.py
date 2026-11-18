"""C3/C4/I10; R2/R8/R12: human-readable briefs bounded by one unit or one feature."""

import json

from . import architecture, fixups, gitwork, plan
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

SIZE_ESTIMATE = (
    "Estimate the grown scope and quality bar for one capable plain agent with the same starting repo, "
    "tools and access. Micro means minutes in one session, small means days, medium means weeks, and "
    "big means months. In sizeWhy state the closest comparable work or its absence, expected effort, "
    "and the biggest uncertainty. Follow an Owner size override and explain its planning consequences. "
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


def question(kind, prompt, facts, schema, examples, targets=(), execution=None, images=()):
    for example in examples:
        validate({"answer": example}, schema)
    return {"kind": kind, "prompt": prompt, "facts": facts, "targets": list(targets), "schema": schema,
            "examples": [{"answer": example} for example in examples],
            "execution": execution or {}, "images": list(images)}


def build(kind, facts, targets=(), execution=None, images=(), owners=(), *, grown=False,
          coach_contracts=False):
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
    prompt = PROMPTS[kind]
    if kind == "DETAIL" and ("Planning approach" in facts or "Steering policy" in facts):
        prompt += ("\nKeep the requested stages, and implement only the current wave's commitments. "
                   "When the area fits one session, give one unit the work and its checks. "
                   "Subdivide for a concrete ownership seam. Preserve the feature-to-requirement trace. "
                   "The compiled plan assigns the try-it guide and its runnable walkthrough.")
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
                    [example], targets, execution, images)


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
        obj(answer=ALIGN_ROUND), [tiny_round_example(), consequential_round_example()])


def check_align_round(answer, sources):
    vision = vision_body(answer)
    constraints = architecture.index(vision["constraints"], "vision.constraints")
    for constraint in constraints.values():
        source = constraint["source"]
        if source not in sources or constraint["value"] not in sources[source]:
            raise Mismatch("vision.constraints", f"Constraint {constraint['value']!r} is absent "
                           f"from source {source!r}. Copy an exact required value from the supplied Sources.")
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
        "sizeWhy": "One interaction and one visible result", "users": ["CLI user"], "scopeOut": [],
        "stories": [{"handle": "greeting", "feature": "Personal greeting", "story": "Greet a visitor by name",
                     "outcome": "The reply contains the supplied name", "constraints": [], "sources": []}],
        "constraints": [], "quality": [{"handle": "clear", "criterion": "Readable greeting",
            "verification": "Run with a name and read the greeting", "constraints": [], "sources": []}]},
        "changed": "The supplied name appears in the greeting.", "questions": [],
        "recommendation": "done", "why": "One clear interaction covers the dream"}


def consequential_round_example():
    return {"vision": {"understanding": "Compare two noise filters on repeatable input", "size": "medium",
        "sizeWhy": "Saving reference runs and measuring them share the same local workflow",
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
    return build("DETAIL", facts, grown=grown)


def labelled(facts):
    return "\n\n".join(label + "\n" + (value if isinstance(value, str) else
                          json.dumps(value, ensure_ascii=False, indent=2)) for label, value in facts.items())


def runtime(setup):
    return {k: setup[k] for k in ("python", "node") if k in setup}


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
    facts = {"User's words": user_words(store),
             "Unit": contract["instructions"], "Write scope": contract["scope"],
             "Requirements": requirement_facts(document, contract["requirements"]),
             "Existing checks": plan.assigned(document)[ref],
             "Execution facts": execution_facts(store, ref),
             "Quality bar": [c["criterion"] for c in plan.select(document, "criterion").values()]}
    if any("visual direction" in c["criterion"].lower() for c in plan.select(document, "criterion").values()
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
    elif store.read(store.cursor().kickoff)["setup"].get("sizeFirst") and plan.guide_owner(document) == ref:
        facts.update({"Guide path": plan.GUIDE_PATH, "Readers": document["nodes"]["vision"]["body"]["users"]})
    if unit.failure:
        facts["Exact failure"] = unit.failure
        facts["Read current patch"] = ["git", "diff", "--binary", unit.base]
    facts["Correction owners"] = owners(document)
    if unit.thread and unit.failure:
        facts = {k: facts[k] for k in ("Exact failure", "Write scope", "Correction owners", "Existing checks",
                                     "Correction observations") if k in facts}
    facts.update(agreement_facts(document, ref))
    return build("WORK", facts, execution={"cwd": unit.worktree, "session": unit.thread},
                 images=images(store, refs), owners=facts["Correction owners"])


def owners(document):
    """Units own their scopes; work no unit of this wave holds goes to the planner."""
    units = plan.select(document, "unit")
    built = {u["area"] for u in units.values()}
    planner = "No unit yet: Seldon's planner gives this work an owner"
    return {**{r: u["scope"] for r, u in units.items()},
            **{r: {"scope": a["scope"], "owner": planner}
               for r, a in plan.select(document, "area").items() if r not in built},
            "none": "No listed scope holds the fix: " + planner}


def execution_facts(store, ref, peers=()):
    document, unit = plan.execution(store), store.unit(ref)
    units = plan.select(document, "unit")
    peers = {ref, *peers, *units[ref]["dependencies"]}
    scopes = [p for u in peers for p in units[u]["scope"]]
    files = [p for p in gitwork.git(store.root.parent, "ls-tree", "-r", "--name-only", unit.base).splitlines()
             if plan.matches(p, scopes)]
    return {"Base": unit.base, "Worktree": unit.worktree, "Paths present": files,
        "Units": {u: {"scope": units[u]["scope"], "state": store.unit(u).state,
                       "commit": store.unit(u).integrated} for u in peers},
        "Runtime": runtime(store.read(store.cursor().kickoff)["setup"]),
        "Proof owner": "WAVE-VERIFY" if (wave := plan.wave_requirements(document, ref)) or units[ref]["handed"] or ref == "guide" else ref,
        "Wave requirements": wave,
        "Execution": "Your peers are building their units right now from the same contracts; their files "
            "are not in this worktree. Build to the contract text. Run what you can here. A check that needs "
            "a peer's files fails here, and that is expected: write it, declare it, say so in your summary "
            "and return done. Seldon runs every check on the integrated product and comes back to you only "
            "if one fails there."}


def observed(store, refs):
    return [{k: b[k] for k in ("commit", "check", "requirements", "purpose", "command",
                             "exitCode", "stdout", "stderr")}
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
                 owners=[ref, *architecture.contracts_for(document, ref)])


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
