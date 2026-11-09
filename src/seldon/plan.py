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
    scopes = answer.get("units", [{"requirements": requirements, "tests": tests,
        "productFiles": answer.get("productFiles", []), "testFiles": answer.get("testFiles", []), "dependencies": []}])
    for index, scope in enumerate(scopes, 1):
        edits.append({"kind": "bead", "targetHandle": f"workflow:{index}", "typedBody": {
            "ownedPaths": [*scope["testFiles"], *scope["productFiles"], *(["TRY.txt"] if index == len(scopes) else [])],
            "testPaths": scope["testFiles"], "dependencies": [f"workflow:{int(d)}" for d in scope["dependencies"]],
            "requirements": scope["requirements"], "tests": scope["tests"],
            "instructions": "Deliver these obligations and semantic checks; final delivery includes TRY.txt with reproduction instructions."}})
    edits.append({"kind": "wave", "targetHandle": "workflow:wave", "typedBody": {
        "goal": "Deliver the accepted product", "beads": [f"workflow:{i}" for i in range(1, len(scopes) + 1)]}})
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
    if edits and nodes == document["nodes"]:
        raise ValueError("Repair changes nothing; unresolved gap requires a concrete edit")
    result = {"nodes": nodes, "nextHandle": maximum + len(slots) + 1}
    validate(result)
    return result


def affected(before, after, roots=()):
    changed = {ref for ref in before["nodes"].keys() | after["nodes"].keys()
               if before["nodes"].get(ref) != after["nodes"].get(ref)}
    changed.update(roots)
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
    products = {p for b in select(plan, "bead").values() for p in b["ownedPaths"] if p not in b["testPaths"]}
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
    if set(beads) != {
            r for w in select(plan, "wave").values() for r in w["beads"]}:
        errors.append("Waves must cover all product work")
    for ref, bead in beads.items():
        if not set(bead["testPaths"]) <= set(bead["ownedPaths"]):
            errors.append("Unit test paths must be owned")
        if not set(bead["requirements"]) >= {r for t in bead["tests"] if t in tests for r in tests[t]["requirements"]}:
            errors.append("Unit checks must refer to its own obligations")
        for path in bead["ownedPaths"]:
            try:
                safe_path(path)
            except ValueError as error:
                errors.append(str(error))
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
