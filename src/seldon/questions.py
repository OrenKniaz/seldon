"""C3/C4/I10; R2/R8/R12: human-readable briefs bounded by one unit or one feature."""

import json

from . import architecture, fixups, gitwork, obligations, plan
from .schema import SOURCE_TARGETS, answer_schema, validate

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

UNIT_EVIDENCE = (
    "The unit JUDGE receives local observations at T and P and owned wave checks at wave start. "
    "For visual requirements, checks save screenshots in SELDON_EVIDENCE_DIR at each observable state. "
    "Owned wave checks observe the baseline at wave start; GREEN is proven at WAVE-VERIFY; "
    "other units' files missing from this worktree are expected, never a blocker.")

AGREED_LITERALS = (
    "The user's words are agreed text; an exact value they state appears verbatim in the commitment, "
    "contract or requirement that owns it. "
    "When asserting an exact value about another area's surface or a shared contract, a check uses "
    "agreed text visible to the area (accepted vision commitment, contract text, or requirement text); "
    "otherwise assert by role, structure or behaviour; an unagreed value needed by two areas is a "
    "contract_gap. A unit's own local implementation values need no agreement.")

PROMPTS = {
    "ALIGN": "What improved dream should we agree to? Recommend size and an observable quality bar. "
             "Keep every exact value the user stated verbatim in the scopeIn item it belongs to; "
             "improving never paraphrases an exact value. "
             + ALIGN_DESIGN,
    "ARCHITECT": "What areas, agreements, decisions and wave goals deliver the accepted vision? "
                 "Assign every supplied commitment handle to an area; name disjoint area scopes "
                 "covering product files and each area's own check subtree, with one owner for shared "
                 "check helpers, and one responsible provider area for each contract. "
                 "Return architecture only: no features, requirements, checks or units. "
                 "Contract text states every exact user-visible or machine-visible value two areas "
                 "must share (labels, names, messages, ids, routes) and nothing more. "
                 "Copy exact values from the user's words into contract text or the owning commitment; "
                 "a value they leave open is yours to choose — record it in decisions.",
    "DETAIL": "What features, requirements, semantic checks and units deliver this area? "
              "Divide its complete scope among units and bind provided and consumed contracts by handle. "
              "A wave check names its local requirements and the consumed contract handles; "
              "code links those contracts' provider requirements. "
              "Declare checks unit or wave: unit checks run in their owner's checkout; a check reaches only "
              "through its owner's product and contracts its unit consumes, so a contract's integrated check "
              "belongs to a consumer and a provider proves its side locally. "
              "Dependencies (local 1-based units or exact files) serve declared local reads only, never "
              "contract consumption or authoring wave checks; assign each integrated scenario and distinct "
              "obligation once at its strongest boundary, with one documentation owner and local 1-based "
              "obligation owners. "
              "Trace quality criteria by their supplied 1-based positions. Commands are argv. "
              "Checks run automatically; Seldon judges visual quality from saved screenshots. "
              "If the answer exceeds the small list bounds, return subdivide with narrower sub-areas "
              "covering this responsibility and their contracts. If an agreement or needed scope is missing, "
              "return contract_gap and the need; do not invent an agreement. " + AGREED_LITERALS,
    "REVIEW": "Does this area's plan deliver its part of the accepted scope and quality bar? "
              "Check its records, agreements and trace coverage. "
              "Judge only the supplied subject. Name concrete gaps only. "
              + AGREED_LITERALS,
    "DECIDE": "Do the user's verbatim words approve, revise or decline this displayed subject?",
    "CONTRACT": "What contract text resolves this worker's need within the accepted commitments? "
                "Replace only consumed contract texts and record the decision; answer user if the fix "
                "would change an accepted commitment.",
    "WORK": "Deliver this whole unit in its worktree: write product and checks inside its scope, "
            "run its unit checks and refine before returning. Meet the accepted quality bar.",
    "JUDGE": "For each requirement: are its checks meaningful (they exercise the requirement, not "
             "skipped, not empty, not asserting nothing; code proves they ran); is anything a stub, "
             "mock or missing? Read the check sources, RED output at T or wave start, passing P output "
             "and screenshots. For baselineDemonstrated, a requirement new in this unit needs a check "
             "that failed without the product; a check that passed there proves preservation only. "
             "A missing product can demonstrate RED; a missing check helper or broken test environment cannot. "
             "Exit codes are CODE facts; interpret their meaning. A no on any obligation is a gap. "
             + JUDGMENT_LEEWAY + " " + AGREED_LITERALS,
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
    if kind == "JUDGE":
        targets = [*targets, *SOURCE_TARGETS]
    if kind == "ALIGN":
        facts = {**facts, "Design guidance used by UI workers": CRAFT}
    if kind == "DETAIL" or kind == "REVIEW" and facts.get("Review subject") != "architecture":
        facts = {**facts, "How the plan executes": (
            "Each unit has one worker and a separate checkout. Disjoint units can run concurrently "
            "once their dependencies are integrated. Files assigned to the same unit share one worker. "
            "Nothing is running: a wave check is an ordinary command on the integrated checkout "
            "that starts what it needs and stops it. "
            "checkPaths selects each unit's local check files for its tests-only commit. "
            "Area scopes cannot grow in DETAIL; missing space returns "
            "to ARCHITECT. Contract text is available before parallel work; consuming it does not wait "
            "for its implementation. Reading another unit's newly built files requires an explicit "
            "dependency. Include newly authored check helpers and fixtures needed "
            "to reach product assertions on this baseline. Every owned requirement needs a local check "
            "that runs "
            "in that checkout before integration. "
            "A check covering requirements from different units runs on the integrated product at "
            "wave level, never by rebuilding another area's surface in a local fixture; "
            "one unit owns its source file. Code records each check's exit at T and, if it passed there, "
            "at wave start, with all units' check sources and helpers retained; all checks must pass at P. "
            "Shared check mechanics belong in checkPaths. A requirement new in this unit needs a check "
            "that failed without the product; a check that passed there proves preservation only."),
            "Unit evidence": UNIT_EVIDENCE, "Judge coverage": {
            "Unit JUDGE": "Its own requirements and standing source obligations, its checks, product "
                          "files at P, its T..P diff, outputs and screenshots. "
                          "No sibling or whole-product files.",
            "DEMO": "One requirement's behaviour: traced checks, final observations, screenshots and "
                    "a CODE summary of reached unit source verdicts and unchanged scope trees.",
            "PRODUCT": "One criterion's behaviour: traced checks, final observations, screenshots and "
                       "a CODE summary of reached unit source verdicts and unchanged scope trees."}}

    if kind == "DETAIL":
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
        "CONTRACT": {"verdict": "user"},
        "WORK": {"outcome": "done", "summary": "Implemented and ran the assigned checks", "discoveries": []},
        "JUDGE": {"verdict": "satisfied", "gaps": [],
                  "assessments": assessments(targets)},
        "DEMO": {"verdict": "satisfied", "gaps": [], "assessments": assessments(targets, final=True)},
        "PRODUCT": {"verdict": "satisfied", "gaps": [], "assessments": assessments(targets, final=True)},
    }[kind]
    example["why"] = ("The responsibilities, agreements and waves cover the accepted promises"
                      if kind == "REVIEW" and facts.get("Review subject") == "architecture" else
                      "The observed interaction and check sources support this answer")
    schema = answer_schema(kind, targets, owners)
    validate({"answer": example}, schema)
    return {"kind": kind, "prompt": PROMPTS[kind] + (" " + CRAFT if kind == "WORK" else ""),
            "facts": facts, "targets": list(targets), "schema": schema,
            "examples": [{"answer": example}], "execution": execution or {}, "images": list(images)}


def assessments(targets, *, final=False):
    return [{"target": target, "meaningful": True, "noStubsMocksMissing": True,
             **({} if final or target in SOURCE_TARGETS else
                {"baselineDemonstrated": True}),
             "why": "The real interaction asserts the required observable behavior"} for target in targets]


def example_plan():
    return {"edits": [
        {"kind": "feature", "targetHandle": "new:f", "typedBody": {
            "story": "Receive a greeting", "acceptance": "The name is greeted"}},
        {"kind": "requirement", "targetHandle": "new:r", "typedBody": {
            "obligation": "Greet the supplied name", "design": "One argument", "contract": "name -> greeting",
            "features": ["new:f"]}},
        {"kind": "check", "targetHandle": "new:c", "typedBody": {
            "purpose": "Run the greeting command", "level": "unit",
            "command": ["python", "-B", "greeting/check.py"], "requiredEnvironment": ["Python"],
            "expectedEvidence": "Actual stdout", "requirements": ["new:r"]}},
        {"kind": "area", "targetHandle": "new:a", "typedBody": {
            "responsibility": "Deliver greetings", "scope": ["greeting/**"]}}],
        "units": [{"scope": ["greeting/**"], "checkPaths": ["greeting/check.py"],
                   "requirements": ["new:r"], "dependencies": [], "instructions": "Implement the greeting",
                   "area": "new:a", "provides": [], "consumes": [], "reads": []}], "skeleton": [],
        "quality": [{"criterion": 1, "checks": ["new:c"]}]}


def example_architecture():
    return {"areas": [{"handle": "greeting", "responsibility": "Deliver greetings",
                       "scope": ["greeting/**", "checks/greeting/**"],
                       "commitments": ["scopeIn:1", "quality:1"]}],
            "contracts": [], "decisions": [], "waves": [{"goal": "Deliver a complete greeting"}]}


def example_detail():
    example = example_plan()
    return {"verdict": "planned", "edits": [e for e in example["edits"] if e["kind"] != "area"],
            "units": [{k: v for k, v in u.items() if k != "area"} for u in example["units"]],
            "quality": example["quality"]}


def user_words(store):
    cursor = store.cursor()
    if cursor.vision:
        answer = store.read(cursor.vision)
        attempt = store.read(answer["attempt"])
        dream = store.read(attempt["question"])["facts"]["Dream"]
    else:
        dream = store.read(cursor.kickoff)["words"]
    changes = [store.read(b["words"])["words"] for _, _, b in store.records("approval")
               if b.get("choice") == "revise"]
    return {"Dream": dream, **({"Requested change": changes} if changes else {})}


def architect_brief(vision, words, gap=None, previous=None):
    facts = {"Approved vision": {k: v for k, v in vision.items() if k != "why"},
             "User's words": words,
             "Commitment handles": architecture.commitments(vision),
             "Ownership": "Areas own complete disjoint scopes including their check subtrees. "
                          "Shared check helpers have one named owner. DETAIL cannot enlarge these scopes.",
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
    return {k: setup[k] for k in ("python", "node", "playwrightPackage", "browserExecutable", "browserHowTo")
            if k in setup}


def requirement_facts(document, refs):
    return {r: {k: v for k, v in document["nodes"][r]["body"].items() if k != "features"} for r in refs}


def agreement_facts(document, ref):
    # Endpoints and adjacent units stay in the plan; this question needs only its own agreements.
    contracts = architecture.contracts_for(document, ref)
    return {"Contract authority": "Contract nodes below override copies in requirement prose.",
            **{label: {r: {k: c[k] for k in ("kind", "text")} for r, c in contracts.items()
                    if architecture.endpoint(ref, c) == side}
            for label, side in (("Contracts provided", "provider"), ("Contracts consumed", "consumer"),
                                ("Area contracts", None))
            if any(architecture.endpoint(ref, c) == side for c in contracts.values())}}


def contract_brief(store, ref, discoveries):
    document = store.plan(store.cursor().revision)
    units, contracts = plan.select(document, "unit"), plan.select(document, "contract")
    consumed = {r: {**contracts[r], "Provider responsibility":
                   units[contracts[r]["provider"]]["instructions"]}
                for r in units[ref]["consumes"] + units[ref]["provides"]}
    return build("CONTRACT", {"Worker's need": discoveries, "Contracts consumed": consumed,
        "Accepted commitments": document["nodes"]["vision"]["body"]}, targets=tuple(consumed))


def repair_facts(store, assignment):
    refs = [r for g in assignment["gaps"] for r in
            store.read(g["source"]).get("observations", [g["source"]]) if "evidenceFiles" in store.read(r)]
    return {"Scoped corrections": {"owner": assignment["owner"], "writeScope": assignment["scope"],
        "corrections": [g["correction"] for g in assignment["gaps"]], "gaps": assignment["gaps"]},
        "Failing observations": observed(store, refs),
        "Historical observations": observed(store, assignment["observations"])}, refs


def work_brief(store, ref):
    unit = store.unit(ref)
    document = store.plan(store.cursor().revision)
    contract = plan.select(document, "unit")[ref]
    requirements = requirement_facts(document, contract["requirements"])
    checks = plan.assigned(document)[ref]
    facts = {"Unit": contract["instructions"], "Write scope": contract["scope"],
             "Check paths": contract["checkPaths"], "Requirements": requirements, "Checks": checks,
             "Quality bar": document["nodes"]["vision"]["body"]["quality"],
             "Unit evidence": UNIT_EVIDENCE}
    facts.update(agreement_facts(document, ref))
    refs = []
    if assignment := fixups.active(store, ref):
        repair, refs = repair_facts(store, assignment)
        facts.update(repair)
    if items := obligations.for_area(document, contract["area"]):
        owned = {r: o for r, o in items.items() if o["owner"] == ref}
        if owned:
            facts["Skeleton obligations owned here"] = owned
    writers = plan.owners(document)
    wave_checks = {r: c for r, c in plan.assigned(document)[None].items() if writers[r] == ref}
    if wave_checks:
        facts["Wave checks to write, not run here"] = wave_checks
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
        facts["Current patch"] = gitwork.git(unit.worktree, "diff", "--cached", "--stat", unit.base)
        facts["Read current patch"] = ["git", "diff", "--cached", "--binary", unit.base]
    units = plan.select(document, "unit")
    owners = {ref, *plan.prerequisites(document, ref)}
    owners.update(c["provider"] for c in architecture.contracts_for(document, ref).values())
    facts["Correction owners"] = {r: plan.owned_scope(u)
                                  for r, u in units.items() if r in owners}
    return build("WORK", facts, execution={"cwd": unit.worktree, "session": unit.thread},
                 images=images(store, refs),
                 owners=(*facts["Correction owners"], *contract["consumes"], *contract["provides"]))


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
    document = store.plan(store.cursor().revision)
    contract = plan.select(document, "unit")[ref]
    refs = list(observations if observations is not None else unit.observations)
    if assignment := fixups.active(store, ref):
        refs += [r for r in assignment["observations"] if store.read(r)["purpose"] in ("T", "wave-start")]
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
    writers = plan.owners(document)
    facts = {"Unit responsibility": contract["instructions"],
             "Declared dependency scopes": [plan.select(document, "unit")[r]["scope"]
                                            for r in plan.prerequisites(document, ref)],
             "Requirements": requirements, "Checks": {r: c for r, c in plan.select(document, "check").items()
                                                    if writers[r] == ref},
             "Checkout coverage": "Other units' files are outside this judgment; behaviour across units "
                                  "is proven at wave verify. This judgment covers only this unit's "
                                  "requirements against this unit's checks at P and its source obligations. An owned wave "
                                  "check shows its RED here; its passing run is code-proven at wave verify.",
             "Check sources": checks,
             "Binary files at P (bytes)": binaries,
             # The unit's whole product source at P: a rebased rework can leave the T..P diff empty (T-86).
             "Product files at P": product,
             "Product changes": gitwork.git(root, "diff", "--no-ext-diff", "--no-textconv",
                                            "--no-renames", unit.T, commit, "--", *contract["scope"]),
             "Observations": observed(store, refs)}
    if assignment:
        repair, failing = repair_facts(store, assignment)
        facts.update(repair)
        refs += failing
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
            if question["kind"] == "DETAIL" and isinstance(decoded.get("answer"), dict):
                plan.recorded_levels(decoded["answer"])
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


def satisfied(answer, *, final=False):
    return (answer.get("verdict") == "satisfied" and not answer["gaps"]
            and all((final or "baselineDemonstrated" not in a
                     or a["baselineDemonstrated"] is True)
                    and a["meaningful"] and a["noStubsMocksMissing"]
                    for a in answer["assessments"]))
