"""C9/C21/I6: one typed plan, trace coverage, and canonical approval subjects."""

from pathlib import PurePosixPath

from .store import digest

LINKS = {"feature": {"vision": "vision"}, "requirement": {"features": "feature"},
         "test": {"requirements": "requirement"},
         "bead": {"requirements": "requirement", "tests": "test", "dependencies": "bead"},
         "wave": {"beads": "bead"}}


def safe_path(value):
    path = PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or path.is_absolute()
            or any(part in ("..", ".git", ".seldon") for part in path.parts)
            or path.as_posix() != value):
        raise ValueError(f"Use a project-relative product file path: {value!r}")
    return value


def vision_body(answer):
    from .schema import VISION
    return {field: answer[field] for field in VISION["properties"]}


def create(vision, edits):
    vision = vision_body(vision)
    result = apply({"nodes": {"vision": {"kind": "vision", "body": vision}}}, edits)
    if result["nodes"]["vision"]["body"] != vision or len(result["nodes"]) == 1:
        raise ValueError("PLAN must deliver the accepted vision; change vision through REPAIR")
    return result


def apply(document, edits):
    """Apply one batch to a copy, then validate its complete graph before publication."""
    from copy import deepcopy
    nodes = deepcopy(document["nodes"])
    replacements = {}
    slots = list(dict.fromkeys(e["targetHandle"] for e in edits if e.get("op", "Put") == "Put"
                               and e["targetHandle"].startswith("new:")))
    maximum = document.get("nextHandle", max([int(ref[1:]) for ref in nodes if ref.startswith("n")] or [0]) + 1) - 1
    handles = {slot: f"n{maximum + i}" for i, slot in enumerate(slots, 1)}
    edits = deepcopy(edits)
    for edit in edits:
        for field in ("replacementHandles", "targetHandles"):
            if field in edit:
                edit[field] = [handles.get(ref, ref) for ref in edit[field]]
        if "typedBody" in edit:
            for relation in LINKS.get(edit["kind"], {}):
                edit["typedBody"][relation] = [handles.get(ref, ref) for ref in edit["typedBody"][relation]]
        ref = handles.get(edit["targetHandle"], edit["targetHandle"])
        operation = edit.get("op", "Put")
        if operation == "Retire":
            if ref == "vision":
                raise ValueError("Vision cannot be retired")
            del nodes[ref]
            replacements[ref] = edit["replacementHandles"]
        elif operation == "ReplaceLinks":
            node = nodes[ref]
            if edit["relation"] not in LINKS.get(node["kind"], {}):
                raise ValueError("Relation does not belong to this record")
            node["body"][edit["relation"]] = edit["targetHandles"]
        else:
            nodes[ref] = {"kind": edit["kind"], "body": deepcopy(edit["typedBody"])}
    for node in nodes.values():
        for relation in LINKS.get(node["kind"], {}):
            node["body"][relation] = list(dict.fromkeys(
                target for ref in node["body"][relation]
                for target in replacements.get(ref, [ref])))
    result = {"nodes": nodes, "nextHandle": maximum + len(slots) + 1}
    if nodes == document["nodes"]:
        raise ValueError("Repair changes nothing; unresolved gap requires a concrete edit")
    validate(result)
    return result


def affected(before, after):
    changed = {ref for ref in before["nodes"].keys() | after["nodes"].keys()
               if before["nodes"].get(ref) != after["nodes"].get(ref)}
    while True:
        downstream = {ref for ref, node in after["nodes"].items()
                      if any(changed.intersection(node["body"][relation])
                             for relation in LINKS.get(node["kind"], {}))}
        if downstream <= changed:
            return changed
        changed |= downstream


def validate(plan):
    nodes = plan["nodes"]
    errors = []
    if nodes.get("vision", {}).get("kind") != "vision" or any(
            ref != "vision" and node["kind"] == "vision" for ref, node in nodes.items()):
        errors.append("Exactly one CODE-owned vision handle is required")
    from .schema import BODIES, VISION, validation_errors
    for ref, node in nodes.items():
        errors.extend(validation_errors(node["body"], VISION if node["kind"] == "vision" else BODIES[node["kind"]], ref))
    if errors:
        raise ValueError("\n".join(errors))
    if set(nodes) == {"vision"}:
        return
    for node in nodes.values():
        for relation, target_kind in LINKS.get(node["kind"], {}).items():
            if any(ref not in nodes or nodes[ref]["kind"] != target_kind for ref in node["body"][relation]):
                errors.append(f"{relation}: wrong trace target kind")
    for kind in ("feature", "requirement", "test", "bead", "wave"):
        if not select(plan, kind):
            errors.append(f"Plan needs {kind} records")
    features, requirements, tests, beads = (
        select(plan, k) for k in ("feature", "requirement", "test", "bead"))
    for targets, owners, relation in ((features, requirements, "features"),
                                      (requirements, tests, "requirements"),
                                      (tests, beads, "tests")):
        covered = {ref for body in owners.values() for ref in body[relation]}
        if not targets.keys() <= covered:
            errors.append(f"Uncovered trace records: {targets.keys() - covered}")
    if {test["stage"] for test in tests.values()} != {"verification", "walkthrough"}:
        errors.append("Plan requires verification and walkthrough commands")
    tasks = {b["task"] for b in beads.values()}
    if tasks != {"tests", "build", "guide"}:
        errors.append("Plan requires tests, build and guide work")
    for wave in select(plan, "wave").values():
        if any(beads[ref]["task"] == "guide" for ref in wave["beads"] if ref in beads):
            errors.append("Waves contain product work; CODE schedules guide work after check-in")
    if {r for r, b in beads.items() if b["task"] != "guide"} != {
            r for w in select(plan, "wave").values() for r in w["beads"]}:
        errors.append("Waves must cover all product work")
    paths = {}
    for ref, bead in beads.items():
        order = {"tests": 0, "build": 1, "guide": 2}
        if any(order[beads[dep]["task"]] > order[bead["task"]] for dep in bead["dependencies"] if dep in beads):
            errors.append("Product dependencies cannot reverse CODE-owned task ordering")
        for path in bead["ownedPaths"]:
            try:
                safe_path(path)
            except ValueError as error:
                errors.append(str(error))
            if path in paths:
                errors.append(f"Two writers own {path}")
            paths[path] = ref
    def visit(ref, active, done):
        if ref in active:
            errors.append("Cyclic bead dependencies")
            return
        if ref not in done:
            for dep in beads[ref]["dependencies"]:
                if dep in beads:
                    visit(dep, active | {ref}, done)
            done.add(ref)
    done = set()
    for ref in beads:
        visit(ref, set(), done)
    if errors:
        raise ValueError("\n".join(dict.fromkeys(errors)))
    return plan


def select(plan, kind):
    return {ref: node["body"] for ref, node in plan["nodes"].items() if node["kind"] == kind}


def subject(gate, **bindings):
    body = {"gate": gate, **bindings}
    return {**body, "digest": digest(body)}


# C24 extension: baseline records map existing behavior before change planning.
