"""C9/C12/I6/I7; R6/R14: scoped unit ownership and CODE-assigned check coverage."""

import fnmatch
from graphlib import TopologicalSorter
from pathlib import PurePosixPath

from .schema import BODIES, PLAN, VISION, validate as validate_schema

GUIDE_PATH = "docs/try-it.md"
GUIDE_WORK = ("Write the try-it guide for the integrated product. Give exact project-relative steps "
              "from the delivered product folder, the expected results and the user's real interaction.")


def safe_path(value):
    path = PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or path.is_absolute()
            or path.as_posix() != value
            or any(p in ("..", ".git", ".seldon") or p.endswith((".", " ")) for p in path.parts)):
        raise ValueError(f"Use a project-relative path or pattern: {value!r}")
    return value


def matches(path, patterns):
    # ** spans directories; a directory's /** includes its complete subtree.
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def prefix(pattern):
    index = next((i for i, char in enumerate(pattern) if char in "*?["), len(pattern))
    return pattern[:index]


def contains(outer, inner):
    return outer == inner or (outer.endswith("/**") and inner.startswith(outer[:-2]))


def overlaps(left, right):
    # Conservative for general glob intersections: never admit potentially shared paths.
    def intersect(a, b):
        if prefix(a) == a:
            return matches(a, [b])
        if prefix(b) == b:
            return matches(b, [a])
        return prefix(a).startswith(prefix(b)) or prefix(b).startswith(prefix(a))
    return any(intersect(a, b) for a in left for b in right)


def select(document, kind):
    return {ref: n["body"] for ref, n in document["nodes"].items() if n["kind"] == kind}


def assigned(document):
    owners = {r: u for r, u in select(document, "unit").items()}
    result = {r: {} for r in owners}
    result[None] = {}
    for ref, check in select(document, "check").items():
        local = [r for r, unit in owners.items()
                 if set(check["requirements"]) <= set(unit["requirements"])]
        result[local[0] if local else None][ref] = check
    return result


def create(vision, answer):
    validate_schema({k: v for k, v in answer.items() if k != "why"}, PLAN)
    vision = {field: vision[field] for field in VISION["properties"]}
    nodes = {"vision": {"kind": "vision", "body": vision}}
    handles = {e["targetHandle"]: f"n{i:03}" for i, e in enumerate(answer["edits"], 1)}
    if len(handles) != len(answer["edits"]):
        raise ValueError("Each planned record needs its own handle")
    relations = {"feature": "vision", "requirement": "features", "check": "requirements"}
    for edit in answer["edits"]:
        body = dict(edit["typedBody"])
        relation = relations[edit["kind"]]
        body[relation] = ["vision"] if relation == "vision" else [handles.get(r, r) for r in body[relation]]
        nodes[handles[edit["targetHandle"]]] = {"kind": edit["kind"], "body": body}
    for i, unit in enumerate(answer["units"], 1):
        nodes[f"u{i:03}"] = {"kind": "unit", "body": {**unit,
            "requirements": [handles.get(r, r) for r in unit["requirements"]],
            "dependencies": [f"u{d:03}" for d in unit["dependencies"]]}}
    document = {"nodes": nodes}
    quality = vision["quality"]
    if sorted(q["criterion"] for q in answer["quality"]) != list(range(1, len(quality) + 1)):
        raise ValueError("Trace every accepted quality criterion exactly once")
    for item in answer["quality"]:
        index = item["criterion"]
        nodes[f"quality:{index:03}"] = {"kind": "criterion", "body": {
            **quality[index - 1], "judgment": item["judgment"],
            "checks": [handles.get(r, r) for r in item["checks"]]}}
    validate(document)
    return document


def validate(document):
    nodes = document["nodes"]
    validate_schema(nodes["vision"]["body"], VISION)
    relation_types = {"feature": ("vision", "vision"), "requirement": ("features", "feature"),
                      "check": ("requirements", "requirement"), "unit": ("requirements", "requirement")}
    for ref, node in nodes.items():
        if ref == "vision":
            continue
        kind, body = node["kind"], node["body"]
        if kind == "criterion":
            if not body["checks"] or any(nodes.get(c, {}).get("kind") != "check" for c in body["checks"]):
                raise ValueError(f"{ref}: quality must trace to existing checks")
            continue
        validate_schema(body, BODIES[kind], ref)
        relation, target_kind = relation_types[kind]
        if any(nodes.get(r, {}).get("kind") != target_kind for r in body[relation]):
            raise ValueError(f"{ref}.{relation}: wrong trace target kind")
    features, requirements, checks, units = (
        select(document, k) for k in ("feature", "requirement", "check", "unit"))
    if not all((features, requirements, checks, units)):
        raise ValueError("Plan requires features, requirements, checks and units")
    by_unit = assigned(document)
    uncovered = []
    for ref in requirements:
        owners = [u for u, body in units.items() if ref in body["requirements"]]
        if len(owners) != 1:
            raise ValueError(f"{ref}: needs exactly one unit owner, found {owners}")
        if not any(ref in c["requirements"] for c in by_unit[owners[0]].values()):
            uncovered.append(requirements[ref]["obligation"])
    if uncovered:
        raise ValueError(f"Unit judgment needs a local semantic check for {sorted(uncovered)}. "
                         "A check spanning owners runs only after the units integrate.")
    if set(features) - {f for r in requirements.values() for f in r["features"]}:
        raise ValueError("Every feature needs a requirement")
    for ref, unit in units.items():
        for pattern in [*unit["scope"], *unit["checkPaths"]]:
            safe_path(pattern)
        if not all(any(contains(s, c) for s in unit["scope"]) for c in unit["checkPaths"]):
            raise ValueError(f"{ref}: checkPaths must be inside scope")
        if any(d not in units or d == ref for d in unit["dependencies"]):
            raise ValueError(f"{ref}: unknown or self dependency")
        if overlaps(unit["scope"], [GUIDE_PATH]):
            raise ValueError(f"{ref}: {GUIDE_PATH} belongs to the CODE-created last unit")
        for other, owner in units.items():
            if ref < other and overlaps(unit["scope"], owner["scope"]):
                raise ValueError(f"Scope ownership overlaps: {ref} {unit['scope']} and "
                                 f"{other} {owner['scope']}; assign disjoint subtrees or literal paths")
    tuple(TopologicalSorter({r: b["dependencies"] for r, b in units.items()}).static_order())


def add_guide(document):
    # Scheduling creates the unit; its worker writes the user-facing instructions.
    from copy import deepcopy
    result = deepcopy(document)
    result["nodes"]["guide"] = {"kind": "unit", "body": {
        "scope": [GUIDE_PATH], "checkPaths": [GUIDE_PATH], "requirements": [],
        "dependencies": list(select(document, "unit")),
        "instructions": GUIDE_WORK}}
    return result
