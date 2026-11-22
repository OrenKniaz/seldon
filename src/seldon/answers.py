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


def normalised(record, field, before, after, summary):
    if record is not None and before != after:
        record("answer_normalisation", {"field": field, "original": before,
                                       "value": after, "summary": summary})
    return after


def recorder(store):
    cursor = store.cursor()

    def record(kind, body):
        store.record(kind, {"vision": cursor.vision, "wave": cursor.wave, "stage": cursor.state, **body})

    return record


def links(refs, targets, *, via=None, record=None, field, subject):
    """Prefer canonical handles, then explicit trace links or an unambiguous bare handle."""
    trace = via or {}
    aliases = {}
    for handle in dict.fromkeys([*targets, *trace]):
        aliases.setdefault(handle.split(":", 1)[-1], []).append(handle)
    resolved = []
    for ref in refs:
        if ref not in targets and ref not in trace and len(aliases.get(ref, ())) == 1:
            ref, = aliases[ref]
        # Retain unresolved references for the owning boundary to explain.
        resolved.extend([ref] if ref in targets else trace.get(ref, [ref]))
    result = list(dict.fromkeys(resolved))
    return normalised(record, field, refs, result, f"Resolved {field} for {subject}: {refs} → {result}.")


class Mismatch(ValueError):
    def __init__(self, field, reason):
        self.field, self.reason = field, reason
        super().__init__(reason)


class ConsumptionFailure(ValueError):
    """The same question could not be consumed, even after its exact correction."""


class ContractGap(ValueError):
    def __init__(self, message, areas):
        super().__init__(message)
        self.areas = areas


def index(items, field="areas/contracts", *, key="handle", record=None):
    result = {item[key]: item for item in items}
    if any(item != result[item[key]] for item in items):
        raise Mismatch(field, f"Seldon expected one entry per handle in {field}; got "
                       f"conflicting entries among {len(items)} entries for {len(result)} handles. "
                       "Send one agreed entry per handle.")
    normalised(record, field, len(items), len(result), f"Removed identical entries in {field}.")
    return result
