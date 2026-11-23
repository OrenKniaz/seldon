"""C4/I3: the strict, product-neutral planner answer for a spec edit."""

from ..schema import TEXT, array, choice, obj

NODE_BODIES = {
    "dream": obj(words=TEXT),
    "person": obj(role=TEXT),
    "story": obj(story=TEXT),
    "promise": obj(action=TEXT, result=TEXT, not_met_if=TEXT),
    "design": obj(decision=TEXT, reason=TEXT, words=TEXT),
    "interface": obj(contract=TEXT),
    "check": obj(action=TEXT, expected=TEXT),
    "walk": obj(scenario=TEXT, interruption=TEXT),
}

NODE = {"anyOf": [
    obj(kind=choice(kind), **body["properties"]) for kind, body in NODE_BODIES.items()
]}

BATCH_SCHEMA = obj(
    revision={"type": "integer", "minimum": 0},
    reason=TEXT,
    mode=choice("substantive", "wording-only", "scope-proposal"),
    edits=array({"anyOf": [
        obj(op=choice("add"), name={"anyOf": [TEXT, {"type": "null"}]}, node=NODE),
        obj(op=choice("amend"), id=TEXT, node=NODE),
        obj(op=choice("retire"), id=TEXT),
        obj(op=choice("link", "unlink"), source=TEXT, target=TEXT),
    ]}, 1),
)
"""References are stable IDs or batch-local $names. Amend supplies the full node of the same kind."""
