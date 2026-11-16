"""C9/C12/I6/I7; R6/R14: scoped unit ownership and CODE-assigned check coverage."""

import fnmatch
from graphlib import TopologicalSorter
from pathlib import PurePosixPath

from .schema import BODIES, PLAN, VISION
from .schema import validate as validate_schema

GUIDE_PATH = "docs/try-it.md"
GUIDE_WORK = ("Write docs/try-it.md for the reader: the shortest path from a fresh copy to using the "
              "product, with the exact commands and what they will see. The product is in your worktree: "
              "follow your own guide as that reader would (start what it starts, use the page and the API, "
              "stop it) and fix what trips you. Then leave one check under docs/try-it/ that repeats that "
              "walk on its own from temporary data, saves a screenshot of what the reader sees to "
              "SELDON_EVIDENCE_DIR, and stops what it started; declare it. Nothing in the guide is for a machine.")


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


def execution(store):
    """Worker declarations are canonical records; execution projects them onto the approved intent."""
    from copy import deepcopy
    document = deepcopy(store.plan(store.cursor().revision))
    latest = {b["unit"]: b for _, _, b in store.records("worker_checks")}
    for ref, unit in select(document, "unit").items():
        declaration = latest.get(ref, {"checkPaths": [], "checks": []})
        unit["handed"] = {d["owner"]: d["correction"] for d in declaration.get("discoveries", [])
                          if d["owner"] != ref}
        unit["checkPaths"] = [p for p in declaration["checkPaths"] if matches(p, unit["scope"])]
        for i, check in enumerate(declaration["checks"], 1):
            document["nodes"][f"{ref}:check:{i}"] = {"kind": "check", "body": {
                **check, "owner": ref, "requirements": unit["requirements"]}}
    return document


def assigned(document, *, local=False):
    result = {r: {} for r in select(document, "unit")}
    for ref, check in select(document, "check").items():
        if not local or not (select(document, "unit")[check["owner"]]["handed"]
                            or wave_requirements(document, check["owner"])):
            result[check["owner"]][ref] = check
    return result


def wave_requirements(document, ref):
    """Proof consuming a peer outside the dependency base belongs to the integrated wave."""
    units = select(document, "unit")
    available = {ref}
    while expanded := {d for u in available for d in units[u]["dependencies"]} - available:
        available.update(expanded)
    peers = {c["provider"] for c in select(document, "contract").values() if ref in c["consumers"]}
    return units[ref]["requirements"] if peers - available else []


def traced_checks(document, subject):
    checks = select(document, "check")
    body = document["nodes"][subject]["body"]
    return ({r: c for r, c in checks.items()
             if select(document, "unit")[c["owner"]]["area"] == body["area"]}
            if document["nodes"][subject]["kind"] == "criterion" else
            {r: c for r, c in checks.items() if subject in c["requirements"]})


def traced_units(document, checks):
    requirements = {r for c in checks.values() for r in c["requirements"]}
    writers = {c.get("owner") for c in checks.values()}
    return {r: u for r, u in select(document, "unit").items()
            if r in writers or requirements.intersection(u["requirements"])}


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
    relations = {"feature": "vision", "requirement": "features", "decision": "areas"}
    for edit in answer["edits"]:
        body = dict(edit["typedBody"])
        relation = relations.get(edit["kind"])
        if relation:
            body[relation] = (["vision"] if relation == "vision" else
                              [handles.get(r, r) for r in body[relation]])
        if edit["kind"] == "contract":
            body.update(provider=f"u{body['provider']:03}",
                        consumers=[f"u{r:03}" for r in body["consumers"]])
        nodes[handles[edit["targetHandle"]]] = {"kind": edit["kind"], "body": body}
    for i, unit in enumerate(answer["units"], 1):
        nodes[f"u{i:03}"] = {"kind": "unit", "body": {**{k: v for k, v in unit.items() if k != "provides"},
            "requirements": [handles.get(r, r) for r in unit["requirements"]],
            "area": handles.get(unit["area"], unit["area"]),
            "dependencies": [f"u{d:03}" for d in unit["dependencies"]]}}
    document = {"nodes": nodes}
    validate(document)
    return document


def merge(vision, architecture, leaves):
    """CODE assigns global handles, unit positions and contract endpoints."""
    from . import architecture as owner

    edits, units = [], []
    endpoints, feature_handles = {}, {}
    emitted = set()
    for area, answer in leaves:
        # An inherited endpoint is still an explicit agreement with the parent responsibility.
        local_contracts = {r: {**c,
            "provider": area["handle"] if c["provider"] in area.get("ancestors", []) else c["provider"],
            "consumers": [area["handle"] if a in area.get("ancestors", []) else a for a in c["consumers"]]}
            for r, c in owner.index(architecture["contracts"]).items()}
        owner.local_plan(area, local_contracts, answer, vision)
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
            relation = {"requirement": "features"}.get(edit["kind"])
            if relation:
                refs = body[relation]
                body[relation] = [handles.get(r, r) for r in refs if r in handles]
            edits.append({**edit, "targetHandle": handles[edit["targetHandle"]], "typedBody": body})
        for unit in answer["units"]:
            position = len(units) + 1
            # Area containment is already checked in architecture.local_plan.
            units.append({**unit, "area": area["handle"],
                          "requirements": [handles[r] for r in unit["requirements"]],
                          "dependencies": [d + offset for d in unit["dependencies"]]})
            for ref, contract in local_contracts.items():
                bound = endpoints.setdefault(ref, {"provides": [], "consumes": []})
                if area["handle"] in contract["consumers"]:
                    bound["consumes"].append(position)
                if (contract["provider"] == area["handle"]
                        and (len(answer["units"]) == 1 or ref in unit.get("provides", []))):
                    bound["provides"].append(position)
    if set(a["handle"] for a, _ in leaves) != set(owner.index(architecture["areas"])):
        raise ValueError("Detail every area exactly once")
    for area in architecture["areas"]:
        edits.append({"kind": "area", "targetHandle": area["handle"], "typedBody": {
            "responsibility": area["responsibility"], "scope": list(area["scope"])}})
    corrections = {}

    def correct(areas, error, facts):
        for area in sorted(set(areas)):
            item = {"error": error, "otherSide": facts}
            if item not in corrections.setdefault(area, []):
                corrections[area].append(item)

    for contract in architecture["contracts"]:
        bound = endpoints.get(contract["handle"], {"provides": [], "consumes": []})
        if len(bound["provides"]) != 1:
            participants = {a["handle"] for a, _ in leaves
                            if contract["provider"] in {a["handle"], *a.get("ancestors", [])}}
            correct(participants,
                    f"{contract['handle']}: bind exactly one provider",
                    {"contract": contract["text"]})
            continue
        edits.append({"kind": "contract", "targetHandle": contract["handle"], "typedBody": {
            "kind": contract["kind"], "text": contract["text"],
            "provider": bound["provides"][0], "consumers": bound["consumes"]}})
    for i, decision in enumerate(architecture["decisions"]):
        edits.append({"kind": "decision", "targetHandle": f"architecture-decision:{i}",
                      "typedBody": decision})
    for position, unit in enumerate(units, 1):
        for other, writer in enumerate(units, 1):
            if (position < other and unit["area"] != writer["area"]
                    and overlaps(unit["scope"], writer["scope"])):
                correct([unit["area"], writer["area"]], "Scope ownership overlaps; assign disjoint scopes",
                        {unit["area"]: unit["scope"], writer["area"]: writer["scope"]})
    if corrections:
        raise owner.DetailCorrection(corrections)
    document = create(vision, {"edits": edits, "units": units})
    document["architecture"] = architecture
    for ref, area in zip(select(document, "area"), architecture["areas"], strict=True):
        for index, criterion in enumerate(vision["quality"], 1):
            if f"quality:{index}" in area["commitments"]:
                document["nodes"][f"quality:{index:03}:{ref}"] = {
                    "kind": "criterion", "body": {**criterion, "area": ref}}
    return document


def validate_records(document):
    nodes = document["nodes"]
    validate_schema(nodes["vision"]["body"], VISION)
    relation_types = {"feature": ("vision", "vision"), "requirement": ("features", "feature"),
                      "unit": ("requirements", "requirement")}
    for ref, node in nodes.items():
        if ref == "vision":
            continue
        kind, body = node["kind"], node["body"]
        if kind == "criterion":
            continue
        validate_schema(body, BODIES[kind], ref)
        if kind in relation_types:
            relation, target_kind = relation_types[kind]
            if any(nodes.get(r, {}).get("kind") != target_kind
                   for r in body[relation]):
                raise ValueError(f"{ref}.{relation}: wrong trace target kind")


def validate_owned_work(document):
    """The same area-owned rules serve DETAIL exact-fix and canonical plan validation."""
    features, requirements, units = (
        select(document, k) for k in ("feature", "requirement", "unit"))
    if not all((features, requirements, units)):
        raise ValueError("Plan requires features, requirements and units")
    for ref, unit in units.items():
        for pattern in unit["scope"]:
            safe_path(pattern)
        if any(d not in units or d == ref for d in unit["dependencies"]):
            raise ValueError(f"{ref}: unknown or self dependency")
        if ref != "guide" and overlaps(unit["scope"], [GUIDE_PATH, "docs/try-it/**"]):
            raise ValueError(f"{ref}: {GUIDE_PATH} belongs to the CODE-created last unit")
        for other, owner in units.items():
            if (ref < other and unit["area"] == owner["area"]
                    and overlaps(unit["scope"], owner["scope"])):
                raise ValueError(f"Scope ownership overlaps: {ref} {unit['scope']} and "
                                 f"{other} {owner['scope']}; assign disjoint subtrees or literal paths")

    for ref in requirements:
        owners = [u for u, body in units.items() if ref in body["requirements"]]
        if len(owners) != 1:
            raise ValueError(f"{ref}: needs exactly one unit owner, found {owners}")
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
    from . import architecture
    architecture.validate(document)
    tuple(TopologicalSorter({r: u["dependencies"] for r, u in units.items()}).static_order())
    return coverage(document)


def add_guide(document):
    from copy import deepcopy
    result = deepcopy(document)
    area = next(iter(select(result, "area")))
    result["nodes"][area]["body"]["scope"].extend([GUIDE_PATH, "docs/try-it/**"])
    consumed = [r for r, c in select(result, "contract").items()
                if select(result, "unit")[c["provider"]]["area"] != area]
    for ref in consumed:
        result["nodes"][ref]["body"]["consumers"].append("guide")
    result["nodes"]["guide"] = {"kind": "unit", "body": {
        "scope": [GUIDE_PATH, "docs/try-it/**"], "requirements": [],
        "area": area, "dependencies": list(select(result, "unit")),
        "instructions": GUIDE_WORK}}
    return result


def approved(store):
    cursor = store.cursor()
    subject = store.read(cursor.B)["subject"]
    return (subject["revision"] == cursor.revision
            and subject["plan"] == store.plan(subject["revision"])
            and subject["review"] == store.read(cursor.review))
