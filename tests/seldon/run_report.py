"""C2/C4/C23, charter §6: read-only dev-run findings; CODE counts, no LLM.

Usage: python run_report.py SLOT [--runs PATH]
Tags are explicit vocabulary hints, never semantic verdicts. Empty fields can be
legitimate; reporting them does not assert a schema error or unused code field.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import re
import sqlite3


RUNS = Path("C:/Users/oren/codex-harness/work/ladder/RUNS.md")
LEAK = re.compile(r"check[- ]in|\bbeads?\b|\bwaves?\b|evidence[-_ ]dir|review process|SELDON_", re.I)
PRODUCT = re.compile(r"\b(product|feature|button|page|UI|API|application)\b", re.I)


def load(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload(value):
    # Retained early calls predate the answer envelope.
    answer = value.get("answer", value) if isinstance(value, dict) else {}
    return answer if isinstance(answer, dict) else {}


def read_answer(folder):
    try:
        return load(folder / "answer.txt", {})
    except json.JSONDecodeError:
        return {}  # Rejected malformed JSON is detailed in provider_reply.errors.


def empty_fields(value, schema, path="answer"):
    """Walk only fields actually present in both the answer and its schema."""
    if "anyOf" in schema:
        branches = schema["anyOf"]
        if isinstance(value, dict):
            def match(branch):
                fields = branch.get("properties", {})
                return (sum(value[k] in field["enum"] for k, field in fields.items()
                            if k in value and "enum" in field), len(set(value) & set(fields)))
            branches = sorted(branches, key=match, reverse=True)
        yield from empty_fields(value, branches[0], path)
    elif isinstance(value, dict):
        for key, child in value.items():
            if key in schema.get("properties", {}):
                yield from empty_fields(child, schema["properties"][key], path + "." + key)
    elif value is None or value == [] or (isinstance(value, str) and
            value.strip().lower() in ("", "todo", "tbd", "n/a", "placeholder", "unknown")):
        yield path
    elif isinstance(value, list):
        for child in value:
            yield from empty_fields(child, schema.get("items", {}), path + "[]")


def report(slot):
    slot = Path(slot).resolve()
    databases = list(slot.glob("**/.seldon/state.sqlite3"))
    if len(databases) > 1:
        raise ValueError("Multiple run stores; select a slot with one canonical store")
    rows, cursor, plans = [], {}, []
    if databases:
        db = sqlite3.connect(databases[0].as_uri() + "?mode=ro", uri=True)
        try:
            rows = [(f"r{i}", k, json.loads(b)) for i, k, b in
                    db.execute("SELECT id,kind,body FROM record ORDER BY id")]
            row = db.execute("SELECT cursor FROM run WHERE id=1").fetchone()
            cursor = json.loads(row[0]) if row else {}
            plans = [json.loads(r[0]) for r in db.execute("SELECT document FROM plan_revision ORDER BY id")]
        finally:
            db.close()
    records = {ref: body for ref, _, body in rows}
    transitions = [b for _, k, b in rows if k == "transition"]
    reached = [b["next"] for b in transitions if b["next"] not in ("INCOMPLETE", "ACCEPTED", "SCRIPTED_C")]
    furthest = reached[-1] if reached else cursor.get("state", "unknown")
    outcome = next((b for _, k, b in reversed(rows) if k == "outcome"), {})
    accepted = cursor.get("state") == "ACCEPTED" and outcome.get("accepted") is True
    scripted = cursor.get("state") == "SCRIPTED_C" and outcome.get("scriptedUser") is True and outcome.get("verification_complete") is True and not outcome.get("accepted")
    terminal = "SCRIPTED_C" if scripted else "ACCEPTED" if accepted else "INCOMPLETE"
    reason = outcome.get("reason", "No terminal outcome recorded; state " + cursor.get("state", "unknown"))
    # Early core embedded the entire review in reason; show its human rationale.
    prefix, separator, detail = reason.partition(": ")
    if separator and detail.startswith('{"'):
        try:
            rationale = payload(json.loads(detail)).get("why")
        except json.JSONDecodeError:
            rationale = None
        if rationale:
            reason = prefix + ": " + rationale
    calls = []
    for folder in sorted((slot / "calls").glob("call-*")):
        q = load(folder / "question.json", {})
        a = read_answer(folder)
        calls.append((folder, q, payload(a), load(folder / "result.json", {})))
    replies = [(ref, b) for ref, k, b in rows if k == "provider_reply"]
    repairs = [b for b in transitions if b["next"] == "REPAIR"]
    retries = sum(bool(q.get("context", {}).get("exactFix")) for _, q, _, _ in calls)
    wrong, empties, efficiency = Counter(), Counter(), defaultdict(Counter)
    supplied, cited, seen = defaultdict(set), defaultdict(set), Counter()
    gaps, uncertain, blocked, repeats, refused = [], [], [], [], []
    errors = []
    for ref, body in replies:
        for error in body.get("errors", []):
            field = error.split(":", 1)[0]
            wrong[field] += 1
            errors.append(f"{ref}: {error}")
            if "citation" in error.lower():
                refused.append(f"{ref}: {error}")
    for folder, q, answer, result in calls:
        state = q.get("kind", "unknown")
        context = q.get("context", {})
        label = state + ("(" + str(context.get("purpose", context.get("task", context.get("gate")))) + ")"
                         if any(k in context for k in ("purpose", "task", "gate")) else "")
        stats = efficiency[label]
        stats["calls"] += 1
        stats["seconds"] += result.get("seconds", 0)
        for name, key in (("prompt.txt", "promptBytes"), ("answer.txt", "answerBytes")):
            path = folder / name
            stats[key] += path.stat().st_size if path.is_file() else 0
        usage = result.get("usage")
        if usage is None and (folder / "stdout.bin").is_file():
            for line in (folder / "stdout.bin").read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("type") == "turn.completed":
                    usage = event.get("usage")
        if isinstance(usage, dict):
            stats["usageCalls"] += 1
            for key, value in usage.items():
                if isinstance(value, (int, float)):
                    stats[key] += value
        supplied[label].update(q.get("facts", {}))
        cited[label].update(answer.get("citations", []))
        signature = encode({"kind": state, "facts": q.get("facts", {}),
                            "context": {k: v for k, v in context.items() if k != "exactFix"}})
        if seen[signature]:
            repeats.append(f"{label} {folder.name}")
        seen[signature] += 1
        if answer.get("verdict") == "uncertain":
            uncertain.append(f"{label}: {encode(answer.get('neededFacts', []))}")
        if state == "WORK" and answer.get("outcome") == "blocked":
            blocked.append(f"{folder.name}: {encode(answer)}")
        if state in ("REVIEW", "JUDGE"):
            provider = load(folder / "invocation.json", {}).get("provider", {})
            for gap in answer.get("gaps", []):
                text = gap.get("target", "") + " " + gap.get("correction", "")
                tag = "SELDON-LEAK" if LEAK.search(text) else "PRODUCT" if PRODUCT.search(text) else "UNTAGGED"
                gaps.append(f"{label} {encode(provider)} [{tag}] target: {gap.get('target', '')}\n  correction: {gap.get('correction', '')}")
        empties.update(empty_fields(read_answer(folder), q.get("schema", {})))
    mismatches, red = [], []
    for ref, kind, body in rows:
        if kind == "judgment":
            answer = payload(records.get(body.get("answer"), {}))
            for obs_ref in body.get("observations", []):
                obs = records.get(obs_ref, {})
                code = obs.get("exitCode")
                expected = code != 0 if body.get("purpose") == "RED" else code == 0
                if code is not None and expected != (answer.get("verdict") == "satisfied"):
                    mismatches.append(f"{ref}/{obs_ref} {body.get('purpose')}: {obs.get('test')} exit={code}, judge={answer.get('verdict')}")
            if body.get("purpose") == "RED":
                red.append(f"{ref}: {answer.get('why', 'no reason recorded')}")
    plan = plans[-1] if plans else {}
    plan_bytes = len(encode(plan).encode("utf-8")) if plans else 0
    # No run clock was recorded: use the artifact envelope, not summed call time.
    stamps = [p.stat().st_mtime for f, _, _, _ in calls
              for p in (f / "question.json", f / "answer.txt", f / "result.json") if p.is_file()]
    wall = max(stamps) - min(stamps) if stamps else None
    wall_text = f"{wall:.3f}s (artifact envelope; setup/teardown unknown)" if wall is not None else "unknown"
    flags = len(repairs) + retries + len(refused) + len(uncertain) + len(repeats) + len(blocked) + int(not (accepted or scripted))
    lines = ["CHARTER: serves C2/C4/C23 — violates no I#",
             f"!!! RED FLAGS: {flags} events (categories can overlap); target: zero repairs, zero retries !!!",
             f"Repairs {len(repairs)}; exact-fix retries {retries}; refused citation errors {len(refused)}; uncertain {len(uncertain)}; unchanged-fact repeats {len(repeats)}; blocked workers {len(blocked)}."]
    for repair in repairs:
        lines.append("REPAIR cause " + str(repair.get("result")) + ": " + encode(records.get(repair.get("result"), {})))
    for title, items in (("Exact-fix errors", errors), ("Refused citations", refused),
                         ("Uncertain", uncertain), ("Repeated questions", repeats), ("Blocked workers", blocked)):
        if items:
            lines.append(title + ": " + "; ".join(items))
    lines += [f"OUTCOME: {terminal}; furthest {furthest}; {reason}; wall {wall_text}."]
    if outcome.get("nextAction"):
        lines.append("Next: " + str(outcome["nextAction"]))
    lines += ["REVIEW LOG (vocabulary tags only):", *(gaps or ["No reviewer/judge gaps recorded."]),
              "SCHEMA: wrong fields (error occurrences): " + (encode(wrong.most_common()) if wrong else "none recorded"),
              "Empty/placeholder schema fields (may be legitimate): " + (encode(dict(empties)) if empties else "none"),
              "Code field usage is not recorded; absence of citations does not prove a fact was ignored.",
              "SEMANTIC: supplied fact handles never cited, per state:"]
    lines += [f"  {state}: " + (", ".join(sorted(supplied[state] - cited[state])) or "none") for state in efficiency]
    disagreement = "; ".join(mismatches) or ("none" if any(k == "observation" for _, k, _ in rows) else "none recorded (no observations)")
    lines += ["Test/judge disagreements: " + disagreement,
              "RED reasons: " + ("; ".join(red) or "none recorded"), "EFFICIENCY: call wall time excludes local work; tokens as recorded (overlapping token categories):"]
    for state, stats in efficiency.items():
        tokens = {k: v for k, v in stats.items() if "token" in k.lower()}
        lines.append(f"  {state}: {stats['calls']} calls, {stats['seconds']:.3f}s, prompt {stats['promptBytes']} bytes, answer {stats['answerBytes']} bytes; tokens {encode(tokens) if tokens else 'unknown'} ({stats['usageCalls']}/{stats['calls']} calls).")
    lines.append(f"Plan (latest of {len(plans)} revisions): {len(plan.get('nodes', {}))} nodes, {plan_bytes} bytes (canonical JSON). Total calls: {len(calls)}.")
    kickoff = next((b for _, k, b in rows if k == "kickoff"), {})
    setup = kickoff.get("setup", {})
    commit = setup.get("commit", "unknown (not recorded)")
    summary = [datetime.fromtimestamp(min(stamps), timezone.utc).date().isoformat() if stamps else "unknown",
               str(slot), commit, terminal, furthest, len(repairs), retries,
               len(gaps), len(uncertain), f"{wall:.3f}s*" if wall is not None else "unknown", len(calls),
               len(plan.get("nodes", {})), plan_bytes, flags, setup.get("questionContract", "unknown"),
               hashlib.sha256(encode(plan["nodes"]["vision"]["body"]).encode()).hexdigest() if plan else "unknown"]
    return "\n".join(lines), summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slot", type=Path)
    parser.add_argument("--runs", type=Path, default=RUNS, help="override ledger for offline checks")
    args = parser.parse_args(argv)
    output, row = report(args.slot)
    print(output, flush=True)
    args.runs.parent.mkdir(parents=True, exist_ok=True)
    with args.runs.open("a+", encoding="utf-8") as ledger:
        ledger.seek(0)
        retained = ledger.read()
        if retained and "| Question contract |" not in retained:
            retained = retained.replace("| RED FLAG COUNT |", "| RED FLAG COUNT | Question contract | Vision lock |", 1)
            retained = retained.replace("|" + " --- |" * 14, "|" + " --- |" * 16, 1)
            ledger.seek(0)
            ledger.write(retained)
            ledger.truncate()
        ledger.seek(0, 2)
        if ledger.tell() == 0:
            ledger.write("# Dev runs\n\n*Wall time is the retained call artifact envelope; run commit unknown unless recorded. Counts may overlap.*\n\n")
            ledger.write("| Date (UTC) | Slot | Commit | Outcome | Furthest state | Repairs | Retries | Gaps | Uncertain | Wall time | Calls | Plan nodes | Plan bytes | RED FLAG COUNT | Question contract | Vision lock |\n")
            ledger.write("|" + " --- |" * len(row) + "\n")
        ledger.write("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in row) + " |\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
