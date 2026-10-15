import argparse
import os
import sys
from datetime import date
from pathlib import Path

from watercli.report import history_report, today_report
from watercli.store import add_entry, load, save, set_goal, undo_entry
from watercli.units import parse_amount


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="watercli")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("amount")
    add.add_argument("unit", nargs="?", default="ml")
    commands.add_parser("today")
    history = commands.add_parser("history")
    history.add_argument("days", nargs="?", type=int, default=7)
    goal = commands.add_parser("goal")
    goal.add_argument("ml", nargs="?", type=int)
    commands.add_parser("undo")
    try:
        args = parser.parse_args(argv)
        path = Path(os.environ.get("WATER_CLI_DATA", "./water.json"))
        day = date.today().isoformat()
        data = load(path)
        if args.command == "add":
            ml = parse_amount(args.amount, args.unit)
            save(path, add_entry(data, day, ml))
            print(f"Added {ml} ml.")
        elif args.command == "today":
            print(today_report(data, day))
        elif args.command == "history":
            print("\n".join(history_report(data, day, args.days)))
        elif args.command == "goal":
            if args.ml is not None:
                save(path, set_goal(data, args.ml))
                data = {**data, "goal_ml": args.ml}
            print(f"Goal: {data['goal_ml']} ml.")
        else:
            updated, removed = undo_entry(data, day)
            if removed is None:
                print("Nothing to undo today.")
            else:
                save(path, updated)
                print(f"Removed {removed} ml.")
        return 0
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
