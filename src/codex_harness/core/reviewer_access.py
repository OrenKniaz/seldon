"""Access observations from protected review attempts, never catalog claims."""

import re


def observe_access(*, attempted, mocked, succeeded, error=""):
    """Classify failure diagnostics only; unknown failures stay unknown."""
    category = None
    if not succeeded:
        for name, pattern in (
            ("quota", r"quota|rate.?limit|usage limit|insufficient credits|exhausted"),
            (
                "authentication",
                r"unauthori[sz]ed|not logged in|login required|authentication|invalid api key",
            ),
            (
                "unknown-model",
                r"unknown model|model.{0,50}(?:not found|does not exist|unrecognized)",
            ),
            (
                "entitlement",
                r"not entitled|not available.{0,50}(?:account|plan|login)|"
                r"do not have access|account.{0,40}access denied",
            ),
        ):
            if re.search(pattern, error, re.IGNORECASE):
                category = name
                break
        category = category or "unknown"
    return {
        "state": "observed" if attempted and not mocked else "selected",
        "didAttemptLaunch": attempted,
        "isMocked": mocked,
        "outcome": ("success" if succeeded else "failure")
        if attempted and not mocked
        else "unknown",
        "failureCategory": category if attempted and not mocked else None,
        "nextAction": "Select another reviewer explicitly and start a new review."
        if attempted and not mocked and not succeeded
        else None,
    }
