"""Source-only cockpit mechanics from disposable stores; no providers or UI proof."""

import json
import shutil

from tests.python.core.planning_support import (
    source_only_confirmation,
    source_only_expansion,
)

from codex_harness.core.plan_store import PlanStore
from codex_harness.core.roundtable import run_roundtable
from codex_harness.core.status_api import snapshot


def node(identity, kind, parents, **fields):
    return dict(
        id=identity,
        kind=kind,
        title=identity,
        text=f"Recorded {identity}",
        parents=parents,
        fields=fields,
    )


def tree(root):
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def recorded_review_snapshots(tmp_path):
    """Real persistence, fixed injected returns; no provider or effective-model proof."""
    from tests.python.core.test_roundtable import FIXTURE

    from codex_harness.core.roundtable import findings

    workspace = tmp_path / "recorded-reviews"
    workspace.mkdir()
    with PlanStore(workspace) as store:
        store.import_spine(FIXTURE)
        source_only_expansion(store)
        source_only_confirmation(store, "align", statement="Approve")
        store.advance("expand", by="planner")
        reviewers = [
            dict(name="Review Alpha", provider="codex", model="requested-alpha"),
            dict(name="Review Beta", provider="claude", model="requested-beta"),
        ]
        run_roundtable(
            store,
            reviewers=reviewers,
            executable_for=lambda provider: provider,
            runner=lambda launch, *_: (0, "[]") if launch.cli == "codex" else (7, "[]"),
        )
        error = snapshot(workspace=workspace, runs_root=tmp_path / "absent-runs")
        bead = store.spine().node(store.spine().ids("bead")[0])
        bead["title"] += " revised"
        store.upsert_node(bead)
        template = workspace / "template"
        shutil.copytree(FIXTURE.parent / "template", template)
        store.compile_template(template)

        def response(launch, *_):
            rows = [
                dict(
                    node_id="R1",
                    severity="major",
                    finding="Shared date boundary gap",
                    suggestion="Specify the local date boundary",
                )
            ]
            if launch.cli == "codex":
                rows.extend(
                    dict(node_id="R1", severity="minor", finding=text, suggestion="Record behavior")
                    for text in (
                        "Atomic save interrupted",
                        "Unicode names policy",
                        "Archive history rules",
                    )
                )
            return 0, json.dumps(rows)

        result = run_roundtable(
            store, reviewers=reviewers, runner=response, executable_for=lambda provider: provider
        )
        current = [row for row in findings(store) if row["round_id"] == result["roundId"]]
        for row, disposition in zip(
            [row for row in current if row["memberCount"] == 1],
            ("resolved", "rejected", "deferred"),
            strict=True,
        ):
            store.resolve_findings(
                [
                    dict(
                        id=row["id"],
                        status=disposition,
                        resolution=f"Recorded {disposition} reason",
                    )
                ]
            )
        ready = snapshot(workspace=workspace, runs_root=tmp_path / "absent-runs")
        question = store.ask("Which timezone should the product use?", ["R1"])
        asked = snapshot(workspace=workspace)
        store.answer(question, "Local timezone")
        waived = store.ask("Should old names be kept?", ["R1"])
        store.waive(waived, "Already specified")
        answered = snapshot(workspace=workspace, runs_root=tmp_path / "absent-runs")
    return workspace, dict(error=error, ready=ready, asked=asked, answered=answered)


def test_recorded_reviews_and_plain_summary_from_actual_store_are_read_only(tmp_path):
    """Protect useful recorded detail; raw gate headlines and lost attribution were the gap.

    Strongest boundary: real store/roundtable -> RO snapshot, including revision
    change and dispositions. Injected reviewer returns are explicit source-only evidence.
    """
    workspace, samples = recorded_review_snapshots(tmp_path)
    assert samples["error"]["cockpit"]["answers"]["rightNow"]["code"] == "recorded-review-error"
    ready = samples["ready"]["cockpit"]
    assert ready["answers"]["rightNow"]["text"] == "1 recorded review points still need a response."
    reviews = ready["recordedReviews"]
    assert reviews["availability"] == "available" and reviews["unboundRowCount"] == 0
    current = next(row for row in reviews["rounds"] if row["relevance"] == "current")
    assert current["outcome"] == "completed"
    assert current["reviewedManifestRevision"] == samples["ready"]["plan"]["manifest_revision"]
    assert current["reviewedManifestRevision"] != reviews["sourceRevision"]
    assert [r["requestedModel"] for r in current["reviewers"]] == [
        "requested-alpha",
        "requested-beta",
    ]
    assert all(r["isMocked"] is True and r["didRunProvider"] is False for r in current["reviewers"])
    shared = next(cluster for cluster in current["clusters"] if len(cluster["members"]) == 2)
    assert {member["reviewer"] for member in shared["members"]} == {"Review Alpha", "Review Beta"}
    members = [member for cluster in current["clusters"] for member in cluster["members"]]
    assert {member["disposition"] for member in members} == {
        "open",
        "resolved",
        "rejected",
        "deferred",
    }
    assert {member["reason"] for member in members if member["reason"]} == {
        "Recorded resolved reason",
        "Recorded rejected reason",
        "Recorded deferred reason",
    }
    old = next(row for row in reviews["rounds"] if row["relevance"] == "historical")
    assert old["outcome"] == "error" and old["reviewers"][0]["recordedFindingCount"] == 0
    assert samples["asked"]["cockpit"]["answers"]["rightNow"]["code"] == "recorded-questions"
    assert (
        samples["asked"]["cockpit"]["answers"]["needsYou"][0]["question"]
        == "Which timezone should the product use?"
    )
    assert samples["answered"]["cockpit"]["answers"]["needsYou"] == []
    before = tree(workspace)
    for _ in range(2):
        again = snapshot(workspace=workspace, runs_root=tmp_path / "absent-runs")
        assert again["cockpit"]["recordedReviews"] == reviews
    assert tree(workspace) == before
    public = json.dumps(reviews)
    assert not any(
        name in public
        for name in ("shardDetails", "stderr", "artifactPath", "processId", "request_id")
    )
