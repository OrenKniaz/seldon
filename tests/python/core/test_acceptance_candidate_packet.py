"""Source packet regressions; injected reviewers are never native-provider proof."""

import hashlib
import json

import pytest
from tests.python.core.test_acceptance import REVIEWERS, fix_product
from tests.python.core.test_acceptance import project as _project
from tests.python.core.test_honest_acceptance_guards import (
    native_shaped_counterfactual,
    save_counterfactual,
)

from codex_harness.core import acceptance, roundtable
from codex_harness.core.acceptance import acceptance_review, acceptance_status, run_acceptance
from codex_harness.core.plan_store import PlanStoreError


@pytest.fixture
def project(tmp_path):
    yield from _project.__wrapped__(tmp_path)


def candidate(store, repo, *, large=False):
    bead = store.spine().node("A")
    bead["fields"]["ownedFiles"] = ["tiny.py", ".env", "notes.txt"]
    store.upsert_node(bead)
    if large:
        for key in ("R1", "R2"):
            node = store.spine().node(key)
            node["text"] += "acceptance detail " * 5000
            store.upsert_node(node)
    fix_product(repo)
    (repo / "tiny.py").write_bytes(b"print('Added 250 ml.')\r\n# exact candidate source\r\n")
    (repo / ".env").write_text("PACKET_SECRET_MUST_STAY_HIDDEN")
    (repo / "notes.txt").write_text("UNRELATED_OWNED_PROSE")
    (repo / "unowned.py").write_text("UNOWNED_SOURCE_MUST_STAY_HIDDEN")
    items = [
        {"node_id": "R1", "severity": "note", "finding": f"Existing {status} gap", "suggestion": ""}
        for status in ("open", "resolved", "rejected", "deferred")
    ]
    roundtable.run_roundtable(store, reviewers=REVIEWERS, runner=lambda *_: (0, json.dumps(items)))
    for row in roundtable.findings(store):
        status = row["finding"].split()[1]
        if status != "open":
            store.resolve_findings(
                [
                    {
                        "id": row["id"],
                        "status": status,
                        "resolution": f"Exact reason for {status}; not a vision waiver",
                    }
                ]
            )
    return run_acceptance(store, repo, subject="product")


@pytest.mark.parametrize("large", [False, True])
def test_every_acceptance_shard_contains_current_source_and_existing_dispositions(project, large):
    store, repo = project
    run = candidate(store, repo, large=large)
    packets = []

    def runner(launch, *_):
        packets.append(launch.stdin_text)
        return 0, "[]"

    result = acceptance_review(
        store,
        repo,
        reviewers=REVIEWERS,
        runner=runner,
        token_budgets={provider: 50_000 for provider in ("codex", "claude")},
    )
    assert len(packets) > 2 if large else len(packets) == 2
    digest = hashlib.sha256((repo / "tiny.py").read_bytes()).hexdigest()
    for packet in packets:
        assert "## Read-only candidate evidence" in packet
        start = packet.index("{", packet.index("## Read-only candidate evidence"))
        evidence, _ = json.JSONDecoder().raw_decode(packet[start:])
        assert evidence["manifestRevision"] == store.manifest_revision()
        assert evidence["runId"] == run["id"] and evidence["productRevision"] == run["revision"]
        source = next(row for row in evidence["implementation"] if row["path"] == "tiny.py")
        assert source["source"].encode("utf-8") == (repo / "tiny.py").read_bytes()
        assert source["sha256"] == digest and source["owners"] == ["A"]
        assert {
            row["status"]
            for row in [
                *evidence["planningFindings"]["active"],
                *evidence["planningFindings"]["closed"],
            ]
        } == {
            "open",
            "resolved",
            "rejected",
            "deferred",
        }
        assert digest in packet and "exact candidate source" in packet
        assert run["revision"] in packet and run["id"] in packet
        assert "Deferred findings are recorded gaps, not vision waivers" in packet
        assert "PACKET_SECRET_MUST_STAY_HIDDEN" not in packet
        assert "UNRELATED_OWNED_PROSE" not in packet
        assert "UNOWNED_SOURCE_MUST_STAY_HIDDEN" not in packet
        for row in roundtable.findings(store, expand=True):
            assert row["id"] in packet
            if row["status"] in ("open", "deferred"):
                assert row["finding"] in packet
                if row["resolution"]:
                    assert row["resolution"] in packet
            else:
                assert row["finding"] not in packet and row["resolution"] not in packet
    assert result["isMocked"] is True
    assert all(
        shard["isMocked"] is True and shard["didRunProvider"] is False
        for reviewer in result["reviewers"]
        for shard in reviewer["shardDetails"]
    )
    assert acceptance_status(store)["accepted"] is False


def test_stale_candidate_refuses_before_reviewer_or_new_evidence(project):
    store, repo = project
    candidate(store, repo)
    (repo / "tiny.py").write_text("print('changed')\n")
    calls = []
    with pytest.raises(PlanStoreError, match="Run scenarios on the current revision"):
        acceptance_review(store, repo, reviewers=REVIEWERS, runner=lambda *_: calls.append(True))
    assert calls == []
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_reviews").fetchone()[0] == 0


def test_too_small_packet_budget_refuses_without_provider_or_admission(project):
    store, repo = project
    candidate(store, repo)
    calls = []
    with pytest.raises(ValueError):
        acceptance_review(
            store,
            repo,
            reviewers=REVIEWERS,
            runner=lambda *_: calls.append(True),
            token_budgets={"codex": 1, "claude": 1},
        )
    assert calls == []
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_reviews").fetchone()[0] == 0


def test_candidate_changed_while_building_packet_refuses_before_launch(project, monkeypatch):
    store, repo = project
    candidate(store, repo)
    original = acceptance._candidate_evidence
    calls = []

    def changed(*args):
        evidence = original(*args)
        (repo / "tiny.py").write_text("print('new candidate')\n")
        return evidence

    monkeypatch.setattr(acceptance, "_candidate_evidence", changed)
    with pytest.raises(PlanStoreError, match="Rerun acceptance review"):
        acceptance_review(store, repo, reviewers=REVIEWERS, runner=lambda *_: calls.append(True))
    assert calls == []
    assert store.connection.execute("SELECT COUNT(*) FROM acceptance_reviews").fetchone()[0] == 0


def test_controller_launch_branch_remains_distinct_from_injected_review(project, monkeypatch):
    """Stub the controller launch for branch coverage; this is not native-provider evidence."""
    store, repo = project
    candidate(store, repo)
    calls = []
    monkeypatch.setattr(roundtable, "_executable", lambda *_: "source-test-provider")

    def source_launch_stub(launch, *_):
        calls.append(launch.stdin_text)
        return 0, "[]", ""

    monkeypatch.setattr(roundtable, "_run", source_launch_stub)
    result = acceptance_review(store, repo, reviewers=REVIEWERS)
    assert len(calls) == 2 and all("## Read-only candidate evidence" in packet for packet in calls)
    assert result["isMocked"] is False
    assert all(
        shard["isMocked"] is False and shard["didRunProvider"] is True
        for reviewer in result["reviewers"]
        for shard in reviewer["shardDetails"]
    )
    assert acceptance_status(store)["accepted"] is False


def historical_approval(store, run):
    """SOURCE-ONLY old-history counterfactual, never an actual user approval."""
    row = {
        "id": "source-only-old-approval",
        "runId": run["id"],
        "by": "user",
        "statement": "SOURCE UNIT HISTORY FIXTURE ONLY",
        "isProxy": False,
        "sourceOnlyCounterfactual": True,
    }
    store.connection.execute(
        "INSERT INTO acceptance_approvals VALUES (?, ?)", (row["id"], json.dumps(row))
    )


@pytest.mark.parametrize("kind", ["mocked", "unknown", "native-shaped"])
def test_legacy_approval_status_uses_same_review_eligibility_guard(project, kind):
    store, repo = project
    run = candidate(store, repo)
    if kind == "mocked":
        review = acceptance_review(store, repo, reviewers=REVIEWERS, runner=lambda *_: (0, "[]"))
        assert review["isMocked"] is True
    else:
        # A fresh labelled fixture, never an injected row rewritten to real.
        review = native_shaped_counterfactual(run["id"])
        if kind == "unknown":
            review.pop("isMocked")
        save_counterfactual(store, review)
    historical_approval(store, run)
    status = acceptance_status(store)
    assert status["accepted"] is (kind == "native-shaped")
    assert status["invalidReason"] == (
        None if kind == "native-shaped" else "acceptance-review-required"
    )


def test_open_question_invalidates_old_approval_until_supported_answer(project):
    store, repo = project
    run = candidate(store, repo)
    save_counterfactual(store, native_shaped_counterfactual(run["id"]))
    historical_approval(store, run)
    assert acceptance_status(store)["accepted"] is True  # Counterfactual native metadata only.
    question = store.ask("Required date correctness remains unresolved", ["R1"])
    status = acceptance_status(store)
    assert status["accepted"] is False and status["invalidReason"] == "plan-clarifications-open"
    assert "harness_answer" in status["next"] and status["invalidMessage"]
    store.answer(question, "SOURCE TEST ONLY; required issue now resolved")
    assert acceptance_status(store)["accepted"] is True


@pytest.mark.parametrize("large", [False, True])
def test_packet_has_open_questions_and_closed_hashes_without_closed_bodies(project, large):
    store, repo = project
    candidate(store, repo, large=large)
    opened = store.ask("Required daily correctness question", ["R1"])
    answered = store.ask("ANSWERED_HISTORY_MUST_NOT_BE_DUMPED", ["R2"])
    store.answer(answered, "Old answered history")
    packets = []
    acceptance_review(
        store,
        repo,
        reviewers=REVIEWERS,
        runner=lambda launch, *_: (packets.append(launch.stdin_text) or 0, "[]"),
        token_budgets={"codex": 50_000, "claude": 50_000},
    )
    assert len(packets) > 2 if large else len(packets) == 2
    rows = roundtable.findings(store, expand=True)
    for packet in packets:
        start = packet.index("{", packet.index("## Read-only candidate evidence"))
        evidence, _ = json.JSONDecoder().raw_decode(packet[start:])
        assert evidence["openClarifications"] == [
            {
                "id": opened,
                "question": "Required daily correctness question",
                "node_ids": ["R1"],
                "status": "open",
            }
        ]
        assert "ANSWERED_HISTORY_MUST_NOT_BE_DUMPED" not in packet
        findings = evidence["planningFindings"]
        assert findings["active"] == [row for row in rows if row["status"] in ("open", "deferred")]
        assert (
            findings["closedHashAlgorithm"]
            == "sha256-json-sort_keys-ensure_ascii_false-compact-utf8"
        )
        assert findings["closedCounts"] == {
            status: sum(row["status"] == status for row in rows)
            for status in ("resolved", "rejected")
        }
        for row in rows:
            if row["status"] in ("resolved", "rejected"):
                digest = hashlib.sha256(
                    json.dumps(
                        row, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                ).hexdigest()
                assert {"id": row["id"], "status": row["status"], "sha256": digest} in findings[
                    "closed"
                ]
                assert row["finding"] not in packet and row["resolution"] not in packet
