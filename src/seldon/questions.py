"""C3/C4/I10; R2/R8/R12: human-readable briefs bounded by one unit or one feature."""

import json

from . import gitwork, plan
from .schema import answer_schema, validate
from .store import encode

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
    "words a person would actually say. No TODOs, no partial implementations.")

# Owner decision: accepted-experience leeway applies to unit and final judgments.
JUDGMENT_LEEWAY = (
    "Unrequested improvements that support the accepted experience and keep its meaning, constraints and "
    "commitments are fine; reject one only by citing the accepted obligation or material consequence "
    "it breaks.")

PROMPTS = {
    "ALIGN": "What improved dream should we agree to? Recommend size and an observable quality bar. "
             + ALIGN_DESIGN,
    "PLAN": "What product work is needed in addition to the supplied delivery work? Plan its features, "
            "requirements, semantic checks and scoped units. Trace each accepted quality criterion by its "
            "1-based position to its checks in quality. "
            "Dependencies are 1-based unit positions. "
            "Commands are argv. CODE assigns checks from requirement ownership. "
            "Checks are automated commands that never wait on manual or visual review; "
            "visual quality is judged by Seldon from real screenshots.",
    "REPAIR": "What corrected plan resolves the named review gap while preserving the approved vision? "
              "Return the complete corrected plan; it will be reviewed again before B.",
    "REVIEW": "Does this plan deliver the accepted vision and quality bar? Review every criterion's "
              "check coverage. Name concrete gaps only.",
    "DECIDE": "Do the user's verbatim words approve, revise or decline this displayed subject?",
    "WORK": "Deliver this whole unit in its worktree: write product and checks inside its scope, "
            "run its checks and refine before returning. Meet the accepted quality bar.",
    "JUDGE": "For each requirement: are its checks meaningful; did they actually exercise the "
             "requirement at P (not skipped, not empty, not asserting nothing); is anything a stub, "
             "mock or missing? Read the check sources, RED output at T or wave start, passing P output "
             "and screenshots. For each requirement set baselineDemonstrated only if every establishes "
             "check failed on T or wave start because the required PRODUCT behavior was absent, and "
             "every preserves check exercised existing behavior on its baseline. A missing product can "
             "demonstrate RED; a missing check helper or broken test environment cannot. "
             "Exit codes are CODE facts; interpret their meaning. A no on any obligation is a gap. "
             + JUDGMENT_LEEWAY,
    "DEMO": "Does this requirement's demonstrated behavior meet its obligation "
            "without stubs, mocks or missing behavior? Inspect final observations, files and screenshots. "
            "Identify the real user path and the observed evidence that exercised it "
            "(a browser interaction for a UI). " + JUDGMENT_LEEWAY,
    "PRODUCT": "Does this quality criterion hold in the delivered experience? "
               "Inspect its traced final evidence. A UI that works but is plain or forgettable "
               "does not meet an accepted distinctive visual direction. " + JUDGMENT_LEEWAY,
}


class UnitTooLarge(ValueError):
    pass


def bounded_unit(question):
    if question["kind"] in ("WORK", "JUDGE") and len(encode(question).encode("utf-8")) > 65536:
        raise UnitTooLarge("unit too large to judge; split it")
    return question


def build(kind, facts, targets=(), execution=None, images=()):
    if kind == "ALIGN":
        facts = {**facts, "Design guidance used by UI workers": CRAFT}
    if kind in ("PLAN", "REPAIR", "REVIEW"):
        facts = {**facts, "How the plan executes": (
            "Each unit has one worker and a separate checkout. Disjoint units can run concurrently "
            "once their dependencies are integrated. Files assigned to the same unit share one worker. "
            "Each unit writes its own local check files inside its scope; checkPaths selects those "
            "files for its tests-only commit. Include newly authored check helpers and fixtures needed "
            "to reach product assertions on this baseline. Every owned requirement needs a local check "
            "that runs "
            "in that checkout before integration. "
            "A check covering requirements from different units runs on the integrated product; "
            "one unit owns its source file. An establishes check must fail on the unit base or, if "
            "already passing there, on the wave-start product with the same check sources. It must "
            "pass at P. A preserves check must pass on the unit base and P with the current check "
            "sources."), "Judge coverage": {
            "Unit JUDGE": "Its own requirements, its checks, its T..P diff, outputs and screenshots. "
                          "No sibling or whole-product files.",
            "DEMO": "One requirement with its traced checks, observations, screenshots and the files "
                    "of every unit reached by those checks.",
            "PRODUCT": "One criterion with its traced checks, observations, screenshots and the files "
                       "of every unit reached by those checks.",
            "Scope rule": "A property is judged at the level whose scope owns every file it speaks "
                          "about. Trace coverage lists the units reached by each requirement and "
                          "criterion. Missing an owner is a gap on that trace."}}
    if kind == "REVIEW":
        facts = {**facts, "Trace coverage": plan.coverage(facts["Plan"])}
    if kind in ("PLAN", "REPAIR"):
        facts = {**facts, "Delivery work supplied by Seldon after the product units": {
            "path": plan.GUIDE_PATH, "work": plan.GUIDE_WORK, "owner": "Seldon's final guide unit"}}
    example = {
        "ALIGN": {"size": "tiny", "understanding": "A personal greeting", "users": ["CLI user"],
                  "scopeIn": ["Greet a supplied name"], "scopeOut": [], "improvements": [],
                  "quality": [{"criterion": "Clear output", "verification": "Run with a name"}],
                  "unknowns": []},
        "PLAN": example_plan(), "REPAIR": example_plan(),
        "REVIEW": {"verdict": "adequate", "gaps": []},
        "DECIDE": {"choice": "approve"},
        "WORK": {"outcome": "done", "summary": "Implemented and ran the assigned checks", "discoveries": []},
        "JUDGE": {"verdict": "satisfied", "gaps": [], "assessments": assessments(targets)},
        "DEMO": {"verdict": "satisfied", "gaps": [], "assessments": assessments(targets, final=True)},
        "PRODUCT": {"verdict": "satisfied", "gaps": [], "assessments": assessments(targets, final=True)},
    }[kind]
    example["why"] = "The observed interaction and check sources support this answer"
    schema = answer_schema(kind, targets)
    validate({"answer": example}, schema)
    return bounded_unit({"kind": kind, "prompt": PROMPTS[kind] + (" " + CRAFT if kind == "WORK" else ""),
            "facts": facts, "targets": list(targets), "schema": schema,
            "examples": [{"answer": example}], "execution": execution or {}, "images": list(images)})


def assessments(targets, *, final=False):
    exercise = "exercisedOnFinalUserPath" if final else "exercisedAtP"
    return [{"target": target, "meaningful": True, exercise: True, "noStubsMocksMissing": True,
             **({} if final else {"baselineDemonstrated": True}),
             "why": "The real interaction asserts the required observable behavior"} for target in targets]


def example_plan():
    return {"edits": [
        {"kind": "feature", "targetHandle": "new:f", "typedBody": {
            "story": "Receive a greeting", "acceptance": "The name is greeted"}},
        {"kind": "requirement", "targetHandle": "new:r", "typedBody": {
            "obligation": "Greet the supplied name", "design": "One argument", "contract": "name -> greeting",
            "features": ["new:f"]}},
        {"kind": "check", "targetHandle": "new:c", "typedBody": {
            "purpose": "Run the greeting command", "mode": "establishes",
            "command": ["python", "-B", "greeting/check.py"], "requiredEnvironment": ["Python"],
            "expectedEvidence": "Actual stdout", "requirements": ["new:r"]}}],
        "units": [{"scope": ["greeting/**"], "checkPaths": ["greeting/check.py"],
                   "requirements": ["new:r"], "dependencies": [], "instructions": "Implement the greeting"}],
        "quality": [{"criterion": 1, "checks": ["new:c"]}]}


def labelled(facts):
    return "\n\n".join(label + "\n" + (value if isinstance(value, str) else
                          json.dumps(value, ensure_ascii=False, indent=2)) for label, value in facts.items())


def runtime(setup):
    return {k: setup[k] for k in ("python", "node", "playwrightPackage", "browserExecutable", "browserHowTo")
            if k in setup}


def requirement_facts(document, refs):
    return {r: {k: v for k, v in document["nodes"][r]["body"].items() if k != "features"} for r in refs}


def work_brief(store, ref):
    unit = store.unit(ref)
    document = store.plan(store.cursor().revision)
    contract = plan.select(document, "unit")[ref]
    requirements = requirement_facts(document, contract["requirements"])
    checks = plan.assigned(document)[ref]
    facts = {"Unit": contract["instructions"], "Write scope": contract["scope"],
             "Check paths": contract["checkPaths"], "Requirements": requirements, "Checks": checks,
             "Quality bar": document["nodes"]["vision"]["body"]["quality"]}
    wave_checks = {r: c for r, c in plan.assigned(document)[None].items()
                   if any(plan.matches(arg, contract["scope"]) for arg in c["command"])}
    if wave_checks:
        facts["Wave checks to write in this scope"] = wave_checks
    if ref == "guide":
        # The writer inspects the already integrated product; its brief never copies the whole plan.
        facts["Guide path"] = plan.GUIDE_PATH
        facts["Reader's starting directory"] = str(store.root.parent.resolve())
        vision = document["nodes"]["vision"]["body"]
        facts["Readers"] = vision["users"]
        facts["What the reader came to do"] = vision["understanding"]
    else:
        facts["Runtime"] = runtime(store.read(store.cursor().kickoff)["setup"])
    if unit.failure:
        facts["Exact failure"] = unit.failure
        gitwork.git(unit.worktree, "add", "--all", "--force", "--", ".", ":!.seldon")
        facts["Current patch"] = gitwork.git(unit.worktree, "diff", "--cached", "--binary", unit.base)
    return build("WORK", facts, execution={"cwd": unit.worktree, "session": unit.thread})


def observed(store, refs):
    return [{k: b[k] for k in ("check", "requirements", "purpose", "command", "exitCode", "stdout", "stderr")}
            for b in (store.read(ref) for ref in refs)]


def images(store, refs):
    from pathlib import Path
    return list(dict.fromkeys(p for ref in refs for p in store.read(ref)["evidenceFiles"]
                             if Path(p).read_bytes().startswith((b"\x89PNG", b"\xff\xd8"))))


def judge_brief(store, ref):
    unit = store.unit(ref)
    document = store.plan(store.cursor().revision)
    contract = plan.select(document, "unit")[ref]
    requirements = requirement_facts(document, contract["requirements"])
    facts = {"Requirements": requirements, "Checks": plan.assigned(document)[ref],
             "Checkout coverage": "Other units' files are not in this checkout; behaviour across units "
                                  "is proven at wave verify. This judgment covers only this unit's "
                                  "requirements against this unit's checks at P.",
             "Check sources": gitwork.sources(unit.worktree, unit.P, contract["checkPaths"]),
             "Product changes": gitwork.git(unit.worktree, "diff", "--no-ext-diff", "--no-textconv",
                                            "--no-renames", unit.T, unit.P),
             "Observations": observed(store, unit.observations)}
    return build("JUDGE", facts, requirements, images=images(store, unit.observations))


def ask(provider, question, record, check=lambda answer: None):
    original = question
    rejected = []
    for attempt in range(3):
        raw = provider(bounded_unit(question))
        errors = []
        try:
            decoded = json.loads(raw)
            validate(decoded, question["schema"])
            answer = decoded["answer"]
            if answer.get("verdict") != "uncertain":
                if answer.get("verdict") == "gap" and not answer["gaps"]:
                    raise ValueError("A gap verdict must name its correction")
                check(answer)
        except (ValueError, KeyError) as error:
            errors = str(error).splitlines()
        record({"rawAnswer": raw, "errors": errors})
        if not errors:
            return answer
        rejected.append({"Rejected answer": raw, "Errors": errors})
        if attempt == 2:
            raise ValueError(f"{original['kind']}: two exact-fix retries exhausted; " + "; ".join(errors))
        question = {**original, "facts": {**original["facts"], "Exact corrections": rejected}}


def satisfied(answer, *, final=False):
    exercise = "exercisedOnFinalUserPath" if final else "exercisedAtP"
    return (answer.get("verdict") == "satisfied" and not answer["gaps"]
            and all((final or a.get("baselineDemonstrated") is True) and a["meaningful"]
                    and a.get(exercise) is True and a["noStubsMocksMissing"]
                    for a in answer["assessments"]))
