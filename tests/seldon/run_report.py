"""C2/C23; R11/R14/R19: development run counts and read-only paired fight reports."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

PAIRED_HELP = """The manifest has a "pairs" array with one entry per planned start, e.g.
  {"id":"p1","split":"auto","seldon":{"slot":"runs/s1"},
   "plain":{"slot":"runs/p1","state":"completed","workerSessions":1,
            "model":"gpt-6.1-terra","effort":"medium","usage":"runs/p1/usage.json"}}

Paths are relative to the manifest. Repeat for each of the five starts and omit
an arm if it has no saved slot. Seldon split, size, model and revision come
from its store. Legacy slots without split records use the declared pair split
or stay unknown. A plain usage receipt can provide providerCalls, input_tokens,
cached_input_tokens and output_tokens. Omit values without native evidence.

Either arm may give timing {wallSeconds, humanWaitSeconds, growthSeconds,
timeToCSeconds}, billing {amount, currency} and oracle {receipt, revision,
artifact, artifactRevision}. Give wall time on the same basis for both arms, with the
same treatment of human wait. For a stalled arm, use elapsed wall at stop.
Bind the oracle revision and scored artifact to receipts where known. Give
startRevision and the plain candidateRevision from the run's recorded revisions.
Use comparisonSetup to retain the shared brief, toolchain and timing basis.
"""

RUNS = Path("C:/Users/oren/codex-harness/work/ladder/RUNS.md")
COLUMNS = ["Date (UTC)", "Slot", "Commit", "Outcome", "Furthest state", "Repairs", "Retries", "Gaps",
           "Uncertain", "Wall time", "Calls", "Plan nodes", "Plan bytes", "RED FLAG COUNT",
           "Question contract", "Vision lock"]


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def read_json(path):
    return json.loads(path.read_text("utf-8"))


def native_events(path):
    if path.is_file():
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield event


def fresh_tokens(usage):
    if usage:
        incoming, cached, outgoing = (usage.get("input_tokens"), usage.get("cached_input_tokens", 0),
                                      usage.get("output_tokens"))
        counts = (incoming, cached, outgoing)
        if all(type(value) is int and value >= 0 for value in counts) and cached <= incoming:
            return incoming - cached + outgoing
    return None


def call_observations(records, slot=None):
    """Use the same wave accounting in the legacy report and the paired comparison."""
    starts = {b["attempt"]: Path(b["folder"]) for _, k, b in records if k == "call_started"}
    waves, worker_sessions, worker_without_session = {}, set(), False
    wave = 1
    for ref, kind, body in records:
        if (kind == "transition" and not body["unit"] and body["input"] == "CHECK-IN"
                and body["event"] == "next"):
            wave += 1
        if kind != "attempt":
            continue
        if slot is not None and ref not in starts:
            continue
        folder = starts.get(ref)
        usage = session = None
        if folder is not None:
            if slot is not None:
                folder = slot / "calls" / folder.name
            result = folder / "result.json"
            result_saved = result.is_file()
            if result_saved:
                try:
                    usage = read_json(result).get("usage")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # A crash test can interrupt the receipt write. Recover usage
                    # from the native stream, or count it as unknown.
                    pass
            worker = slot is not None and body["operation"] == "WORK"
            if worker or usage is None and (slot is not None or result_saved):
                stream_usage = None
                for event in native_events(folder / "stdout.bin"):
                    if event.get("type") == "thread.started":
                        session = event.get("thread_id") or session
                    session = event.get("session_id") or session
                    if event.get("type") == "turn.completed":
                        stream_usage = event.get("usage")
                if usage is None:
                    usage = stream_usage
        if slot is not None and body["operation"] == "WORK":
            if session:
                worker_sessions.add(session)
            else:
                worker_without_session = True
        entry = waves.setdefault(wave, {"calls": 0, "freshTokens": 0, "usageUnknownCalls": 0})
        entry["calls"] += 1
        tokens = fresh_tokens(usage)
        if tokens is None:
            entry["usageUnknownCalls"] += 1
        else:
            entry["freshTokens"] += tokens
    for entry in waves.values():
        if entry["usageUnknownCalls"]:
            entry["freshTokens"] = None
    return waves, None if worker_without_session else len(worker_sessions)


def signals(records, document):
    """Observations, never a score or a substitute for ladder acceptance."""
    by_ref = {r: b for r, _, b in records}
    requests = {r: b for r, k, b in records if k == "decision_request"}
    words = {b["request"]: b["words"] for _, k, b in records if k == "user_words"}
    shown = [{"request": r, **b, "answer": words.get(r), "answered": r in words}
             for r, b in requests.items()]
    answers = {b["attempt"]: b["answer"] for _,k,b in records if k == "answer"}
    coached = [{"question": by_ref[b["question"]]["facts"], "answer": answers[r]}
               for r,k,b in records if k == "attempt" and b["operation"] == "COACH"
               and "guidance" in answers.get(r, {})]
    opened, progress, completed, redone, wave = set(), [], set(), set(), 1
    largest, rounds, planning_before_steer = {}, {}, 0
    check_seconds = {}
    per_wave, _ = call_observations(records)
    for r, kind, b in records:
        if kind == "decision_request":
            opened.add(r)
        elif kind == "user_words":
            opened.discard(b["request"])
        elif kind == "transition":
            if b["unit"] is None and b["input"] == "WAVE-VERIFY":
                rounds[wave] = rounds.get(wave, 0) + 1
            unit = b["unit"]
            if unit and b["next"] == "DONE":
                completed.add(unit)
                if opened:
                    progress.append({"unit": unit, "questions": sorted(opened)})
            if b["event"] == "extend":
                completed.discard(unit)
            if unit in completed and b["next"] == "WORKING":
                redone.add(unit)
            if not unit and b["input"] == "CHECK-IN" and b["event"] == "next":
                wave += 1
        elif kind == "observation" and b["purpose"] == "final":
            check_seconds[wave] = check_seconds.get(wave, 0) + b["seconds"]
        elif kind == "question":
            maxima = largest.setdefault(wave, {})
            maxima[b["kind"]] = max(maxima.get(b["kind"], 0), len(encode(b).encode("utf-8")))
        elif kind == "attempt" and opened and b["operation"] in ("ARCHITECT", "DETAIL", "REVIEW"):
            planning_before_steer += 1
    architecture = document.get("architecture", {})
    return ["User questions: " + encode(shown), "Planner-answered questions: " + encode(coached),
            "Work while questions open: " + encode(progress),
            "Recovery: " + encode({"doneUnitsRedone": sorted(redone),
                "workingReopens": sum(k == "transition" and b["event"] == "reopen" for _,k,b in records)}),
            "Waves closed: " + encode({"planned": len(architecture.get("waves", [])),
                "closed": sum(k == "transition" and b["unit"] is None and b["input"] == "CHECK-IN"
                              and b["event"] in ("approve", "next", "last") for _,k,b in records)}),
            "Calls and fresh tokens per wave: " + encode(per_wave),
            "M3 section 2 signals: " + encode({
                "waveVerify": {"roundsPerWave": rounds, "checkProcessSecondsPerWave": check_seconds,
                               "wallSecondsPerRound": None},
                "largestQuestionsPerWave": largest,
                "longestControllerIdleSeconds": None,
                "planningCallsWhileQuestionOpen": planning_before_steer,
                "steers": sum(k == "transition" and b["event"] == "steer" for _,k,b in records),
                "steersChangingUntouchedAreas": None,
                "askedToShownSeconds": None,
                "unknownReason": "Store has no check-start/transition timestamps or per-area steer intent; "
                                 "process seconds overlap and are not wall time"})]


def saved_run(slot):
    databases = list(slot.glob("**/.seldon/state.sqlite3"))
    if len(databases) != 1:
        raise ValueError("Select a slot containing exactly one canonical run store")
    db = sqlite3.connect(databases[0].as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("BEGIN")
        records = [(f"r{i}", k, json.loads(b)) for i, k, b in
                   db.execute("SELECT id,kind,body FROM record ORDER BY id")]
        cursor = json.loads(db.execute("SELECT cursor FROM run WHERE id=1").fetchone()[0])
        units = {r: json.loads(b) for r, b in db.execute("SELECT id,body FROM unit")}
        latest = db.execute("SELECT document FROM plan_revision ORDER BY id DESC LIMIT 1").fetchone()
        plan = json.loads(latest[0]) if latest else {}
    finally:
        db.close()
    return records, cursor, units, plan


def report(slot):
    slot = Path(slot).resolve()
    records, cursor, units, plan = saved_run(slot)
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
    by_ref = {ref: body for ref, _, body in records}
    deferred = [by_ref[ref] for ref in cursor["requirementVerdicts"] + cursor["qualityVerdicts"]]
    deferred += [{"subject": ref, "answer": by_ref[u["judgment"]].get("answer", {})}
                 for ref, u in units.items() if u["judgment"]]
    lines += [v["subject"] + ": not judged: " + "; ".join(dict.fromkeys(v["answer"]["neededFacts"]))
              + " (DV6)" for v in deferred if v["answer"].get("verdict") == "not judged"]
    counts = Counter(q["kind"] for q in questions)
    sizes = {kind: max(len(encode(q).encode("utf-8")) for q in questions if q["kind"] == kind)
             for kind in counts}
    final = [q for q in questions if q["kind"] in ("FLOW", "PRODUCT")]
    final_bytes = sum(len(encode(q).encode("utf-8")) for q in final)
    lines += ["Calls: " + encode(counts), "Largest questions (bytes): " + encode(sizes),
              f"Final judgment: {len(final)} calls; {final_bytes} bytes.",
              f"Final commit: {cursor['finalCommit']}; observed checks: {len(cursor['finalObservations'])}.",
              "Usage is unknown unless recorded in native result/stream artifacts."]
    lines += signals(records, plan)
    for _, kind, body in records:
        if kind == "feature_verdict" and body["answer"]["gaps"]:
            lines.append("Feature gaps: " + encode(body["answer"]["gaps"]))
    row = [datetime.fromtimestamp(min(times), UTC).date().isoformat() if times else "unknown",
           str(slot), setup.get("commit", "unknown"), cursor["state"], furthest, repairs, retries, gaps,
           uncertain, f"{wall:.3f}s*" if wall is not None else "unknown", len(attempts),
           len(plan.get("nodes", {})), plan_bytes, flags, setup.get("questionContract", "unknown"),
           hashlib.sha256(encode(plan["nodes"]["vision"]["body"]).encode()).hexdigest()
           if plan else "unknown"]
    return "\n".join(lines), row


TOUCHES = ("A", "B", "CHECK-IN", "C")
RATIOS = ("providerCalls", "freshTokens", "billedCost", "wallSeconds")


def oracle_score(root, source):
    if not source:
        return None
    receipt = read_json(root / source["receipt"])
    checks = receipt["checks"]
    names = sorted(check["name"] for check in checks)
    counts = Counter(check["status"] for check in checks)
    if not names or len(set(names)) != len(names) or counts.keys() - {"pass", "fail", "unverified"}:
        raise ValueError(f"Oracle receipt {source['receipt']} needs unique pass/fail/unverified checks")
    return {"receipt": source["receipt"],
            "oracleRevision": receipt.get("oracleRevision", source.get("revision")),
            "artifact": receipt.get("artifact", source.get("artifact")),
            "artifactRevision": receipt.get("artifactRevision", source.get("artifactRevision")),
            "totals": {status: counts[status] for status in ("pass", "fail", "unverified")},
            "scoredTotal": len(checks), "checks": names}


def saved_seldon(root, source):
    slot = (root / source["slot"]).resolve()
    records, cursor, _, _ = saved_run(slot)
    setup = next(body["setup"] for _, kind, body in records if kind == "kickoff")
    outcome = next((body for _, kind, body in reversed(records) if kind == "outcome"), {})
    requests = [body for _, kind, body in records if kind == "decision_request"]
    step_count = sum("step" in request["subject"] and request["gate"] != "UNIT" for request in requests)
    gates = Counter(request["gate"] for request in requests
                    if request["gate"] == "UNIT" or "step" not in request["subject"])
    counts, sessions = call_observations(records, slot)
    attempts = [body for _, kind, body in records if kind == "attempt"]
    starts = [body["startedAt"] for body in attempts]
    ends = [body["finishedAt"] for _, kind, body in records if kind in ("attempt_end", "provider_reply")]
    state = cursor["state"]
    candidate = cursor.get("candidate")
    offered = any(request["gate"] == "C" and "step" not in request["subject"]
                  and request["subject"].get("commit") == candidate for request in requests)
    accepted = state == "ACCEPTED" and bool(outcome.get("accepted"))
    scripted = state == "SCRIPTED_C" and bool(outcome.get("scriptedUser"))
    # The controller runs its eligibility check while consuming the C answer. A pending offer has no verdict.
    eligible = (accepted or scripted) and outcome.get("candidate") == candidate
    eligibility = True if eligible else None if offered else False
    gate_a = cursor.get("A")
    approval = next((body for ref, kind, body in records if kind == "approval" and ref == gate_a), {})
    accepted_size = approval.get("subject", {}).get("vision", {}).get("size")
    providers = {encode({key: provider[key] for key in ("provider", "model", "effort") if key in provider})
                 for body in attempts if (provider := body.get("provider"))}
    return {"slot": source["slot"], "state": state, "split": setup.get("split"),
            "sizeFirst": setup.get("sizeFirst"), "acceptedSize": accepted_size,
            "models": [json.loads(item) for item in sorted(providers)],
            "seldonRevision": setup.get("commit"), "candidateRevision": candidate,
            "planRevision": cursor.get("revision"), "cOffered": offered, "eligibleC": eligibility,
            "scriptedC": scripted, "realAcceptance": accepted,
            "verificationComplete": outcome.get("verification_complete"),
            "autonomousC": eligible and not (gates["UNIT"] or step_count),
            "unitQuestions": gates["UNIT"], "stepQuestions": step_count,
            "expectedTouches": {gate: gates[gate] for gate in TOUCHES},
            "providerCalls": sum(wave["calls"] for wave in counts.values()),
            "callsPerWave": counts, "workerSessions": sessions,
            "freshTokens": sum(wave["freshTokens"] for wave in counts.values())
            if all(wave["freshTokens"] is not None for wave in counts.values()) else None,
            "providerActivitySeconds": max(ends) - min(starts) if starts and ends else None}


def saved_plain(root, source):
    usage = read_json(root / source["usage"]) if source.get("usage") else {}
    return {"slot": source.get("slot"), "state": source.get("state"), "model": source.get("model"),
            "effort": source.get("effort"), "candidateRevision": source.get("candidateRevision"),
            "workerSessions": source.get("workerSessions"), "providerCalls": usage.get("providerCalls"),
            "freshTokens": fresh_tokens(usage)}


def saved_arm(root, source, seldon):
    if source is None:
        return None
    arm = saved_seldon(root, source) if seldon else saved_plain(root, source)
    timing = source.get("timing", {})
    billing = source.get("billing", {})
    wall = timing.get("wallSeconds")
    growth = timing.get("growthSeconds") if seldon else None
    finished = arm.get("cOffered") if seldon else arm["state"] == "completed"
    arm.update(startRevision=source.get("startRevision"), oracle=oracle_score(root, source.get("oracle")),
               wallSeconds=wall, humanWaitSeconds=timing.get("humanWaitSeconds"),
               elapsedToStopSeconds=None if finished else wall,
               timeToCSeconds=timing.get("timeToCSeconds") if arm.get("cOffered") else None,
               growthSeconds=growth, growthShare=growth / wall if growth is not None and wall else None,
               billedCost=billing.get("amount"), currency=billing.get("currency"))
    return arm


def compare_pair(seldon, plain):
    left, right = seldon or {}, plain or {}
    ratios, lower_bounds = {}, {}
    finished = bool(left.get("cOffered"))
    baseline_finished = right.get("state") == "completed"
    for metric in RATIOS:
        a, b = left.get(metric), right.get(metric)
        comparable = (metric != "billedCost" or left.get("currency")
                      and left["currency"] == right.get("currency"))
        ratio = a / b if baseline_finished and comparable and a is not None and b and b > 0 else None
        ratios[metric] = ratio if finished else None
        lower_bounds[metric] = ratio if not finished else None
    a, b = left.get("oracle"), right.get("oracle")
    quality = None
    if (a and b and a["oracleRevision"] and a["oracleRevision"] == b["oracleRevision"]
            and a["checks"] == b["checks"] and a["artifactRevision"] and b["artifactRevision"]
            and a["artifactRevision"] == left.get("candidateRevision")
            and b["artifactRevision"] == right.get("candidateRevision")):
        quality = a["totals"]["pass"] >= b["totals"]["pass"] and a["totals"]["fail"] <= b["totals"]["fail"]
    return ratios, lower_bounds, quality


def cohort_summary(pairs):
    medians = {}
    for metric in RATIOS:
        known = [pair["ratios"][metric] for pair in pairs if pair["ratios"][metric] is not None]
        censored = [pair["ratioLowerBounds"][metric] or 0
                    for pair in pairs if pair["ratios"][metric] is None]
        lower = median(known + censored)
        upper = median(known + [float("inf")] * len(censored))
        medians[metric] = lower if lower == upper else None
    qualified = sum(bool(pair["seldon"] and pair["seldon"]["autonomousC"] and pair["sameOrBetterOracle"])
                    for pair in pairs)
    return {"pairs": len(pairs), "medianRatios": medians, "autonomousSameOrBetter": qualified}


def paired_report(path):
    """A manifest enumerates planned starts, including empty arms, and points to saved receipts."""
    manifest = read_json(path)
    entries = manifest["pairs"]
    if not entries or len({item["id"] for item in entries}) != len(entries):
        raise ValueError("Give each planned start a distinct pair ID, including unfinished or empty slots")
    pairs, cohorts = [], {}
    for entry in entries:
        seldon = saved_arm(path.parent, entry.get("seldon"), True)
        plain = saved_arm(path.parent, entry.get("plain"), False)
        ratios, lower_bounds, quality = compare_pair(seldon, plain)
        # Record expected split on empty slots so interrupted or unstarted arms keep their denominator.
        split = (seldon or {}).get("split") or entry.get("split") or "unknown"
        pair = {"id": entry["id"], "split": split, "seldon": seldon, "plain": plain,
                "ratios": ratios, "ratioLowerBounds": lower_bounds, "sameOrBetterOracle": quality}
        pairs.append(pair)
        cohorts.setdefault(split, []).append(pair)
    return {"pairs": pairs, "cohorts": {split: cohort_summary(rows) for split, rows in cohorts.items()},
            "comparisonSetup": manifest.get("comparisonSetup"),
            "costProxy": "freshTokens when comparable billing is unavailable",
            "missingData": "null means unknown; unfinished Seldon slots contribute known lower bounds only"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=PAIRED_HELP,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("slot", type=Path, nargs="?")
    parser.add_argument("--paired", type=Path, metavar="MANIFEST",
                        help="Read a JSON manifest of saved comparison slots without updating RUNS.md")
    parser.add_argument("--runs", type=Path, default=RUNS)
    args = parser.parse_args(argv)
    if args.paired is not None:
        if args.slot is not None:
            parser.error("Select either a single slot or --paired MANIFEST")
        try:
            pairing = paired_report(args.paired)
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
            parser.error(f"Could not read paired evidence from {args.paired}: {error}")
        print(json.dumps(pairing, indent=2), flush=True)
        return 0
    if args.slot is None:
        parser.error("Supply a saved slot or --paired MANIFEST")
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
