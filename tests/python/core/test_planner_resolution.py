"""Actual planner consumers over durable source-only review/evidence fixtures.

Protected behavior: host repair guidance names all publication-blocking clusters.
Credible regression: generic phase prose hides stale/minor open work or sends a
prospective plan to an invented oracle directory. Existing review ingestion and
gate tests do not prove the MCP/CLI next-action contract. The primary boundary
is real store -> MCP wire serialization and CLI JSON, not a copied helper answer.
Provider launches and initial native responses are source-only doubles. The
bounded public CLI sequence also runs a real oracle audit and its subprocesses.
No live model, external controller or worker delivery is exercised.
"""

import json
import sys

from tests.python.core import test_planner_replay_gaps
from tests.python.core.planning_support import source_controller_roundtable, source_only_b

from codex_harness.core import plan_cli
from codex_harness.core.roundtable import findings

project = test_planner_replay_gaps.project


def _at_roundtable(store):
    # The replay fixture's identical expectation must be a supported statement,
    # rather than a one-line method hidden by the former synthetic audit credit.
    for name in ("test_answer.py", "test_journey.py"):
        proof = store.workspace / "template" / "tests" / name
        source = proof.read_text()
        proof.write_text(
            source.replace(
                "def test_result(self): self.assertEqual(answer(), 42)",
                "def test_result(self):\n        self.assertEqual(answer(), 42)",
            )
        )
    test_planner_replay_gaps.confirmed_vision(store)
    for phase in ("requirements", "tech-stack", "plan", "roundtable"):
        if phase == "roundtable":
            source_only_b(
                store,
                reviewers=[
                    dict(name="Alpha", provider="codex"),
                    dict(name="Beta", provider="claude"),
                ],
            )
        store.advance(phase, by="source-test")
    store.compile_template(store.workspace / "template")


def test_public_repair_loop_refreshes_review_before_canonical_publication(
    project, monkeypatch, capsys
):
    """A bounded host sequence follows public replies, without bypassing gates.

    Initial native forms and reviewer processes are source-only fixture inputs.
    The author edit, parser, real oracle audit, fresh packet and publication
    guards run normally. This is neither a live controller nor product proof.
    """
    from codex_harness.core import roundtable

    _at_roundtable(project)
    packets = []

    def reviewer_return(launch, *_):
        packets.append(launch.stdin_text)
        return (
            0,
            json.dumps(
                [
                    dict(
                        node_id="R1",
                        severity="major",
                        finding="The accepted answer 42 is absent from the requirement text",
                        suggestion=(
                            "State the accepted answer in R1, retaining the protected proofs"
                        ),
                    )
                ]
                if len(packets) == 1
                else []
            ),
        )

    reviewer = dict(name="Alpha", provider="codex")
    source_controller_roundtable(
        project,
        process_stub=reviewer_return,
        reviewers=[reviewer],
        executable_for=lambda provider: provider,
    )
    monkeypatch.setattr(roundtable, "_run", reviewer_return)
    monkeypatch.setattr(roundtable, "_executable", lambda *_: sys.executable)
    prefix = ["--workspace", str(project.workspace), "--json"]

    def public(*arguments, expected=0):
        if arguments == ("status",):
            arguments += ("--full",)
        assert plan_cli.main([*prefix, *arguments]) == expected
        result = json.loads(capsys.readouterr().out)
        assert result["isError"] is (expected != 0)
        return result

    refusal = public("advance", "--to", "publish", expected=2)
    assert "resolve" in refusal["next"]
    assert refusal["data"]["authorHandoff"]["state"] == "author_action_required"
    assert refusal["text"].count("**Author repair:") == 1
    original_revision = refusal["status"]["manifest_revision"]
    open_findings = public("findings")["data"]["findings"]
    assert len(open_findings) == 1 and open_findings[0]["status"] == "open"
    finding_id = open_findings[0]["id"]
    assert finding_id in refusal["next"]
    shown = public("show", "R1")["data"]
    repaired = {key: shown[key] for key in ("id", "kind", "title", "text", "parents", "fields")}
    repaired["text"] = "Receive the accepted answer 42"
    public("node", "--json", json.dumps(repaired))
    public("compile", "--template", "template", "--out", "manifest.json")
    public(
        "resolve",
        "--id",
        finding_id,
        "--resolved",
        "--resolution",
        "R1 now states accepted answer 42; unchanged protected unit and journey proofs cover it",
    )
    stale = public("status")["status"]["reviewResolution"]
    assert stale["manifestRevision"] != original_revision
    assert stale["audit"]["current"] is False and stale["review"]["current"] is False
    assert stale["nextAction"]["kind"] == "audit_required"
    # The oracle route is explicitly supplied by this source fixture, not guessed.
    assert public("audit", "--oracle", "oracle")["data"]["ok"] is True
    pending = public("status")["status"]["reviewResolution"]
    assert pending["nextAction"]["kind"] == "review_required"
    assert len(packets) == 1
    public("advance", "--to", "publish", expected=2)
    reviewer = source_only_b(project, reviewers=[reviewer])[0]
    reviewed = public("roundtable", "--reviewer", json.dumps(reviewer))
    assert not reviewed["data"]["invalid"] and len(packets) == 2
    assert packets[0] != packets[1] and "Receive the accepted answer 42" in packets[1]
    ready = public("status")["status"]["reviewResolution"]
    assert ready["nextAction"]["kind"] == "attempt_phase_advance"
    public("advance", "--to", "publish")
    public("publish")
    terminal = public("status")["status"]
    assert terminal["publication"]["up_to_date"] is True
    assert terminal["published_revision"] == ready["manifestRevision"]
    assert terminal["acceptance"]["accepted"] is False
    preserved = next(row for row in findings(project) if row["id"] == finding_id)
    assert preserved["status"] == "resolved" and preserved["resolution"]
    assert len(list(project.connection.execute("SELECT id FROM review_runs"))) == 2
