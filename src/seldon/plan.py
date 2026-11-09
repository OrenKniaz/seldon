"""C9/C21/I6: one typed plan, trace coverage, and canonical approval subjects."""

from pathlib import PurePosixPath
import shutil

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


def create(vision, answer):
    vision = vision_body(vision)
    edits = list(answer["edits"])
    requirements = [e["targetHandle"] for e in edits if e["kind"] == "requirement"]
    tests = [e["targetHandle"] for e in edits if e["kind"] == "test"]
    for task, paths in (("tests", answer["testFiles"]), ("build", [*answer["productFiles"], "TRY.txt"])):
        edits.append({"kind": "bead", "targetHandle": "workflow:" + task, "typedBody": {
            "task": task, "ownedPaths": paths, "dependencies": [], "requirements": requirements,
            "tests": tests, "instructions": {"tests": "Write semantic checks for the declared tests; save browser evidence in SELDON_EVIDENCE_DIR.",
                "build": "Implement the accepted requirements and quality bar. Deliver TRY.txt with usage, demonstration and reproduction instructions."}[task]}})
    edits.append({"kind": "wave", "targetHandle": "workflow:wave", "typedBody": {
        "goal": "Deliver the accepted product", "beads": ["workflow:tests", "workflow:build"]}})
    return apply({"nodes": {"vision": {"kind": "vision", "body": vision}}}, edits)


def apply(document, edits):
    """Apply one batch to a copy, then validate its complete graph before publication."""
    from copy import deepcopy
    nodes = deepcopy(document["nodes"])
    replacements = {}
    slots = list(dict.fromkeys(e["targetHandle"] for e in edits if e.get("op", "Put") == "Put"
                               and e["targetHandle"].startswith(("new:", "workflow:"))))
    maximum = document.get("nextHandle", max([int(ref[1:]) for ref in nodes if ref.startswith("n")] or [0]) + 1) - 1
    handles = {slot: f"n{maximum + i}" for i, slot in enumerate(slots, 1)}
    edits = deepcopy(edits)
    for edit in edits:
        if "replacementHandles" in edit:
            edit["replacementHandles"] = [handles.get(ref, ref) for ref in edit["replacementHandles"]]
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
        else:
            nodes[ref] = {"kind": edit["kind"], "body": deepcopy(edit["typedBody"])}
    for node in nodes.values():
        for relation, kind in LINKS.get(node["kind"], {}).items():
            links = node["body"][relation]
            node["body"][relation] = list(dict.fromkeys(
                target for ref in links
                for target in replacements.get(ref, [ref])))
    for bead in (n["body"] for n in nodes.values() if n["kind"] == "bead"):
        bead["tests"] += [r for r in handles.values() if nodes.get(r, {}).get("kind") == "test"
                          and r not in bead["tests"] and set(nodes[r]["body"]["requirements"]).intersection(bead["requirements"])]
    if edits and nodes == document["nodes"]:
        raise ValueError("Repair changes nothing; unresolved gap requires a concrete edit")
    result = {"nodes": nodes, "nextHandle": maximum + len(slots) + 1}
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
    products = {p for b in select(plan, "bead").values() if b["task"] == "build" for p in b["ownedPaths"]}
    for ref, test in select(plan, "test").items():
        executable = test["command"][0]
        if executable.replace("\\", "/").removeprefix("./") not in products and not shutil.which(executable):
            errors.append(f"{ref}.command[0] must name an installed verification tool or a declared product output: {executable!r}; put each argument in its own element.")
    features, requirements, tests, beads = (
        select(plan, k) for k in ("feature", "requirement", "test", "bead"))
    for targets, owners, relation in ((features, requirements, "features"),
                                      (requirements, tests, "requirements"),
                                      (tests, beads, "tests")):
        covered = {ref for body in owners.values() for ref in body[relation]}
        if not targets.keys() <= covered:
            errors.append(f"Uncovered trace records: {targets.keys() - covered}")
    if not any(test["stage"] == "verification" for test in tests.values()):
        errors.append("Plan requires verification commands")
    tasks = {b["task"] for b in beads.values()}
    if tasks != {"tests", "build"}:
        errors.append("Plan requires tests and build work")
    if set(beads) != {
            r for w in select(plan, "wave").values() for r in w["beads"]}:
        errors.append("Waves must cover all product work")
    paths = {}
    for ref, bead in beads.items():
        order = {"tests": 0, "build": 1}
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
    from graphlib import TopologicalSorter, CycleError
    try:
        tuple(TopologicalSorter({r: b["dependencies"] for r, b in beads.items()}).static_order())
    except CycleError:
        errors.append("Cyclic bead dependencies")
    if errors:
        raise ValueError("\n".join(dict.fromkeys(errors)))


def select(plan, kind):
    return {ref: node["body"] for ref, node in plan["nodes"].items() if node["kind"] == kind}


def subject(gate, **bindings):
    body = {"gate": gate, **bindings}
    return {**body, "digest": digest(body)}
