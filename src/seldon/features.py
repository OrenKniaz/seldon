"""Feature switches for A/B cohorts (Oren 2026-11-17): SELDON_OFF=name[,name] turns a feature off.

Each switch lives at the one point where behaviour branches; it is deleted once an A/B decides (R16).
Switches: severity (judge gap severity; off = every gap blocks, as before 11-17).
answerRepair (one exact correction for answer consumption failures; off = report immediately).
contractGap (a judge gap owned by a contract goes to the planner as a contract correction; off = the
unit itself, or the contract's provider, is sent the gap as before 11-18).
checkContract (cross-unit check disputes go to the planner; repeated product checks get a plain question).
growPersona (proposal-aware development simulator; off = scripted menu or grouped transport control).
askContracts (COACH resolves peer agreements or sends the gap back to the planner).
sizeFirst (new kickoffs record effort-based planning and the SELDON_SPLIT experiment arm).
"""
import os


def enabled(name):
    return name not in {n.strip() for n in os.environ.get("SELDON_OFF", "").split(",")}


def planning_setup():
    return {"sizeFirst": enabled("sizeFirst"),
            "split": "force" if os.environ.get("SELDON_SPLIT") == "force" else "auto"}


def blocking(gap):
    """A judge gap that starts a fix round: must-fix, or any gap when severity is switched off."""
    return gap.get("severity", "must-fix") == "must-fix" or not enabled("severity")
