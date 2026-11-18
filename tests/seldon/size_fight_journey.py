"""Saved fight replay. Run explicitly with pytest tests/seldon/size_fight_journey.py."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_journey import strict_schema

RETAINED = Path("C:/seldon/tests")
LADDER = Path("C:/Users/oren/codex-harness/work/ladder")
REPORT = Path(__file__).with_name("run_report.py")


@pytest.mark.fast
def test_paired_report_retains_usage_gaps_and_stalled_slots(tmp_path):
    """The single-slot golden report cannot catch filtered slots or absent plain usage in a comparison."""
    completed = RETAINED / "m1/quietbench-555101a-c01"
    stalled = RETAINED / "m1/shelflife-main-e57b218-z11"
    log = RETAINED / "qb3/c01.log"
    costs = LADDER / "design-review/effic-data-2026-11-16.txt"
    if not all(p.exists() for p in (completed, stalled, log, costs)):
        pytest.skip("Retained Quiet Bench and ShelfLife fight recordings are required")
    for slot in (completed, stalled):
        question = min((slot / "calls").glob("*/question.json"))
        strict_schema(json.loads(question.read_text(encoding="utf-8"))["schema"])

    saved = log.read_text(encoding="utf-8")
    waves = json.loads(next(line.split(": ", 1)[1] for line in saved.splitlines()
                            if line.startswith("Calls and fresh tokens per wave: ")))
    legacy = re.search(r"^terra-native-1: calls=(\d+) .*output=(\dk)", costs.read_text("utf-8"), re.M)
    assert legacy and legacy.groups() == ("0", "0k")
    # The plaintext summary records a session but supplies no native usage evidence.
    plain = {"workerSessions": 1, "model": "gpt-6.1-terra", "effort": "medium", "state": "completed"}
    manifest = {
        "pairs": [
            {"id": "qb3-c01", "seldon": {"slot": str(completed), "oracle": {
                "artifact": str(RETAINED / "oracle-qb3"),
                "receipt": str(RETAINED / "oracle-qb3-ev/oracle.json")}},
             "plain": {**plain, "oracle": {
                 "artifact": str(RETAINED / "oracle-qbplain"),
                 "receipt": str(RETAINED / "oracleCal-4/oracle-qbplain/oracle.json")}}},
            {"id": "z11", "seldon": {"slot": str(stalled)}, "plain": plain},
            *({"id": f"unstarted-{i}"} for i in range(3, 6)),
        ],
    }
    path = tmp_path / "fight.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    ledger = tmp_path / "RUNS.md"
    result = subprocess.run([sys.executable, "-B", str(REPORT), "--paired", str(path), "--runs", str(ledger)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert not ledger.exists()
    report = json.loads(result.stdout)
    c01, z11, *_ = report["pairs"]
    seldon = c01["seldon"]
    assert seldon["callsPerWave"] == waves
    assert (seldon["providerCalls"], seldon["freshTokens"]) == (176, 2639554)
    assert seldon["eligibleC"] and seldon["scriptedC"] and seldon["verificationComplete"]
    assert not seldon["realAcceptance"] and not seldon["autonomousC"]
    assert seldon["cOffered"] and seldon["acceptedSize"] == "large" and seldon["split"] is None
    assert seldon["unitQuestions"] == 11
    assert seldon["expectedTouches"] == {"A": 1, "B": 1, "CHECK-IN": 3, "C": 1}
    revision = subprocess.check_output(["git", "-C", str(completed / "product"), "rev-parse", "HEAD"],
                                       text=True, timeout=15).strip()
    assert seldon["candidateRevision"] == revision
    assert Path(seldon["oracle"]["artifact"]).name == "oracle-qb3"
    assert seldon["oracle"]["totals"] == {"pass": 7, "fail": 2, "unverified": 5}
    assert c01["plain"]["oracle"]["totals"] == {"pass": 10, "fail": 0, "unverified": 4}
    assert c01["plain"]["workerSessions"] == 1
    assert c01["plain"]["providerCalls"] is c01["plain"]["freshTokens"] is None
    assert c01["ratios"]["providerCalls"] is c01["ratios"]["freshTokens"] is None
    assert c01["sameOrBetterOracle"] is None  # The old receipts lack revision bindings.
    assert z11["seldon"]["state"] == "EXECUTE"
    assert not z11["seldon"]["eligibleC"] and z11["seldon"]["timeToCSeconds"] is None
    assert z11["seldon"]["providerActivitySeconds"] > 0
    assert len(report["pairs"]) == 5
    assert report["cohorts"]["unknown"]["pairs"] == 5
    assert report["cohorts"]["unknown"]["medianRatios"]["wallSeconds"] is None
