"""C3/C5/C6/C11/I10: bounded facts and one interchangeable provider function."""

import json

from .schema import answer_schema, validate
from .store import encode

PROMPTS = {
    "ALIGN": "What improved dream should we agree to? Recommend size and a complete quality bar.",
    "DECIDE": "Interpret these verbatim user words about this exact displayed subject.",
    "PLAN": "What work and semantic checks deliver this accepted vision? Use typed Put edits.",
    "REVIEW": "Does this plan deliver the accepted dream? Implementation evidence is not needed.",
    "WORK": "Complete this task in its assigned worktree and owned files. Report, do not prove.",
    "JUDGE": "What does this observed evidence establish for each requested assessment?",
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


def ask(provider, question):
    """The sole inference boundary: a scripted reply or later native call returns JSON."""
    raw = provider(question)
    answer = json.loads(raw)
    validate(answer, question["schema"])
    if not set(answer["citations"]) <= question["facts"].keys():
        raise ValueError("Citations must name supplied facts")
    return answer


# C5/C9/C11 extension: page large subjects with explicit coverage in bounded projections.
