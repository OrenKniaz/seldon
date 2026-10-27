"""Load and validate the work graph used by provider sample plans."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType


class PlanError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Bead:
    id: str
    title: str
    owned_files: tuple[str, ...]
    depends_on: tuple[str, ...]
    requirements: tuple[str, ...]
    proof_module: str | None
    provider: str | None
    requirement_refs: tuple[tuple[str, str], ...] = ()
    spec_refs: tuple[tuple[str, str, str], ...] | None = None
    # None binds all transitive prerequisites; [] declares a fully stubbed proof.
    proof_depends_on: tuple[str, ...] | None = None
    capability_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    name: str
    root: Path
    validation: tuple[str, ...]
    requirement_files: tuple[str, ...]
    beads: Mapping[str, Bead]
    integration_requirements: tuple[str, ...]
    integration_proof_module: str | None
    plan_revision: str | None = None
    verification: Mapping | None = None
    requirement_files_revision: str | None = None
    semantic: Mapping | None = None


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError("plan-schema-invalid", "Expected a nonempty string")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PlanError("plan-schema-invalid", "Expected a list of strings")
    return tuple(_string(item) for item in value)


def validate_capability_refs(value: object) -> tuple[str, ...]:
    """Validate inert reference identifiers, never commands or capability grants."""
    if (
        not isinstance(value, list)
        or any(
            not isinstance(item, str)
            or len(item) > 128
            or re.fullmatch(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*", item) is None
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise PlanError(
            "plan-schema-invalid",
            "capabilityRefs must be unique lowercase dotted identifiers of at most 128 characters",
        )
    return tuple(value)


def _revision(value: object) -> str:
    revision = _string(value)
    if len(revision) != 64 or any(character not in "0123456789abcdef" for character in revision):
        raise PlanError("plan-schema-invalid", "Expected a lowercase sha256 revision")
    return revision


def _requirement_refs(value: object, requirements: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or any(not isinstance(ref, dict) for ref in value):
        raise PlanError("plan-schema-invalid", "Expected requirement refs as objects")
    refs = tuple((_string(ref["id"]), _revision(ref["revision"])) for ref in value)
    if refs and (
        len({ref[0] for ref in refs}) != len(refs) or {ref[0] for ref in refs} != set(requirements)
    ):
        raise PlanError("plan-schema-invalid", "Requirement refs must match requirement ids")
    return refs


def _file_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in value or not path.parts:
        raise PlanError("plan-schema-invalid", f"Expected a relative plan path: {value!r}")
    return path


def test_dir(manifest_or_plan: Mapping | Plan) -> str:
    """Return the runner's protected directory; preserve legacy discovery parsing."""
    verification = (
        manifest_or_plan.verification
        if isinstance(manifest_or_plan, Plan)
        else manifest_or_plan.get("verification")
    )
    if verification is not None:
        from codex_harness.core.runners import resolve_runner

        return resolve_runner(manifest_or_plan).test_dir
    validation = (
        manifest_or_plan.validation
        if isinstance(manifest_or_plan, Plan)
        else manifest_or_plan["validation"]
    )
    if not isinstance(validation, list | tuple):
        raise PlanError("plan-schema-invalid", "Expected validation as a list of strings")

    class DiscoveryParser(argparse.ArgumentParser):
        def error(self, message):
            raise PlanError("plan-schema-invalid", f"Invalid discovery test directory: {message}")

    parser = DiscoveryParser(add_help=False)
    parser.add_argument("-s", "--start-directory", action="append", default=[])
    parser.add_argument("-t", "--top-level-directory", action="append", default=[])
    # Consume unittest's other flags too, including short-option clusters, so
    # e.g. -vsother cannot hide another start directory from protection.
    for short, long in (
        ("-v", "--verbose"),
        ("-q", "--quiet"),
        ("-f", "--failfast"),
        ("-c", "--catch"),
        ("-b", "--buffer"),
    ):
        parser.add_argument(short, long, action="store_true")
    parser.add_argument("--locals", action="store_true")
    parser.add_argument("-k")
    # Worker commands append the proof pattern after the manifest's final -p.
    parser.add_argument("-p", "--pattern", nargs="?")
    discovery = (
        validation[validation.index("discover") + 1 :] if "discover" in validation else validation
    )
    arguments, unknown = parser.parse_known_args(discovery)
    if "discover" in validation and unknown:
        raise PlanError(
            "plan-schema-invalid", "Unsupported discovery arguments; use -s/--start-directory"
        )
    if len(arguments.start_directory) > 1 or arguments.top_level_directory:
        raise PlanError(
            "plan-schema-invalid",
            "Ambiguous discovery: use one -s/--start-directory and no -t/--top-level-directory",
        )
    if not arguments.start_directory:
        return "tests"
    value = arguments.start_directory[0]
    if not isinstance(value, str) or not value.strip() or value.startswith("-"):
        raise PlanError("plan-schema-invalid", "validation -s requires a nonempty test directory")
    try:
        return str(_file_path(value))
    except PlanError as error:
        raise PlanError(
            "plan-schema-invalid", f"Invalid validation -s test directory: {value!r}"
        ) from error


def _spec_refs(value: object) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(value, list) or any(not isinstance(ref, dict) for ref in value):
        raise PlanError("plan-schema-invalid", "Expected spec refs as objects")
    refs = tuple(
        (_string(ref["id"]), _string(ref["kind"]), _revision(ref["revision"])) for ref in value
    )
    if len({ref[0] for ref in refs}) != len(refs) or any(
        ref[1] not in {"decision", "design", "requirement"} for ref in refs
    ):
        raise PlanError(
            "plan-schema-invalid", "Expected unique decision, design or requirement refs"
        )
    return refs


def load_plan(path: Path) -> Plan:
    """Load a manifest, rejecting unsafe ownership and invalid dependencies."""
    manifest_path = path / "sample.json" if path.is_dir() else path
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not isinstance(manifest["beads"], dict):
            raise PlanError("plan-schema-invalid", "Expected a plan and bead mapping")
        semantic = manifest.get("planMode") == "semantic-v1"
        if semantic:
            from codex_harness.core.semantic_schema import validate

            validate(
                manifest["semantic"]["scenarios"],
                manifest_path.parent,
                manifest["semantic"]["targets"],
                require_entries=False,
            )
            phases = {b.get("semanticPhase"): key for key, b in manifest["beads"].items()}
            if len(manifest["beads"]) != 2 or set(phases) != {"contracts", "build"}:
                raise ValueError("Multiple/parallel Build unsupported in semantic-v1")
            if manifest["beads"][phases["contracts"]].get("dependsOn") != [] or manifest["beads"][
                phases["build"]
            ].get("dependsOn") != [phases["contracts"]]:
                raise ValueError("semantic-v1 requires Contracts -> Build")
            if any("proofModule" in b for b in manifest["beads"].values()) or manifest.get(
                "integration"
            ):
                raise ValueError("semantic-v1 uses scenario proof, not proofModule")
        directory = None if semantic else test_dir(manifest)
        beads = {}
        for bead_id, data in manifest["beads"].items():
            if not isinstance(data, dict):
                raise PlanError("plan-schema-invalid", "Expected a bead object")
            provider = data.get("suggestedProvider")
            requirements = _strings(data["requirements"])
            beads[_string(bead_id)] = Bead(
                id=bead_id,
                title=_string(data["title"]),
                owned_files=_strings(data["ownedFiles"]),
                depends_on=_strings(data.get("dependsOn", [])),
                requirements=requirements,
                proof_module=None if semantic else _string(data["proofModule"]),
                provider=_string(provider) if provider is not None else None,
                requirement_refs=_requirement_refs(data.get("requirementRefs", []), requirements),
                spec_refs=_spec_refs(data["specRefs"]) if "specRefs" in data else None,
                proof_depends_on=_strings(data["proofDependsOn"])
                if "proofDependsOn" in data
                else None,
                capability_refs=validate_capability_refs(data.get("capabilityRefs", [])),
            )
        integration = manifest.get("integration", {})
        if not isinstance(integration, dict):
            raise PlanError("plan-schema-invalid", "Expected an integration object")
        proof_module = integration.get("proofModule")
        plan = Plan(
            name=_string(manifest["sample"]),
            root=manifest_path.resolve().parent,
            validation=_strings(manifest["validation"]),
            requirement_files=_strings(manifest["requirementFiles"]),
            beads=MappingProxyType(beads),
            integration_requirements=_strings(integration.get("requirements", [])),
            integration_proof_module=_string(proof_module) if proof_module is not None else None,
            plan_revision=_revision(manifest["planRevision"])
            if "planRevision" in manifest
            else hashlib.sha256(
                json.dumps(
                    manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest(),
            verification=manifest.get("verification"),
            requirement_files_revision=_revision(manifest["requirementFilesRevision"])
            if "requirementFilesRevision" in manifest
            else None,
            semantic=manifest["semantic"] if semantic else None,
        )
        if "verification" in manifest:
            from codex_harness.core.runners import resolve_runner

            resolve_runner(manifest)
    except (KeyError, json.JSONDecodeError) as error:
        raise PlanError("plan-schema-invalid", f"Invalid plan manifest: {error}") from error

    order = topological_order(plan)
    ancestors: dict[str, set[str]] = {}
    protected = tuple(
        _file_path(name) for name in (*plan.requirement_files, *([directory] if directory else []))
    )
    owners: dict[PurePosixPath, list[str]] = {}
    for bead_id in order:
        bead = plan.beads[bead_id]
        ancestors[bead_id] = set(bead.depends_on)
        for dependency in bead.depends_on:
            ancestors[bead_id].update(ancestors[dependency])
        if bead.proof_depends_on is not None and (
            len(set(bead.proof_depends_on)) != len(bead.proof_depends_on)
            or set(bead.proof_depends_on) - ancestors[bead_id]
        ):
            raise PlanError(
                "plan-proof-dependency-invalid",
                f"{bead_id} proof dependencies must be unique transitive prerequisites",
            )
        for name in bead.owned_files:
            owned = _file_path(name)
            if any(owned == item or item in owned.parents for item in protected):
                raise PlanError(
                    "plan-owned-file-protected", f"{bead_id} owns protected file {name}"
                )
            for owner in owners.get(owned, []):
                if owner != bead_id and owner not in ancestors[bead_id]:
                    raise PlanError(
                        "plan-ownership-overlap", f"{owner} and {bead_id} both own {name}"
                    )
            owners.setdefault(owned, []).append(bead_id)
    return plan


def topological_order(plan: Plan) -> list[str]:
    """Return dependency order, choosing manifest order among ready beads."""
    indices = {bead_id: index for index, bead_id in enumerate(plan.beads)}
    remaining = {}
    dependents: dict[str, list[str]] = {bead_id: [] for bead_id in plan.beads}
    for bead_id, bead in plan.beads.items():
        dependencies = set(bead.depends_on)
        for dependency in dependencies:
            if dependency not in plan.beads:
                raise PlanError(
                    "plan-unknown-dependency", f"{bead_id} depends on unknown bead {dependency}"
                )
            dependents[dependency].append(bead_id)
        remaining[bead_id] = len(dependencies)
    ready = [(indices[bead_id], bead_id) for bead_id, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        _, bead_id = heapq.heappop(ready)
        order.append(bead_id)
        for dependent in dependents[bead_id]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heapq.heappush(ready, (indices[dependent], dependent))
    if len(order) != len(plan.beads):
        raise PlanError("plan-dependency-cycle", "Plan dependencies contain a cycle")
    return order


def waves(plan: Plan) -> list[list[str]]:
    """Return dependency levels in manifest order, starting with dependency-free beads."""
    levels: dict[str, int] = {}
    for bead_id in topological_order(plan):
        levels[bead_id] = 1 + max(
            (levels[key] for key in plan.beads[bead_id].depends_on), default=0
        )
    return [
        [key for key in plan.beads if levels[key] == level]
        for level in range(1, max(levels.values(), default=0) + 1)
    ]


def ready_beads(plan: Plan, done: Iterable[str], running: Iterable[str] = ()) -> list[str]:
    """Return unstarted beads whose dependencies have all completed."""
    completed, active = set(done), set(running)
    return [
        bead_id
        for bead_id in topological_order(plan)
        if bead_id not in completed
        and bead_id not in active
        and completed.issuperset(plan.beads[bead_id].depends_on)
    ]
