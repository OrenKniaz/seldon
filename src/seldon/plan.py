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


def create(vision, edits):
    """Resolve temporary handles and validate the whole initial graph before saving."""
    handles = {edit["targetHandle"]: f"n{i}" for i, edit in enumerate(edits, 1)}
    if len(handles) != len(edits) or "vision" in handles:
        raise ValueError("Plan handles must be unique; vision is supplied by CODE")
    handles["vision"] = "vision"
    nodes = {"vision": {"kind": "vision", "body": vision}}
    for edit in edits:
        kind, body = edit["kind"], dict(edit["typedBody"])
        for relation in LINKS[kind]:
            body[relation] = [handles[ref] for ref in body[relation]]
        nodes[handles[edit["targetHandle"]]] = {"kind": kind, "body": body}
    for node in nodes.values():
        for relation, target_kind in LINKS.get(node["kind"], {}).items():
            if any(nodes[ref]["kind"] != target_kind for ref in node["body"][relation]):
                raise ValueError(f"{relation}: wrong trace target kind")
    plan = {"nodes": nodes}
    for kind in ("feature", "requirement", "test", "bead", "wave"):
        if not select(plan, kind):
            raise ValueError(f"Plan needs {kind} records")
    features, requirements, tests, beads = (
        select(plan, k) for k in ("feature", "requirement", "test", "bead"))
    for targets, owners, relation in ((features, requirements, "features"),
                                      (requirements, tests, "requirements"),
                                      (tests, beads, "tests")):
        covered = {ref for body in owners.values() for ref in body[relation]}
        if not targets.keys() <= covered:
            raise ValueError(f"Uncovered trace records: {targets.keys() - covered}")
    paths = {}
    for ref, bead in beads.items():
        for path in bead["ownedPaths"]:
            safe_path(path)
            if path in paths:
                raise ValueError(f"Two writers own {path}")
            paths[path] = ref
    def visit(ref, active, done):
        if ref in active:
            raise ValueError("Cyclic bead dependencies")
        if ref not in done:
            for dep in beads[ref]["dependencies"]:
                visit(dep, active | {ref}, done)
            done.add(ref)
    done = set()
    for ref in beads:
        visit(ref, set(), done)
    return plan


def select(plan, kind):
    return {ref: node["body"] for ref, node in plan["nodes"].items() if node["kind"] == kind}


def subject(gate, **bindings):
    body = {"gate": gate, **bindings}
    return {**body, "digest": digest(body)}


# C24 extension: baseline records map existing behavior before change planning.
