"""Reference solution for Bead B (proof that R4-R5 are satisfiable).

Never shown to workers.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from tipsplit.money import parse_amount, split


def format_cents(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def format_shares(shares: Sequence[int]) -> list[str]:
    return [f"Person {index}: {format_cents(share)}" for index, share in enumerate(shares, 1)]


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # one line, no usage dump
        raise ValueError(message)


def main(argv: Sequence[str]) -> int:
    parser = _Parser(prog="tipsplit", add_help=False)
    parser.add_argument("total")
    parser.add_argument("people", type=int)
    parser.add_argument("--tip", type=int, default=0)
    try:
        args = parser.parse_args(list(argv))
        shares = split(parse_amount(args.total), args.people, args.tip)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for line in format_shares(shares):
        print(line)
    return 0
