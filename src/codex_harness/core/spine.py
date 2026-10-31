"""Canonical planning ancestry, revisions, impact and work-graph compilation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from copy import deepcopy
from pathlib import Path, PurePosixPath

from codex_harness.core.graph import PlanError, test_dir, validate_capability_refs
from codex_harness.core.semantic_schema import target_entry

SCHEMA = "codex-harness/spine-v1"
MARKDOWN_FILES = ("PLAN.md", "REQUIREMENTS.md", "TRACE.md", "VISION.md")
ALLOWED_PARENTS = {
    "vision": set(),
    "feature": {"vision"},
    "acceptance": {"vision", "feature"},
    "story": {"feature"},
    "requirement": {"story", "feature"},
    "decision": {"vision", "feature", "requirement"},
    "design": {"requirement", "decision"},
    "bead": {"requirement", "design", "decision"},
    "test": {"requirement", "bead"},
}


class SpineError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        raise SpineError(code, "Graph contains a cycle")
    return {node_id: sorted(values) for node_id, values in children.items()}


class Spine:
    """Validated snapshot; node access returns copies so revisions cannot become stale."""

    def __init__(self, data: dict) -> None:
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
                or not node["id"].strip()
                or not node["title"].strip()
                or node["kind"] not in ALLOWED_PARENTS
                or not _strings(node["parents"])
                or len(set(node["parents"])) != len(node["parents"])
                or not isinstance(node["fields"], dict)
            ):
                raise SpineError("spine-schema-invalid", "Invalid node shape or kind")
            if node["id"] in self._nodes:
                raise SpineError("spine-duplicate-id", f"Duplicate node {node['id']}")
            node["parents"].sort()
            self._nodes[node["id"]] = node
        self._nodes = dict(sorted(self._nodes.items()))
        self._parents = {node_id: node["parents"] for node_id, node in self._nodes.items()}
        for node_id, parents in self._parents.items():
            for parent in parents:
                if parent not in self._nodes:
                    raise SpineError(
                        "spine-unknown-parent", f"{node_id} has unknown parent {parent}"
                    )
        # Detect cycles first, even though a cycle necessarily also violates the kind table.
        self._children = _acyclic(self._parents, "spine-cycle")
        if len(self.ids("vision")) != 1:
            raise SpineError("spine-vision-count", "Expected exactly one vision")
        mode = self.node(self.ids("vision")[0])["fields"].get("planMode", "legacy")
        if mode not in ("legacy", "semantic-v1"):
            raise SpineError("spine-schema-invalid", "Unsupported planMode")
        self.semantic = mode == "semantic-v1"
        for node_id, node in self._nodes.items():
            if any(
                self._nodes[parent]["kind"] not in ALLOWED_PARENTS[node["kind"]]
                for parent in node["parents"]
            ):
                raise SpineError(
                    "spine-parent-kind-not-allowed", f"Invalid parent kind for {node_id}"
                )
            if node["kind"] == "bead":
                self._validate_bead(node)
            elif node["kind"] == "test":
                if self.semantic:
                    raise SpineError(
                        "spine-schema-invalid", "semantic-v1 uses scenario data, not test nodes"
                    )
                fields = node["fields"]
                if (
                    not isinstance(fields.get("module"), str)
                    or not fields["module"].strip()
                    or fields.get("level") not in ("unit", "e2e")
                ):
                    raise SpineError(
                        "spine-test-fields-invalid", f"Invalid test fields for {node_id}"
                    )
        _acyclic(
            {node_id: self._nodes[node_id]["fields"]["dependsOn"] for node_id in self.ids("bead")},
            "spine-bead-fields-invalid",
        )
        try:
            self._revisions = {
                node_id: _hash(_canonical(node)) for node_id, node in self._nodes.items()
            }
        except (TypeError, ValueError) as error:
            raise SpineError("spine-schema-invalid", "Fields must be finite JSON values") from error

    def _validate_bead(self, node: dict) -> None:
        fields = node["fields"]
        try:
            validate_capability_refs(fields.get("capabilityRefs", []))
        except PlanError as error:
            raise SpineError("spine-bead-fields-invalid", str(error)) from error
        valid = (
            all(_strings(fields.get(key)) for key in ("ownedFiles", "dependsOn", "lessons"))
            and bool(fields["ownedFiles"])
            and (
                fields.get("semanticPhase") in ("contracts", "build")
                and "proofModule" not in fields
                if self.semantic
                else isinstance(fields.get("proofModule"), str)
                and bool(fields["proofModule"].strip())
            )
            and (
                "provider" not in fields
                or isinstance(fields["provider"], str)
                and bool(fields["provider"].strip())
            )
        )
        if valid:
            for key, kind in (("dependsOn", "bead"), ("lessons", "decision")):
                values = fields[key]
                valid = (
                    valid
                    and len(values) == len(set(values))
                    and all(
                        value in self._nodes and self._nodes[value]["kind"] == kind
                        for value in values
                    )
                )
        if not valid:
            raise SpineError("spine-bead-fields-invalid", f"Invalid bead fields for {node['id']}")

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

    def plan_revision(self) -> str:
        return _hash("\n".join(f"{key}:{value}" for key, value in self._revisions.items()))

    def coverage_gaps(self) -> list[dict[str, str]]:
        gaps = []
        for node_id, node in self._nodes.items():
            if node["kind"] == "acceptance" and not node["fields"].get("steps"):
                gaps.append({"id": node_id, "gap": "acceptance scenario has no executable steps"})
            elif node["kind"] == "requirement":
                kinds = {self._nodes[item]["kind"] for item in self.descendants(node_id)}
                for kind in ("bead",) if self.semantic else ("bead", "test"):
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
    affected = set(changed)
    followers = {}
    for version, spine in (("old", old), ("new", new)):
        edges = {key: set(spine._children[key]) for key in spine.ids()}
        for consumer in spine.ids("bead"):
            for producer in spine.node(consumer)["fields"]["dependsOn"]:
                edges[producer].add(consumer)
        followers[version] = edges
    # A single multi-source traversal; old edges are followed only for removals.
    pending = [("new", key) for key in changed if key in new._nodes]
    pending.extend(("old", key) for key in changes["removed"])
    visited: set[tuple[str, str]] = set()
    while pending:
        version, key = pending.pop()
        if (version, key) in visited:
            continue
        visited.add((version, key))
        affected.add(key)
        pending.extend((version, child) for child in followers[version][key])
    # Include retired beads/tests in affected lists so consumers can retire their evidence.
    beads = set(old.ids("bead")) | set(new.ids("bead"))
    tests = set(old.ids("test")) | set(new.ids("test"))
    return {
        "changed": sorted(changed),
        "affected": sorted(affected),
        "affectedBeads": sorted(affected & beads),
        "affectedTests": sorted(affected & tests),
        "untouchedBeads": sorted(set(new.ids("bead")) - affected),
        "staleDocs": _stale_docs(old, new, changes),
    }


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
        stale.update(_DOCS_BY_KIND[node["kind"]])
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


def semantic_spec(spine, product=None):
    from codex_harness.core.semantic_schema import validate

    root = spine.node(spine.ids("vision")[0])["fields"]
    for field in ("scenarios", "targets"):
        if not isinstance(root.get(field), dict):
            raise ValueError(
                f"Missing/malformed {field}; author vision.fields.{field} as an object. "
                "See harness_schema kind=vision semanticScenarios."
            )
    document, targets = root["scenarios"], root["targets"]
    try:
        validate(document, product or Path.cwd(), targets, require_entries=False)
        # The surface follows the targets; only a mixed or contradicting declaration is wrong.
        kinds = {"web" if t.get("kind") == "web" else "cli" for t in targets.values()}
        if len(kinds) > 1 or root.get("productSurface", next(iter(kinds), "cli")) not in kinds:
            raise ValueError("Targets must all be web or all CLI and match productSurface")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"Invalid scenarios/targets: {error}. Author scenarios with schema, scenarios, "
            "requirementRefs and contractRefs, plus targets bindings; "
            "see harness_schema kind=vision semanticScenarios."
        ) from error
    covered, gaps, messages = set(), [], []
    for scenario in document["scenarios"]:
        for field, kind in (("requirementRefs", "requirement"), ("contractRefs", "design")):
            for ref in scenario[field]:
                key, pinned, revision = ref.partition("@")
                if key not in spine.ids(kind) or pinned and revision != spine.revision(key):
                    gaps.append(f"{ref} missing/stale ({field} in {scenario['id']})")
                    messages.append(
                        f"Stale/unknown semantic reference: {ref}; "
                        f"author {kind} node {key} ({field}) with a fresh ref before advancing"
                    )
                if kind == "requirement":
                    covered.add(key)
    gaps.extend(
        f"{key} uncovered by any scenario"
        for key in sorted(set(spine.ids("requirement")) - covered)
    )
    if gaps:
        error = ValueError("Check semantic scenario references: " + "; ".join([*messages, *gaps]))
        error.open_gaps = gaps
        raise error
    return document, targets, covered


def compile_manifest(spine: Spine, template_dir: str | Path | None = None) -> dict:
    from codex_harness.core.wave_refine import execution_scope

    scope = execution_scope(spine, template_dir)
    root = spine.node(spine.ids("vision")[0])["fields"]
    if not spine.semantic and not all(
        _strings(root.get(key)) for key in ("validation", "requirementFiles")
    ):
        raise SpineError("spine-schema-invalid", "Vision requires validation and requirementFiles")
    beads = {}
    for node_id in spine.ids("bead"):
        node = spine.node(node_id)
        fields = node["fields"]
        refs = requirement_refs(spine, node_id)
        bead = {
            "title": node["title"],
            "ownedFiles": fields["ownedFiles"],
            **(
                {"semanticPhase": fields["semanticPhase"]}
                if spine.semantic
                else {"proofModule": fields["proofModule"]}
            ),
            "dependsOn": fields["dependsOn"],
            "requirementRefs": refs,
            "specRefs": [
                {"id": key, "kind": spine.node(key)["kind"], "revision": spine.revision(key)}
                for key in spine.ancestors(node_id)
                if spine.node(key)["kind"] in {"decision", "design", "requirement"}
            ],
            "requirements": [ref["id"] for ref in refs],
            "lessons": [
                {"decisionId": key, "text": spine.node(key)["text"]} for key in fields["lessons"]
            ],
        }
        if "provider" in fields:
            bead["suggestedProvider"] = fields["provider"]
        if "capabilityRefs" in fields:
            bead["capabilityRefs"] = fields["capabilityRefs"]
        beads[node_id] = bead
    if spine.semantic:
        if (
            root.get("productSurface", "cli") not in ("cli", "web")
            or scope
            or spine.ids("acceptance")
        ):
            raise ValueError("Other surfaces unsupported in semantic-v1; use Python CLI/static web")
        document, targets, covered = semantic_spec(spine, template_dir)
        phases = {b["semanticPhase"]: key for key, b in beads.items()}
        if len(beads) != 2 or set(phases) != {"contracts", "build"}:
            raise ValueError(
                "Multiple/parallel Build unsupported in semantic-v1; "
                "declare Contracts and one Build"
            )
        if beads[phases["contracts"]]["dependsOn"] or beads[phases["build"]]["dependsOn"] != [
            phases["contracts"]
        ]:
            raise ValueError("semantic-v1 requires Contracts -> Build -> controller Validation")
        contract_ids = {
            ref.partition("@")[0] for s in document["scenarios"] for ref in s["contractRefs"]
        }
        for key in beads:
            if not covered.issubset(
                {r["id"] for r in beads[key]["requirementRefs"]}
            ) or not contract_ids.issubset(spine.ancestors(key)):
                raise ValueError("Both workers must reference every requirement and contract")
            if not {target_entry(binding) for binding in targets.values()}.issubset(
                beads[key]["ownedFiles"]
            ):
                raise ValueError(
                    "Both workers must own entry files (web: index.html) or module __main__.py"
                )
        manifest = dict(
            schema="codex-harness/provider-sample-v1",
            sample=spine.name,
            planMode="semantic-v1",
            semantic={
                "scenarios": document,
                "targets": targets,
                "contracts": [spine.node(key) for key in spine.ids("design")],
                **({"designRefs": root["designRefs"]} if "designRefs" in root else {}),
            },
            validation=[],
            requirementFiles=[
                *MARKDOWN_FILES,
                "SCENARIOS.json",
                *(r["path"] for r in root.get("designRefs", [])),
            ],
            beads=beads,
            integration={},
            planRevision=spine.plan_revision(),
        )
        if template_dir is not None:
            revision = requirement_files_revision(template_dir, manifest["requirementFiles"])
            manifest.update(
                requirementFilesRevision=revision,
                planRevision=_hash(f"{spine.plan_revision()}:{revision}"),
            )
        return manifest
    integration_tests = [
        key for key in spine.ids("test") if spine.node(key)["fields"]["level"] == "e2e"
    ]
    if len(integration_tests) > 1:
        raise SpineError("spine-integration-invalid", "Manifest supports one integration test")
    integration = {}
    if integration_tests:
        test_id = integration_tests[0]
        integration = {
            "proofModule": spine.node(test_id)["fields"]["module"],
            "requirements": [ref["id"] for ref in requirement_refs(spine, test_id)],
        }
    if scope:
        integration = {
            "proofModule": scope["boundaryIntegrationModule"],
            "requirements": scope["inScopeRequirementIds"],
        }
    manifest = {
        "schema": "codex-harness/provider-sample-v1",
        "sample": spine.name,
        "validation": root["validation"],
        "requirementFiles": list(
            dict.fromkeys([*root["requirementFiles"], *MARKDOWN_FILES, test_dir(root)])
        ),
        "planRevision": spine.plan_revision(),
        "beads": beads,
        "integration": integration,
    }
    if "verification" in root:
        from codex_harness.core.runners import resolve_runner

        manifest["verification"] = root["verification"]
        resolve_runner(manifest)
    if template_dir is not None:
        revision = requirement_files_revision(template_dir, manifest["requirementFiles"])
        manifest["requirementFilesRevision"] = revision
        manifest["planRevision"] = _hash(f"{spine.plan_revision()}:{revision}")
    if scope:
        manifest["waveScope"] = scope
        manifest["planRevision"] = _hash(
            _canonical(
                {
                    "algorithm": "wave-scope-v1",
                    "compiledRevision": manifest["planRevision"],
                    "waveScope": scope,
                }
            )
        )
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
            print(json.dumps(impact(load_spine(args.old), load_spine(args.new)), sort_keys=True))
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
