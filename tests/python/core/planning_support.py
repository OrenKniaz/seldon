"""Publication prerequisites produced by real proof execution; reviews are injected."""

from pathlib import Path

from codex_harness.core.roundtable import run_roundtable
from codex_harness.core.spine import compile_manifest
from codex_harness.core.test_audit import audit_revised_tests

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"


def publication_evidence(store):
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
    if not store.successful_reviews(revision):
        run_roundtable(
            store,
            reviewers=[
                {"name": "fixture-codex", "provider": "codex"},
                {"name": "fixture-claude", "provider": "claude"},
            ],
            runner=lambda *args: (0, "[]"),
            executable_for=lambda provider: provider,
        )
