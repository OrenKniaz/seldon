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
