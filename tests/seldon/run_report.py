"""C2/C23; R11/R14: read-only development counts from canonical unit and commit records."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

RUNS = Path("C:/Users/oren/codex-harness/work/ladder/RUNS.md")
COLUMNS = ["Date (UTC)", "Slot", "Commit", "Outcome", "Furthest state", "Repairs", "Retries", "Gaps",
           "Uncertain", "Wall time", "Calls", "Plan nodes", "Plan bytes", "RED FLAG COUNT",
           "Question contract", "Vision lock"]


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def report(slot):
    slot = Path(slot).resolve()
    databases = list(slot.glob("**/.seldon/state.sqlite3"))
    if len(databases) != 1:
        raise ValueError("Select a slot containing exactly one canonical run store")
    db = sqlite3.connect(databases[0].as_uri() + "?mode=ro", uri=True)
    try:
        records = [(f"r{i}", k, json.loads(b)) for i, k, b in
                   db.execute("SELECT id,kind,body FROM record ORDER BY id")]
        cursor = json.loads(db.execute("SELECT cursor FROM run WHERE id=1").fetchone()[0])
        units = {r: json.loads(b) for r, b in db.execute("SELECT id,body FROM unit")}
        latest = db.execute("SELECT document FROM plan_revision ORDER BY id DESC LIMIT 1").fetchone()
        plan = json.loads(latest[0]) if latest else {}
    finally:
        db.close()
    answers = [b["answer"] for _, k, b in records if k == "answer"]
    outcome = next((b for _, k, b in reversed(records) if k == "outcome"), {})
    attempts = [b for _, k, b in records if k == "attempt"]
    questions = [b for _, k, b in records if k == "question"]
    transitions = [b for _, k, b in records if k == "transition" and b["unit"] is None]
    furthest = next((b["input"] for b in reversed(transitions) if b["input"] != "NEW"), cursor["state"])
    retries = sum(bool(b.get("retryOf")) for b in attempts)
    retries += sum(bool(q["facts"].get("Exact corrections")) for q in questions)
    interruptions = sum(k == "transition" and b["event"] == "reopen" for _, k, b in records)
    repairs = sum(k == "unit_failure" for _, k, _ in records) + cursor["repairs"]
    gaps = sum(len(a.get("gaps", [])) for a in answers)
    uncertain = sum(a.get("verdict") == "uncertain" for a in answers)
    times = [b["startedAt"] for b in attempts]
    ends = [b["finishedAt"] for _, k, b in records if k == "attempt_end"]
    wall = max(ends) - min(times) if ends and times else None
    kickoff = next(b for _, k, b in records if k == "kickoff")
    setup = kickoff["setup"]
    plan_bytes = len(encode(plan).encode("utf-8"))
    flags = repairs + retries + interruptions + gaps + uncertain + int(
        cursor["state"] not in ("SCRIPTED_C", "ACCEPTED"))
    lines = [f"Outcome {cursor['state']}; accepted={outcome.get('accepted', False)}; "
             f"verification_complete={outcome.get('verification_complete', False)}.",
             outcome.get("reason", "No terminal outcome recorded"),
             "Units: " + encode(dict(Counter(u["state"] for u in units.values()))),
             f"Repairs {repairs}; retries {retries}; interrupted units {interruptions}; "
             f"gaps {gaps}; uncertain {uncertain}; flags {flags}."]
    lines += [f"{ref}: {row['state']}; {row['failure']}" for ref, row in units.items() if row["failure"]]
    counts = Counter(q["kind"] for q in questions)
    sizes = {kind: max(len(encode(q).encode("utf-8")) for q in questions if q["kind"] == kind)
             for kind in counts}
    lines += ["Calls: " + encode(counts), "Largest questions (bytes): " + encode(sizes),
              f"Final commit: {cursor['finalCommit']}; observed checks: {len(cursor['finalObservations'])}.",
              "Usage is unknown unless recorded in native result/stream artifacts."]
    for _, kind, body in records:
        if kind == "feature_verdict" and body["answer"]["gaps"]:
            lines.append("Feature gaps: " + encode(body["answer"]["gaps"]))
    row = [datetime.fromtimestamp(min(times), timezone.utc).date().isoformat() if times else "unknown",
           str(slot), setup.get("commit", "unknown"), cursor["state"], furthest, repairs, retries, gaps,
           uncertain, f"{wall:.3f}s*" if wall is not None else "unknown", len(attempts),
           len(plan.get("nodes", {})), plan_bytes, flags, setup.get("questionContract", "unknown"),
           hashlib.sha256(encode(plan["nodes"]["vision"]["body"]).encode()).hexdigest()
           if plan else "unknown"]
    return "\n".join(lines), row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slot", type=Path)
    parser.add_argument("--runs", type=Path, default=RUNS)
    args = parser.parse_args(argv)
    output, row = report(args.slot)
    print(output, flush=True)
    args.runs.parent.mkdir(parents=True, exist_ok=True)
    with args.runs.open("a+", encoding="utf-8") as ledger:
        ledger.seek(0)
        retained = ledger.read()
        if retained and "| Question contract |" not in retained:
            retained = retained.replace("| RED FLAG COUNT |",
                                        "| RED FLAG COUNT | Question contract | Vision lock |", 1)
            retained = retained.replace("|" + " --- |" * 14, "|" + " --- |" * 16, 1)
            ledger.seek(0)
            ledger.write(retained)
            ledger.truncate()
        ledger.seek(0, 2)
        if not retained:
            ledger.write("# Dev runs\n\n*Wall time is the recorded call envelope; counts may overlap.*\n\n")
            ledger.write("| " + " | ".join(COLUMNS) + " |\n|" + " --- |" * len(COLUMNS) + "\n")
        ledger.write("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in row) + " |\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
