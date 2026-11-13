"""C9/C12/I6/I7; R6/R14: scoped unit ownership and CODE-assigned check coverage."""

import fnmatch
from graphlib import CycleError, TopologicalSorter
from pathlib import PurePosixPath

from .schema import BODIES, PLAN, VISION
from .schema import validate as validate_schema

GUIDE_PATH = "docs/try-it.md"
GUIDE_WORK = ("Write the try-it guide for the integrated product. Give exact steps, the expected results "
              "and the user's real interaction. Link every named path relative to the guide file; "
              "argv commands run from the delivered product root. Put every command, "
              "if the product has any that finish on their own, in an argv fenced block containing a "
              "JSON string array, in execution order. "
              "Use prose for expected results and manual interactions.")


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
    return outer == "**" or outer == inner or (outer.endswith("/**") and inner.startswith(outer[:-2]))


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


def traced_checks(document, subject):
    checks = select(document, "check")
    body = document["nodes"][subject]["body"]
    return ({r: checks[r] for r in body["checks"]} if "checks" in body else
            {r: c for r, c in checks.items() if subject in c["requirements"]})


def traced_units(document, checks):
    requirements = {r for c in checks.values() for r in c["requirements"]}
    arguments = [a for c in checks.values() for a in c["command"]]
    return {r: u for r, u in select(document, "unit").items()
            if requirements.intersection(u["requirements"])
            or any(matches(a, u["checkPaths"]) for a in arguments)}


def coverage(document):
    return {r: {"checks": list(checks), "units": list(traced_units(document, checks))}
            for r in {**select(document, "requirement"), **select(document, "criterion")}
            for checks in [traced_checks(document, r)]}


def create(vision, answer):
    validate_schema({k: v for k, v in answer.items() if k != "why"}, PLAN)
    vision = {field: vision[field] for field in VISION["properties"]}
    nodes = {"vision": {"kind": "vision", "body": vision}}
    handles = {e["targetHandle"]: f"n{i:03}" for i, e in enumerate(answer["edits"], 1)}
    if len(handles) != len(answer["edits"]):
        raise ValueError("Each planned record needs its own handle")
    relations = {"feature": "vision", "requirement": "features", "check": "requirements", "decision": "areas"}
    for edit in answer["edits"]:
        body = dict(edit["typedBody"])
        relation = relations.get(edit["kind"])
        if relation:
            body[relation] = (["vision"] if relation == "vision" else
                              [handles.get(r, r) for r in body[relation]])
        if edit["kind"] == "contract":
            body.update(provider=f"u{body['provider']:03}",
                        consumers=[f"u{r:03}" for r in body["consumers"]])
        if edit["kind"] == "obligation":
            body.update(owner=f"u{body['owner']:03}",
                        closingRequirement=handles.get(body["closingRequirement"], body["closingRequirement"]))
        nodes[handles[edit["targetHandle"]]] = {"kind": edit["kind"], "body": body}
    for i, unit in enumerate(answer["units"], 1):
        nodes[f"u{i:03}"] = {"kind": "unit", "body": {**unit,
            "requirements": [handles.get(r, r) for r in unit["requirements"]],
            "area": handles.get(unit["area"], unit["area"]),
            "provides": [handles.get(r, r) for r in unit["provides"]],
            "consumes": [handles.get(r, r) for r in unit["consumes"]],
            "dependencies": [f"u{d:03}" for d in unit["dependencies"]]}}
    document = {"nodes": nodes, "skeleton": [f"u{r:03}" for r in answer["skeleton"]]}
    quality = vision["quality"]
    if sorted(q["criterion"] for q in answer["quality"]) != list(range(1, len(quality) + 1)):
        raise ValueError("Trace every accepted quality criterion exactly once")
    for item in answer["quality"]:
        index = item["criterion"]
        nodes[f"quality:{index:03}"] = {"kind": "criterion", "body": {
            **quality[index - 1],
            "checks": [handles.get(r, r) for r in item["checks"]]}}
    validate(document)
    return document


def merge(vision, architecture, leaves):
    """CODE assigns global handles, unit positions, contract endpoints and skeleton membership."""
    from . import architecture as owner

    edits, units, quality = [], [], {}
    endpoints, file_dependencies, feature_handles, deferred_checks = {}, {}, {}, []
    emitted = set()
    for area, answer in leaves:
        relevant = {r: c for r, c in owner.index(architecture["contracts"]).items()
                    if any(owner.endpoint(a, c) for a in [area["handle"], *area.get("ancestors", [])])}
        # An inherited endpoint is still an explicit agreement with the parent responsibility.
        local_contracts = {r: {**c,
            "provider": area["handle"] if c["provider"] in area.get("ancestors", []) else c["provider"],
            "consumers": [area["handle"] if a in area.get("ancestors", []) else a for a in c["consumers"]]}
            for r, c in relevant.items()}
        owner.local_plan(area, local_contracts, answer, vision, len(architecture["areas"]) == 1)
        offset = len(units)
        handles = {e["targetHandle"]: f"{area['handle']}/{e['targetHandle']}" for e in answer["edits"]}
        # Equal feature records with the same proposed handle are one story across areas.
        # Different stories may freely reuse a local handle; no semantic text matching is used.
        for edit in answer["edits"]:
            if edit["kind"] == "feature":
                ref = edit["targetHandle"]
                prior = feature_handles.setdefault(ref, [])
                shared = next((h for body, h in prior if body == edit["typedBody"]), None)
                if shared:
                    handles[ref] = shared
                else:
                    prior.append((edit["typedBody"], handles[ref]))
        for edit in answer["edits"]:
            if handles[edit["targetHandle"]] in emitted:
                continue
            emitted.add(handles[edit["targetHandle"]])
            body = dict(edit["typedBody"])
            relation = {"requirement": "features", "check": "requirements"}.get(edit["kind"])
            if relation:
                refs = body[relation]
                body[relation] = [handles[r] for r in refs if r in handles]
                if edit["kind"] == "check":
                    deferred_checks.append((body, [r for r in refs if r not in handles]))
            if edit["kind"] == "obligation":
                body.update(owner=body["owner"] + offset,
                            closingRequirement=handles[body["closingRequirement"]])
            edits.append({**edit, "targetHandle": handles[edit["targetHandle"]], "typedBody": body})
        for unit in answer["units"]:
            position = len(units) + 1
            file_dependencies[position] = [d for d in unit["dependencies"] if isinstance(d, str)]
            units.append({**unit, "area": area["handle"],
                          "requirements": [handles[r] for r in unit["requirements"]],
                          "dependencies": [d + offset for d in unit["dependencies"] if isinstance(d, int)]})
            for side in ("provides", "consumes"):
                for ref in unit[side]:
                    endpoints.setdefault(ref, {"provides": [], "consumes": []})[side].append(
                        (position, {area["handle"], *area.get("ancestors", [])}))
        for item in answer["quality"]:
            quality.setdefault(item["criterion"], []).extend(handles[r] for r in item["checks"])
    if set(a["handle"] for a, _ in leaves) != set(owner.index(architecture["areas"])):
        raise ValueError("Detail every area exactly once")
    for area in architecture["areas"]:
        edits.append({"kind": "area", "targetHandle": area["handle"], "typedBody": {
            k: area[k] for k in ("responsibility", "scope")}})
    corrections = {}

    def correct(areas, error, facts):
        for area in sorted(set(areas)):
            item = {"error": error, "otherSide": facts}
            if item not in corrections.setdefault(area, []):
                corrections[area].append(item)

    for contract in architecture["contracts"]:
        bound = endpoints.get(contract["handle"], {"provides": [], "consumes": []})
        if (len(bound["provides"]) != 1 or not bound["consumes"]
                or {c for _, areas in bound["consumes"] for c in contract["consumers"] if c in areas}
                != set(contract["consumers"])):
            missing = set(contract["consumers"]) - {
                c for _, areas in bound["consumes"] for c in contract["consumers"] if c in areas}
            if len(bound["provides"]) != 1:
                missing.add(contract["provider"])
            participants = {a["handle"] for a, _ in leaves
                            if missing & {a["handle"], *a.get("ancestors", [])}}
            correct(participants,
                    f"{contract['handle']}: bind exactly one provider and every named consumer",
                    {"contract": contract["text"]})
            continue
        edits.append({"kind": "contract", "targetHandle": contract["handle"], "typedBody": {
            "kind": contract["kind"], "text": contract["text"],
            "provider": bound["provides"][0][0], "consumers": [p for p, _ in bound["consumes"]]}})
    for i, decision in enumerate(architecture["decisions"]):
        edits.append({"kind": "decision", "targetHandle": f"architecture-decision:{i}",
                      "typedBody": decision})
    providers = {e["targetHandle"]: e["typedBody"]["provider"] for e in edits if e["kind"] == "contract"}
    for criterion in set(range(1, len(vision["quality"])+1)) - quality.keys():
        correct([a["handle"] for a, _ in leaves if f"quality:{criterion}" in a["commitments"]],
                f"Trace accepted quality criterion {criterion} to semantic checks", {})
    for check, refs in deferred_checks:
        check["requirements"] = list(dict.fromkeys([*check["requirements"],
            *(r for contract in refs if contract in providers
              for r in units[providers[contract] - 1]["requirements"])]))

    def planned_paths(path, reader):
        return [{"area": writer["area"], "path": p} for i, writer in enumerate(units, 1)
                if writer["area"] == reader["area"] or any(
                    providers.get(c) == i for c in reader["consumes"])
                for p in writer["scope"] if PurePosixPath(p).name == PurePosixPath(path).name]

    area_names = {a["handle"]: {a["handle"], *a.get("ancestors", [])} for a, _ in leaves}

    for position, paths in file_dependencies.items():
        for path in paths:
            writers = [i for i, u in enumerate(units, 1) if path in u["scope"] or matches(path, u["scope"])]
            if len(writers) != 1 or writers[0] == position:
                correct([units[position-1]["area"]],
                        f"{path}: a file prerequisite needs one other planned writer",
                        {"plannedPaths": planned_paths(path, units[position-1])})
                continue
            deps = units[position - 1]["dependencies"]
            if writers[0] not in deps:
                deps.append(writers[0])
    for position, unit in enumerate(units, 1):
        for other, writer in enumerate(units, 1):
            if (position < other and unit["area"] != writer["area"]
                    and overlaps(unit["scope"], writer["scope"])):
                correct([unit["area"], writer["area"]], "Scope ownership overlaps; assign disjoint scopes",
                        {unit["area"]: unit["scope"], writer["area"]: writer["scope"]})
        for path in unit["reads"]:
            owners = [(i, u) for i, u in enumerate(units, 1)
                      if path in u["scope"] or matches(path, u["scope"])]
            if len(owners) != 1:
                correct([unit["area"]], f"{path}: needs one planned file owner",
                        {"plannedPaths": planned_paths(path, unit)})
            else:
                other, writer = owners[0]
                agreements = [c for c in architecture["contracts"] if (
                        c["provider"] in area_names[writer["area"]]
                        and set(c["consumers"]) & area_names[unit["area"]])]
                if writer["area"] != unit["area"] and other not in unit["dependencies"]:
                    if not agreements:
                        raise owner.ContractGap(
                            f"{unit['area']} reading {path} needs a contract with {writer['area']}",
                            [unit["area"], writer["area"]])
                    correct([unit["area"]], f"Reading {path} needs an explicit dependency edge to its writer",
                            {"area": writer["area"], "plannedPath": path})
    dependencies = {i: u["dependencies"] for i, u in enumerate(units, 1)}
    try:
        tuple(TopologicalSorter(dependencies).static_order())
    except CycleError as error:
        cycle = error.args[1]
        for area in {units[i-1]["area"] for i in cycle}:
            local = [i for i, u in enumerate(units, 1) if u["area"] == area]
            correct([area], "Explicit dependency cycle; split work or move integration checks "
                           "after integration",
                    {"edges": [{"unit": local.index(i)+1, "needs": [
                        {"area": units[d-1]["area"], "scope": units[d-1]["scope"]}
                        for d in dependencies[i] if d in cycle]}
                        for i in local if i in cycle]})
    if corrections:
        raise owner.DetailCorrection(corrections)
    document = create(vision, {"edits": edits, "units": units,
        "skeleton": list(range(1, len(units) + 1)) if len(units) > 1 else [],
        "quality": [{"criterion": c, "checks": list(dict.fromkeys(checks))}
                    for c, checks in quality.items()]})
    document["architecture"] = architecture
    return document


def validate_records(document, external=()):
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
        if kind in relation_types:
            relation, target_kind = relation_types[kind]
            if any(nodes.get(r, {}).get("kind") != target_kind
                   and not (kind == "check" and r in external) for r in body[relation]):
                raise ValueError(f"{ref}.{relation}: wrong trace target kind")


def validate_owned_work(document):
    """The same area-owned rules serve DETAIL exact-fix and canonical plan validation."""
    features, requirements, checks, units = (
        select(document, k) for k in ("feature", "requirement", "check", "unit"))
    if not all((features, requirements, checks, units)):
        raise ValueError("Plan requires features, requirements, checks and units")
    for ref, unit in units.items():
        for pattern in [*unit["scope"], *unit["checkPaths"]]:
            safe_path(pattern)
        if not all(any(contains(s, c) for s in unit["scope"]) for c in unit["checkPaths"]):
            raise ValueError(f"{ref}: checkPaths must be inside scope")
        if any(d not in units or d == ref for d in unit["dependencies"]):
            raise ValueError(f"{ref}: unknown or self dependency")
        if ref != "guide" and overlaps(unit["scope"], [GUIDE_PATH]):
            raise ValueError(f"{ref}: {GUIDE_PATH} belongs to the CODE-created last unit")
        for other, owner in units.items():
            if (ref < other and unit["area"] == owner["area"]
                    and overlaps(unit["scope"], owner["scope"])):
                raise ValueError(f"Scope ownership overlaps: {ref} {unit['scope']} and "
                                 f"{other} {owner['scope']}; assign disjoint subtrees or literal paths")

    by_unit = assigned(document)
    uncovered = []
    for ref in requirements:
        owners = [u for u, body in units.items() if ref in body["requirements"]]
        if len(owners) != 1:
            raise ValueError(f"{ref}: needs exactly one unit owner, found {owners}")
        unit = units[owners[0]]
        if not any(ref in c["requirements"]
                   and any(matches(a, unit["checkPaths"]) for a in c["command"])
                   for c in by_unit[owners[0]].values()):
            uncovered.append(requirements[ref]["obligation"])
    if uncovered:
        raise ValueError(f"Unit judgment needs a local semantic check for {sorted(uncovered)}. "
                         "A check spanning owners runs only after the units integrate.")
    if set(features) - {f for r in requirements.values() for f in r["features"]}:
        raise ValueError("Every feature needs a requirement")


def validate(document):
    validate_records(document)
    validate_owned_work(document)
    units = select(document, "unit")
    for ref, unit in units.items():
        for other, owner in units.items():
            if (ref < other and unit["area"] != owner["area"]
                    and overlaps(unit["scope"], owner["scope"])):
                raise ValueError(f"Scope ownership overlaps: {ref} and {other}")
    from . import architecture, obligations
    architecture.validate(document)
    obligations.validate(document)
    tuple(TopologicalSorter({r: prerequisites(document, r) for r in units}).static_order())
    return coverage(document)


def prerequisites(document, ref):
    return select(document, "unit")[ref]["dependencies"]


def add_guide(document):
    # Scheduling creates the unit; its worker writes the user-facing instructions.
    from copy import deepcopy
    result = deepcopy(document)
    area = next(iter(select(result, "area")))
    result["nodes"][area]["body"]["scope"].append(GUIDE_PATH)
    result["nodes"]["guide"] = {"kind": "unit", "body": {
        "scope": [GUIDE_PATH], "checkPaths": [GUIDE_PATH], "requirements": [],
        "area": area, "provides": [], "consumes": [], "reads": [],
        "dependencies": list(select(document, "unit")),
        "instructions": GUIDE_WORK}}
    return result
