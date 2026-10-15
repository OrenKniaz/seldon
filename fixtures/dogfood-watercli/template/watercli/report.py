def today_report(data: dict, day: str) -> str:
    """YYYY-MM-DD: N ml / G ml (P.P%); percentage is uncapped."""
    raise NotImplementedError


def history_report(data: dict, day: str, days: int = 7) -> list[str]:
    """Chronological calendar window including today and empty days."""
    raise NotImplementedError
