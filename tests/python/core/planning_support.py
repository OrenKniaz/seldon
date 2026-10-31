"""Source-only export mechanics fixtures, never end-to-end review-backed publication.

The test-local evidence double retains real audit and mocked-review assertions.
It never patches stale approval, clarification or finding admission guards.
"""

from contextlib import contextmanager
from pathlib import Path
from types import MethodType
from unittest.mock import patch

from codex_harness.core.plan_store import PlanStore
from codex_harness.core.planner_confirmations import complete_confirmation, prepare_confirmation
from codex_harness.core.roundtable import run_roundtable
from codex_harness.core.spine import compile_manifest
from codex_harness.core.test_audit import audit_revised_tests

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"


def source_only_expansion(store):
    """Synthetic proposed choices, never user consent or product acceptance."""
    vision = store.spine().node(store.spine().ids("vision")[0])
    vision["fields"].update(
        quality_bar="Retain every cent",
        done_feels_like="Shares add up",
        expansion=dict(
            mode="quality",
            reason="Precise CLI scope",
            upgrades=[
                dict(
                    id=str(i),
                    text=f"Upgrade {i}",
                    kind="risk",
                    impact=f"Impact {i}",
                    disposition=choice,
                )
                for i, choice in enumerate(("accept", "reject", "defer"), 1)
            ],
        ),
    )
    store.upsert_node(vision)


def source_only_confirmation(store, gate, *, statement="Approve"):
    """Exercise real gate storage with a synthetic client form, never human/runtime proof.

    No production approval guard is patched; callers must be at the actual gate.
    Native transport correlation is separately tested by the MCP bridge tests.
    """
    request = prepare_confirmation(store, gate)
    result = complete_confirmation(
        store,
        request["request_id"],
        "accept",
        {"approve": True, "statement": statement, "conditions": ""},
    )
    assert result["state"] == "approved" and not result["human_identity_verified"]
    return result["approval_id"]


def source_only_vision_lock(store, *, statement="Approve"):
    """Drive initial fixture alignment/lock through real phase and native-store contracts."""
    if "expansion" not in store.spine().node(store.spine().ids("vision")[0])["fields"]:
        source_only_expansion(store)
    if store.phase() == "align":
        source_only_confirmation(store, "align")
        store.advance("expand", by="source-test")
    if store.phase() == "expand":
        store.advance("vision-lock", by="source-test")
    return source_only_confirmation(store, "vision-lock", statement=statement)


def _source_only_evidence_gate(store, revision):
    assert store.passing_audit(revision), "Source export mechanics still require a passing audit"
    rows = list(
        store.connection.execute(
            "SELECT isMocked, didRunProvider FROM review_runs WHERE plan_revision=? "
            "AND exit_code=0 AND invalid=0",
            (revision,),
        )
    )
    assert rows and all(row["isMocked"] == 1 and row["didRunProvider"] == 0 for row in rows)
    assert not store.successful_reviews(revision), "Synthetic evidence must never earn real credit"


@contextmanager
def source_only_publication():
    """Scope CLI export/phase mechanics to synthetic evidence; retain all other gates."""
    with patch.object(PlanStore, "_check_evidence", _source_only_evidence_gate):
        yield


def publication_evidence(store):
    """Prepare synthetic reviews and double only this store's evidence gate until close."""
    revision = store.manifest_revision()
    if not store.passing_audit(revision):
        template = store.workspace / "template"
        if not template.is_dir():
            template = FIXTURE / "template"
        manifest = compile_manifest(
            store.spine(), template if template == store.workspace / "template" else None
        )
        result = audit_revised_tests(
            repo=store.workspace,
            base_ref="template",
            revised_template=template,
            manifest=manifest,
            impact={"affectedBeads": store.spine().ids("bead")},
            oracle_dir=FIXTURE / "reference",
        )
        assert result["ok"], result
        store.record_audit(revision, result)
    exists = store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_runs'"
    ).fetchone()
    recorded = (
        exists
        and store.connection.execute(
            "SELECT 1 FROM review_runs WHERE plan_revision=? AND isMocked=1 "
            "AND didRunProvider=0 AND exit_code=0 AND invalid=0",
            (revision,),
        ).fetchone()
    )
    if not recorded:
        run_roundtable(
            store,
            reviewers=[
                {"name": "fixture-codex", "provider": "codex"},
                {"name": "fixture-claude", "provider": "claude"},
            ],
            runner=lambda *args: (0, "[]"),
            executable_for=lambda provider: provider,
        )

    store._check_evidence = MethodType(_source_only_evidence_gate, store)


def source_controller_roundtable(store, *, process_stub, **kwargs):
    """Simulate the controller API branch; this does not prove actual provider execution.

    Unlike runner injection this exercises the production launch consumer. Existing
    synthetic database rows are never relabeled or granted real-review credit.
    """
    from codex_harness.core import roundtable

    kwargs["reviewers"] = source_only_b(store, reviewers=kwargs.get("reviewers"))
    with patch.object(roundtable, "_run", process_stub):
        return roundtable.run_roundtable(store, **kwargs)


def source_only_b(
    store, *, reviewers=None, workers=None, max_parallel=None, timeout=900, call_cap=16
):
    """Real V2 producer for downstream mechanics fixtures, with explicit synthetic consent.

    Consent regressions use the public producers directly and never this setup helper.
    """
    from copy import deepcopy
    from uuid import uuid4

    from tests.python.core.test_plan_store import mock_catalog, mock_choice

    from codex_harness.core import approvals
    from codex_harness.core.spine import Spine

    defaults = {
        "codex": ("gpt-6.1-sol", "high"),
        "claude": ("claude-sonnet-5-5", None),
        "copilot": ("gpt-4.1", None),
    }
    reviewers = reviewers or [
        dict(name="codex-reviewer", provider="codex"),
        dict(name="claude-reviewer", provider="claude"),
    ]
    previous = approvals.current(store, "B")
    chosen = (previous or {}).get("subject", {}).get("reviewers", [])
    reviewers = [
        {
            **dict(model=defaults[r["provider"]][0], effort=defaults[r["provider"]][1]),
            **next(
                (
                    old
                    for old in chosen
                    if old["name"] == r["name"] and old["provider"] == r["provider"]
                ),
                {},
            ),
            **r,
        }
        for r in reviewers
    ]
    previous = approvals.current(store, "B")
    if previous and all(
        approvals.reviewer_tuple(r) in previous["subject"]["reviewers"] for r in reviewers
    ):
        try:
            approvals.require_approval(
                store,
                "B",
                dict(
                    execution=dict(
                        workers=workers
                        or [
                            dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                            for b in store.spine().ids("bead")
                        ],
                        maxParallel=max_parallel or (1 if store.spine().semantic else 2),
                        timeout=timeout,
                        callCap=call_cap,
                    ),
                    reviewAllowance=10,
                    reviewPolicy=dict(maxRounds=100, maxTotal=None, timeout=900),
                ),
            )
        except ValueError:  # Stale execution scopes also refuse after adding a bead.
            pass
        else:
            return reviewers
    if not previous or any(
        approvals.reviewer_tuple(r) not in previous["subject"]["reviewers"] for r in reviewers
    ):
        raw = mock_catalog()
        raw["id"] = "RC-" + str(uuid4())
        raw["providers"] = []
        for provider in dict.fromkeys(r["provider"] for r in reviewers):
            item = deepcopy(mock_catalog()["providers"][0])
            item["provider"] = provider
            item["choices"] = [
                dict(
                    model=r["model"],
                    effort={"kind": "unsupported"}
                    if r["effort"] is None
                    else {"kind": "named", "value": r["effort"]},
                    availability="available",
                    reviewPathSupported=True,
                    reason="SOURCE-ONLY fixture",
                )
                for r in reviewers
                if r["provider"] == provider
            ]
            item["choices"] = list(
                {(c["model"], c["effort"].get("value")): c for c in item["choices"]}.values()
            )
            raw["providers"].append(item)
        catalog = store.record_reviewer_catalog(raw, store._revision())["catalog"]
        node = mock_choice(catalog)
        node["parents"] = store.spine().ids("vision")
        node["fields"]["reviewers"] = []
        for r in reviewers:
            provider = next(p for p in catalog["providers"] if p["provider"] == r["provider"])
            choice = next(
                c
                for c in provider["choices"]
                if c["model"] == r["model"] and c["effort"].get("value") == r["effort"]
            )
            node["fields"]["reviewers"].append(
                dict(
                    name=r["name"],
                    provider=r["provider"],
                    **{k: choice[k] for k in ("choiceId", "model", "effort")},
                )
            )
        node["fields"]["assurance"]["crossProviderRequired"] = False
        store.record_reviewer_selection(node, store._revision())
    manifest = store.compile_template(store.workspace / "template")
    workers = workers or [
        dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
        for b in manifest["beads"]
    ]
    request = prepare_confirmation(
        store,
        "plan-execution",
        execution=dict(
            expected_revision=manifest["planRevision"],
            workers=workers,
            max_parallel=max_parallel or (1 if Spine(store._data()).semantic else 2),
            timeout=timeout,
            call_cap=call_cap,
        ),
        review_max_starts=10,
        review_policy=dict(maxRounds=100, maxTotal=None, timeout=900),
    )
    result = complete_confirmation(
        store,
        request["request_id"],
        "accept",
        dict(approve=True, statement="Approve", conditions=""),
    )
    assert result["state"] == "approved"
    return reviewers
