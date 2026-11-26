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
PRIORITY = choice("must", "should", "nice")
YES_NO = {"type": "boolean"}
SPEC_EDITS = {
    **{kind: array(obj(ref=TEXT, **body["properties"], **({"title": TITLE} if kind in TITLED else {})))
       for kind, body in NODE_BODIES.items() if kind != "dream"},
    "retire": REFS, "link": LINK_ROWS, "unlink": LINK_ROWS, "replaces": LINK_ROWS,
}
SPEC_STORY = array(obj(**SPEC_EDITS["story"]["items"]["properties"],
                       core_job_why=TEXT, core_job=YES_NO, nice_why=TITLE, nice=YES_NO))
OPTION_FIELDS = {
    f"option_{kind}": array(obj(option_id=TEXT, **(
        {"ref": TEXT} if kind == "retire" else field["items"]["properties"])))
    for kind, field in SPEC_EDITS.items()
}

NODE = {"anyOf": [
    obj(kind=choice(kind), **body["properties"]) for kind, body in NODE_BODIES.items()
]}
for kind, contract in zip(NODE_BODIES, NODE["anyOf"], strict=True):
    if kind in TITLED:
        contract["properties"]["title"] = TITLE
    if kind == "story":
        contract["properties"]["priority"] = PRIORITY
    if kind == "design":
        contract["properties"]["owner_choice"] = YES_NO

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
        for row in answer.get("story", []) if isinstance(answer.get("story"), list) else ():
            if isinstance(row, dict) and row.get("core_job") is True:
                row.update(nice=False, nice_why="")
        if "unresolved_issue" in fields and answer["unresolved_issue"] is None:
            answer["unresolved_issue"] = ""
        for kind in (*TITLED, *(f"option_{kind}" for kind in TITLED)):
            if (isinstance(answer.get(kind), list)
                    and "title" in fields.get(kind, {}).get("items", {}).get("properties", {})):
                answer[kind] = [
                    {**row, "title": row["title"] if isinstance(row.get("title"), str) else ""}
                    if isinstance(row, dict) else row for row in answer[kind]
                ]
    return {"answer": answer}


def normalise_edits(graph, answer, references, *, prefix=""):
    """Resolve shown references and identify edits that cannot be applied."""
    from .graph import CONTEXT, LINEAGE, PROOF

    def resolve(label):
        name = label.strip().lstrip("$")
        return references.get(name, "$" + prefix + name) if name else "$"

    if "amendments" in answer:
        edits, errors = [], []
        for field, tag, value in (("amendments", "kind", "promise"), ("assumptions", "owner_choice", False)):
            for row in answer[field]:
                ref = resolve(row["ref"])
                if graph.nodes.get(ref, {}).get(tag) != value:
                    errors.append(f"{row['ref']!r}: use an existing {'assumption' if value is False else value} ID.")
                    continue
                updated = {**graph.nodes[ref], **{k: v for k, v in row.items() if k != "ref"}}
                if field == "assumptions":
                    updated.update(owner_choice=True, words=row["decision"])
                edits.append(dict(op="amend", id=ref, node=updated))
        return edits, errors

    kinds = {ref: node["kind"] for ref, node in graph.nodes.items()}
    fixed = {ref for ref, node in graph.nodes.items() if node["kind"] == "dream" or "owner_choice" in node}
    edits, seen, errors = [], set(), []
    for kind, fields in NODE_BODIES.items():
        for row in answer.get(kind, ()):
            ref = resolve(row["ref"])
            if ref in fixed and kinds[ref] == kind and all(
                    row.get(k) == graph.nodes[ref].get(k) for k in fields["properties"]):
                continue
            if ref == "$" or ref in fixed or ref in seen or kinds.get(ref, kind) != kind:
                errors.append(f"{kind} {row['ref']!r}: use a distinct new name or an editable {kind} ID.")
                continue
            seen.add(ref)
            node = {"kind": kind, **{key: row[key] for key in fields["properties"]}}
            if kind == "story":
                node["priority"] = graph.nodes.get(ref, {}).get("priority", "must")
                if "core_job" in row:
                    node["priority"] = "must" if row["core_job"] else "nice" if row["nice"] else "should"
            if kind in TITLED and (title := row.get("title", "").strip()
                                  or graph.nodes.get(ref, {}).get("title")):
                node["title"] = title
            edits.append(dict(op="amend", id=ref, node=node) if ref in kinds else
                         dict(op="add", name=ref, node=node))
            kinds[ref] = kind
    for label in answer.get("retire", ()):
        ref = resolve(label)
        if ref in kinds and ref not in fixed:
            edits.append(dict(op="retire", id=ref))
            del kinds[ref]
        else:
            errors.append(f"Retire {label!r}: use an existing editable ID.")
    for row in answer.get("replaces", ()):
        original = resolve(row["source"])
        if graph.nodes.get(original, {}).get("kind") != "promise":
            errors.append(f"Replace {row['source']!r}: use an existing promise ID.")
            continue
        for label in row["targets"]:
            replacement = resolve(label)
            if kinds.get(replacement) == "promise":
                edits.extend(dict(op="link", source=replacement, target=target)
                             for source, target in graph.links if source == original and target in kinds
                             and graph.kinds((source, target)) in PROOF)
            else:
                errors.append(f"Replacement {label!r}: use a declared promise ID.")
    allowed = LINEAGE | CONTEXT
    for op in ("unlink", "link"):
        for row in answer.get(op, ()):
            source = resolve(row["source"])
            for label in row["targets"]:
                target = resolve(label)
                pair = kinds.get(source), kinds.get(target)
                if pair in allowed or pair[::-1] in allowed:
                    edits.append(dict(op=op, source=source, target=target))
                elif op == "unlink" and (source, target) in graph.links and (source not in kinds or target not in kinds):
                    errors.append(f"Unlink {row['source']!r} from {label!r}: retiring the node already removes this link.")
                elif source != target or source not in kinds:
                    errors.append(f"{op} {row['source']!r} to {label!r}: use declared IDs of compatible kinds.")
    return edits, errors
