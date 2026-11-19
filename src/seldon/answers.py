"""C4/I8; R3/R12: consumption errors explain an answer contract without exposing code."""

import json


def render_constraint_values(decoded):
    """Accept structured literals at the reply boundary while keeping the provider schema strict."""
    answer = decoded.get("answer") if isinstance(decoded, dict) else None
    vision = answer.get("vision") if isinstance(answer, dict) else None
    constraints = vision.get("constraints") if isinstance(vision, dict) else None
    changes = []
    for constraint in constraints if isinstance(constraints, list) else ():
        if isinstance(constraint, dict) and isinstance(value := constraint.get("value"), (dict, list)):
            constraint["value"] = json.dumps(value, ensure_ascii=False)
            changes.append({"subject": constraint.get("handle"), "action": "rendered",
                            "original": value, "value": constraint["value"],
                            "summary": f"Rendered constraint {constraint.get('handle')!r} as text."})
    return changes


class Mismatch(ValueError):
    def __init__(self, field, reason):
        self.field, self.reason = field, reason
        super().__init__(reason)


class ConsumptionFailure(ValueError):
    """The same question could not be consumed, even after its exact correction."""

