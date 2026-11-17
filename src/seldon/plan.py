"""C9/C12/I6/I7; R6/R14: scoped unit ownership and CODE-assigned check coverage."""

import fnmatch
from graphlib import TopologicalSorter
from pathlib import PurePosixPath

from .schema import BODIES, VISION
from .schema import validate as validate_schema

GUIDE_PATH = "docs/try-it.md"
GUIDE_WORK = ("The page is absent from your worktree and no contract names its fields. "
              "Write docs/try-it.md and declare a docs/try-it/ WAVE-VERIFY check from contracts: start with "
              "temporary data, use the product as this wave's commitments promise, check the promised text, "
              "save a screenshot to SELDON_EVIDENCE_DIR, and stop what you started; leave unseen forms to the "
              "page owner's check. Walk through only this wave's commitments; a later wave extends the guide.")


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
        row = store.unit(ref)
        declaration = ({} if row and row.state == "DROPPED" else latest).get(
            ref, {"checkPaths": [], "checks": []})
        unit["handed"] = {d["owner"]: d["correction"] for d in declaration.get("discoveries", [])
                          if d["owner"] != ref}
        unit["checkPaths"] = [p for p in declaration["checkPaths"] if matches(p, unit["scope"])]
        for i, check in enumerate(declaration["checks"], 1):
            document["nodes"][f"{ref}:check:{i}"] = {"kind": "check", "body": {
                **check, "owner": ref, "requirements": unit["requirements"]}}
    return document


def acceptance(document, ref):
    requirements = select(document, "unit")[ref]["requirements"]
    last = max((int(r.split(".")[0]) for r in requirements), default=0)
    return [r for r in requirements if r.startswith(f"{last}.")]


def left_out(store):
    document = execution(store)
    return {ref: {"requirements": {r: document["nodes"][r]["body"]["acceptance"]
                                  for r in unit["requirements"]},
                  "commitments": next(a["commitments"] for a in document["architecture"]["areas"]
                                      if a["handle"] == unit["area"]),
                  "reason": "left out by the user"}
            for ref, unit in select(document, "unit").items()
            if store.unit(ref) and store.unit(ref).state == "DROPPED"}


def assigned(document, *, local=False):
    result = {r: {} for r in select(document, "unit")}
    for ref, check in select(document, "check").items():
        if not local or not (check["owner"] == "guide"
                            or select(document, "unit")[check["owner"]]["handed"]
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


def merge(vision, architecture, leaves, previous=None, wave=1):
    """Accumulate one wave; a scope keeps its owner and its earlier requirements."""
    from copy import deepcopy
    from . import architecture as owner

    document = deepcopy(previous) if previous else {"nodes": {}}
    nodes = document["nodes"]
    nodes["vision"] = {"kind": "vision", "body": {k: vision[k] for k in VISION["properties"]}}
    units = select(document, "unit")
    endpoints = {r: {"provides": [c["provider"]], "consumes": [u for u in c["consumers"] if u != "guide"]}
                 for r, c in select(document, "contract").items()}
    feature_handles = {}
    for area, answer in leaves:
        local_contracts = {r: {**c,
            "provider": area["handle"] if c["provider"] in area.get("ancestors", []) else c["provider"],
            "consumers": [area["handle"] if a in area.get("ancestors", []) else a for a in c["consumers"]]}
            for r, c in owner.index(architecture["contracts"]).items()}
        owner.local_plan(area, local_contracts, answer, vision)
        handles = {e["targetHandle"]: f"{wave}.{area['handle']}.{e['kind']}{i}"
                   for i, e in enumerate(answer["edits"], 1)}
        for edit in answer["edits"]:
            if edit["kind"] == "feature":
                key = edit["targetHandle"]
                prior = feature_handles.setdefault(key, [])
                shared = next((h for body, h in prior if body == edit["typedBody"]), None)
                if shared:
                    handles[key] = shared
                else:
                    prior.append((edit["typedBody"], handles[key]))
        for edit in answer["edits"]:
            body = deepcopy(edit["typedBody"])
            if edit["kind"] == "feature":
                body["vision"] = ["vision"]
            else:
                body["features"] = [handles[r] for r in body["features"]]
            ref = handles[edit["targetHandle"]]
            # A re-detail in this wave replaces its own records, never a finished wave's.
            nodes[ref] = {"kind": edit["kind"], "body": body}
        local_units = {}
        for i, unit in enumerate(answer["units"], 1):
            ref = next((r for r, u in units.items() if set(u["scope"]) == set(unit["scope"])), None)
            if ref is None:
                number = 1
                while (ref := f"{wave}.{area['handle']}.{number}") in units or ref in local_units.values():
                    number += 1
            local_units[i] = ref
        for i, unit in enumerate(answer["units"], 1):
            ref = local_units[i]
            before = units.get(ref, {"requirements": [], "dependencies": []})
            body = {k: deepcopy(v) for k, v in unit.items() if k != "provides"}
            body.update(area=area["handle"],
                        requirements=list(dict.fromkeys([*before["requirements"],
                                                         *(handles[r] for r in unit["requirements"])])),
                        dependencies=list(dict.fromkeys([*before["dependencies"],
                                                         *(local_units[d] for d in unit["dependencies"])])))
            nodes[ref] = {"kind": "unit", "body": body}
            units[ref] = body
            for handle, contract in local_contracts.items():
                bound = endpoints.setdefault(handle, {"provides": [], "consumes": []})
                if area["handle"] in contract["consumers"] and ref not in bound["consumes"]:
                    bound["consumes"].append(ref)
                if (contract["provider"] == area["handle"]
                        and (len(answer["units"]) == 1 or handle in unit.get("provides", []))
                        and ref not in bound["provides"]):
                    bound["provides"].append(ref)
    for area in architecture["areas"]:
        ref = area["handle"]
        nodes[ref] = {"kind": "area", "body": {
            "responsibility": area["responsibility"], "scope": list(area["scope"])}}
        if "guide" in units and units["guide"]["area"] == ref:
            nodes[ref]["body"]["scope"].extend(units["guide"]["scope"])
        for i, criterion in enumerate(vision["quality"], 1):
            if f"quality:{i}" in area["commitments"]:
                nodes[f"quality:{i:03}:{ref}"] = {
                    "kind": "criterion", "body": {**criterion, "area": ref}}
    corrections = {}
    for contract in architecture["contracts"]:
        bound = endpoints.get(contract["handle"], {"provides": [], "consumes": []})
        if not bound["provides"] and not any(
                contract["provider"] in {a["handle"], *a.get("ancestors", [])} for a, _ in leaves):
            continue  # A later agreement has no implementing unit yet.
        if len(bound["provides"]) != 1:
            for area, _ in leaves:
                if contract["provider"] in {area["handle"], *area.get("ancestors", [])}:
                    corrections.setdefault(area["handle"], []).append({
                        "error": f"{contract['handle']}: bind exactly one provider",
                        "otherSide": {"contract": contract["text"]}})
            continue
        nodes[contract["handle"]] = {"kind": "contract", "body": {
            "kind": contract["kind"], "text": contract["text"],
            "provider": bound["provides"][0], "consumers": bound["consumes"]}}
    for i, decision in enumerate(architecture["decisions"]):
        nodes[f"architecture-decision:{i}"] = {"kind": "decision", "body": deepcopy(decision)}
    for ref, unit in units.items():
        for other, writer in units.items():
            if ref < other and overlaps(unit["scope"], writer["scope"]):
                for area in {unit["area"], writer["area"]}:
                    corrections.setdefault(area, []).append({
                        "error": "Scope ownership overlaps; assign disjoint scopes",
                        "otherSide": {unit["area"]: unit["scope"], writer["area"]: writer["scope"]}})
    if corrections:
        raise owner.DetailCorrection(corrections)
    document["architecture"] = deepcopy(architecture)
    validate(document)
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
            raise ValueError(f"{ref}: {GUIDE_PATH} belongs to the guide unit")
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


def add_guide(document, wave=1):
    from copy import deepcopy
    result = deepcopy(document)
    area = next(iter(select(result, "area")))
    result["nodes"][area]["body"]["scope"].extend([GUIDE_PATH, "docs/try-it/**"])
    current = {r for r, u in select(result, "unit").items()
               if any(q.startswith(f"{wave}.") for q in u["requirements"])}
    readers = {c["handle"]: c["consumers"] for c in document["architecture"]["contracts"]}
    for handle, contract in select(result, "contract").items():
        contract["consumers"] = [u for u in contract["consumers"] if u != "guide"]
        # An agreement whose consumer areas have no unit yet belongs to a later wave's walkthrough.
        later = readers.get(handle) and not contract["consumers"]
        if current.intersection([contract["provider"], *contract["consumers"]]) and not later:
            contract["consumers"].append("guide")
    result["nodes"]["guide"] = {"kind": "unit", "body": {
        "scope": [GUIDE_PATH, "docs/try-it/**"], "requirements": [],
        "area": area, "dependencies": [],
        "instructions": GUIDE_WORK + "\nThis wave: " + document["architecture"]["waves"][wave - 1]["goal"]}}
    return result


def built(store):
    """Facts from the finished wave, without turning worker reports into verdicts."""
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    units = select(document, "unit")
    finished = max((row.wave for row in store.units().values()), default=cursor.wave)
    rows = {}
    for ref, row in store.units().items():
        if row.wave != finished:
            continue
        answer = store.read(row.report)["answer"] if row.report else {}
        rows[ref] = {"scope": units[ref]["scope"], "summary": answer.get("summary", ""),
                     "discoveries": answer.get("discoveries", []),
                     "open gaps": [b for _, _, b in store.records("open_gap") if b["unit"] == ref]}
    words = store.read(cursor.checkin)["words"] if cursor.checkin else None
    if isinstance(words, str):
        words = store.read(words)["words"]
    return {"Previous architecture": document["architecture"], "Finished wave": rows,
            "User check-in": words, "Remaining waves": document["architecture"]["waves"][finished:]}


def approved(store):
    cursor = store.cursor()
    subject = store.read(cursor.B)["subject"]
    vision = subject["plan"]["nodes"]["vision"]["body"]
    return vision == {k: v for k, v in store.read(cursor.vision)["answer"].items() if k != "why"}
