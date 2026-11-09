"""C3/C5/C6/C11/I10: bounded facts and one interchangeable provider function."""

import json
import sys

from .schema import answer_schema, validate
from .store import encode

PROMPTS = {
    "ALIGN": "What improved dream should we agree to? Recommend size and a complete quality bar.",
    "DECIDE": "Interpret these verbatim user words about this exact displayed subject.",
    "PLAN": "What product work and semantic checks deliver the accepted vision? "
            "Write only features, requirements, executable tests and productFiles/testFiles. "
            "Commands are argv: [executable, argument, ...], never PowerShell or shell strings. "
            "No beads, waves, check-in, guide or evidence paths: CODE owns those.",
    "REPAIR": "What minimal product edits resolve the supplied cause and actual command output (last 4 KiB per stream)?",
    "IMPACT": "Does this proposed change preserve the user-authorized scope? Uncertainty grants no authorization.",
    "REVIEW": "Does this plan deliver the accepted dream? No implementation evidence is needed. "
              "The wave check-in is a Seldon workflow step, not product work or a product dependency.",
    "WORK": "Complete this task in its assigned worktree and owned files. Report, do not prove.",
    "JUDGE": "What does this evidence establish for the requested obligations? RED means a "
             "functioning test observes missing behavior; GREEN/demo require real passing interactions, "
             "complete scope and accepted quality, including supplied visual evidence. Output is the last 4 KiB per stream.",
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
            "purpose": "Exercise the interaction", "stage": "verification", "command": [sys.executable, "-B", "tests/check.py"],
            "requiredEnvironment": ["Configured verification tool"], "expectedEvidence": "Observed result", "requirements": ["new:2"]}},
    ], "productFiles": ["index.html"], "testFiles": ["tests/check.py"]},
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
        nodes = next((f["boundedContent"]["document"]["nodes"] for f in facts.values()
                      if "document" in f["boundedContent"]), {})
        if nodes:
            target, node = next((r, n) for r, n in nodes.items() if n["kind"] == "requirement")
            body = {**node["body"], "obligation": node["body"]["obligation"] + "; clarify the observed gap"}
            example = {"edits": [{"kind": "requirement", "targetHandle": target, "typedBody": body}]}
        else:
            example = None
    if kind == "JUDGE":
        example = {"verdict": "satisfied", "gaps": [], "neededFacts": [],
            "assessments": [{"target": target, "satisfied": True,
                             "why": "The supplied interaction establishes this obligation"}
                            for target in context["assessments"]]}
    if example is not None:
        example.update(why="The supplied facts support this decision", citations=[ref])
    uncertain = {"verdict": "uncertain", "neededFacts": [{"target": ref,
        "question": "What observed environment capability supports this obligation?"}],
        "why": "The supplied facts do not establish this", "citations": [ref]}
    example, uncertain = {"answer": example or uncertain}, {"answer": uncertain}
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
            if answer.get("verdict") != "uncertain":
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
