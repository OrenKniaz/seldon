"""Host-side promotion of validated lessons into confirmed project memory."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from codex_harness.core.project_memory import (
    MAX_TEXT_CHARS,
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


class MemoryPromotionUnavailable(MemoryPromotionError):
    """Advisory content cannot fit without losing its evidence references."""


@dataclass(frozen=True)
class PromotionEvidence:
    integration_commit: str
    validation_receipt: str
    decision_id: str | None = None
    source_ref: str | None = None


def promote_lesson(
    workspace_root: str | Path,
    *,
    text: str,
    evidence: PromotionEvidence,
    author: str,
    provider: str,
    model: str,
    recovery_identity: str | None = None,
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
    content = f"{text.strip()} [evidence: commit={commit[:12]} receipt={receipt}]"
    provenance = Provenance(author=author, provider=provider, model=model, commit=commit)
    store = ProjectMemoryStore(workspace_root)
    token = None
    if recovery_identity is not None:
        token = hashlib.sha256(
            json.dumps(
                [
                    recovery_identity,
                    text.strip(),
                    commit,
                    receipt,
                    evidence.decision_id,
                    author,
                    provider,
                    model,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        content += f" [promotion: {token}]"
    if len(content) > MAX_TEXT_CHARS:
        # Keep token inputs and complete evidence intact; only summarize the lesson body.
        suffix = content[len(text.strip()) :]
        if evidence.source_ref:
            suffix = f" [full lesson: {evidence.source_ref}]" + suffix
        marker = " ... [summary]"
        budget = MAX_TEXT_CHARS - len(suffix) - len(marker)
        if budget < 1:
            raise MemoryPromotionUnavailable(
                "memory-promotion-evidence-too-long",
                "Full evidence references exceed memory budget",
            )
        content = text.strip()[:budget] + marker + suffix
    if token is not None:
        entries = store.search(token, include_superseded=True, limit=100)
        originals = [entry for entry in entries if entry.supersedes is None]
        if originals:
            if len(originals) != 1 or any(
                entry.text != content
                or entry.tags != tuple(dict.fromkeys(tag.strip().lower() for tag in tags))
                or entry.provenance != provenance
                or entry.status != "unverified"
                for entry in originals
            ):
                raise MemoryPromotionError("memory-promotion-recovery-mismatch", token)
            return originals[0].id
    entry = store.add(
        content,
        tags=tags,
        status="unverified",
        provenance=provenance,
    )
    return entry.id


def confirm_lesson(
    workspace_root: str | Path,
    entry_id: str,
    *,
    by: str,
    provider: str | None = None,
    model: str | None = None,
    recover: bool = False,
) -> None:
    """Confirm through the store only when the confirmer is a different identity."""
    store = ProjectMemoryStore(workspace_root)
    original = store.get(entry_id)
    if original.provenance.author is not None and by.strip() == original.provenance.author.strip():
        raise MemoryPromotionError(
            "memory-self-confirmation-refused", "a lesson's author cannot confirm their own lesson"
        )
    marker = re.search(r"\[promotion: ([0-9a-f]{64})\]$", original.text)
    if recover and marker and original.superseded:
        successors = [
            entry
            for entry in store.search(marker[1], include_superseded=True, limit=100)
            if entry.supersedes == entry_id
        ]
        if len(successors) == 1:
            entry = successors[0]
            if (
                entry.status == "confirmed"
                and entry.text == original.text
                and entry.tags == original.tags
                and entry.provenance
                == Provenance(
                    author=by.strip(),
                    provider=provider,
                    model=model,
                    run_id=original.provenance.run_id,
                    bead_id=original.provenance.bead_id,
                    turn_id=original.provenance.turn_id,
                    commit=original.provenance.commit,
                )
            ):
                return
        raise MemoryPromotionError("memory-confirmation-recovery-mismatch", entry_id)
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
