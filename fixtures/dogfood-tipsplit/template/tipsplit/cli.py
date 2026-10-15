"""Bead B: command line. See PLAN.md requirements R4-R5."""

from __future__ import annotations

from collections.abc import Sequence


def format_cents(cents: int) -> str:
    raise NotImplementedError("Bead B: implement R4")


def format_shares(shares: Sequence[int]) -> list[str]:
    raise NotImplementedError("Bead B: implement R4")


def main(argv: Sequence[str]) -> int:
    raise NotImplementedError("Bead B: implement R5")
