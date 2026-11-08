"""Canonical planning ancestry, revisions, impact and work-graph compilation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from copy import deepcopy
from pathlib import Path, PurePosixPath

from codex_harness.core.graph import PlanError, test_dir, validate_capability_refs
from codex_harness.core.semantic_schema import TRACE_LINKS, trace_refusal, validate_trace_fields
from codex_harness.core.record_io import write_stdout_json

SCHEMA = "codex-harness/spine-v1"
MARKDOWN_FILES = ("PLAN.md", "REQUIREMENTS.md", "TRACE.md", "VISION.md")
ALLOWED_PARENTS = {
    "vision": set(),
    "feature": {"vision"},
    "acceptance": {"vision", "feature"},
    "story": {"feature"},
    "requirement": {"story", "feature"},
    "decision": {"vision", "feature", "story", "requirement", "research", "discovery"},
    "architecture": {"vision", "feature", "requirement", "decision"},
    "design": {"requirement", "decision", "architecture"},
    "contract": {"requirement", "architecture", "design"},
    "bead": {"requirement", "architecture", "design", "contract", "decision", "test"},
    "test": {"requirement", "architecture", "design", "contract", "bead"},
    "code": {"feature", "requirement", "contract", "test", "bead"},
    "result": {"feature", "requirement", "test", "bead", "code", "iteration"},
    "research": {"vision", "feature", "requirement", "decision"},
    "discovery": {"vision", "feature", "requirement", "research", "test", "code", "result"},
    "amendment": {"vision", "discovery", "research", "result", "decision"},
    "metric": {"vision", "feature", "requirement", "research"},
    "stop-condition": {"vision", "metric", "decision"},
    "iteration": {"metric", "research", "bead", "code"},
    "baton": {"vision", "decision", "result", "iteration"},
}
ALLOWED_PARENTS["result"] = set(ALLOWED_PARENTS) - {"result"}


class SpineError(ValueError):
    def __init__(self, code: str, message: str, node_id=None, *sections) -> None:
        super().__init__(message)
        self.code = code
        if node_id:
            self.__dict__.update(trace_refusal(node_id, message, *sections).__dict__)


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


SEMANTIC_PHASES = ("contracts", "build")


def bead_contract(semantic=False):
    return {
        "ownedFiles": (["hello.py"], lambda v: _strings(v) and bool(v)),
        "dependsOn": ([], _strings),
        "lessons": ([], _strings),
        **(
            {"semanticPhase": (SEMANTIC_PHASES[0], lambda v: v in SEMANTIC_PHASES)}
            if semantic
            else {"proofModule": ("test_parser", lambda v: isinstance(v, str) and bool(v.strip()))}
        ),
    }


def bead_rules(semantic=False):
    return (
        "Required fields (arrays contain nonblank strings): "
        + _canonical({k: example for k, (example, _) in bead_contract(semantic).items()})
        + ". ownedFiles is nonempty; dependsOn/lessons are unique existing bead/decision IDs. "
        "Dependencies are acyclic. Provider/model choice belongs to kickoff; capabilityRefs are unique "
        "lowercase dotted identifiers, requested/unverified, never permissions. "
        + (
            "DV2 permits one build bead and an optional contracts supplier until Stage 3a."
            if semantic
            else ""
        )
    )


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def content_revision(node: dict) -> str:
    """Bind this node's content and link IDs, never another node's revision."""
    def fields(value):
        return {k: [r["id"] if isinstance(r, dict) else r for r in v]
                if k in TRACE_LINKS or k == "targetRefs" else
                v["id"] if k in {"sourceRef", "itemRef"} and isinstance(v, dict) else
                fields(v) if k in {"baseline", "evidence"} else v for k, v in value.items()}
    return _hash(_canonical(dict(node, parents=sorted(node["parents"]), fields=fields(node["fields"]))))


def _acyclic(parents: dict[str, list[str]], code: str) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {node_id: [] for node_id in parents}
    remaining = {node_id: len(values) for node_id, values in parents.items()}
    for node_id, values in parents.items():
        for parent in values:
            children[parent].append(node_id)
    ready = deque(node_id for node_id, count in remaining.items() if count == 0)
    visited = 0
    while ready:
        visited += 1
        for child in children[ready.popleft()]:
            remaining[child] -= 1
            if remaining[child] == 0:
                ready.append(child)
    if visited != len(parents):
        path, positions, key = [], {}, next(k for k, n in remaining.items() if n)
        while key not in positions:
            positions[key] = len(path)
            path.append(key)
            key = next(p for p in parents[key] if remaining[p])
        cycle, field = path[positions[key]:], "parents" if code == "spine-cycle" else "fields.dependsOn"
        error = SpineError(code, f"{code}: cycle in {field}; remove a cycle edge among {sorted(cycle)}")
        error.refused_sections = {key: [field] for key in cycle}
        error.editable_fields = {key: [field] for key in cycle}
        raise error
    return {node_id: sorted(values) for node_id, values in children.items()}


class Spine:
    """Validated snapshot; node access returns copies so revisions cannot become stale."""

    def __init__(self, data: dict, *, previous=None) -> None:
        if (
            not isinstance(data, dict)
            or data.get("schema") != SCHEMA
            or not isinstance(data.get("name"), str)
            or not data["name"].strip()
            or not isinstance(data.get("nodes"), list)
        ):
            raise SpineError("spine-schema-invalid", "Expected a named spine-v1 node list")
        self.name = data["name"]
        self._nodes: dict[str, dict] = {}
        for node in deepcopy(data["nodes"]):
            if (
                not isinstance(node, dict)
                or set(node) != {"id", "kind", "title", "text", "parents", "fields"}
                or any(not isinstance(node[key], str) for key in ("id", "kind", "title", "text"))
                or not _strings([node["id"], node["title"]])
                or node["kind"] not in ALLOWED_PARENTS
                or not _strings(node["parents"])
                or not isinstance(node["fields"], dict)
            ):
                raise SpineError("spine-schema-invalid", "Invalid node shape or kind")
            if node["id"] in self._nodes:
                raise SpineError("spine-duplicate-id", f"Duplicate node {node['id']}")
            if len(set(node["parents"])) != len(node["parents"]):
                raise SpineError("spine-schema-invalid", f"{node['id']}: use unique parents", node["id"], "parents")
            node["parents"].sort()
            self._nodes[node["id"]] = node
        self._nodes = dict(sorted(self._nodes.items()))
        self._parents = {node_id: node["parents"] for node_id, node in self._nodes.items()}
        for node_id, parents in self._parents.items():
            for parent in parents:
                if parent not in self._nodes:
                    raise SpineError("spine-unknown-parent", f"{node_id} has unknown parent {parent}", node_id, "parents")
        # Detect cycles first, even though a cycle necessarily also violates the kind table.
        self._children = _acyclic(self._parents, "spine-cycle")
        if len(self.ids("vision")) != 1:
            raise SpineError("spine-vision-count", "Expected exactly one vision")
        mode = self.node(self.ids("vision")[0])["fields"].get("planMode", "legacy")
        if mode not in ("legacy", "semantic-v1"):
            raise SpineError("spine-schema-invalid", "Unsupported planMode", self.ids("vision")[0], "fields.planMode")
        self.semantic = mode == "semantic-v1"
        for node_id, node in self._nodes.items():
            if any(self._nodes[parent]["kind"] not in ALLOWED_PARENTS[node["kind"]] for parent in node["parents"]):
                raise SpineError("spine-parent-kind-not-allowed", f"Invalid parent kind for {node_id}; choose parents of kind {sorted(ALLOWED_PARENTS[node['kind']])}", node_id, "parents")
            if node["kind"] == "bead":
                self._validate_bead(node)
            elif node["kind"] == "test":
                fields = node["fields"]
                checks = {"module": _strings([fields.get("module")]), "level": fields.get("level") in ("unit", "semantic", "e2e")}
                invalid = [key for key, valid in checks.items() if not valid]
                if invalid:
                    raise SpineError("spine-test-fields-invalid", f"Invalid test fields for {node_id}", node_id, *("fields." + k for k in invalid))
        _acyclic({node_id: self._nodes[node_id]["fields"]["dependsOn"] for node_id in self.ids("bead")}, "spine-bead-fields-invalid")
        try:
            for node in self._nodes.values():
                validate_trace_fields(node, self._nodes, previous)
                bindings = [(node["id"], ref["id"], ("fields.baseline." + link,))
                            for link in ("codeRefs", "testRefs", "resultRefs") for ref in node["fields"].get("baseline", {}).get(link, [])]
                if node["kind"] == "result":
                    bindings.append((node["fields"]["evidence"]["itemRef"]["id"], node["id"], ("parents", "fields.evidence.itemRef")))
                for ancestor, descendant, sections in bindings:
                    if ancestor not in self.ancestors(descendant):
                        error = trace_refusal(node["id"], f"{descendant} must descend from {ancestor}; restore the owning ancestry", *sections)
                        if previous and previous["nodes"]:
                            before = Spine(previous)
                            if ancestor in before.ids() and descendant in before.ids() and ancestor in before.ancestors(descendant):
                                path = set(before.descendants(ancestor)) & {*before.ancestors(descendant), descendant}
                                if changed := {key: ["parents"] for key in path if key in self._nodes and self.parents(key) != before.parents(key)}:
                                    error.editable_fields, error.refused_sections = changed, deepcopy(changed)
                        raise error
        except ValueError as error:
            refusal = SpineError("spine-trace-invalid", str(error))
            refusal.__dict__.update({key: getattr(error, key, {}) for key in ("editable_fields", "refused_sections")})
            raise refusal from error
        try:
            self._revisions = {node_id: content_revision(node) for node_id, node in self._nodes.items()}
        except (TypeError, ValueError) as error:
            raise SpineError("spine-schema-invalid", "Fields must be finite JSON values") from error
        for key in self.ids("result"):
            evidence = self._nodes[key]["fields"]["evidence"]
            ref = evidence["itemRef"]
            if evidence["state"] != "awaiting" and not self.bindings_current({ref["id"]: ref["revision"]}):
                evidence["state"] = "stale"
                self._revisions[key] = content_revision(self._nodes[key])

    def _impact_parents(self) -> dict[str, list[str]]:
        parents = {key: set(values) for key, values in self._parents.items()}
        # Every trace field participates. Nested baseline/evidence provenance is history.
        for key, node in self._nodes.items():
            fields = node["fields"]
            parents[key].update(ref["id"] for link in TRACE_LINKS for ref in fields.get(link, []))
            if node["kind"] == "bead":
                parents[key].update([*fields["dependsOn"], *fields["lessons"]])
        return {key: sorted(values) for key, values in parents.items()}

    def _validate_bead(self, node: dict) -> None:
        fields = node["fields"]
        invalid = [key for key, (_, check) in bead_contract(self.semantic).items() if not check(fields.get(key))]
        invalid += ["proofModule"] if self.semantic and "proofModule" in fields else []
        try:
            validate_capability_refs(fields.get("capabilityRefs", []))
        except PlanError as error:
            raise SpineError("spine-bead-fields-invalid", str(error), node["id"], "fields.capabilityRefs") from error
        for key, kind in (("dependsOn", "bead"), ("lessons", "decision")):
            if key not in invalid and (len(fields[key]) != len(set(fields[key])) or any(
                value not in self._nodes or self._nodes[value]["kind"] != kind for value in fields[key])):
                invalid.append(key)
        if invalid:
            raise SpineError("spine-bead-fields-invalid", f"Invalid bead fields for {node['id']}: {invalid}. {bead_rules(self.semantic)}",
                             node["id"], *("fields." + key for key in invalid))

    def ids(self, kind: str | None = None) -> list[str]:
        return [key for key, node in self._nodes.items() if kind is None or node["kind"] == kind]

    def node(self, node_id: str) -> dict:
        return deepcopy(self._nodes[node_id])

    def parents(self, node_id: str) -> list[str]:
        return list(self._parents[node_id])

    def children(self, node_id: str) -> list[str]:
        return list(self._children[node_id])

    def _walk(self, node_id: str, adjacency: dict[str, list[str]]) -> list[str]:
        seen: set[str] = set()
        pending = list(adjacency[node_id])
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(adjacency[current])
        return sorted(seen)

    def ancestors(self, node_id: str) -> list[str]:
        return self._walk(node_id, self._parents)

    def descendants(self, node_id: str) -> list[str]:
        return self._walk(node_id, self._children)

    def trace_up(self, node_id: str) -> list[list[str]]:
        self._nodes[node_id]  # Preserve the ordinary KeyError for an unknown node.
        paths = []
        pending = [[node_id]]
        while pending:
            path = pending.pop()
            current = path[-1]
            if self._nodes[current]["kind"] == "vision":
                paths.append(path)
            else:
                pending.extend([*path, parent] for parent in self._parents[current])
        return sorted(paths)

    def revision(self, node_id: str) -> str:
        return self._revisions[node_id]

    def dependency_revisions(self, ids) -> dict[str, str]:
        """Capture content bindings over the reference graph; cycles terminate."""
        parents, pending, bound = self._impact_parents(), list(ids), {}
        while pending:
            key = pending.pop()
            if key not in bound:
                bound[key] = self.revision(key)
                pending.extend(parents[key])
        return dict(sorted(bound.items()))

    def bindings_current(self, bindings) -> bool:
        if not bindings or any(self._revisions.get(key) != rev for key, rev in bindings.items()):
            return False
        pending, seen = list(bindings), set()
        while pending:
            key = pending.pop()
            if key in seen: continue
            seen.add(key)
            node, fields = self._nodes[key], self._nodes[key]["fields"]
            if node["kind"] == "result" and fields["evidence"]["state"] in {"stale", "awaiting"}: continue
            # evidenceRefs are append-only history; their IDs still participate in capture/impact.
            refs = [r for link in TRACE_LINKS if link != "evidenceRefs" for r in fields.get(link, [])]
            if node["kind"] == "result": refs.append(fields["evidence"]["itemRef"])
            if any(self._revisions.get(ref["id"]) != ref["revision"] for ref in refs): return False
            pending.extend(ref["id"] for ref in refs)
        return True

    def plan_revision(self) -> str:
        return _hash("\n".join(f"{key}:{value}" for key, value in self.dependency_revisions(self.ids()).items()))

    def coverage_gaps(self) -> list[dict[str, str]]:
        gaps = []
        for node_id, node in self._nodes.items():
            if node["kind"] == "acceptance" and not node["fields"].get("steps"):
                gaps.append({"id": node_id, "gap": "acceptance scenario has no executable steps"})
            elif node["kind"] == "requirement":
                kinds = {self._nodes[item]["kind"] for item in self.descendants(node_id)}
                for kind in ("bead", "test"):
                    if kind not in kinds:
                        gaps.append({"id": node_id, "gap": f"requirement-without-{kind}"})
            elif node["kind"] == "bead" and not any(
                self._nodes[item]["kind"] == "vision" for item in self.ancestors(node_id)
            ):
                gaps.append({"id": node_id, "gap": "bead-without-vision"})
            elif node["kind"] == "test" and not any(
                self._nodes[item]["kind"] in ("requirement", "bead")
                for item in self.ancestors(node_id)
            ):
                gaps.append({"id": node_id, "gap": "test-covering-nothing"})
        return gaps

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "name": self.name,
            "nodes": [self.node(key) for key in self.ids()],
        }

    def evidence_states(self) -> dict:
        """Project recorded observations; absence grants no proof or acceptance."""
        states = {key: {"state": "awaiting", "evidenceRefs": []} for key in self.ids()}
        priority = {"failed": 0, "stale": 1, "awaiting": 2, "integrated": 3, "validated live": 4}
        for key in self.ids("result"):
            evidence = self.node(key)["fields"]["evidence"]
            subject = evidence["itemRef"]["id"]
            state = evidence["state"]
            item = states[subject]
            if not item["evidenceRefs"] or priority[state] < priority[item["state"]]:
                item["state"] = state
            item["evidenceRefs"].append({"id": key, "revision": self.revision(key)})
            states[key]["state"] = state
        return states


def load_spine(path: str | Path) -> Spine:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SpineError("spine-schema-invalid", f"Invalid JSON: {error}") from error
    return Spine(data)


def save_spine(spine: Spine, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(spine.to_dict(), sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def diff(old: Spine, new: Spine) -> dict[str, list[str]]:
    old_ids, new_ids = set(old.ids()), set(new.ids())
    return {
        "added": sorted(new_ids - old_ids),
        "removed": sorted(old_ids - new_ids),
        "changed": sorted(
            key for key in old_ids & new_ids if old.revision(key) != new.revision(key)
        ),
    }


def impact(old: Spine, new: Spine) -> dict[str, list[str]]:
    changes = diff(old, new)
    changed = set().union(*changes.values())
    # Both snapshots preserve removed/reparented dependencies and retired proof.
    affected = changed | {key for spine in (old, new) for key in spine.ids()
                          if set(spine.dependency_revisions([key])) & changed}
    # Include retired beads/tests in affected lists so consumers can retire their evidence.
    return {
        "changed": sorted(changed),
        "affected": sorted(affected),
        "affectedBeads": sorted(affected & (set(old.ids("bead")) | set(new.ids("bead")))),
        "affectedTests": sorted(affected & (set(old.ids("test")) | set(new.ids("test")))),
        "untouchedBeads": sorted(set(new.ids("bead")) - affected),
        "staleDocs": _stale_docs(old, new, changes),
    }


def stale_evidence(old: Spine, new: Spine) -> Spine:
    """Invalidate affected retained observations once at the canonical transaction boundary."""
    for key, prior_node in old._nodes.items():
        fields, current = prior_node["fields"], new._nodes.get(key, {})
        current_fields = current.get("fields", {})
        if any(ref not in current_fields.get("evidenceRefs", []) for ref in fields.get("evidenceRefs", [])):
            raise SpineError("spine-evidence-preservation-required", f"Retain {key}'s evidenceRefs; append evidence without dropping history", key, "fields.evidenceRefs")
        if prior_node["kind"] == "result" and fields["evidence"]["observation"] is not None:
            after = current_fields.get("evidence", {}) if current.get("kind") == "result" else {}
            if any(fields["evidence"][field] != after.get(field) for field in ("itemRef", "candidate", "source", "observation")):
                raise SpineError("spine-evidence-preservation-required", f"Retain observed result {key}; append a new result for new evidence", key, "fields.evidence")
        if prior_node["kind"] == "feature" and "baseline" in fields:
            if current.get("kind") != "feature" or current_fields.get("baseline") != fields["baseline"]:
                raise SpineError("spine-baseline-preservation-required", f"Retain existing feature {key} and its baseline provenance; record changes as linked amendments", key, "fields.baseline")
    nodes = new.to_dict()["nodes"]
    for node in (n for n in nodes if n["kind"] == "result"):
        key = node["id"]
        evidence = node["fields"]["evidence"]
        prior = old.node(key)["fields"]["evidence"] if key in old.ids("result") else None
        retained = prior is not None and prior["observation"] is not None
        ref = evidence["itemRef"]
        bindings = old.dependency_revisions([key]) if retained else {ref["id"]: ref["revision"]}
        if evidence["state"] != "awaiting" and not new.bindings_current(bindings):
            evidence["state"] = "stale"
    return Spine({**new.to_dict(), "nodes": nodes})


# Which rendered documents show each kind of node (see spine_render).
_DOCS_BY_KIND = {
    "vision": ("VISION.md", "PLAN.md"),
    "decision": ("VISION.md",),
    "feature": ("REQUIREMENTS.md",),
    "acceptance": ("REQUIREMENTS.md",),
    "story": ("REQUIREMENTS.md",),
    "requirement": ("REQUIREMENTS.md", "PLAN.md", "TRACE.md"),
    "design": ("PLAN.md",),
    "bead": ("PLAN.md", "TRACE.md"),
    "test": ("TRACE.md",),
}


def _stale_docs(old: Spine, new: Spine, changes: dict[str, list[str]]) -> list[str]:
    """Documents whose rendered body can change, derived without re-rendering."""

    stale: set[str] = set()
    lessons = {
        lesson
        for spine in (old, new)
        for bead in spine.ids("bead")
        for lesson in spine.node(bead)["fields"].get("lessons", [])
    }
    for key in changes["changed"] + changes["added"] + changes["removed"]:
        node = new._nodes.get(key) or old._nodes[key]
        stale.update(_DOCS_BY_KIND.get(node["kind"], ("TRACE.md",)))
        if node["kind"] == "acceptance" and (
            bool(old._nodes.get(key, {}).get("fields", {}).get("steps"))
            != bool(new._nodes.get(key, {}).get("fields", {}).get("steps"))
        ):
            stale.add("TRACE.md")
        if node["kind"] == "decision" and (
            key in lessons
            or any(
                spine.node(child)["kind"] == "bead"
                or (
                    spine.node(child)["kind"] == "test"
                    and spine.node(child)["fields"]["level"] == "e2e"
                )
                for spine in (old, new)
                if key in spine.ids()
                for child in spine.descendants(key)
            )
        ):
            stale.add("PLAN.md")
    if changes["added"] or changes["removed"]:
        stale.add("TRACE.md")  # coverage gaps and links can change
    return sorted(stale)


def requirement_refs(spine: Spine, node_id: str) -> list[dict[str, str]]:
    return [
        {"id": key, "revision": spine.revision(key)}
        for key in spine.ancestors(node_id)
        if spine._nodes[key]["kind"] == "requirement"
    ]


def requirement_file_sources(template_dir: str | Path, names: list[str]) -> dict[str, Path]:
    """Expand protected paths once for revision hashing and requirement overlays.

    Missing entries represent deletions. Reject paths that escape the template
    or could expose Git metadata or the worker-hidden reference implementation.
    """
    template = Path(template_dir).resolve()
    if not template.is_dir():
        raise SpineError("spine-template-missing", str(template))
    sources = {}
    for name in names:
        relative = PurePosixPath(name.replace("\\", "/"))
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or ":" in name
            or relative.parts[0].lower() in {"reference", ".git"}
        ):
            raise SpineError("spine-requirement-path-invalid", name)
        source = template / relative
        paths = [source, *source.rglob("*")] if source.is_dir() else [source]
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(template):
                raise SpineError("spine-requirement-path-invalid", str(path))
            if path.is_file():
                sources[path.relative_to(template).as_posix()] = path
    return dict(sorted(sources.items()))


def requirement_files_revision(template_dir: str | Path, names: list[str]) -> str:
    """Canonical published protected-file identity, including directory membership."""
    sources = requirement_file_sources(template_dir, names)
    file_revisions = [
        (
            name,
            hashlib.sha256(
                path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            ).hexdigest(),
        )
        for name, path in sources.items()
    ]
    return _hash(_canonical(file_revisions))


def scenario_nodes(spine):
    """Scenario aliases refer to their declared requirement owners, never new spine nodes."""
    if not spine.semantic:
        return {}
    document, _, _ = semantic_spec(spine, draft=True)
    scenarios = document["scenarios"]
    return {
        s["id"]: sorted({ref.split("@", 1)[0] for ref in s["requirementRefs"]}) for s in scenarios
    }


def semantic_spec(spine, product=None, *, draft=False):
    from codex_harness.core.semantic_schema import validate

    root = spine.node(spine.ids("vision")[0])["fields"]
    root.setdefault("targets", {})
    root.setdefault("scenarios", {"schema": "seldon/scenarios-v1", "scenarios": []})
    for field in ("scenarios", "targets"):
        if not isinstance(root.get(field), dict):
            raise ValueError(
                f"vision.fields.{field}: expected object; allowed keys "
                f"{['schema', 'scenarios'] if field == 'scenarios' else 'target IDs'}; use harness_upsert_node."
            )
    document, targets = root.get("scenarios"), root.get("targets", {})
    if isinstance(document.get("scenarios"), list):
        covered = {
            ref.split("@", 1)[0]
            for scenario in document["scenarios"]
            for ref in scenario.get("requirementRefs", [])
            if isinstance(ref, str)
        }
        for key in sorted(set(spine.ids("requirement")) - covered):
            document["scenarios"].append(
                dict(
                    id=f"unvalidated-{key}",
                    requirementRefs=[key],
                    contractRefs=[],
                    setup={},
                    actions=[],
                    observe=[],
                    verdict="all",
                    validationMessage=spine.node(key)["text"],
                )
            )
    try:
        validate(
            document,
            product or Path.cwd(),
            targets,
            require_entries=False,
            draft=draft,
            planning=True,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid scenarios/targets: {error}") from error
    if draft:
        return document, targets, set()
    covered, gaps, messages = set(), [], []
    for scenario in document["scenarios"]:
        for field, kinds in (("requirementRefs", ("requirement",)),
                             ("contractRefs", ("architecture", "design", "contract"))):
            kind = kinds[0]
            for ref in scenario[field]:
                key, pinned, revision = ref.partition("@")
                if key not in {k for kind in kinds for k in spine.ids(kind)} or pinned and not spine.bindings_current({key: revision}):
                    gaps.append(f"{ref} missing/stale ({field} in {scenario['id']})")
                    messages.append(
                        f"Stale/unknown semantic reference: {ref}; "
                        f"author {kind} node {key} ({field}) with a fresh ref before advancing"
                    )
                if kind == "requirement":
                    covered.add(key)
    if gaps:
        error = ValueError("Check semantic scenario references: " + "; ".join([*messages, *gaps]))
        error.open_gaps = gaps
        raise error
    return document, targets, covered


def compile_manifest(spine: Spine, template_dir: str | Path | None = None) -> dict:
    """Compile one lossless trace; retained runner metadata grants no live evidence."""
    from codex_harness.core.wave_refine import execution_scope

    scope = execution_scope(spine, template_dir)
    root = spine.node(spine.ids("vision")[0])["fields"]
    beads = {}
    for node_id in spine.ids("bead"):
        node = spine.node(node_id)
        fields = node["fields"]
        refs = requirement_refs(spine, node_id)
        bead = {
            "title": node["title"], "ownedFiles": fields["ownedFiles"],
            "dependsOn": fields["dependsOn"], "requirementRefs": refs,
            "specRefs": [
                {"id": key, "kind": spine.node(key)["kind"], "revision": spine.revision(key)}
                for key in spine.dependency_revisions([node_id]) if key != node_id
            ],
            "requirements": [ref["id"] for ref in refs],
            "lessons": [{"decisionId": key, "text": spine.node(key)["text"]}
                        for key in fields["lessons"]],
        }
        for key in ("proofModule", "semanticPhase", "capabilityRefs"):
            if key in fields:
                bead[key] = fields[key]
        beads[node_id] = bead
    manifest = {
        "schema": "codex-harness/provider-sample-v1", "sample": spine.name,
        "validation": root.get("validation", []),
        "requirementFiles": list(dict.fromkeys([*root.get("requirementFiles", []), *MARKDOWN_FILES])),
        "planRevision": spine.plan_revision(), "beads": beads, "integration": {},
        "trace": spine.to_dict(),
        "traceRevisions": spine.dependency_revisions(spine.ids()),
        "evidenceStates": spine.evidence_states(),
    }
    if spine.semantic:
        document, targets, _ = semantic_spec(spine, template_dir)
        manifest.update(planMode="semantic-v1", semantic={
            "scenarios": document, "targets": targets,
            "contracts": [spine.node(key) for kind in ("architecture", "design", "contract")
                          for key in spine.ids(kind)],
            **({"designRefs": root["designRefs"]} if "designRefs" in root else {}),
        })
        manifest["requirementFiles"].extend([
            "SCENARIOS.json", *(r["path"] for r in root.get("designRefs", []))])
    else:
        if not all(_strings(root.get(key)) for key in ("validation", "requirementFiles")):
            raise SpineError("spine-schema-invalid", "Vision requires validation and requirementFiles")
        manifest["requirementFiles"] = list(dict.fromkeys([*manifest["requirementFiles"], test_dir(root)]))
        # The legacy runner projects one suite selector; every test remains in trace.
        suites = [spine.node(k) for k in spine.ids("test")
                  if spine.node(k)["fields"]["level"] == "e2e"]
        if len(suites) == 1:
            test = suites[0]
            manifest["integration"] = {
                "proofModule": test["fields"]["module"],
                "requirements": [ref["id"] for ref in requirement_refs(spine, test["id"])],
            }
        if "verification" in root:
            from codex_harness.core.runners import resolve_runner
            manifest["verification"] = root["verification"]
            resolve_runner(manifest)
    if scope:
        manifest["integration"] = {"proofModule": scope["boundaryIntegrationModule"],
                                   "requirements": scope["inScopeRequirementIds"]}
        manifest["waveScope"] = scope
    if template_dir is not None:
        revision = requirement_files_revision(template_dir, manifest["requirementFiles"])
        manifest.update(requirementFilesRevision=revision,
                        planRevision=_hash(f"{spine.plan_revision()}:{revision}"))
    if scope:
        manifest["planRevision"] = _hash(_canonical({
            "algorithm": "wave-scope-v1", "compiledRevision": manifest["planRevision"],
            "waveScope": scope}))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command, output in (("render", "outdir"), ("compile", "out_json")):
        subparser = subcommands.add_parser(command)
        subparser.add_argument("spine", type=Path)
        subparser.add_argument(output, type=Path)
        if command == "compile":
            subparser.add_argument("--template", type=Path)
            subparser.add_argument(
                "--no-render", action="store_true", help="for legacy fixtures only"
            )
    subparser = subcommands.add_parser("impact")
    subparser.add_argument("old", type=Path)
    subparser.add_argument("new", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "impact":
            write_stdout_json(impact(load_spine(args.old), load_spine(args.new)), sort_keys=True)
        elif args.command == "compile":
            template = args.template
            if template is None and (args.spine.parent / "template").is_dir():
                template = args.spine.parent / "template"
            spine = load_spine(args.spine)
            if template is not None and not args.no_render:
                from codex_harness.core.spine_render import render_into_template

                render_into_template(spine, template)
            args.out_json.write_text(
                json.dumps(compile_manifest(spine, template), sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        else:
            from codex_harness.core.spine_render import render_markdown

            documents = render_markdown(load_spine(args.spine))
            args.outdir.mkdir(parents=True, exist_ok=True)
            for name, content in documents.items():
                (args.outdir / name).write_text(content, encoding="utf-8", newline="\n")
    except SpineError as error:
        parser.exit(2, f"{error.code}: {error}\n")


if __name__ == "__main__":
    main()
