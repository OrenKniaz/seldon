"""Source packet regressions; injected reviewers are never native-provider proof."""

import json

import pytest
from tests.python.core.test_acceptance import REVIEWERS, fix_product
from tests.python.core.test_acceptance import project as _project

from codex_harness.core import acceptance, roundtable
from codex_harness.core.acceptance import acceptance_review, run_acceptance
from codex_harness.core.plan_store import PlanStoreError


@pytest.fixture
def project(tmp_path):
    yield from _project.__wrapped__(tmp_path)


def candidate(store, repo, *, large=False, reviewers=None):
    reviewers = reviewers or REVIEWERS
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
    roundtable.run_roundtable(store, reviewers=reviewers, runner=lambda *_: (0, json.dumps(items)))
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
    import shutil
    from pathlib import Path

    from tests.python.core.planning_support import source_only_b

    template = store.workspace / "template"
    if not template.is_dir():
        shutil.copytree(
            Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/template", template
        )
    source_only_b(store, reviewers=reviewers)
    return run_acceptance(store, repo, subject="product")


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
