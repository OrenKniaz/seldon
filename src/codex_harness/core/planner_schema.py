"""Planner record shapes, input validation and read-only inspection; PlanStore owns truth."""

import ast
import hashlib
import re
from copy import deepcopy


from codex_harness.core.run_schemas import obj, TEXT, choice, array, DATA
JUDGMENT_RESPONSE_SCHEMA = obj(judgmentId=TEXT, binding=TEXT, verdict=choice("positive", "negative", "inconclusive", "unsupported"),
    rationale=TEXT, meaning=dict(type="object"), gaps=array())


def judgment_refs(spine, ids):
    missing = set(ids) - set(spine.ids())
    if missing:
        raise ValueError(
            f"Missing subject/evidence-record IDs {sorted(missing)!r}; evidence_ids uses trace result IDs, not observation digests. For candidate assessment answer the controller-bound execution question with harness_chat"
        )
    return [
        dict(id=key, revision=spine.revision(key), node=spine.node(key)) for key in sorted(set(ids))
    ]


def json_value(response):
    import json

    def reject_constant(value):
        raise ValueError(f"Use finite JSON values instead of {value}")

    return response if isinstance(response,dict) else json.loads(response,parse_constant=reject_constant)


def judgment_response(response, record):
    """Check only shape and identity. The caller's LLM supplies all semantic meaning."""
    parsed = json_value(response)
    validate_input(parsed, JUDGMENT_RESPONSE_SCHEMA, "response")
    if parsed["judgmentId"] != record["id"] or parsed["binding"] != record["binding"]:
        raise ValueError("Copy judgmentId and binding exactly from the prepared judgment")
    for ref in record["sourceRefs"]:
        if ref["kind"] == "candidate-observation":
            expected = {k: ref["payload"][k] for k in ("check", "evidenceBinding", "phase")}
            if any(parsed["meaning"].get(k) != v for k, v in expected.items()):
                raise ValueError(
                    f"Candidate assessment requires meaning {expected!r}; copy these exact identities from the prepared request"
                )
    if parsed["verdict"] == "negative" and not parsed["gaps"]:
        raise ValueError("A negative verdict needs at least one concrete gap in gaps")
    if parsed["verdict"] == "positive" and parsed["gaps"]:
        raise ValueError("Resolve the gaps or return a negative/inconclusive verdict")
    facts = next(
        (
            ref["payload"]["facts"]
            for ref in record["sourceRefs"]
            if isinstance(ref.get("payload", {}).get("facts"), dict)
        ),
        {},
    )
    if record["question"] == "finding-equivalence" and parsed["verdict"] == "positive":
        ids = {row["id"] for row in facts.get("members", [])}
        groups = parsed["meaning"].get("groups")
        if not isinstance(groups, list) or any(
            not isinstance(group, list) or not group for group in groups
        ):
            raise ValueError("meaning.groups must list nonempty groups of exact member IDs")
        flat = [key for group in groups for key in group]
        if any(not isinstance(key, str) or key not in ids for key in flat) or len(set(flat)) != len(
            flat
        ):
            raise ValueError("Equivalence groups must contain disjoint captured member IDs")
    if record["question"] == "finding-disposition" and parsed["verdict"] == "positive":
        ids = {row["id"] for row in facts.get("members", [])}
        dispositions = parsed["meaning"].get("memberDispositions")
        if (
            not isinstance(dispositions, dict)
            or set(dispositions) != ids
            or any(
                value not in {"resolved", "rejected", "deferred", "open"}
                for value in dispositions.values()
            )
        ):
            raise ValueError(
                "meaning.memberDispositions must disposition every captured member exactly once"
            )
    if record["question"] == "user-decision" and parsed["meaning"].get("decision") == "unclear":
        parsed["verdict"] = "inconclusive"
    return {key: parsed[key] for key in ("verdict", "rationale", "meaning", "gaps")}


def validate_input(value, schema: dict, label="arguments", _root=None) -> None:
    """Collect the shared schema boundary's violations, retaining their editable paths."""
    root = schema if _root is None else _root
    def violations(value, schema, label):
        if "$ref" in schema: schema = root["$defs"][schema["$ref"].rsplit("/",1)[1]]
        if "anyOf" in schema:
            branches = [b for b in schema["anyOf"] if (value is None or b.get("type") != "null") and (not isinstance(value,dict) or all(k not in value or value[k] in p["enum"] for k,p in b.get("properties",{}).items() if "enum" in p))]
            results = [violations(value,b,label) for b in branches or schema["anyOf"]]
            return min(results, key=lambda r:sum(len(m) for _,m in r))
        expected = schema["type"]
        types = {"string":isinstance(value,str), "object":isinstance(value,dict), "array":isinstance(value,list),
                 "boolean":type(value) is bool, "integer":type(value) is int, "number":type(value) in (int,float), "null":value is None}
        if not any(types[k] for k in (expected if isinstance(expected,list) else [expected])):
            return [(label,f"{label} must be {expected}")]
        errors = []
        if schema == DATA:
            try: json_value(value)
            except ValueError as error: errors.append((label,f"{label}: supply valid JSON literal data ({error})"))
        if "enum" in schema and value not in schema["enum"]: errors.append((label,f"{label} must be one of {schema['enum']}"))
        if isinstance(value,str) and (schema.get("minLength") and not value.strip() or "pattern" in schema and re.search(schema["pattern"],value) is None): errors.append((label,f"{label} must match {schema.get('pattern','nonblank text')}"))
        if isinstance(value,dict) and "properties" in schema:
            missing, extra = set(schema["required"])-value.keys(), value.keys()-schema["properties"].keys()
            errors.extend((f"{label}.{k}",f"{label}.{k}: missing required property") for k in sorted(missing))
            errors.extend((f"{label}.{k}",f"{label}.{k}: unexpected property") for k in sorted(extra))
            for k in sorted(value.keys() & schema["properties"].keys()): errors.extend(violations(value[k],schema["properties"][k],f"{label}.{k}"))
        if isinstance(value,list):
            if len(value)>schema.get("maxItems",len(value)): errors.append((label,f"{label} exceeds its supplied item count"))
            if len(value)<schema.get("minItems",0): errors.append((label,f"{label} needs at least {schema['minItems']} item(s)"))
            for item in value if "items" in schema else []: errors.extend(violations(item,schema["items"],label))
        return errors
    if errors := violations(value,schema,label):
        error = ValueError("; ".join(message for _,message in errors))
        error.locations = [path for path,_ in errors]
        raise error


def inspect_node(
    self,
    node_id: str,
    *,
    body: bool = False,
    test: str | None = None,
    include_source: bool = False,
) -> dict:
    """Inspect linked protected Python tests using AST only, never imports."""
    from codex_harness.core.plan_store import PlanStoreError

    if body and not test:
        raise ValueError("--body requires --test <name>")
    spine = self.spine()
    nodes = {node["id"]: node for node in spine.to_dict()["nodes"]}
    if node_id not in nodes:
        raise ValueError(f"Unknown node: {node_id}; use harness_nodes for available IDs")
    node = nodes[node_id]
    result = {
        **node,
        "children": spine.children(node_id),
        "testFiles": [],
    }
    if node["kind"] not in ("requirement", "test"):
        return result
    linked = {node_id, *spine.descendants(node_id)}
    modules = {nodes[key]["fields"]["module"] for key in linked if nodes[key]["kind"] == "test"}
    template = (self.workspace / "template").resolve()
    protected = set()
    from codex_harness.core.graph import test_dir

    for vision in (item for item in nodes.values() if item["kind"] == "vision"):
        fields = vision["fields"]
        for name in (
            *fields.get("requirementFiles", []),
            test_dir({"validation": fields.get("validation", [])}),
        ):
            path = (template / name).resolve()
            if not path.is_relative_to(template):
                raise ValueError(f"Protected file outside template: {name}")
            candidates = path.rglob("*.py") if path.is_dir() else [path]
            for candidate in candidates:
                if not candidate.resolve().is_relative_to(template):
                    raise ValueError(f"Protected file outside template: {candidate}")
                if candidate.suffix == ".py" and candidate.is_file():
                    protected.add(candidate)
    remaining = 8 * 1024
    source_remaining = 256 * 1024
    found = False
    for path in sorted(protected):
        relative = path.relative_to(template).as_posix()
        dotted = relative[:-3].replace("/", ".")
        if not any(
            module in (relative, path.name, dotted, path.stem) or dotted.endswith("." + module)
            for module in modules
        ):
            continue
        raw = path.read_bytes()
        source = raw.decode("utf-8-sig")
        tree = ast.parse(source, filename=relative)
        functions = []
        for item in tree.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                functions.append((item.name, item))
            elif isinstance(item, ast.ClassDef):
                functions.extend(
                    (f"{item.name}.{method.name}", method)
                    for method in item.body
                    if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
                )
        functions = [(name, item) for name, item in functions if item.name.startswith("test")]
        entry = {
            "path": relative,
            "functions": [name for name, _ in functions],
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if include_source:
            source_remaining -= len(raw)
            if source_remaining < 0:
                raise PlanStoreError("plan-test-source-too-large", "256 KiB source budget")
            entry["source"] = source
        if body:
            selected = [(name, item) for name, item in functions if test in (name, item.name)]
            if len(selected) > 1:
                raise ValueError(f"Ambiguous test {test} in {relative}; use Class.test_name")
            if selected:
                found = True
                name, item = selected[0]
                start = min([item.lineno] + [d.lineno for d in item.decorator_list])
                text = "".join(source.splitlines(keepends=True)[start - 1 : item.end_lineno])
                encoded = text.encode("utf-8")
                clipped = encoded[:remaining].decode("utf-8", errors="ignore")
                remaining -= len(clipped.encode("utf-8"))
                entry.update(
                    test=name,
                    body=clipped,
                    truncated=len(encoded) > len(clipped.encode("utf-8")),
                )
        result["testFiles"].append(entry)
    if body and not found:
        raise ValueError(f"Test not found in linked protected files: {test}")
    return result


def schema_help(tools, *, tool=None, kind=None, include_teaching=False, semantic=False):
    """Read public transport contracts; semantic schemas belong to internal calls."""
    selected = tool or "harness_chat"
    contract = next((t for t in tools if t["name"] == selected), None)
    if contract is None:
        raise ValueError("Unknown tool; use harness_chat and read-only inspection")
    return dict(tool=selected, inputSchema=deepcopy(contract["inputSchema"]),
                tools=[t["name"] for t in tools], authority=contract["description"],
                semantics="Relay actual user words. Seldon owns structured model calls and every transition.")
