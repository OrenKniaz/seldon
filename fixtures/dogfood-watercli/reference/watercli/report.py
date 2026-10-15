from datetime import date, timedelta


def today_report(data: dict, day: str) -> str:
    total = sum(entry["ml"] for entry in data["entries"] if entry["day"] == day)
    goal = data["goal_ml"]
    return f"{day}: {total} ml / {goal} ml ({total / goal * 100:.1f}%)"


def history_report(data: dict, day: str, days: int = 7) -> list[str]:
    if type(days) is not int or days <= 0:
        raise ValueError("days must be a positive integer")
    end = date.fromisoformat(day)
    if days > (end - date.min).days + 1:
        raise ValueError("history window extends before the minimum calendar date")
    return [
        today_report(data, (end - timedelta(days=offset)).isoformat())
        for offset in range(days - 1, -1, -1)
    ]
