"""Planner record shapes, input validation and read-only inspection; PlanStore owns truth."""

import ast
import hashlib
from copy import deepcopy
from typing import Literal, NotRequired, TypedDict


class Judgment(TypedDict):
    """One event-backed semantic record; evidence state and human authority are separate."""

    id: str
    question: str
    subjectRefs: list[dict]
    evidenceRefs: list[dict]
    sourceRefs: list[dict]
    dependencyRevisions: NotRequired[dict[str, str]]
    candidate: str | None
    binding: str
    status: Literal["pending-judgment", "judged"]
    verdict: Literal["positive", "negative", "inconclusive", "unsupported"] | None
    rationale: str | None
    meaning: dict
    gaps: list[str]
    fixesIssued: int
    disposition: Literal["judge", "exact-fix", "steer", "complete"]
    owner: None
    source: str | None
    sourceVerified: bool
    supersededBy: NotRequired[str]
    error: str | None


JUDGMENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "judgmentId": {"type": "string", "minLength": 1},
        "binding": {"type": "string", "minLength": 1},
        "verdict": {
            "type": "string",
            "enum": ["positive", "negative", "inconclusive", "unsupported"],
        },
        "rationale": {"type": "string", "minLength": 1},
        "meaning": {"type": "object"},
        "gaps": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "required": ["judgmentId", "binding", "verdict", "rationale", "meaning", "gaps"],
    "additionalProperties": False,
}


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
    _root = schema if _root is None else _root
    if "$ref" in schema:
        schema = _root["$defs"][schema["$ref"].rsplit("/", 1)[1]]
    if value is None and any(branch.get("type") == "null" for branch in schema.get("anyOf", [])):
        return
    if "anyOf" in schema:
        failures = []
        for branch in schema["anyOf"]:
            try:
                validate_input(value, branch, label, _root)
                return
            except ValueError as error:
                failures.append(str(error))
        raise ValueError(min(failures, key=len))
    expected = schema["type"]
    types = {
        "string": isinstance(value, str),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "null": value is None,
    }
    allowed = expected if isinstance(expected, list) else [expected]
    if not any(types[kind] for kind in allowed):
        raise ValueError(
            f"{label} must be {expected}; allowed keys {sorted(schema.get('properties', {}))}"
        )
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{label} must be one of {schema['enum']}")
    if isinstance(value, str) and schema.get("minLength") and not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    if isinstance(value, dict) and "properties" in schema:
        missing = set(schema["required"]) - value.keys()
        extra = value.keys() - schema["properties"].keys()
        if missing or extra:
            raise ValueError(
                f"{label}: missing {sorted(missing)}, unexpected {sorted(extra)}; allowed keys {sorted(schema['properties'])}"
            )
        for key, item in value.items():
            validate_input(item, schema["properties"][key], f"{label}.{key}", _root)
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", len(value)):
            raise ValueError(f"{label} exceeds its supplied item count")
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{label} needs at least {schema['minItems']} item(s)")
        for item in value:
            validate_input(item, schema["items"], label, _root)


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
    nodes = {node["id"]: node for node in self.list_nodes()}
    if node_id not in nodes:
        raise ValueError(f"Unknown node: {node_id}; use harness_nodes for available IDs")
    node = nodes[node_id]
    result = {
        **node,
        "children": sorted(key for key, child in nodes.items() if node_id in child["parents"]),
        "testFiles": [],
    }
    if node["kind"] not in ("requirement", "test"):
        return result
    linked = {node_id}
    while True:
        descendants = linked | {
            key for key, child in nodes.items() if linked.intersection(child["parents"])
        }
        if descendants == linked:
            break
        linked = descendants
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
