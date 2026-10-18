"""The ten guide-only replay failures, through canonical producers and CLI consumers."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from tests.python.core.planning_support import source_controller_roundtable, source_only_vision_lock
from tests.python.core.test_plan_cli import cli

from codex_harness.core import plan_cli, roundtable
from codex_harness.core.plan_quality import quality_report
from codex_harness.core.plan_store import PlanStore, PlanStoreError
from codex_harness.core.spine_diagrams import text_fallback, user_journey

REVIEWERS = [
    {"name": "codex-reviewer", "provider": "codex"},
    {"name": "claude-reviewer", "provider": "claude"},
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(roundtable, "_run", lambda *args: pytest.fail("Real reviewer forbidden"))
    template = tmp_path / "template"
    (template / "tests").mkdir(parents=True)
    (template / "answer.py").write_text("def answer(): raise NotImplementedError\n")
    for module in ("test_answer.py", "test_journey.py"):
        (template / "tests" / module).write_text(
            "import unittest\nfrom answer import answer\n"
            "class Proof(unittest.TestCase):\n"
            "    def test_result(self): self.assertEqual(answer(), 42)\n"
        )
    oracle = tmp_path / "oracle"
    oracle.mkdir()
    (oracle / "answer.py").write_text("def answer(): return 42\n")

    def node(id, kind, parents, fields):
        return dict(id=id, kind=kind, title=id, text=id, parents=parents, fields=fields)

    nodes = [
        node(
            "V1",
            "vision",
            [],
            dict(
                first_user_moment="Receive the answer",
                scope_in=["Ask", "Receive 42"],
                validation=["-m", "unittest", "discover", "-s", "tests", "-p"],
                requirementFiles=["tests"],
            ),
        ),
        node("F1", "feature", ["V1"], {}),
        node("R1", "requirement", ["F1"], {}),
        node(
            "B1",
            "bead",
            ["R1"],
            dict(
                ownedFiles=["answer.py"],
                dependsOn=[],
                proofModule="test_answer.py",
                lessons=[],
            ),
        ),
        node("T1", "test", ["R1"], dict(module="test_answer.py", level="unit")),
        node("TE", "test", ["R1"], dict(module="test_journey.py", level="e2e")),
    ]
    with PlanStore(tmp_path) as store:
        path = tmp_path / "input.json"
        path.write_text(
            json.dumps(dict(schema="codex-harness/spine-v1", name="Answer", nodes=nodes))
        )
        store.import_spine(path)
        store.compile_template(template)
        yield store


def review(store, reviewers=REVIEWERS, process_stub=None, **kwargs):
    """Controller-path source simulation for coverage/retry mechanics, not provider proof."""
    return source_controller_roundtable(
        store,
        reviewers=reviewers,
        process_stub=process_stub or (lambda *args: (0, "[]")),
        executable_for=lambda provider: provider,
        **kwargs,
    )


def audit(store):
    return cli(store.workspace, "audit", "--oracle", "oracle", machine=True)


def check(store, name):
    return next(item for item in quality_report(store)["checks"] if item["id"] == name)


def confirmed_vision(store):
    """Complete this source fixture's card and pass real gates with a synthetic client form.

    This is neither human approval nor native-transport/provider evidence. Call it
    only for cases testing downstream behavior after the alignment/vision gates.
    """
    node = store.spine().node("V1")
    node["fields"].update(
        vision="A tiny offline command-line tool that returns 42.",
        users="One person using the local command line.",
        scope_out="Network services and graphical interfaces.",
        assumptions=[],
    )
    store.upsert_node(node)
    source_only_vision_lock(
        store, statement="SOURCE-ONLY approve the complete Answer fixture card."
    )
    store.advance("features", by="source-test")


def test_gap1_non_git_project_audits_template_oracle_and_integration(project):
    before = (project.workspace / "template/answer.py").read_bytes()
    result = audit(project)
    assert result["data"]["baseline"] == "template"
    assert result["data"]["ok"] and result["data"]["integrationProof"]["passed"]
    assert project.passing_audit(project.manifest_revision()) == result["data"]
    assert (project.workspace / "template/answer.py").read_bytes() == before


@pytest.mark.parametrize("failure", ["exit", "timeout", "parse"])
def test_gap2_failure_artifact_exposes_full_output_and_command(project, failure):
    def runner(launch, cwd, timeout):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(
                launch.command, timeout, output=b"partial", stderr=b"timed out"
            )
        return (
            (7, "partial", "first error\nsecond error")
            if failure == "exit"
            else (0, "bad JSON", "parse detail")
        )

    result = review(project, REVIEWERS[:1], runner)
    reviewer = result["reviewers"][0]
    artifact = json.loads(Path(reviewer["artifactPath"]).read_text(encoding="utf-8"))
    assert artifact["command"][-1] == "-" and "--skip-git-repo-check" in artifact["command"]
    assert artifact["stdout"] and artifact["stderr"]
    assert artifact["exitCode"] == (None if failure == "timeout" else 7 if failure == "exit" else 0)
    card = roundtable.render_roundtable_card(result, [])
    assert reviewer["artifactPath"] in card and reviewer["stderrPreview"] in card


def test_gap2_windows_prefers_newest_desktop_bundle_and_preserves_override(tmp_path, monkeypatch):
    root = tmp_path / "OpenAI/Codex/bin"
    old, new = root / "old/codex.exe", root / "new/codex.exe"
    for path, timestamp in ((old, 1000), (new, 2000)):
        path.parent.mkdir(parents=True)
        path.touch()
        os.utime(path, (timestamp, timestamp))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(roundtable.os, "name", "nt")
    assert roundtable._executable("codex", None) == str(new)
    assert roundtable._executable("codex", {"codex": "operator.exe"}) == "operator.exe"


def test_gap3_retry_keeps_other_providers_success_for_same_revision(project):
    project.connection.execute("UPDATE meta SET phase='roundtable'")
    first = review(
        project,
        process_stub=lambda launch, *args: (7, "", "launch failed")
        if launch.cli == "codex"
        else (0, "[]", ""),
    )
    assert check(project, "roundtable")["status"] == "fail"
    retry = review(project, REVIEWERS[:1])
    assert retry["crossSubscription"]
    assert check(project, "roundtable")["status"] == "pass"
    assert f"Review manifest revision {project.manifest_revision()}" in roundtable.review_packet(
        project
    )
    assert {run["provider"] for run in retry["successfulReviews"]} == {"codex", "claude"}
    assert (
        next(run for run in retry["successfulReviews"] if run["provider"] == "claude")["round_id"]
        == first["roundId"]
    )
    proof = project.workspace / "template/tests/test_answer.py"
    proof.write_text(proof.read_text() + "\n# changed requirement\n")
    assert not project.successful_reviews(project.manifest_revision())


def test_gap4_round_budget_is_per_reviewer_revision_and_reserved_before_launch(project):
    def concurrent(launch, *args):
        with PlanStore(project.workspace) as second:
            with pytest.raises(PlanStoreError, match="used 1/1"):
                review(second, REVIEWERS[:1], max_rounds=1)
        return 0, "[]"

    result = review(project, REVIEWERS[:1], concurrent, max_rounds=1)
    assert result["maxRounds"] == 1 and result["reviewers"][0]["round"] == 1
    with pytest.raises(PlanStoreError, match="used 1/1"):
        review(project, REVIEWERS[:1], max_rounds=1)
    review(project, REVIEWERS[1:], max_rounds=1)
    node = project.spine().node("R1")
    node["text"] += " revised"
    project.upsert_node(node)
    assert review(project, REVIEWERS[:1], max_rounds=1)["reviewers"][0]["round"] == 1


def test_gap5_error_json_exits_two_and_preserves_reviewer_data(project, capsys, monkeypatch):
    monkeypatch.setattr(roundtable, "_run", lambda *args: (9, "out", "failure detail"))
    monkeypatch.setattr(roundtable, "_executable", lambda *args: "fake.exe")
    code = plan_cli.main(
        [
            "--workspace",
            str(project.workspace),
            "--json",
            "roundtable",
            "--reviewer",
            json.dumps(REVIEWERS[0]),
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert result["isError"] and code == 2
    assert result["data"]["reviewers"][0]["stderrPreview"] == "failure detail"
    assert result["data"]["reviewers"][0]["artifactPath"] in result["text"]
    assert "roundtable --reviewer" in result["next"] and "Inspect" in result["next"]


def test_gap6_failed_audit_next_recovers_audit_not_later_phases(project):
    (project.workspace / "oracle/answer.py").write_text("def answer(): return 0\n")
    result = cli(project.workspace, "audit", "--oracle", "oracle", machine=True, code=2)
    assert "audit " in result["next"] and "--baseline template" in result["next"]
    assert "advance" not in result["next"] and not project.passing_audit(
        project.manifest_revision()
    )


def test_gap7_vision_lock_journey_uses_vision_before_stories(project):
    journey = user_journey(project.spine())
    assert "Receive the answer: 3" in journey and "Receive 42: 3" in journey
    assert "first_user_moment: Receive the answer" in text_fallback(project.spine(), "user-journey")


def test_gap8_decision_records_choice_and_rationale_before_next_phase(project):
    confirmed_vision(project)
    project.connection.execute("UPDATE meta SET phase='tech-stack'")
    card = dict(
        id="D1",
        question="Runtime?",
        options=[dict(name="Python", pros="Simple", cons="Runtime")],
        recommendation="Python",
        parents=["V1"],
    )
    proposed = cli(project.workspace, "decision", "--json", json.dumps(card), machine=True)
    assert "decision --id D1 --choose" in proposed["next"] and "advance" not in proposed["next"]
    cli(project.workspace, "decision", "--id", "D1", "--choose", "Python", code=2)
    chosen = cli(
        project.workspace,
        "decision",
        "--id",
        "D1",
        "--choose",
        "Python",
        "--rationale",
        "Stdlib fits",
        machine=True,
    )
    assert project.spine().node("D1")["fields"]["chosen"] == "Python"
    assert project.spine().node("D1")["fields"]["rationale"] == "Stdlib fits"
    assert "advance --to plan" in chosen["next"]


def test_gap9_status_explains_spine_manifest_and_published_revisions(project):
    before = cli(project.workspace, "status", machine=True)
    for name in ("spine_revision", "manifest_revision", "published_revision"):
        assert name + ":" in before["text"] and name in before["status"]
    assert before["status"]["manifest_revision"] != before["status"]["spine_revision"]
    (project.workspace / "template/tests/test_answer.py").write_text("# changed protected proof\n")
    after = cli(project.workspace, "status", machine=True)["status"]
    assert after["spine_revision"] == before["status"]["spine_revision"]
    assert after["manifest_revision"] != before["status"]["manifest_revision"]
    assert not after["publication"]["up_to_date"]


def test_gap10_quality_and_publish_require_current_audit_and_review(project):
    confirmed_vision(project)
    project.connection.execute("UPDATE meta SET phase='publish'")
    assert check(project, "audit")["status"] == "fail"
    assert check(project, "roundtable")["status"] == "fail"
    missing = cli(project.workspace, "publish", machine=True, code=2)
    assert "passing audit" in missing["text"] and "successful review" in missing["text"]
    audit(project)
    assert check(project, "audit")["status"] == "pass"
    assert "successful review" in cli(project.workspace, "publish", code=2)
    review(project, REVIEWERS[:1])
    assert check(project, "roundtable")["status"] == "warn"
    assert "one provider" in check(project, "roundtable")["advice"]
    cli(project.workspace, "publish")
    assert project.status()["publication"]["up_to_date"]
    proof = project.workspace / "template/tests/test_answer.py"
    proof.write_text(proof.read_text() + "\n# revised protected file\n")
    assert check(project, "audit")["status"] == "fail"
    assert check(project, "roundtable")["status"] == "fail"
    cli(project.workspace, "publish", code=2)
    audit(project)
    review(project, REVIEWERS[:1])
    cli(project.workspace, "publish")
    assert project.status()["publication"]["up_to_date"]
