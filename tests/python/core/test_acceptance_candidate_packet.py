"""Source packet regressions; injected reviewers are never native-provider proof."""

import hashlib
import json

import pytest
from tests.python.core.test_acceptance import REVIEWERS, fix_product
from tests.python.core.test_acceptance import project as _project

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
        assert {row["status"] for row in evidence["planningFindings"]} == {
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
            assert row["id"] in packet and row["finding"] in packet
            if row["resolution"]:
                assert row["resolution"] in packet
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
