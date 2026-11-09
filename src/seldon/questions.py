"""C3/C5/C6/C11/I10: bounded facts and one interchangeable provider function."""

import json

from .schema import answer_schema, validate
from .store import encode

PROMPTS = {
    "ALIGN": "What improved dream should we agree to? Recommend size and a complete quality bar.",
    "DECIDE": "Interpret these verbatim user words about this exact displayed subject.",
    "PLAN": "What product work and semantic checks deliver the accepted vision? "
            "Use supplied handles and installed tools; CODE orders tests, build, check-in and guide.",
    "REPAIR": "What minimal graph edits resolve the supplied cause? Propose edits, never provenance.",
    "IMPACT": "Does this proposed change preserve the user-authorized scope? Uncertainty grants no authorization.",
    "REVIEW": "Does this plan deliver the accepted dream? No implementation evidence is needed. "
              "The wave check-in is a Seldon workflow step, not product work or a product dependency.",
    "WORK": "Complete this task in its assigned worktree and owned files. Report, do not prove.",
    "JUDGE": "What does this evidence establish for the requested obligations? RED means a "
             "functioning test observes missing behavior; GREEN/demo require real passing interactions, "
             "complete scope and accepted quality, including supplied visual evidence.",
}


EXAMPLES = {
    "ALIGN": {"size": "tiny", "understanding": "Deliver the requested interaction",
        "users": ["The requester"], "scopeIn": ["The agreed interaction"], "scopeOut": [],
        "improvements": [], "quality": [{"criterion": "Clear feedback",
        "verification": "Observe the interaction"}], "unknowns": []},
    "DECIDE": {"choice": "approve", "requestedChange": None},
    "PLAN": {"edits": [
        {"kind": "feature", "targetHandle": "new:1", "typedBody": {
            "story": "The user completes the agreed interaction", "acceptance": "The expected result is visible", "vision": ["vision"]}},
        {"kind": "requirement", "targetHandle": "new:2", "typedBody": {
            "obligation": "Produce the requested result", "design": "Use the supplied input",
            "contract": "Input yields the agreed result", "features": ["new:1"]}},
        {"kind": "test", "targetHandle": "new:3", "typedBody": {
            "purpose": "Exercise the interaction", "stage": "verification", "command": ["check-result"],
            "requiredEnvironment": ["Configured verification tool"], "expectedEvidence": "Observed result", "requirements": ["new:2"]}},
        {"kind": "test", "targetHandle": "new:4", "typedBody": {
            "purpose": "Demonstrate the interaction", "stage": "walkthrough", "command": ["show-result"],
            "requiredEnvironment": ["Configured demonstration tool"], "expectedEvidence": "Live result", "requirements": ["new:2"]}},
        {"kind": "bead", "targetHandle": "new:5", "typedBody": {
            "task": "tests", "instructions": "Write the interaction check", "ownedPaths": ["verification.spec"],
            "dependencies": [], "requirements": ["new:2"], "tests": ["new:3"]}},
        {"kind": "bead", "targetHandle": "new:6", "typedBody": {
            "task": "build", "instructions": "Implement the agreed interaction", "ownedPaths": ["product.source"],
            "dependencies": [], "requirements": ["new:2"], "tests": ["new:3"]}},
        {"kind": "bead", "targetHandle": "new:7", "typedBody": {
            "task": "guide", "instructions": "Explain how to reproduce the result", "ownedPaths": ["TRY.txt"],
            "dependencies": [], "requirements": ["new:2"], "tests": ["new:4"]}},
        {"kind": "wave", "targetHandle": "new:8", "typedBody": {
            "goal": "Deliver the agreed interaction", "beads": ["new:5", "new:6"]}}]},
    "REVIEW": {"verdict": "adequate", "gaps": [], "neededFacts": []},
    "WORK": {"outcome": "done", "summary": "Completed the assigned changes",
             "artifactPaths": [], "discoveries": []},
    "IMPACT": {"verdict": "within_scope", "neededFacts": []},
}


def build(kind, facts, context):
    from copy import deepcopy
    contract = answer_schema(kind, facts, context)
    ref = next(iter(facts))
    example = deepcopy(EXAMPLES.get(kind, EXAMPLES["PLAN"]))
    if kind == "REPAIR":
        target, node = next(((ref, node) for ref, node in context["handles"].items()
                             if node["kind"] == "bead"), next(iter(context["handles"].items())))
        body = deepcopy(node["body"])
        field = "instructions" if node["kind"] == "bead" else "understanding"
        body[field] += "; make the expected result explicit"
        example = {"edits": [{"kind": node["kind"], "targetHandle": target, "typedBody": body}]}
    if kind == "JUDGE":
        example = {"verdict": "satisfied", "gaps": [], "neededFacts": [],
            "assessments": [{"target": target, "satisfied": True,
                             "why": "The supplied interaction establishes this obligation"}
                            for target in context["assessments"]]}
    example.update(why="The supplied facts support this decision", citations=[ref])
    uncertain = {"verdict": "uncertain", "neededFacts": [{"target": ref,
        "question": "What observed environment capability supports this obligation?"}],
        "why": "The supplied facts do not establish this", "citations": [ref]}
    example, uncertain = {"answer": example}, {"answer": uncertain}
    validate(example, contract)
    validate(uncertain, contract)
    question = {"kind": kind, "prompt": PROMPTS[kind], "facts": facts, "context": context,
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
            missing = set(answer["citations"]) - question["facts"].keys()
            if missing:
                raise ValueError(f"Citations must name supplied facts: {sorted(missing)}")
            if answer.get("verdict") == "uncertain":
                if not answer["neededFacts"]:
                    raise ValueError("Uncertain must name needed facts")
            else:
                if answer.get("neededFacts"):
                    raise ValueError("A definite verdict cannot require missing facts")
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


# C5/C9/C11 extension: page large subjects with explicit coverage in bounded projections.
