"""Feature switches for A/B cohorts (Oren 2026-11-17): SELDON_OFF=name[,name] turns a feature off.

Each switch lives at the one point where behaviour branches; it is deleted once an A/B decides (R16).
Switches: severity (judge gap severity; off = every gap blocks, as before 11-17).
answerRepair (one exact correction for answer consumption failures; off = report immediately).
"""
import os


def enabled(name):
    return name not in {n.strip() for n in os.environ.get("SELDON_OFF", "").split(",")}


def blocking(gap):
    """A judge gap that starts a fix round: must-fix, or any gap when severity is switched off."""
    return gap.get("severity", "must-fix") == "must-fix" or not enabled("severity")
