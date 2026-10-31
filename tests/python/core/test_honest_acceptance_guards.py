"""MOCKED source fixtures and native-shaped counterfactuals, never provider proof."""

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
