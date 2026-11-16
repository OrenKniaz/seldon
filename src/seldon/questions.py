"""C3/C4/I10; R2/R8/R12: human-readable briefs bounded by one unit or one feature."""

import json

from . import architecture, fixups, gitwork, plan
from .schema import answer_schema, validate

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
    "when the product and its observations plainly meet the meaning; name the limit in why.")

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

PROMPTS = {
    "ALIGN": "What improved dream should we agree to? Recommend size and an observable quality bar. "
             "Keep every exact value the user stated verbatim in the scopeIn item it belongs to; "
             "improving never paraphrases an exact value. "
             + ALIGN_DESIGN,
    "ARCHITECT": 'What areas, agreements and wave goals deliver the accepted vision? Split it the way a '
                 'small team would start on the same morning: each area is work one builder finishes alone, '
                 'at the same time as the others, from the contract text. Put into contracts everything two '
                 'areas must agree on (routes and payloads, data rules, file names, commands, page '
                 "structure), complete enough that neither side needs to see the other's files. A "
                 'session-sized app is usually 2-4 areas; a truly tiny one is 1. Give every commitment '
                 'handle to one area, and give areas disjoint path scopes.',
    "DETAIL": 'What features, requirements and units deliver this area? Each requirement has one plain-words '
              'acceptance sentence. Assign disjoint scopes, requirements, contracts and dependencies to '
              'session-sized units. Subdivide if needed; a missing agreement returns a contract_gap to the '
              'planner. Normally the area is one unit. Use more only when they can be built at the same '
              "time. Name a dependency only when a unit cannot be written until another unit's files exist.",
    "REVIEW": "Do the planned requirements and responsibilities cover the accepted commitments? "
              "Name a missing accepted promise and its correction.",
    "COACH": "Answer this worker's question using the accepted requirements, contracts and tools. "
             "Give practical guidance so the worker can continue within its scope. "
             "Answer yes or no first when the question allows it. " + DEFECT_HANDOFF,
    "DECIDE": "Do the user's verbatim words approve, revise or decline this displayed subject?",
    "WORK": "Deliver this whole unit inside its scope. Write and run your own checks for the "
            "acceptance sentences; declare each check's command, plus checkPaths "
            "including helpers needed without the product. Code observes RED without product and GREEN "
            "with it. Return a question in summary for missing facts or requirement meaning. Save visual evidence "
            "in SELDON_EVIDENCE_DIR. " + DEFECT_HANDOFF,
    "JUDGE": "Judge only the supplied acceptance sentences by their meaning: delivered, exercised by "
             "a meaningful check with the supplied execution observations, and no stub replacing that behaviour? "
             "A gap quotes the acceptance sentence it breaks; other suggestions are notes. "
             + JUDGMENT_LEEWAY,
    "DEMO": "Does this requirement's demonstrated behavior meet its obligation "
            "without stubs, mocks or missing behavior? Inspect final observations and screenshots. "
            "Identify the real user path and the observed evidence that exercised it "
            "(a browser interaction for a UI). " + JUDGMENT_LEEWAY,
    "PRODUCT": "Does this quality criterion hold in the delivered experience? "
               "Inspect its traced final evidence. A UI that works but is plain or forgettable "
               "does not meet an accepted distinctive visual direction. " + JUDGMENT_LEEWAY,
}


class MissingFacts(ValueError):
    pass


def build(kind, facts, targets=(), execution=None, images=(), owners=()):
    if kind in ("ARCHITECT", "DETAIL", "WORK", "COACH"):
        facts = {**facts, "Available tools and skills": AVAILABLE_TOOLS}
    if kind == "ALIGN":
        facts = {**facts, "Design guidance used by UI workers": CRAFT}
    if kind in ("DETAIL", "REVIEW"):
        facts = {**facts, "Delivery work supplied by Seldon after the product units": {
            "path": plan.GUIDE_PATH, "work": plan.GUIDE_WORK, "owner": "Seldon's final guide unit"}}
    example = {
        "ALIGN": {"size": "tiny", "understanding": "A personal greeting", "users": ["CLI user"],
                  "scopeIn": ["Greet a supplied name"], "scopeOut": [], "improvements": [],
                  "quality": [{"criterion": "Clear output", "verification": "Run with a name"}],
                  "unknowns": []},
        "ARCHITECT": example_architecture(), "DETAIL": example_detail(),
        "REVIEW": {"verdict": "adequate", "gaps": []},
        "DECIDE": {"choice": "approve"},
        "COACH": {"guidance": "Use the declared contract and write a check of the acceptance sentence."},
        "WORK": {"outcome": "done", "summary": "Implemented and checked the acceptance",
                 "checkPaths": [], "checks": [], "discoveries": []},
        "JUDGE": {"verdict": "satisfied", "gaps": []},
        "DEMO": {"verdict": "satisfied", "gaps": []},
        "PRODUCT": {"verdict": "satisfied", "gaps": []},
    }[kind]
    example["why"] = ("The responsibilities, agreements and waves cover the accepted promises"
                      if kind == "REVIEW" and facts.get("Review subject") == "architecture" else
                      "The observed interaction and check sources support this answer")
    schema = answer_schema(kind, targets, owners)
    validate({"answer": example}, schema)
    return {"kind": kind, "prompt": PROMPTS[kind],
            "facts": facts, "targets": list(targets), "schema": schema,
            "examples": [{"answer": example}], "execution": execution or {}, "images": list(images)}


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


def example_architecture():
    return {"areas": [
        {"handle": "api", "responsibility": "Serve greetings",
         "scope": ["api/**"], "commitments": ["scopeIn:1"]},
        {"handle": "page", "responsibility": "Display greetings",
         "scope": ["page/**"], "commitments": ["quality:1"]}],
        "contracts": [{"handle": "greeting", "kind": "api", "provider": "api", "consumers": ["page"],
            "text": 'GET /api/greeting?name=<URL-encoded name>: 200 JSON {"message":"Hello, <name>!"}; '
                    'empty/missing name: 400 JSON {"error":"Name required"}. Page / submits a labelled '
                    'name input and displays message or error in its result region. Same origin; no auth.'}],
        "decisions": [], "waves": [{"goal": "Deliver the greeting API and its page"}]}


def example_detail():
    example = example_plan()
    return {"verdict": "planned", "edits": [e for e in example["edits"] if e["kind"] != "area"],
            "units": [{k: v for k, v in u.items() if k != "area"} for u in example["units"]]}


def user_words(store):
    cursor = store.cursor()
    if cursor.vision:
        answer = store.read(cursor.vision)
        attempt = store.read(answer["attempt"])
        dream = store.read(attempt["question"])["facts"]["Dream"]
    else:
        dream = store.read(cursor.kickoff)["words"]
    changes = [store.read(b["words"])["words"] for _, _, b in store.records("approval")
               if b.get("choice") == "revise" or "step" in b["subject"]]
    return {"Dream": dream, **({"Requested change": changes} if changes else {})}


def architect_brief(vision, words, gap=None, previous=None):
    facts = {"Approved vision": {k: v for k, v in vision.items() if k != "why"},
             "User's words": words,
             "Commitment handles": architecture.commitments(vision),
             "Ownership": "Each area owns disjoint paths, with room for its own checks. "
                          "`checks/kit` is supplied by Seldon in every worktree; nobody owns or builds it.",
             "Contracts": "Agreements are shared text, available before implementation. Only explicit "
                          "file dependencies order execution; mutual contracts are allowed."}
    if gap:
        facts["Architecture correction"] = gap
        facts["Previous architecture"] = previous
    return build("ARCHITECT", facts)


def detail_brief(store, outline, area):
    cursor = store.cursor()
    vision = store.read(cursor.vision)["answer"]
    participants = [area["handle"], *area.get("ancestors", [])]
    contracts = {c["handle"]: c for c in outline["contracts"]}
    listing = gitwork.git(store.root.parent, "ls-tree", "-r", "--name-only", cursor.candidate).splitlines()
    files = [p for p in listing if plan.matches(p, area["scope"])]
    goals = architecture.commitments(vision)
    facts = {"User's words": user_words(store),
             "Approved scope": {"scopeIn": [goals[r] for r in area["commitments"]
                                           if not r.startswith("quality:")],
                               "scopeOut": vision["scopeOut"], "quality": vision["quality"]},
             "Accepted commitments": {r: goals[r] for r in area["commitments"]},
             "Area": {k: area[k] for k in ("handle", "responsibility", "scope")},
             "Current wave": outline["waves"][0],
             "Contracts": {r: {"kind": c["kind"], "text": c["text"],
                              "side": "provider" if c["provider"] in participants else "consumer"}
                           for r, c in contracts.items()},
             "Decisions": [{k: d[k] for k in ("choice", "reason")} for d in outline["decisions"]
                           if area["handle"] in d["areas"]],
             "Product in this area": {"files": files[:64], "additionalFiles": max(0, len(files) - 64)},
             "Runtime": runtime(store.read(cursor.kickoff)["setup"])}
    return build("DETAIL", facts)


def labelled(facts):
    return "\n\n".join(label + "\n" + (value if isinstance(value, str) else
                          json.dumps(value, ensure_ascii=False, indent=2)) for label, value in facts.items())


def runtime(setup):
    return {k: setup[k] for k in ("python", "node") if k in setup}


def requirement_facts(document, refs):
    return {r: {k: v for k, v in document["nodes"][r]["body"].items() if k != "features"} for r in refs}


def agreement_facts(document, ref):
    contracts = architecture.contracts_for(document, ref)
    return {"Contract authority": "Contract nodes below override copies in requirement prose.",
            **{label: {r: {k: c[k] for k in ("kind", "text", "provider")} for r, c in contracts.items()
                    if architecture.endpoint(ref, c) == side}
            for label, side in (("Contracts provided", "provider"), ("Contracts consumed", "consumer"),
                                ("Area contracts", None))
            if any(architecture.endpoint(ref, c) == side for c in contracts.values())}}


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
             "Execution facts": execution_facts(store, ref),
             "Quality bar": [c["criterion"] for c in plan.select(document, "criterion").values()]}
    if any("visual direction" in c["criterion"].lower() for c in plan.select(document, "criterion").values()
           if c["area"] == contract["area"]):
        facts["Design guidance used by UI workers"] = CRAFT
    facts.update(agreement_facts(document, ref))
    refs = []
    if assignment := fixups.active(store, ref):
        repair, refs = repair_facts(store, assignment)
        facts.update(repair)
    if ref == "guide":
        facts["Guide path"] = plan.GUIDE_PATH
        vision = document["nodes"]["vision"]["body"]
        facts["Readers"] = vision["users"]
        facts["What the reader came to do"] = vision["understanding"]
    if unit.failure:
        facts["Exact failure"] = unit.failure
        facts["Read current patch"] = ["git", "diff", "--binary", unit.base]
    facts["Correction owners"] = {r: u["scope"] for r, u in plan.select(document, "unit").items()}
    return build("WORK", facts, execution={"cwd": unit.worktree, "session": unit.thread},
                 images=images(store, refs), owners=facts["Correction owners"])


def execution_facts(store, ref, peers=()):
    document, unit = plan.execution(store), store.unit(ref)
    units = plan.select(document, "unit")
    peers = {ref, *peers, *units[ref]["dependencies"]}
    scopes = [p for u in peers for p in units[u]["scope"]]
    files = [p for p in gitwork.git(unit.worktree, "ls-tree", "-r", "--name-only", unit.base).splitlines()
             if plan.matches(p, scopes)]
    return {"Base": unit.base, "Worktree": unit.worktree, "Paths present": files,
        "Units": {u: {"scope": units[u]["scope"], "state": store.unit(u).state,
                       "commit": store.unit(u).integrated} for u in peers},
        "Runtime": runtime(store.read(store.cursor().kickoff)["setup"]),
        "Proof owner": "WAVE-VERIFY" if (wave := plan.wave_requirements(document, ref)) or units[ref]["handed"] else ref,
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
    requirements = requirement_facts(document, contract["requirements"])
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
    return build("JUDGE", facts, requirements,
                 images=images(store, refs))


class AnswerRejected(ValueError):
    def __init__(self, message, answer):
        super().__init__(message)
        self.answer = answer


def ask(provider, question, record, check=lambda answer: None, retries=2):
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
            if answer.get("verdict") != "uncertain":
                if answer.get("verdict") == "gap" and not answer["gaps"]:
                    raise ValueError("A gap verdict must name its correction")
                check(answer)
        except architecture.ContractGap:
            record({"rawAnswer": raw, "errors": ["Needs an architecture correction"]})
            raise
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


def satisfied(answer):
    return answer.get("verdict") == "satisfied" and not answer["gaps"]
