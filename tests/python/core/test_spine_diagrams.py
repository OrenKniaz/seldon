"""Chat views use real spine ancestry, dependencies and coverage gaps."""

from copy import deepcopy
from pathlib import Path

import pytest

from codex_harness.core.spine import SCHEMA, Spine, load_spine
from codex_harness.core.spine_diagrams import (
    bead_dag,
    decision_map,
    diagram,
    feature_map,
    text_fallback,
    trace_view,
    user_journey,
)

KINDS = ("feature-map", "user-journey", "decision-map", "bead-dag", "trace")
FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"


def node(key, kind, parents=(), **fields):
    return dict(id=key, kind=kind, title=key, text="", parents=list(parents), fields=fields)


def spine(*nodes):
    return Spine(dict(schema=SCHEMA, name="Example", nodes=[node("V", "vision"), *nodes]))


def bead(key, dependencies=(), parents=()):
    return node(
        key,
        "bead",
        parents,
        dependsOn=list(dependencies),
        ownedFiles=[f"{key}.py"],
        proofModule="test.py",
        lessons=[],
    )


@pytest.mark.parametrize("kind", KINDS)
def test_real_fixture_headers_and_fallback(kind):
    plan = load_spine(FIXTURE)
    header = (
        "journey"
        if kind == "user-journey"
        else "flowchart TD"
        if kind == "feature-map"
        else "flowchart LR"
    )
    assert diagram(plan, kind).startswith(header + "\n")
    assert text_fallback(plan, kind).startswith(kind + ": dogfood-tipsplit\n")
    assert len(text_fallback(plan, kind).splitlines()) > 1


def test_fixture_relationships_and_public_functions():
    plan = load_spine(FIXTURE)
    for kind, function in zip(
        KINDS, (feature_map, user_journey, decision_map, bead_dag, trace_view), strict=True
    ):
        assert diagram(plan, kind) == function(plan)
    assert "V1 --> F1" in feature_map(plan)
    assert "F1 --> S1" in feature_map(plan)
    assert "S1 --> R1" in feature_map(plan)
    assert "A --> B" in bead_dag(plan)
    assert "tipsplit/money.py" in bead_dag(plan)
    assert "D1 --> A" in decision_map(plan)
    assert "R1 --> A" in trace_view(plan)
    assert "A --> test_money" in trace_view(plan)
    assert "(unit)" in trace_view(plan) and "(e2e)" in trace_view(plan)
    assert "class R6 gap" in trace_view(plan)
    assert "requirement-without-bead" in text_fallback(plan, "trace")


def test_parallel_layers_exclude_dependency_paths():
    plan = spine(bead("A"), bead("B"), bead("C", ["A"]), bead("D", ["B"]), bead("E", ["C", "D"]))
    output = bead_dag(plan)
    groups = output.split("subgraph ")[1:]
    assert len(groups) == 2
    first, second = (group.split("  end")[0] for group in groups)
    assert "can run in parallel" in first and "A[" in first and "B[" in first
    assert "C[" not in first and "D[" not in first
    assert "C[" in second and "D[" in second and "E[" not in second
    assert "A --> C" in output and "C --> E" in output
    assert "can run in parallel" in text_fallback(plan, "bead-dag")


def test_design_ancestry_and_orphans_remain_visible():
    plan = spine(
        node("R", "requirement"),
        node("D", "decision", ["R"]),
        node("X", "design", ["D"]),
        bead("A", parents=["X"]),
        node("T", "test", ["A"], module="test.py", level="e2e"),
    )
    assert "R --> D" in decision_map(plan)
    assert "D --> X" in decision_map(plan)
    assert "X --> A" in decision_map(plan)
    assert "R --> A" in trace_view(plan)
    assert "A --> T" in trace_view(plan)
    assert "class R gap" not in trace_view(plan)
    assert "R[" in feature_map(plan)


def test_uncovered_requirement_has_highlight():
    plan = spine(node("R", "requirement"))
    assert "classDef gap " in trace_view(plan)
    assert "class R gap" in trace_view(plan)
    assert "requirement-without-test" in text_fallback(plan, "trace")


@pytest.mark.parametrize("kind", KINDS)
def test_vision_only_and_partial_spines(kind):
    assert diagram(spine(), kind)
    assert text_fallback(spine(), kind)
    partial = spine(node("F", "feature"), node("S", "story"), node("R", "requirement"))
    assert diagram(partial, kind)
    assert text_fallback(partial, kind)
    if kind == "user-journey":
        assert "Ungrouped stories" in diagram(partial, kind)
        assert "S#58; S: 3" in diagram(partial, kind)


@pytest.mark.parametrize("kind", KINDS)
def test_labels_escape_syntax_and_preserve_plain_text(kind):
    data = load_spine(FIXTURE).to_dict()
    title = 'Quote " [square] | pipe\n#35; <script>: 5'
    for item in data["nodes"]:
        item["title"] = title
    output = diagram(Spine(data), kind)
    assert "#34;" in output and "#124;" in output and "#10;" in output
    assert "#91;" in output and "#93;" in output and "#35;35#59;" in output
    assert title not in output and "<script>" not in output
    assert 'Quote " [square] | pipe #35; <script>: 5' in text_fallback(Spine(data), kind)


@pytest.mark.parametrize("kind", KINDS)
def test_deterministic_under_node_and_parent_reordering(kind):
    plan = load_spine(FIXTURE)
    data = deepcopy(plan.to_dict())
    data["nodes"].reverse()
    for item in data["nodes"]:
        item["parents"].reverse()
    reordered = Spine(data)
    assert diagram(plan, kind) == diagram(reordered, kind) == diagram(plan, kind)
    assert text_fallback(plan, kind) == text_fallback(reordered, kind)


def test_journey_uses_spine_order_and_feature_groups():
    plan = spine(node("F", "feature"), node("S2", "story", ["F"]), node("S1", "story", ["F"]))
    output = user_journey(plan)
    assert "section F#58; F" in output
    assert output.index("S1#58;") < output.index("S2#58;")


def test_journey_honors_explicit_story_order():
    plan = spine(
        node("F", "feature"),
        node("S1", "story", ["F"], order=2),
        node("S2", "story", ["F"], order=1),
    )
    for output in (user_journey(plan), text_fallback(plan, "user-journey")):
        assert output.index("S2") < output.index("S1")


def test_unsafe_and_reserved_ids_are_stable_and_distinct():
    plan = spine(bead("A B"), bead("node_412042"), bead("end", ["A B"]), bead("A_B"))
    output = bead_dag(plan)
    assert 'node_412042["' in output
    assert 'node_6e6f64655f343132303432["' in output
    assert "node_412042 --> node_656e64" in output
    assert 'A_B["' in output


@pytest.mark.parametrize("function", [diagram, text_fallback])
def test_unknown_kind_rejected(function):
    with pytest.raises(ValueError, match="Unknown diagram kind"):
        function(spine(), "unknown")
