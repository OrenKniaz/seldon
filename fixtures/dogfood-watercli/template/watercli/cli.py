from datetime import date as date

from watercli.report import history_report as history_report
from watercli.report import today_report as today_report
from watercli.store import add_entry as add_entry
from watercli.store import load as load
from watercli.store import save as save
from watercli.store import set_goal as set_goal
from watercli.store import undo_entry as undo_entry
from watercli.units import parse_amount as parse_amount


def main(argv: list[str] | None = None) -> int:
    """Commands add/today/history/goal/undo; success 0, clear stderr errors 2."""
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())


