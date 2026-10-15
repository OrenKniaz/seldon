"""Deterministic chat cards and status, with no providers or filesystem."""

import pytest
from tests.python.core.test_plan_store import draft_inputs

from codex_harness.core import planner_views as views
from codex_harness.core.plan_store import PHASES, open_plan_store


def status(phase="align"):
    return {
        "name": "tip-split",
        "plan_revision": "0123456789abcdef",
        "phase": phase,
        "open_clarifications": 2,
        "pending_gate": "approval:align" if phase == "align" else None,
    }


def test_status_line_exact():
    assert views.status_line(status()) == (
        "Revision 0123456789ab · open questions 2 · pending: approval:align"
    )
    assert views.status_line(status("accept")).endswith("pending: none")


def test_full_draft_map_and_legacy_unavailable_without_inferred_waves(tmp_path):
    with open_plan_store(tmp_path) as store:
        assert "unavailable / not measured" in views.outcome_waves_card(store.outcome_waves())
        inputs = draft_inputs(store)
        assert "Outcome 1" not in views.outcome_waves_card(store.outcome_waves())
        store.record_wave_map(**inputs)
        text = views.outcome_waves_card(store.outcome_waves())
        assert text.index("Outcome 1") < text.index("Outcome 2")
        for value in (
            "X1",
            "X2",
            "B1",
            "SK2",
            "Later meaningful refinement",
            "Later owner",
            "Current owner",
            "src/current.py",
            "dependsOn",
            "pending producers: B1",
            "completed-real 0",
            "outstanding 5",
            "Independent full vision review",
            "not remeasured",
            "blockers: 7",
            "draft / lock unconfirmed",
        ):
            assert value in text
        node = store.spine().node("R1")
        node["text"] = "Changed requirement"
        store.upsert_node(node)
        text = views.outcome_waves_card(store.outcome_waves())
        assert "stale" in text and "current accounting unavailable / not measured" in text
        assert "completed-real" not in text


@pytest.mark.parametrize("subject,label", [("product", "product"), ("oracle", "oracle dry run")])
def test_acceptance_outcome_and_first_failure(subject, label):
    run = dict(
        id="run",
        revision="rev",
        subject=subject,
        results=[
            dict(id="ok", passed=True, steps=[]),
            dict(
                id="stdin",
                passed=False,
                steps=[
                    dict(command="setup", exit=0, passed=True),
                    dict(command="check\nstdin " + "x" * 200, exit=3, passed=False),
                    dict(command="not executed", exit=4, passed=False),
                ],
            ),
        ],
    )
    text = views.acceptance_run_card(run)
    assert text.splitlines()[1] == f"- **1/2 passed** · {label} · failing: stdin"
    assert "check stdin" in text and "exit 3" in text and "…" in text
    assert "setup" not in text and "not executed" not in text
    run["results"] = run["results"][:1]
    assert f"- **1/1 scenarios passed** · {label}" in views.acceptance_run_card(run)


def test_text_next_only_first_action():
    text = views.response(status(), "Saved.\nNext: old", "compile then audit then quality")
    assert text.endswith("Next: compile") and text.count("Next:") == 1


@pytest.mark.parametrize("phase", PHASES)
def test_banner_and_tracker_all_phases(phase):
    current = status(phase)
    index = PHASES.index(phase)
    text = views.banner(current)
    assert text.splitlines() == [
        f"Harness Plan Mode: tip-split · Phase {index + 1}/11: "
        f"{phase.replace('-', ' ').title()} · pending: {current['pending_gate'] or 'none'}",
    ]
    tracker = views.phase_tracker(current)
    assert f"**{phase}**" in tracker
    assert "position, not proof" in tracker
    assert all(p in tracker for p in PHASES)
    assert tracker == views.phase_tracker(current)


def test_understanding_card_fields_and_confirmation():
    text = views.understanding_card(
        "Split fairly", "Diners", "Exact shares", "Dollars", "History", ["CLI users"]
    )
    assert text == (
        "**Here's what I understood**\n- Vision: Split fairly\n- Users: Diners\n"
        "- First user moment: Exact shares\n- Scope in: Dollars\n- Scope out: History\n"
        "- Assumptions: CLI users\n- Your call: confirm, or tell me what's wrong."
    )
    assert "Assumptions: none" in views.understanding_card("v", "u", "m", "i", "o", [])


def test_decision_card_options_tradeoffs_exact_reply():
    text = views.decision_card(
        "D3",
        "How to round?",
        [
            {"name": "Half up", "pros": "Predictable", "cons": "Bias"},
            {"name": "Even", "pros": "Unbiased", "cons": "Surprising"},
        ],
        "Half up for diner expectations",
        ["R2", "V1"],
    )
    assert "| Half up | Predictable | Bias |" in text
    assert "Recommendation: Half up for diner expectations" in text
    assert text.endswith(
        "Your call: reply with an option name (Half up / Even), or explain the change you want."
    )
    assert "Linked to: R2, V1" in text


def test_change_digest_exact_and_sorted():
    assert views.change_digest(
        {
            "changed": ["D3"],
            "affected": ["test_money", "D3", "A"],
            "affectedBeads": ["A"],
            "untouchedBeads": ["B"],
            "affectedTests": ["test_money"],
            "staleDocs": ["VISION.md"],
        }
    ) == (
        "**What changed**\n- Changed nodes: D3\n- Affected nodes: A, D3, test_money\n"
        "- Affected beads: A\n- Untouched beads: B\n"
        "- Affected tests: test_money\n- Stale docs: VISION.md"
    )


def test_publish_summary_dependency_waves():
    beads = [
        {"id": "C", "fields": {"dependsOn": ["A", "B"]}},
        {"id": "B", "fields": {"dependsOn": []}},
        {"id": "A", "fields": {"dependsOn": []}},
    ]
    assert views.publish_summary(beads, 1) == (
        "**Publication plan (planned, not proved)**\n- Beads (3): A, B, C\n"
        "- Execution order: Wave 1 (can run in parallel): A, B; "
        "Wave 2 (single bead): C\n- Open assumptions: 1"
    )
    assert views.publish_summary([], 0) == (
        "**Publication plan (planned, not proved)**\n- Beads (0): none\n"
        "- Execution order: no beads\n- Open assumptions: 0"
    )
    assert views.publish_summary(beads, 1) == views.publish_summary(list(reversed(beads)), 1)


def test_publish_summary_refuses_dependency_cycle():
    with pytest.raises(ValueError, match="cycle"):
        views.publish_summary([{"id": "A", "fields": {"dependsOn": ["A"]}}], 0)


def test_chat_cards_bound_lines_and_keep_approval_origin():
    current = status()
    current["name"] = "Long name\n" * 100
    current["pending_gate"] = "long gate " * 100
    assert len(views.status_line(current)) <= 120
    assert len(views.banner(current).splitlines()) == 1
    card = views.data_card("Recorded results", {"scenarios": [{"id": "A", "detail": "x" * 1000}]})
    assert 3 <= sum(line.startswith("- ") for line in card.splitlines()) <= 7
    assert '{"' not in card
    assert "approved by you" in views.approval_label("user")
    assert "approved by proxy (Opus) · not your approval" == views.approval_label("opus-user-proxy")
    assert "not your approval" in views.approval_label("proxy:Alice", is_proxy=True)
    assert (
        views.response(current, "Saved.\nNext: old", "Review.").splitlines()[-1] == "Next: Review."
    )


def test_diagram_caps_large_serial_dag_and_preserves_full_machine_output(tmp_path):
    import json
    from pathlib import Path

    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.plan_store import open_plan_store

    fixture = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"
    data = json.loads(fixture.read_text())
    prototype = next(n for n in data["nodes"] if n["kind"] == "bead")
    for index in range(85):
        node = json.loads(json.dumps(prototype))
        node["id"] = f"extra_{index}"
        node["title"] = 'Quoted "label" [with] | syntax; #60; ` fence'
        node["fields"]["dependsOn"] = [f"extra_{index - 1}"] if index else []
        data["nodes"].append(node)
    with open_plan_store(tmp_path) as store:
        source = tmp_path / "spine.json"
        source.write_text(json.dumps(data), encoding="utf-8")
        store.import_spine(source)
    server = PlannerServer(tmp_path)
    try:
        reply = server.call_tool("harness_diagram", {"kind": "bead-dag"})
        assert not reply["isError"], reply
        text = reply["content"][0]["text"]
        mermaid = text.split("```mermaid\n")[1].split("\n```")[0]
        assert sum('["' in line for line in mermaid.splitlines()) <= 40
        assert "Summarized" in text
        assert "extra_84" in reply["structuredContent"]["mermaid"]
        assert "#34;" in reply["structuredContent"]["mermaid"]
        assert text.splitlines()[-1].startswith("Next:")
    finally:
        server.close()
