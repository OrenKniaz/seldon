"""Source-only review-start ceilings; no provider, host or human evidence."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.python.core.test_roundtable import REVIEWERS, reviewer_stream
from tests.python.core.test_roundtable import store as source_store

from codex_harness.core import plan_cli, roundtable
from codex_harness.core import provider_call_budget as budget
from codex_harness.core.plan_store import PlanStore


@pytest.fixture
def store(tmp_path):
    yield from source_store.__wrapped__(tmp_path)


def register(store, cap=10):
    return budget.register_planning_review_budget(store, "P1-source-only", cap)


def claimed(store):
    return budget.planning_review_budget(store)["spentAdmissions"]


def test_public_prepare_is_no_launch_and_immutable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: pytest.fail("provider launch"))
    argv = [
        "--workspace",
        str(tmp_path),
        "review-budget",
        "--machine-json",
        "--campaign",
        "P1-source-only",
        "--max-starts",
        "10",
    ]
    assert plan_cli.main(argv) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["campaign"] == "P1-source-only" and data["spentAdmissions"] == 0
    assert plan_cli.main(argv) == 0
    capsys.readouterr()
    assert plan_cli.main(argv[:-1] + ["9"]) == 2
    capsys.readouterr()
    with PlanStore(tmp_path) as current:
        assert claimed(current) == 0
        with pytest.raises(ValueError, match="planning-review-budget"):
            budget.register_planning_review_budget(current, "replacement", 10)


def test_ten_starts_sticky_across_reopen_revision_and_reviewer(tmp_path):
    with PlanStore(tmp_path) as current:
        register(current)
        for index in range(5):
            assert budget.claim_planning_review_start(current, REVIEWERS[0]) == index + 1
        current.upsert_node(
            dict(
                id="V1", kind="vision", title="Changed", text="New revision", fields={}, parents=[]
            )
        )
    with PlanStore(tmp_path) as current:
        for index in range(5, 10):
            assert budget.claim_planning_review_start(current, REVIEWERS[1]) == index + 1
        with pytest.raises(ValueError, match="planning-review-start-cap-exhausted"):
            budget.claim_planning_review_start(current, REVIEWERS[0])
        assert claimed(current) == 10


@pytest.mark.parametrize(
    "damage",
    [
        "registration-missing",
        "registration-bad",
        "claim-missing",
        "claim-bad",
        "registration-event-missing",
    ],
)
def test_configured_missing_or_corrupt_original_cannot_reset(tmp_path, damage):
    with PlanStore(tmp_path) as current:
        root = current.path.parent / "planning-review-budget"
        if damage == "registration-event-missing":
            root.mkdir()
            (root / "claims").mkdir()
            (root / "registration.json").write_text("{}", encoding="utf-8")
        else:
            register(current)
            budget.claim_planning_review_start(current, REVIEWERS[0])
            original = root / (
                "registration.json" if damage.startswith("registration") else "claims/00000001.json"
            )
            if damage.endswith("missing"):
                original.unlink()
            else:
                original.write_text('{"changed": true}', encoding="utf-8")
        with pytest.raises((ValueError, OSError), match="planning-review-budget"):
            budget.claim_planning_review_start(current, REVIEWERS[0])
        with pytest.raises((ValueError, OSError), match="planning-review-budget"):
            register(current)


def test_concurrent_original_claims_never_exceed_ten(tmp_path):
    with PlanStore(tmp_path) as current:
        register(current)

    def attempt(_):
        with PlanStore(tmp_path) as current:
            try:
                return budget.claim_planning_review_start(current, REVIEWERS[0])
            except ValueError as error:
                assert "planning-review-start-cap-exhausted" in str(error)
                return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(14)))
    assert sorted(value for value in results if value is not None) == list(range(1, 11))
    with PlanStore(tmp_path) as current:
        assert claimed(current) == 10


def test_ordinary_overflow_reshards_stop_before_eleventh_child(store, monkeypatch):
    register(store)
    calls = []
    shard = dict(
        packet="source-only",
        hash="fixed",
        nodeIds=["R1"],
        features=[],
        truncatedNodes=[],
        contextNodeIds=[],
        estimatedTokens=1,
    )

    def shards(*args, token_budget=None, **kwargs):
        return [dict(shard)] * (1 if token_budget is None else 7)

    def child(launch, cwd, timeout):
        calls.append(launch)
        if len(calls) in (1, 9):
            raise ValueError("maximum context tokens exceeded")
        return 0, reviewer_stream("codex", [])

    monkeypatch.setattr(roundtable, "packet_shards", shards)
    monkeypatch.setattr(roundtable, "_run", child)
    reviewers = [dict(REVIEWERS[0], name="first"), dict(REVIEWERS[0], name="second")]
    with pytest.raises(ValueError, match="planning-review-start-cap-exhausted"):
        roundtable.run_roundtable(
            store, reviewers=reviewers, executable_for={"codex": "source-only-codex"}, max_rounds=1
        )
    assert len(calls) == claimed(store) == 10


def test_ordinary_repeat_without_flags_uses_original(store, monkeypatch):
    register(store, 1)
    calls = []
    monkeypatch.setattr(
        roundtable, "_run", lambda *a, **k: (calls.append(a) or (0, reviewer_stream("codex", [])))
    )
    for index in range(2):
        if index == 0:
            roundtable.run_roundtable(
                store, reviewers=[REVIEWERS[0]], executable_for={"codex": "source-only-codex"}
            )
        else:
            with pytest.raises(ValueError, match="planning-review-start-cap-exhausted"):
                roundtable.run_roundtable(
                    store,
                    reviewers=[dict(REVIEWERS[0], name="renamed")],
                    executable_for={"codex": "source-only-codex"},
                )
    assert len(calls) == claimed(store) == 1


def test_failed_child_claim_not_refunded(store, monkeypatch):
    register(store, 1)
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: (_ for _ in ()).throw(OSError("lost")))
    roundtable.run_roundtable(
        store, reviewers=[REVIEWERS[0]], executable_for={"codex": "source-only-codex"}
    )
    assert claimed(store) == 1
    with pytest.raises(ValueError, match="planning-review-start-cap-exhausted"):
        budget.claim_planning_review_start(store, REVIEWERS[0])


def test_unconfigured_legacy_and_injected_runner_are_unchanged(store):
    assert budget.claim_planning_review_start(store, REVIEWERS[0]) is None
    register(store)
    roundtable.run_roundtable(
        store, reviewers=[REVIEWERS[0]], runner=lambda *a: (0, reviewer_stream("codex", []))
    )
    assert claimed(store) == 0


@pytest.mark.parametrize("exhausted", [False, True])
def test_canonical_real_child_uses_same_ceiling(store, monkeypatch, exhausted):
    register(store, 1)
    if exhausted:
        budget.claim_planning_review_start(store, REVIEWERS[0])
    context = {"reviewerExecutable": {"path": "source-only-codex"}}
    header = {
        "context": context,
        "packet": "source-only",
        "launch": {},
        "registrationId": "source-only-registration",
    }
    monkeypatch.setattr(store, "register_execution_assessment", lambda **k: header)
    monkeypatch.setattr(store, "execution_assessment_state", lambda: {"held": True})
    monkeypatch.setattr(store, "commit_execution_assessment", lambda *a: {"sourceOnly": True})
    monkeypatch.setattr(roundtable, "_execution_context", lambda *a: context)
    monkeypatch.setattr(roundtable, "_execution_events", lambda *a: [])
    monkeypatch.setattr(roundtable, "_execution_capture", lambda *a: None)
    from codex_harness.core import acceptance_recovery

    monkeypatch.setattr(acceptance_recovery, "_launch_identity", lambda *a: {})
    calls = []
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: (calls.append(a) or (0, b"", b"")))
    result = roundtable.assess_execution_findings(store, reviewer=REVIEWERS[0], timeout=1)
    assert claimed(store) == 1 and len(calls) == (0 if exhausted else 1)
    if exhausted:
        assert result == {"held": True}
        error = store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='execution-assessment-observation-error'"
        ).fetchone()
        assert "planning-review-start-cap-exhausted" in error[0]
    else:
        assert result == {"sourceOnly": True}


def test_public_roundtable_omitted_flags_still_capped(store, monkeypatch, capsys):
    base = ["--workspace", str(store.workspace)]
    assert (
        plan_cli.main(
            base
            + [
                "review-budget",
                "--campaign",
                "P1-source-only",
                "--max-starts",
                "1",
                "--machine-json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    calls = []
    monkeypatch.setattr(
        roundtable, "_run", lambda *a, **k: (calls.append(a) or (0, reviewer_stream("codex", [])))
    )
    monkeypatch.setattr(roundtable, "_executable", lambda *a: "source-only-codex")
    for index in range(2):
        reviewer = dict(REVIEWERS[0], name=f"public-{index}")
        result = plan_cli.main(
            base
            + [
                "roundtable",
                "--reviewer",
                json.dumps(reviewer),
                "--max-rounds",
                "1",
                "--machine-json",
            ]
        )
        output = json.loads(capsys.readouterr().out)
        assert result == (0 if index == 0 else 2)
        if index:
            assert "planning-review-start-cap-exhausted" in output["text"]
    assert len(calls) == claimed(store) == 1


def test_configured_missing_original_holds_before_ordinary_child(store, monkeypatch):
    register(store)
    (store.path.parent / "planning-review-budget" / "registration.json").unlink()
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: pytest.fail("provider launch"))
    with pytest.raises(ValueError, match="planning-review-budget-invalid-original"):
        roundtable.run_roundtable(
            store, reviewers=[REVIEWERS[0]], executable_for={"codex": "source-only-codex"}
        )


def test_existing_support_refusal_precedes_budget_admission(store, monkeypatch):
    register(store)
    monkeypatch.setattr(roundtable, "_run", lambda *a, **k: pytest.fail("provider launch"))

    def refused():
        raise ValueError("source-only support hold")

    with pytest.raises(ValueError, match="source-only support hold"):
        roundtable._review_shard(
            store,
            {"roundId": "source-only"},
            REVIEWERS[0],
            roundtable.packet_shards(store, "codex")[0],
            executable="source-only-codex",
            runner=None,
            timeout=1,
            before_launch=refused,
        )
    assert claimed(store) == 0


@pytest.mark.parametrize("exhausted", [False, True])
def test_public_acceptance_reviewer_counts_same_project_budget(tmp_path, monkeypatch, exhausted):
    import sys

    from tests.python.core.test_acceptance_recovery import (
        REVIEWERS as source_reviewers,
    )
    from tests.python.core.test_acceptance_recovery import (
        candidate,
        project,
        protocol,
    )

    from codex_harness.core import acceptance

    fixture = project.__wrapped__(tmp_path)
    current, repo = next(fixture)
    try:
        candidate(current, repo)
        register(current, 2)
        if exhausted:
            budget.claim_planning_review_start(current, REVIEWERS[0])
            budget.claim_planning_review_start(current, REVIEWERS[1])
        calls = []
        monkeypatch.setattr(roundtable, "_executable", lambda *a: sys.executable)

        def child(*args, **kwargs):
            provider = source_reviewers[len(calls)]["provider"]
            calls.append(args)
            return protocol(provider, text="[]")

        monkeypatch.setattr(roundtable, "_run", child)
        if exhausted:
            with pytest.raises(ValueError, match="planning-review-start-cap-exhausted"):
                acceptance.acceptance_review(current, repo, reviewers=source_reviewers)
        else:
            acceptance.acceptance_review(current, repo, reviewers=source_reviewers)
        assert len(calls) == (0 if exhausted else 2)
        assert claimed(current) == 2
    finally:
        fixture.close()
