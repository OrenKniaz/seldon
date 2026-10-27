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
    vision = store.spine().node("V1")
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


def source_only_confirmation(store, gate, *, statement="SOURCE-ONLY synthetic native response"):
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


def source_only_vision_lock(store, *, statement="SOURCE-ONLY synthetic vision response"):
    """Drive initial fixture alignment/lock through real phase and native-store contracts."""
    if "expansion" not in store.spine().node("V1")["fields"]:
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

    with patch.object(roundtable, "_run", process_stub):
        return roundtable.run_roundtable(store, **kwargs)
