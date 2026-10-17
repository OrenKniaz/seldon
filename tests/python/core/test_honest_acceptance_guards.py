"""MOCKED source fixtures and native-shaped counterfactuals, never provider proof."""

import json

import pytest
from tests.python.core.test_acceptance import (
    fix_product,
    review,
    run_acceptance,
    to_accept,
)
from tests.python.core.test_acceptance import project as project

from codex_harness.core.plan_store import PlanStoreError


@pytest.fixture
def ready(request):
    store, repo = request.getfixturevalue("project")
    to_accept(store)  # Existing explicitly synthetic publication mechanics fixture.
    fix_product(repo)
    run = run_acceptance(store, repo, subject="product")
    assert run["passed"]
    return store, repo, run


def native_shaped_counterfactual(run_id):
    """Construct fresh SOURCE-ONLY counterfactual rows; never relabel injected review."""
    return {
        "id": "source-only-counterfactual-review",
        "runId": run_id,
        "isMocked": False,
        "sourceOnlyCounterfactual": True,
        "reviewers": [
            {
                "provider": provider,
                "shards": 1,
                "shardDetails": [
                    {"isMocked": False, "didRunProvider": True, "exitCode": 0, "invalid": 0}
                ],
            }
            for provider in ("codex", "claude")
        ],
    }


def save_counterfactual(store, evidence):
    assert evidence["sourceOnlyCounterfactual"] is True
    store.connection.execute(
        "INSERT INTO acceptance_reviews VALUES (?, ?)", (evidence["id"], json.dumps(evidence))
    )


def assert_refused(store, code):
    with pytest.raises(PlanStoreError) as error:
        store.accept(by="user", statement="SOURCE TEST ONLY; no actual product acceptance")
    assert error.value.code == code
    assert not store.connection.execute("SELECT 1 FROM acceptance_approvals").fetchone()


def test_injected_mocked_review_cannot_authorize_approval(ready):
    store, repo, _ = ready
    evidence = review(store, repo)
    assert evidence["isMocked"] is True
    assert_refused(store, "acceptance-review-required")


@pytest.mark.parametrize(
    "case",
    [
        "legacy-unknown",
        "shard-unknown",
        "not-observed",
        "failure",
        "invalid",
        "stale",
        "one-provider",
        "missing-shard",
        "no-shards",
        "bool-exit",
    ],
)
def test_nonadmissible_native_shaped_counterfactual_is_refused(ready, case):
    store, _, run = ready
    evidence = native_shaped_counterfactual(run["id"])
    shard = evidence["reviewers"][0]["shardDetails"][0]
    if case == "legacy-unknown":
        evidence.pop("isMocked")
    elif case == "shard-unknown":
        shard.pop("isMocked")
    elif case == "not-observed":
        shard["didRunProvider"] = False
    elif case == "failure":
        shard["exitCode"] = 2
    elif case == "invalid":
        shard["invalid"] = 1
    elif case == "stale":
        evidence["runId"] = "another-run"
    elif case == "one-provider":
        evidence["reviewers"][1]["provider"] = "codex"
    elif case == "missing-shard":
        evidence["reviewers"][0]["shards"] = 2
    elif case == "no-shards":
        evidence["reviewers"][0].update(shards=0, shardDetails=[])
    elif case == "bool-exit":
        shard["exitCode"] = False
    save_counterfactual(store, evidence)
    assert_refused(store, "acceptance-review-required")


def test_native_shaped_counterfactual_positive_without_packet_bound_policy(ready):
    store, _, run = ready
    evidence = native_shaped_counterfactual(run["id"])
    evidence["packetBound"] = False  # Existing binding is a label, not extra admission.
    save_counterfactual(store, evidence)
    approval = store.accept(
        by="user", statement="SOURCE-ONLY counterfactual; not actual acceptance"
    )
    assert approval["runId"] == run["id"]


def test_open_clarification_blocks_even_native_shaped_counterfactual(ready):
    store, _, run = ready
    evidence = native_shaped_counterfactual(run["id"])
    save_counterfactual(store, evidence)
    question = store.ask("SOURCE-ONLY unresolved final question", ["V1"])
    assert_refused(store, "plan-clarifications-open")
    store.answer(question, "Source-only answer")
    approval = store.accept(by="user", statement="SOURCE-ONLY positive after answer")
    assert approval["runId"] == run["id"]


@pytest.fixture
def public_rebound_source_counterfactual(ready):
    """Public rebind producer with explicit SOURCE-ONLY historical/native-shaped inputs."""
    from codex_harness.core.acceptance import _integrated_revision_v1

    store, repo, old_run = ready
    original = native_shaped_counterfactual(old_run["id"])
    save_counterfactual(store, original)
    store.accept(by="user", statement="SOURCE-ONLY historical approval counterfactual")
    # Same historical digest migration exercised by the existing legacy fixtures.
    legacy = dict(old_run, revision=_integrated_revision_v1(repo))
    legacy.pop("revisionAlgorithm")
    store.connection.execute(
        "UPDATE acceptance_runs SET result_json=? WHERE id=?", (json.dumps(legacy), legacy["id"])
    )
    rebound = store.accept_rebind(repo)
    assert rebound["reviews"][0]["originalReviewId"] == original["id"]
    assert "reviewers" not in rebound["reviews"][0]
    assert "isMocked" not in rebound["reviews"][0]
    return store, repo, original, rebound


def test_public_rebind_preserves_native_original_review_lineage(
    public_rebound_source_counterfactual,
):
    store, _, original, rebound = public_rebound_source_counterfactual
    assert store.acceptance_review_guard(rebound["run"]["id"]) is None
    assert store.status()["acceptance"]["accepted"] is True
    approval = store.accept(by="user", statement="SOURCE-ONLY rebound approval")
    assert approval["runId"] == rebound["run"]["id"]
    saved = json.loads(
        store.connection.execute(
            "SELECT result_json FROM acceptance_reviews WHERE id=?", (original["id"],)
        ).fetchone()[0]
    )
    assert saved == original  # Original native-shaped evidence was never rewritten.


@pytest.mark.parametrize(
    "case",
    ["mocked", "unknown", "missing", "cross-run", "missing-receipt", "mismatched-receipt", "cycle"],
)
def test_rebound_lineage_refuses_invalid_original_or_links(
    public_rebound_source_counterfactual, case
):
    store, _, original, rebound = public_rebound_source_counterfactual
    link = dict(rebound["reviews"][0])
    if case in {"mocked", "unknown", "cross-run"}:
        changed = dict(original)
        if case == "mocked":
            changed["isMocked"] = True
        elif case == "unknown":
            changed.pop("isMocked")
        else:
            changed["runId"] = "unrelated-run"
        store.connection.execute(
            "UPDATE acceptance_reviews SET result_json=? WHERE id=?",
            (json.dumps(changed), original["id"]),
        )
    elif case == "missing":
        store.connection.execute("DELETE FROM acceptance_reviews WHERE id=?", (original["id"],))
    elif case in {"missing-receipt", "mismatched-receipt"}:
        # Preserve append-only historical receipts. A newly manufactured reference
        # must not borrow the genuine producer's receipt for a different row id.
        store.connection.execute("DELETE FROM acceptance_reviews WHERE id=?", (link["id"],))
        link["id"] = "source-only-unrecorded-reference"
        store.connection.execute(
            "INSERT INTO acceptance_reviews VALUES (?, ?)", (link["id"], json.dumps(link))
        )
        if case == "mismatched-receipt":
            wrong_run = dict(rebound["run"], id="mismatched-run")
            store._event(
                "acceptance-rebound",
                {
                    "sourceOnlyCounterfactual": True,
                    "by": "harness",
                    "run": wrong_run,
                    "reviews": [link],
                    "reboundFrom": link["reboundFrom"],
                },
            )
    else:
        # A second reference and appended counterfactual receipt form a two-run
        # cycle without deleting or altering the genuine public producer event.
        previous = json.loads(
            store.connection.execute(
                "SELECT result_json FROM acceptance_runs WHERE id=?", (original["runId"],)
            ).fetchone()[0]
        )
        cycle = dict(
            link,
            id=original["id"],
            runId=original["runId"],
            originalReviewId=link["id"],
            reboundFrom=link["runId"],
        )
        store.connection.execute(
            "UPDATE acceptance_reviews SET result_json=? WHERE id=?",
            (json.dumps(cycle), cycle["id"]),
        )
        store._event(
            "acceptance-rebound",
            {
                "sourceOnlyCounterfactual": True,
                "by": "harness",
                "run": previous,
                "reviews": [cycle],
                "reboundFrom": cycle["reboundFrom"],
            },
        )
    assert store.acceptance_review_guard(rebound["run"]["id"]) == "acceptance-review-required"
    status = store.status()["acceptance"]
    assert status["accepted"] is False
    assert status["invalidReason"] == "acceptance-review-required"
    with pytest.raises(PlanStoreError) as error:
        store.accept(by="user", statement="SOURCE-ONLY invalid lineage must refuse")
    assert error.value.code == "acceptance-review-required"
    saved_approval = json.loads(
        store.connection.execute(
            "SELECT result_json FROM acceptance_approvals ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]
    )
    # Retained historical approval is provenance, never current acceptance credit.
    assert saved_approval == rebound["approval"]


def test_open_question_still_blocks_public_rebound_lineage(public_rebound_source_counterfactual):
    store, _, _, rebound = public_rebound_source_counterfactual
    store.ask("SOURCE-ONLY unresolved question after rebind", ["V1"])
    assert store.acceptance_review_guard(rebound["run"]["id"]) == "plan-clarifications-open"
