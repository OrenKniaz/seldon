"""C3/C5/C6/C11/I10: bounded facts and one interchangeable provider function."""

import json
import sys

from .schema import answer_schema, validate
from .store import encode

CRAFT = (" If this product has a user interface, it must look designed by someone with a point of view, following the accepted visual direction. "
         "Avoid the default AI look: a system-font stack as the whole typography, a centered column on off-white, "
         "a generic pill button, purple gradients, cards in cards, gray text on color, bounce easing. "
         "Instead: deliberate type with real hierarchy from fonts available offline; a confident palette with strong contrast; "
         "generous rhythm in spacing; motion that is brief, purposeful and honors reduced-motion; words a person would actually say.")

PROMPTS = {
    "ALIGN": "What improved dream should we agree to? Recommend size and a complete quality bar. "
             "Improve it toward the most beautiful, simplest, most delightful version of what was asked, within scope. "
             "Every quality criterion must be observable with the supplied setup; beauty means craft, not extra requirements. "
             "A product with a user interface states one distinctive visual direction as a quality criterion.",
    "DECIDE": "Interpret the supplied verbatim words about this exact displayed subject. "
              "For revise, state the requested change in why.",
    "PLAN": "What product work and semantic checks deliver the accepted vision, by the simplest design that reaches its quality bar? "
            "Describe features, requirements, executable tests and their file scopes. "
            "Commands are argv: [executable, argument, ...], never PowerShell or shell strings. "
            "For multiple units, decompose semantic scopes and dependency indices; CODE assigns durable identities and schedules work.",
    "REPAIR": "What minimal product edits resolve the supplied cause and actual command output (last 4 KiB per stream)? "
              "Amend product semantics and existing unit contracts together, including local requirement/test links and owned files. "
              "CODE owns identities, scheduling and waves.",
    "IMPACT": "Does this proposed change preserve the user-authorized scope? Uncertainty grants no authorization.",
    "REVIEW": "Does this plan deliver the accepted dream and its quality bar, as simply as possible? No implementation evidence is needed.",
    "WORK": "Complete this task in its assigned worktree and owned files. Build it beautifully the first time: "
            "meet the accepted quality bar with nothing that does not earn its place." + CRAFT + " "
            "Run the assigned checks and refine before returning. Local checks are feedback; Seldon independently observes acceptance.",
    "JUDGE": "Judge only the requested semantic obligations using the established execution and applicability facts. "
             "Unrequested improvements that support the accepted experience and keep its meaning, constraints and commitments are fine; "
             "reject one only by citing the accepted obligation or material consequence it breaks. "
             "Classify each gap by cause and name its target and correction. Output is the last 4 KiB per stream.",
}

JUDGMENTS = {"RED": "Review the actual test source against its accepted obligations: would it detect their absence or violation? "
                    "Attribute the observed failure to the absent/unsatisfied product or to defective tests/tooling. "
                    "The subject is the test's meaningfulness, not evidence of product behavior. "
                    "Obligations declared already satisfied must remain green.",
             "GREEN": "Does the observed check establish its promised behavior?",
             "demo": "Does the demonstrated product meet the accepted quality bar, without stubs, mocks or missing behavior, and have a usable try-it guide? Inspect the actual UI screenshots."}


EXAMPLES = {
    "ALIGN": {"size": "tiny", "understanding": "Deliver the requested interaction",
        "users": ["The requester"], "scopeIn": ["The agreed interaction"], "scopeOut": [],
        "improvements": [], "quality": [{"criterion": "Clear feedback",
        "verification": "Observe the interaction"}], "unknowns": []},
    "DECIDE": {"choice": "approve"},
    "PLAN": {"edits": [
        {"kind": "feature", "targetHandle": "new:1", "typedBody": {
            "story": "The user completes the agreed interaction", "acceptance": "The expected result is visible", "vision": ["vision"]}},
        {"kind": "requirement", "targetHandle": "new:2", "typedBody": {
            "obligation": "Produce the requested result", "design": "Use the supplied input",
            "contract": "Input yields the agreed result", "features": ["new:1"]}},
        {"kind": "test", "targetHandle": "new:3", "typedBody": {
            "purpose": "Exercise the interaction", "stage": "verification", "command": [sys.executable, "-B", "tests/check.py"],
            "requiredEnvironment": ["Configured verification tool"], "expectedEvidence": "Observed result", "requirements": ["new:2"]}},
    ], "productFiles": ["index.html"], "testFiles": ["tests/check.py"]},
    "REVIEW": {"verdict": "adequate", "gaps": []},
    "WORK": {"outcome": "done", "summary": "Completed the assigned changes",
             "artifactPaths": [], "discoveries": []},
    "IMPACT": {"verdict": "within_scope"},
}


def build(kind, facts, context):
    from copy import deepcopy
    if kind in ("PLAN", "REVIEW", "WORK", "JUDGE"):
        context = {**context, "verification": {"checks": "Automated checks never wait on manual or visual review.",
            "visualQuality": "Seldon judges visual quality from real screenshots at demo."}}
    if kind == "JUDGE":
        context = {**context, "executionMeaning": "Exit codes are execution facts. Tooling failure, all-skipped runs and checks that assert nothing establish neither RED nor GREEN."}
    contract = answer_schema(kind, facts, context)
    ref = next(iter(facts))
    example = deepcopy(EXAMPLES.get(kind, EXAMPLES["PLAN"]))
    if kind == "PLAN" and context.get("size", "tiny") != "tiny":
        example = {"edits": example["edits"], "units": [{"requirements": ["new:2"], "tests": ["new:3"],
            "productFiles": example["productFiles"], "testFiles": example["testFiles"], "dependencies": []}]}
    if kind == "REPAIR":
        nodes = next((f["boundedContent"]["document"]["nodes"] for f in facts.values()
                      if "document" in f["boundedContent"]), {})
        if nodes:
            target, node = next((r, n) for r, n in nodes.items() if n["kind"] == "requirement")
            body = {**node["body"], "obligation": node["body"]["obligation"] + "; clarify the observed gap"}
            example = {"edits": [{"kind": "requirement", "targetHandle": target, "typedBody": body}]}
        else:
            example = None
    if kind == "JUDGE":
        example = {"verdict": "satisfied", "gaps": [],
            "assessments": [{"target": target, "satisfied": True,
                             "why": ("The test asserts the obligation and fails on its absence" if context["purpose"] == "RED" else "The supplied interaction establishes this obligation")}
                            for target in context["assessments"]]}
    if example is not None:
        example.update(why="The supplied facts support this decision", citations=[ref])
    uncertain = {"verdict": "uncertain", "neededFacts": [{"target": ref,
        "question": "What observed environment capability supports this obligation?"}],
        "why": "The supplied facts do not establish this", "citations": [ref]}
    example, uncertain = {"answer": example or uncertain}, {"answer": uncertain}
    validate(example, contract)
    validate(uncertain, contract)
    question = {"kind": kind, "prompt": PROMPTS[kind] + (" " + JUDGMENTS[context["purpose"]] if kind == "JUDGE" else ""), "facts": facts, "context": context,
                "schema": contract, "examples": [example, uncertain]}
    if len(encode(question).encode("utf-8")) > 65536:
        raise ValueError("Question exceeds 64 KiB; split its coverage before asking")
    return question


def ask(provider, question, record, check=lambda answer: None):
    """One inference boundary; exactly two fixes, then an explicit failure."""
    original = question
    facts = dict(original["facts"])
    for attempt in range(3):
        raw = provider(question)
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
        ref = record({"question": question, "rawAnswer": raw, "errors": errors})
        if not errors:
            return answer
        if attempt == 2:
            raise ValueError(f"{original['kind']}: malformed answer after two exact-fix retries; "
                             f"rejected answer {ref}: " + "; ".join(errors))
        facts[ref] = {"sourceRecord": ref, "revision": None,
                      "boundedContent": {"rejectedAnswer": raw, "errors": errors}}
        question = build(original["kind"], dict(facts), {**original["context"],
            "exactFix": "Correct every recorded error. Original facts and all rejected answers "
                        "remain supplied. Return the complete corrected answer."})
