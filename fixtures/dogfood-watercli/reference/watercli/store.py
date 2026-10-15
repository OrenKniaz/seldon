import copy
import json
import os
import tempfile
from datetime import date
from pathlib import Path


class DataError(ValueError):
    """An operation could not safely use the data file."""


def _validate(data: dict) -> None:
    if not isinstance(data, dict) or set(data) != {"goal_ml", "entries"}:
        raise DataError("invalid data schema")
    if type(data["goal_ml"]) is not int or data["goal_ml"] <= 0:
        raise DataError("goal_ml must be a positive integer")
    if not isinstance(data["entries"], list):
        raise DataError("entries must be a list")
    for entry in data["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"day", "ml"}:
            raise DataError("invalid entry")
        try:
            if date.fromisoformat(entry["day"]).isoformat() != entry["day"]:
                raise ValueError
        except (TypeError, ValueError):
            raise DataError("invalid entry day") from None
        if type(entry["ml"]) is not int or entry["ml"] <= 0:
            raise DataError("entry ml must be a positive integer")


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise DataError(f"cannot read data file {path}: {error}") from error
    _validate(data)
    return data


def save(path: Path, data: dict) -> None:
    _validate(data)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False
        ) as stream:
            temp = Path(stream.name)
            json.dump(data, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except OSError as error:
        raise DataError(f"cannot write data file {path}: {error}") from error
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def add_entry(data: dict, day: str, ml: int) -> dict:
    updated = copy.deepcopy(data)
    updated["entries"].append({"day": day, "ml": ml})
    _validate(updated)
    return updated


def undo_entry(data: dict, day: str) -> tuple[dict, int | None]:
    updated = copy.deepcopy(data)
    for index in range(len(updated["entries"]) - 1, -1, -1):
        if updated["entries"][index]["day"] == day:
            return updated, updated["entries"].pop(index)["ml"]
    return updated, None


def set_goal(data: dict, ml: int) -> dict:
    updated = copy.deepcopy(data)
    updated["goal_ml"] = ml
    _validate(updated)
    return updated
