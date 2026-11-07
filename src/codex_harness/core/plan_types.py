"""Planning identities and record primitives; no store or continuation policy."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from codex_harness.core.spine import SpineError

PHASES = (
    "align",
    "expand",
    "vision-lock",
    "features",
    "requirements",
    "tech-stack",
    "plan",
    "roundtable",
    "publish",
    "execute",
    "accept",
)


class PlanStoreError(SpineError):
    """A stable refusal code from the planning store."""


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _nonempty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PlanStoreError("plan-input-invalid", f"{label} must be a nonempty string")
