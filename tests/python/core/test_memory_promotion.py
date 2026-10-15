from __future__ import annotations

import sys
from pathlib import Path

import pytest

from codex_harness.core.memory_promotion import (
    MemoryPromotionError,
    PromotionEvidence,
    confirm_lesson,
    memory_prompt_for,
    project_memory_prompt_part,
    promote_lesson,
)
from codex_harness.core.project_memory import ProjectMemoryStore, Provenance

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="project memory holds Windows workspace leases; other hosts fail closed",
)

EVIDENCE = PromotionEvidence("1234567890abcdef1234567890abcdef12345678", "R1-R3-passed")


def test_lesson_reaches_prompt_only_after_independent_confirmation(tmp_path: Path) -> None:
    lesson = "Use the recorded payments fixture in tests."
    entry_id = promote_lesson(
        tmp_path,
        text=lesson,
        evidence=EVIDENCE,
        author="worker-a",
        provider="codex",
        model="worker-model",
    )
    store = ProjectMemoryStore(tmp_path)
    entry = store.get(entry_id)
    assert entry.status == "unverified"
    assert entry.tags == ("lesson",)
    assert entry.provenance == Provenance(
        author="worker-a",
        provider="codex",
        model="worker-model",
        commit=EVIDENCE.integration_commit,
    )
    assert entry.text == lesson + " [evidence: commit=1234567890ab receipt=R1-R3-passed]"
    assert memory_prompt_for(tmp_path) is None

    confirm_lesson(tmp_path, entry_id, by="integrator", provider="claude", model="review-model")

    prompt = memory_prompt_for(tmp_path)
    assert prompt is not None
    assert lesson in prompt
    assert prompt == project_memory_prompt_part(tmp_path)
    confirmed = store.confirmed_slice()[0]
    assert confirmed.supersedes == entry_id
    assert confirmed.text == entry.text
    assert confirmed.provenance.author == "integrator"
    assert confirmed.provenance.provider == "claude"
    assert confirmed.provenance.model == "review-model"
    assert confirmed.provenance.commit == EVIDENCE.integration_commit


@pytest.mark.parametrize("by", ["worker-a", " worker-a "])
def test_self_confirmation_is_refused(tmp_path: Path, by: str) -> None:
    entry_id = promote_lesson(
        tmp_path,
        text="A lesson awaiting independent confirmation.",
        evidence=EVIDENCE,
        author="worker-a",
        provider="codex",
        model="worker-model",
    )
    with pytest.raises(MemoryPromotionError) as excinfo:
        confirm_lesson(tmp_path, entry_id, by=by)
    assert excinfo.value.code == "memory-self-confirmation-refused"
    assert ProjectMemoryStore(tmp_path).get(entry_id).status == "unverified"
    assert memory_prompt_for(tmp_path) is None


@pytest.mark.parametrize(
    ("text", "commit", "receipt"),
    [
        ("", "abc", "receipt"),
        ("  ", "abc", "receipt"),
        ("lesson", "  ", "receipt"),
        ("lesson", "", "receipt"),
        ("lesson", "abc", "  "),
    ],
)
def test_missing_evidence_is_refused(tmp_path: Path, text: str, commit: str, receipt: str) -> None:
    with pytest.raises(MemoryPromotionError) as excinfo:
        promote_lesson(
            tmp_path,
            text=text,
            evidence=PromotionEvidence(commit, receipt),
            author="worker-a",
            provider="codex",
            model="worker-model",
        )
    assert excinfo.value.code == "memory-promotion-evidence-missing"
    assert not (tmp_path / ".codex-harness").exists()


def test_d1_lesson_reaches_next_worker_prompt(tmp_path: Path) -> None:
    lesson = "tip-split amounts are integer cents everywhere; never floats (D1)"
    entry_id = promote_lesson(
        tmp_path,
        text=lesson,
        evidence=PromotionEvidence(EVIDENCE.integration_commit, EVIDENCE.validation_receipt, "D1"),
        author="worker-a",
        provider="codex",
        model="worker-model",
    )
    assert ProjectMemoryStore(tmp_path).get(entry_id).tags == ("lesson", "d1")
    assert memory_prompt_for(tmp_path) is None

    confirm_lesson(tmp_path, entry_id, by="planner")

    prompt = memory_prompt_for(tmp_path)
    assert prompt is not None
    assert lesson in prompt
