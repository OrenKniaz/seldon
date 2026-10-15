from pathlib import Path


class DataError(ValueError):
    """Missing, unreadable or invalid data; preserve the original file."""


def load(path: Path) -> dict:
    raise NotImplementedError


def save(path: Path, data: dict) -> None:
    """Validate, write a sibling temporary file, then atomically replace."""
    raise NotImplementedError


def add_entry(data: dict, day: str, ml: int) -> dict:
    """Return a new data object with an appended entry."""
    raise NotImplementedError


def undo_entry(data: dict, day: str) -> tuple[dict, int | None]:
    """Remove the last entry of this day, preserving other days and order."""
    raise NotImplementedError


def set_goal(data: dict, ml: int) -> dict:
    raise NotImplementedError
