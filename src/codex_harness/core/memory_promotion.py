"""Host-side promotion of validated lessons into confirmed project memory."""

from dataclasses import dataclass
from pathlib import Path

from codex_harness.core.project_memory import (
    ProjectMemoryError,
    ProjectMemoryStore,
    Provenance,
)
from codex_harness.core.project_memory import (
    project_memory_prompt_part as read_project_memory_prompt_part,
)


class MemoryPromotionError(ValueError):
    """Promotion failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PromotionEvidence:
    integration_commit: str
    validation_receipt: str
    decision_id: str | None = None


def promote_lesson(
    workspace_root: str | Path,
    *,
    text: str,
    evidence: PromotionEvidence,
    author: str,
    provider: str,
    model: str,
) -> str:
    """Record an unverified lesson after the host's integrated validation passes.

    The store has commit provenance but no receipt field, so evidence is also
    appended to the text. This function records evidence; the host validates it.
    """
    commit = evidence.integration_commit.strip()
    receipt = evidence.validation_receipt.strip()
    if not text.strip() or not commit or not receipt:
        raise MemoryPromotionError(
            "memory-promotion-evidence-missing",
            "promotion requires lesson text, an integrated commit and a validation receipt",
        )
    tags = ["lesson"]
    if evidence.decision_id:
        tags.append(evidence.decision_id)
    entry = ProjectMemoryStore(workspace_root).add(
        f"{text.strip()} [evidence: commit={commit[:12]} receipt={receipt}]",
        tags=tags,
        status="unverified",
        provenance=Provenance(author=author, provider=provider, model=model, commit=commit),
    )
    return entry.id


def confirm_lesson(
    workspace_root: str | Path,
    entry_id: str,
    *,
    by: str,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Confirm through the store only when the confirmer is a different identity."""
    store = ProjectMemoryStore(workspace_root)
    original = store.get(entry_id)
    if original.provenance.author is not None and by.strip() == original.provenance.author.strip():
        raise MemoryPromotionError(
            "memory-self-confirmation-refused", "a lesson's author cannot confirm their own lesson"
        )
    store.confirm(entry_id, by=by, provider=provider, model=model)


def project_memory_prompt_part(workspace_root: str | Path) -> str | None:
    """Preserve the optional launch projection: absent or invalid memory yields None."""
    if not ProjectMemoryStore(workspace_root).path.is_file():
        return None
    try:
        return read_project_memory_prompt_part(workspace_root)
    except ProjectMemoryError:
        return None


def memory_prompt_for(workspace_root: str | Path) -> str | None:
    """Return the existing worker prompt projection unchanged."""
    return project_memory_prompt_part(workspace_root)
