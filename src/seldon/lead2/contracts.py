"""C4/I3: compact provider answers compiled into strict spec edits."""

import json

from ..schema import TEXT, array, choice, obj

STRING = {"type": "string"}
EDIT_ROWS = array(obj(op=choice("add", "amend", "retire", "link", "unlink"),
                      ref_or_source=TEXT, target=STRING, node_json=STRING))


def answer_schema(**fields):
    """All provider-facing Lead2 contracts use plain fields and rows, with no unions."""
    pending = list(fields.values())
    while pending:
        field = pending.pop()
        if field.get("type") == "array":
            pending.append(field["items"])
        elif field.get("type") == "object":
            pending.extend(field["properties"].values())
        elif field.get("type") not in {"string", "boolean", "integer"}:
            raise ValueError("Use plain fields and rows in Lead2 answers.")
        if {"anyOf", "oneOf", "allOf", "$ref"} & field.keys():
            raise ValueError("Use plain fields and rows in Lead2 answers.")
    return obj(answer=obj(**fields))

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


def typed_reply(body, schema):
    answer = body.get("answer", body) if isinstance(body, dict) else body
    if isinstance(answer, dict):
        fields = schema["properties"]["answer"]["properties"]
        answer = {key: answer.get(key, []) if field.get("type") == "array" else answer.get(key)
                  for key, field in fields.items()}
    return {"answer": answer}


def spec_edits(rows):
    edits = []
    for row in rows:
        op, ref = row["op"], row["ref_or_source"]
        if op in {"add", "amend"}:
            node = json.loads(row["node_json"])
            if not isinstance(node, dict):
                raise ValueError("Each changed node must be an object.")
            edit = {"op": op, "name" if op == "add" else "id": ref, "node": node}
        elif op == "retire":
            edit = {"op": op, "id": ref}
        else:
            edit = {"op": op, "source": ref, "target": row["target"]}
        edits.append(edit)
    return edits


def vision_edits(graph, answer, refs):
    edits = [{"op": "retire", "id": ref} for ref in answer["retire"]]
    handles = {ref: label for label, ref in refs.items()}
    for kind, field, key in (("person", "people", "role"), ("story", "stories", "story")):
        for row in answer[field]:
            label = row["ref"].strip().lstrip("$")
            node = {"kind": kind, key: row[key]}
            edits.append({"op": "amend", "id": label, "node": node} if label in refs else
                         {"op": "add", "name": label, "node": node})
            if kind == "story":
                existing = {handles[source] for source, target in graph.links if target == refs.get(label)}
                wanted = set(row["people"] + row["sources"])
                edits.extend({"op": op, "source": ref, "target": label}
                             for op, group in (("link", wanted - existing), ("unlink", existing - wanted))
                             for ref in sorted(group))
    return edits


def normalise_edits(graph, edits, references):
    """Resolve shown references and omit unsupported relations."""
    from .graph import CONTEXT, LINEAGE

    kinds = {ref: node["kind"] for ref, node in graph.nodes.items()}
    kinds.update(("$" + edit["name"].strip().lstrip("$"), edit["node"]["kind"])
                 for edit in edits if edit["op"] == "add" and edit["name"])
    result = []
    for raw in edits:
        edit = raw.copy()
        for key in ("id", "source", "target"):
            if key in edit:
                handle = edit[key]
                edit[key] = references.get(handle.strip().lstrip("$"), handle)
        node = edit.get("node", {})
        existing = graph.nodes.get(edit.get("id"), {})
        if (node.get("kind") == "dream" or kinds.get(edit.get("id")) == "dream"
                or node.get("kind") == "design" and node.get("reason") == "accepted default"
                or existing.get("kind") == "design" and existing.get("reason") == "accepted default"):
            continue
        if edit["op"] in {"link", "unlink"}:
            pair = tuple(kinds.get(ref) or kinds.get("$" + ref.strip().lstrip("$"))
                         for ref in (edit["source"], edit["target"]))
            if all(pair) and pair not in LINEAGE | CONTEXT and pair[::-1] not in LINEAGE | CONTEXT:
                continue
        result.append(edit)
    return result
