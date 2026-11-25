"""C4/I3: compact provider answers compiled into strict spec edits."""

from ..schema import REFS, TEXT, array, choice, obj

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
LINK_ROWS = array(obj(source=TEXT, targets=REFS))
TITLED = ("story", "promise", "walk")
TITLE = {"type": "string"}
EDIT_FIELDS = {
    **{kind: array(obj(ref=TEXT, **body["properties"]))
       for kind, body in NODE_BODIES.items() if kind != "dream"},
    "retire": REFS, "link": LINK_ROWS, "unlink": LINK_ROWS, "replaces": LINK_ROWS,
}
SPEC_EDITS = {**EDIT_FIELDS, **{
    kind: array(obj(ref=TEXT, **NODE_BODIES[kind]["properties"], title=TITLE)) for kind in TITLED
}}
OPTION_EDITS = array(obj(
    question=TEXT, option=choice("recommended", "alternative"),
    kind=choice(*EDIT_FIELDS), ref=TEXT, values=REFS,
))


def option_rows(rows):
    """Compile one option's flat rows into the same edit tables used by SPEC."""
    answer = {kind: [] for kind in EDIT_FIELDS}
    for row in rows:
        kind, ref, values = row["kind"], row["ref"], row["values"]
        if kind == "retire":
            answer[kind].append(ref)
        elif kind in NODE_BODIES:
            fields = NODE_BODIES[kind]["properties"]
            if len(values) == len(fields):
                answer[kind].append({"ref": ref, **dict(zip(fields, values, strict=True))})
        else:
            answer[kind].append({"source": ref, "targets": values})
    return answer

NODE = {"anyOf": [
    obj(kind=choice(kind), **body["properties"]) for kind, body in NODE_BODIES.items()
]}
for kind, contract in zip(NODE_BODIES, NODE["anyOf"], strict=True):
    if kind in TITLED:
        contract["properties"]["title"] = TITLE

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
        if stack := answer.get("stack"):
            answer["design"] = [*stack, *answer["design"]]
        for kind in TITLED:
            if (isinstance(answer.get(kind), list)
                    and "title" in fields.get(kind, {}).get("items", {}).get("properties", {})):
                answer[kind] = [
                    {**row, "title": row["title"] if isinstance(row.get("title"), str) else ""}
                    if isinstance(row, dict) else row for row in answer[kind]
                ]
    return {"answer": answer}


def normalise_edits(graph, answer, references, *, prefix=""):
    """Resolve shown references and omit unsupported relations."""
    from .graph import CONTEXT, LINEAGE, PROOF

    def resolve(label):
        name = label.strip().lstrip("$")
        return references.get(name, "$" + prefix + name) if name else "$"

    if "amendments" in answer:
        rows = {resolve(row["ref"]): row for row in answer["amendments"]}
        edits = [dict(op="amend", id=ref,
                      node={**graph.nodes[ref], "result": row["result"], "not_met_if": row["not_met_if"]})
                 for ref, row in rows.items() if graph.nodes.get(ref, {}).get("kind") == "promise"]
        return edits, len(answer["amendments"]) - len(edits)

    kinds = {ref: node["kind"] for ref, node in graph.nodes.items()}
    fixed = {ref for ref, node in graph.nodes.items()
             if node["kind"] == "dream" or node.get("reason") == "accepted default"}
    edits, seen, ignored = [], set(), 0
    for kind, fields in NODE_BODIES.items():
        for row in answer.get(kind, ()):
            ref = resolve(row["ref"])
            if (ref == "$" or ref in fixed or ref in seen or kinds.get(ref, kind) != kind
                    or (kind == "design" and row["reason"] == "accepted default")):
                ignored += 1
                continue
            seen.add(ref)
            node = {"kind": kind, **{key: row[key] for key in fields["properties"]}}
            if kind in TITLED and (title := row.get("title", "").strip()
                                  or graph.nodes.get(ref, {}).get("title")):
                node["title"] = title
            edits.append(dict(op="amend", id=ref, node=node) if ref in kinds else
                         dict(op="add", name=ref, node=node))
            kinds[ref] = kind
    for label in answer["retire"]:
        ref = resolve(label)
        if ref in kinds and ref not in fixed:
            edits.append(dict(op="retire", id=ref))
            del kinds[ref]
        else:
            ignored += 1
    for row in answer["replaces"]:
        original = resolve(row["source"])
        if graph.nodes.get(original, {}).get("kind") != "promise":
            continue
        for label in row["targets"]:
            replacement = resolve(label)
            if kinds.get(replacement) == "promise":
                edits.extend(dict(op="link", source=replacement, target=target)
                             for source, target in graph.links if source == original and target in kinds
                             and graph.kinds((source, target)) in PROOF)
    allowed = LINEAGE | CONTEXT
    for op in ("unlink", "link"):
        for row in answer[op]:
            source = resolve(row["source"])
            for label in row["targets"]:
                target = resolve(label)
                pair = kinds.get(source), kinds.get(target)
                if pair in allowed or pair[::-1] in allowed:
                    edits.append(dict(op=op, source=source, target=target))
                else:
                    ignored += 1
    return edits, ignored
