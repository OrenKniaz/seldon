"""Deterministic chat diagrams and text outlines projected from the planning spine."""

from __future__ import annotations

import re

from codex_harness.core.spine import Spine

_KINDS = {
    "feature-map": ("vision", "feature", "story", "requirement"),
    "decision-map": ("decision", "design", "requirement", "bead"),
    "bead-dag": ("bead",),
    "trace": ("requirement", "bead", "test", "acceptance"),
    "user-journey": ("feature", "story"),
}


def _escape(label: str) -> str:
    # Encode in one pass so literal entity-looking input cannot become Mermaid syntax.
    return "".join(f"#{ord(char)};" if char in '"[]|\n\r\t#&<>`:;%' else char for char in label)


def _ids(spine: Spine) -> dict[str, str]:
    # Preserve ordinary ids (including A --> B), reserving a disjoint encoded namespace.
    return {
        key: key
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key)
        and key
        not in {
            "end",
            "subgraph",
            "graph",
            "flowchart",
            "style",
            "class",
            "classDef",
            "click",
            "direction",
            "linkStyle",
        }
        and not key.startswith(("node_", "parallel_"))
        else "node_" + key.encode("utf-8").hex()
        for key in spine.ids()
    }


def _label(node: dict) -> str:
    label = f"{node['id']}: {node['title']}"
    if node["kind"] == "bead":
        label += " | files: " + ", ".join(sorted(node["fields"]["ownedFiles"]))
    elif node["kind"] == "test":
        label += f" ({node['fields']['level']})"
    return label


def _projection(spine: Spine, kind: str) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    if kind not in _KINDS:
        raise ValueError(f"Unknown diagram kind: {kind}")
    nodes = {key: spine.node(key) for key in spine.ids() if spine.node(key)["kind"] in _KINDS[kind]}
    edges = set()
    for key, node in nodes.items():
        if kind == "bead-dag":
            edges.update((parent, key) for parent in node["fields"]["dependsOn"])
            continue
        # Skip hidden ancestry nodes, stopping at the nearest visible parents.
        pending = list(node["parents"])
        seen = set()
        while pending:
            parent = pending.pop()
            if parent in seen:
                continue
            seen.add(parent)
            if parent in nodes:
                edges.add((parent, key))
            else:
                pending.extend(spine.parents(parent))
    return nodes, sorted(edges)


def _layers(nodes: dict[str, dict], edges: list[tuple[str, str]]) -> list[list[str]]:
    remaining = set(nodes)
    layers = []
    while remaining:
        ready = sorted(remaining - {child for parent, child in edges if parent in remaining})
        layers.append(ready)
        remaining.difference_update(ready)
    return layers


def _journey_groups(spine: Spine) -> list[tuple[str, list[str]]]:
    def order(key: str) -> tuple[float, str]:
        value = spine.node(key)["fields"].get("order")
        return (value if type(value) in (int, float) else float("inf"), key)

    groups = [(key, spine.children(key)) for key in spine.ids("feature")]
    groups = [
        (key, [s for s in children if spine.node(s)["kind"] == "story"]) for key, children in groups
    ]
    orphans = [key for key in spine.ids("story") if not spine.parents(key)]
    if orphans:
        groups.append(("", orphans))
    return [(key, sorted(stories, key=order)) for key, stories in groups]


def _flow(spine: Spine, kind: str) -> str:
    nodes, edges = _projection(spine, kind)
    ids = _ids(spine)
    lines = ["flowchart TD" if kind == "feature-map" else "flowchart LR"]
    groups = _layers(nodes, edges) if kind == "bead-dag" else [list(nodes)]
    for index, group in enumerate(groups):
        parallel = kind == "bead-dag" and len(group) > 1
        if parallel:
            lines.append(f'  subgraph parallel_{index}["can run in parallel"]')
        for key in group:
            lines.append(f'    {ids[key]}["{_escape(_label(nodes[key]))}"]')
        if parallel:
            lines.append("  end")
    lines.extend(f"  {ids[parent]} --> {ids[child]}" for parent, child in edges)
    if kind == "trace":
        acceptance = [ids[key] for key in nodes if nodes[key]["kind"] == "acceptance"]
        if acceptance:
            lines.append("  classDef acceptance fill:#e0f2fe,stroke:#0369a1")
            lines.append(f"  class {','.join(acceptance)} acceptance")
        gaps = sorted({gap["id"] for gap in spine.coverage_gaps() if gap["id"] in nodes})
        lines.append("  classDef gap fill:#fff0cc,stroke:#b45309,stroke-width:2px")
        if gaps:
            lines.append(f"  class {','.join(ids[key] for key in gaps)} gap")
    if not nodes:
        lines.append("  %% No planned nodes yet")
    return "\n".join(lines) + "\n"


def user_journey(spine: Spine) -> str:
    # Mermaid requires a score; 3 is a neutral display default, not plan evidence.
    lines = ["journey", f"  title {_escape(spine.name)}"]
    for feature, stories in _journey_groups(spine):
        label = _label(spine.node(feature)) if feature else "Ungrouped stories"
        lines.append(f"  section {_escape(label)}")
        lines.extend(f"    {_escape(_label(spine.node(key)))}: 3" for key in stories)
    if not spine.ids("story"):
        for key in spine.ids("vision"):
            fields = spine.node(key)["fields"]
            lines.append("  section Vision")
            for field in ("first_user_moment", "scope_in"):
                values = fields.get(field, [])
                if not isinstance(values, list):
                    values = [values]
                lines.extend(f"    {_escape(str(value))}: 3" for value in values if value)
    return "\n".join(lines) + "\n"


def bead_dag(spine: Spine) -> str:
    return _flow(spine, "bead-dag")


def diagram(spine: Spine, kind: str) -> str:
    if kind not in _KINDS:
        raise ValueError(f"Unknown diagram kind: {kind}")
    return user_journey(spine) if kind == "user-journey" else _flow(spine, kind)
