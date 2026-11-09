"""C3/C5/C6/C11/I10: bounded facts and one interchangeable provider function."""

import json

from .schema import answer_schema, validate
from .store import encode

PROMPTS = {
    "ALIGN": "What improved dream should we agree to? Recommend size and a complete quality bar.",
    "DECIDE": "Interpret these verbatim user words about this exact displayed subject.",
    "PLAN": "What work and semantic checks deliver this accepted vision? Use typed Put edits. "
            "Define features, requirements, verification and walkthrough tests, and owned beads. "
            "Links use your unique targetHandles; the supplied vision handle is 'vision'. "
            "Each ownedPaths entry is an exact file, with one writer. Tests beads precede build "
            "beads; guide beads run after the wave check-in, so exclude guide beads from waves. "
            "Include all three tasks: tests, build, guide, with dependencies in that order. "
            "Declare ordinary commands runnable in the integrated project with installed tools. "
            "Commands must leave product files unchanged; write screenshots/evidence only to "
            "the SELDON_EVIDENCE_DIR environment path supplied at execution time.",
    "REVIEW": "Does this plan deliver the accepted dream? Implementation evidence is not needed.",
    "WORK": "Complete this task in its assigned worktree and owned files. Report, do not prove.",
    "JUDGE": "What does this observed evidence establish for each requested assessment? "
             "For purpose RED, satisfied means the functioning semantic test demonstrates "
             "the missing intended behavior, not broken tooling. For GREEN/demo require actual "
             "passing interactions, complete scope, no mocks/stubs/missing behavior and the "
             "accepted quality bar. Inspect supplied browser screenshots for visual quality. "
             "If evidence is absent or inadequate, name the concrete gap or missing facts.",
    "STEER": "The wave is integrated and judged. Propose its check-in before final walkthrough.",
}


def example(contract, uncertain=False):
    """Construct shape examples, never product facts or acceptance evidence."""
    if "anyOf" in contract:
        return example(contract["anyOf"][0], uncertain)
    if "const" in contract:
        return contract["const"]
    if "enum" in contract:
        values = contract["enum"]
        return next((v for v in ("uncertain", "unclear", "blocked") if uncertain and v in values),
                    values[0])
    kind = contract["type"]
    if kind == "object":
        return {key: example(value, uncertain) for key, value in contract["properties"].items()}
    if kind == "array":
        return [example(contract["items"], uncertain) for _ in range(contract["minItems"])]
    return {"string": "example reference or content", "boolean": False, "null": None}[kind]


def build(kind, facts, context):
    contract = answer_schema(kind)
    question = {"kind": kind, "prompt": PROMPTS[kind], "facts": facts, "context": context,
                "schema": contract, "examples": [example(contract), example(contract, True)]}
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
            answer = json.loads(raw)
            validate(answer, question["schema"])
            missing = set(answer["citations"]) - question["facts"].keys()
            if missing:
                raise ValueError(f"Citations must name supplied facts: {sorted(missing)}")
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
