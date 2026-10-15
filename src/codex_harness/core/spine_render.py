"""Readable Markdown projections of the canonical planning spine."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codex_harness.core.spine import Spine


def _heading(spine: Spine, node_id: str, depth: int) -> list[str]:
    node = spine.node(node_id)
    return [
        f"{'#' * depth} {node_id}: {node['title']} ({spine.revision(node_id)[:12]})",
        "",
        node["text"],
        "",
    ]


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _header(body: str) -> str:
    # Each document is stamped with its own content revision, so editing one
    # node marks only the documents that actually render it as changed.
    revision = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return f"<!-- Generated from spine.json; document revision {revision} -->"


def render_markdown(spine: Spine) -> dict[str, str]:
    from codex_harness.core.spine import MARKDOWN_FILES

    vision = _heading(spine, spine.ids("vision")[0], 1)
    vision.extend(["## Decisions", ""])
    for node_id in spine.ids("decision"):
        vision.extend(_heading(spine, node_id, 3))
        vision.extend([f"Parents: {', '.join(spine.parents(node_id)) or '(none)'}", ""])

    requirements = ["# Requirements", ""]
    # Shared requirements appear under each feature, keeping every accepted lineage visible.
    rendered: set[str] = set()
    for feature in spine.ids("feature"):
        requirements.extend(_heading(spine, feature, 2))
        for story in spine.children(feature):
            if spine.node(story)["kind"] != "story":
                continue
            requirements.extend(_heading(spine, story, 3))
            for child in spine.children(story):
                if spine.node(child)["kind"] == "requirement":
                    requirements.extend(_heading(spine, child, 4))
                    rendered.add(child)
        for child in spine.children(feature):
            if spine.node(child)["kind"] == "requirement":
                requirements.extend(_heading(spine, child, 3))
                rendered.add(child)
    orphaned = set(spine.ids("requirement")) - rendered
    if orphaned:
        requirements.extend(["## Unlinked requirements", ""])
        for node_id in sorted(orphaned):
            requirements.extend(_heading(spine, node_id, 3))

    if spine.ids("acceptance"):
        requirements.extend(["## Acceptance scenarios", ""])
        for node_id in spine.ids("acceptance"):
            node = spine.node(node_id)
            fields = node["fields"]
            requirements.extend(_heading(spine, node_id, 3))
            requirements.extend([f"Parents: {', '.join(node['parents']) or '(none)'}", ""])
            for field in ("given", "firstMoment", "cleanState"):
                if field in fields:
                    requirements.extend([f"{field}: {fields[field]}", ""])
            steps = []
            for step in fields.get("steps", []):
                steps.append(step if isinstance(step, str) else step["command"])
                if isinstance(step, dict) and "expect" in step:
                    steps.append("# Expect: " + json.dumps(step["expect"], sort_keys=True))
            # Use a fence longer than any command's backtick run.
            fence = "`" * max(
                3, max((len(run) + 1 for run in re.findall(r"`+", "\n".join(steps))), default=3)
            )
            requirements.extend([fence + "sh", *steps, fence, ""])
            if "expect" in fields:
                requirements.extend(["Expect: " + json.dumps(fields["expect"], sort_keys=True), ""])

    plan = ["# Plan", ""]
    root = spine.node(spine.ids("vision")[0])["fields"]
    if "verification" in root:
        verification = "Verification argv: `" + json.dumps(root["verification"]["argv"]) + "`"
        plan.extend([verification, ""])
        requirements[2:2] = [verification, ""]
    else:
        plan.extend([f"Validation argv prefix: `{' '.join(root.get('validation', []))}`", ""])
        plan.extend(["Append the bead's proof module to this prefix; do not run it by itself.", ""])
    requirement_files = dict.fromkeys([*root.get("requirementFiles", []), *MARKDOWN_FILES])
    plan.extend([f"Requirement files: {', '.join(requirement_files)}", ""])
    for node_id in spine.ids("bead"):
        node = spine.node(node_id)
        fields = node["fields"]
        refs = [
            f"{key}@{spine.revision(key)[:12]}"
            for key in spine.ancestors(node_id)
            if spine.node(key)["kind"] == "requirement"
        ]
        plan.extend(_heading(spine, node_id, 2))
        plan.extend(
            [
                f"- Owned files: {', '.join(fields['ownedFiles'])}",
                f"- Dependencies: {', '.join(fields['dependsOn']) or '(none)'}",
                f"- Requirement refs: {', '.join(refs) or '(none)'}",
                f"- Proof: `{fields['proofModule']}`",
                f"- Provider: {fields.get('provider', '(unspecified)')}",
                f"- Parents: {', '.join(node['parents']) or '(none)'}",
                "",
            ]
        )
        for ancestor in spine.ancestors(node_id):
            if spine.node(ancestor)["kind"] in {"requirement", "decision"}:
                plan.extend(_heading(spine, ancestor, 3))
        for lesson in fields["lessons"]:
            plan.extend([f"Lesson after validation ({lesson}): {spine.node(lesson)['text']}", ""])
    plan.extend(["## Designs", ""])
    for node_id in spine.ids("design"):
        plan.extend(_heading(spine, node_id, 3))
        plan.extend([f"Parents: {', '.join(spine.parents(node_id)) or '(none)'}", ""])
    plan.extend(["## Integration", ""])
    for node_id in spine.ids("test"):
        node = spine.node(node_id)
        if node["fields"]["level"] == "e2e":
            plan.extend(_heading(spine, node_id, 3))
            plan.extend([f"Proof: `{node['fields']['module']}`", ""])
            for ancestor in spine.ancestors(node_id):
                if spine.node(ancestor)["kind"] in {"requirement", "decision"}:
                    plan.extend(_heading(spine, ancestor, 4))

    trace = ["# Trace", "", "| Requirement | Beads | Tests |", "| --- | --- | --- |"]
    for node_id in spine.ids("requirement"):
        descendants = spine.descendants(node_id)
        beads = [key for key in descendants if spine.node(key)["kind"] == "bead"]
        tests = [key for key in descendants if spine.node(key)["kind"] == "test"]
        test_refs = [
            f"{key} ({spine.node(key)['fields']['level']}; {spine.node(key)['fields']['module']})"
            for key in tests
        ]
        columns = (node_id, ", ".join(beads) or "—", ", ".join(test_refs) or "—")
        trace.append("| " + " | ".join(_cell(value) for value in columns) + " |")
    trace.extend(["", "## Gaps", ""])
    gaps = spine.coverage_gaps()
    trace.extend(f"- {gap['id']}: {gap['gap']}" for gap in gaps)
    if not gaps:
        trace.append("None.")
    bodies = {
        name: "\n".join(lines).rstrip() + "\n"
        for name, lines in sorted(
            {
                "VISION.md": vision,
                "REQUIREMENTS.md": requirements,
                "PLAN.md": plan,
                "TRACE.md": trace,
            }.items()
        )
    }
    return {name: f"{_header(body)}\n\n{body}" for name, body in bodies.items()}


def render_into_template(spine: Spine, template_dir: str | Path) -> None:
    """Replace derived docs with the current canonical spine projection."""
    from codex_harness.core.spine import MARKDOWN_FILES, requirement_file_sources

    template = Path(template_dir)
    # Validate existing targets before writing, including symlinks.
    requirement_file_sources(template, list(MARKDOWN_FILES))
    for name, content in render_markdown(spine).items():
        (template / name).write_text(content, encoding="utf-8", newline="\n")
