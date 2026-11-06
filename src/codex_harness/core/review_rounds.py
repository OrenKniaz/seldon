"""Planning review invocation, retained diagnostics and round convergence."""
from __future__ import annotations

import json
import math
import re
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from codex_harness.core import review_packets, review_provider
from codex_harness.core.approvals import require_approval, require_reviewers
from codex_harness.core.plan_quality import quality_report
from codex_harness.core.plan_store import PlanStore, PlanStoreError, _now
from codex_harness.core.review_findings import _parse_review_return
from codex_harness.core.review_packets import bind_review_dossiers
from codex_harness.core.review_provider import _launch_review, coverage, packet_binding
from codex_harness.core.reviewer_evidence import finding_records
from codex_harness.core.worker_packet_budget import PROVIDER_TOKEN_BUDGETS

def _table(store: PlanStore) -> None:
    store.connection.execute(
        "CREATE TABLE IF NOT EXISTS review_runs ("
        "id TEXT PRIMARY KEY, round_id TEXT NOT NULL, plan_revision TEXT NOT NULL, "
        "reviewer TEXT NOT NULL, provider TEXT NOT NULL, model TEXT, exit_code INTEGER, "
        "findings INTEGER NOT NULL, invalid INTEGER NOT NULL, "
        "started_at TEXT NOT NULL, finished_at TEXT NOT NULL)"
    )
    for table in ("review_runs",):
        columns = {row[1] for row in store.connection.execute(f"PRAGMA table_info({table})")}
        for name, kind in (("packetBound", "INTEGER"), ("outOfPacketTools", "TEXT")):
            if name not in columns:
                store.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    columns = {row[1] for row in store.connection.execute("PRAGMA table_info(review_runs)")}
    for name, kind in (
        ("isMocked", "INTEGER"),
        ("didRunProvider", "INTEGER"),
        ("shards", "INTEGER"),
        ("shardDetails", "TEXT"),
        ("truncatedNodes", "TEXT"),
        ("reshardedFrom", "TEXT"),
        ("nodesDigest", "TEXT"),
    ):
        if name not in columns:
            store.connection.execute(f"ALTER TABLE review_runs ADD COLUMN {name} {kind}")

def _event(store: PlanStore, kind: str, payload: dict) -> None:
    if callable(getattr(store, "_event", None)):
        store._event(kind, payload)

def _diagnostic(store, result, record, launch, output, stderr) -> None:
    """Persist the exact failure without environment values or the stdin packet."""
    path = (
        store.path.parent
        / "reviews"
        / result.get("roundId", result.get("id"))
        / (uuid4().hex + ".json")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "command": list(launch.command) if launch else [],
                "stdout": output,
                "stderr": stderr,
                "exitCode": record["exitCode"],
                "rejected": record["rejected"],
                "primaryError": record.get("primaryError"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    record["artifactPath"] = str(path)
    record["stderrPreview"] = "\n".join(stderr.splitlines()[-10:])

def _review_shard(
    store, result, reviewer, shard, *, executable, runner, timeout, capture=None, before_launch=None
):
    """Capture and persist provider errors before attempting temporary-dir cleanup."""
    record = {key: value for key, value in shard.items() if key != "packet"}
    record.update(
        exitCode=None, rejected=[], invalid=0, isMocked=runner is not None, didRunProvider=False
    )
    launch, output, stderr, directory = None, "", "", None
    items = []
    checking_launch = False
    attempted = False
    started_at = _now()
    try:
        if "\n## Bound review requests\n" not in shard["packet"]:
            bind_review_dossiers(store, {reviewer["provider"]: [shard]}, [reviewer])
        judgment = store.review_judgment(reviewer, shard)
        launch = _launch_review(reviewer, shard["packet"], executable)
        directory = TemporaryDirectory(prefix="harness-roundtable-")
        if before_launch is not None or runner is None:
            checking_launch = True
            if before_launch is not None:
                before_launch()
            if runner is None:
                require_approval(store, "B", dict(reviewer=reviewer))
            checking_launch = False
        attempted = True
        while True:
            response = (runner if runner is not None else review_provider._run)(launch, Path(directory.name), timeout)
            record["didRunProvider"] = runner is None
            record["exitCode"], output = response[:2]
            stderr = response[2] if len(response) > 2 else ""
            record, items = _parse_review_return(store, reviewer, shard, response, mocked=runner is not None)
            if record.get("disposition") != "exact-fix" or capture is not None:
                break
            judgment = store.judgment(record["judgmentId"])
            launch = replace(launch, stdin_text=launch.stdin_text + "\nExact fix required: " + judgment["error"])
        if capture is not None:
            capture(reviewer, shard, launch, executable, response)
        if record["invalid"]:
            _diagnostic(store, result, record, launch, output, stderr)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        if checking_launch:
            raise
        if isinstance(error, subprocess.TimeoutExpired):

            def decoded(value):
                return (
                    value.decode("utf-8", errors="replace")
                    if isinstance(value, bytes)
                    else value or ""
                )

            output, stderr = decoded(error.stdout), decoded(error.stderr)
        result_errors = []
        overflow_evidence = [stderr, str(error)]
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if record["exitCode"] not in (None, 0):
                    overflow_evidence.append(line)
                continue
            if isinstance(event, dict) and event.get("type") == "result" and event.get("is_error"):
                result_errors.append(event)
            if isinstance(event, dict) and (
                event.get("type") == "error"
                or (event.get("type") == "result" and event.get("is_error"))
            ):
                # Successful text and usage metadata are not provider errors.
                overflow_evidence.extend(
                    json.dumps(event[key], ensure_ascii=False)
                    for key in ("message", "error", "result", "errors")
                    if key in event
                )
        error_text = "\n".join(overflow_evidence)
        reason = str(error)
        if result_errors:
            reason += ": " + json.dumps(result_errors[-1], ensure_ascii=False)
        elif stderr:
            reason += ": " + "\n".join(stderr.splitlines()[-10:])
        elif record["exitCode"] not in (None, 0) and output:
            reason += ": " + "\n".join(output.splitlines()[-10:])
        record["primaryError"] = {
            "exitCode": record["exitCode"],
            "stderrTail": "\n".join(stderr.splitlines()[-10:]),
            "resultErrors": result_errors,
            "promptTooLong": bool(
                re.search(
                    r"prompt.{0,30}too (?:long|large)|"
                    r"context.{0,40}(?:exceed|overflow|too (?:long|large)|limit reached)|"
                    r"maximum.{0,30}(?:context|tokens)|too many tokens",
                    error_text,
                    re.IGNORECASE,
                )
            ),
            "reason": reason,
        }
        record["rejected"].append({"status": "rejected-unparsed", "reason": reason})
        record["invalid"] = 1
        _diagnostic(store, result, record, launch, output, stderr or str(error))
    finally:
        from codex_harness.core.reviewer_access import observe_access
        record["launchSelection"] = {
            "provider": reviewer["provider"],
            "model": reviewer.get("model"),
            "effort": reviewer.get("effort"),
            "startedAt": started_at,
            "finishedAt": _now(),
        }
        record["accessObservation"] = observe_access(
            attempted=attempted,
            mocked=runner is not None,
            succeeded=not record["invalid"],
            error=record.get("primaryError", {}).get("reason", ""),
        )
        record.update(packet_binding(output, reviewer["provider"]))
        if directory is not None:
            for attempt in range(3):
                try:
                    directory.cleanup()
                    break
                except OSError as error:
                    if attempt == 2:
                        record["cleanupWarning"] = str(error)
                    else:
                        time.sleep(0.05 * (attempt + 1))
    return record, items

def _review_dossier(
    store,
    result,
    reviewer,
    record,
    *,
    executable,
    runner,
    timeout,
    token_budget=None,
    shards=None,
    stop_on_invalid=False,
    capture=None,
    abandon=None,
    before_launch=None,
    **packet_options,
):
    """Replace overflowed dossiers once; the request owner retains obligations."""
    provider = reviewer["provider"]
    if provider not in PROVIDER_TOKEN_BUDGETS:
        raise ValueError(
            f"Unknown provider {provider!r}; use one of: {', '.join(PROVIDER_TOKEN_BUDGETS)}"
        )
    budget = PROVIDER_TOKEN_BUDGETS[provider] if token_budget is None else token_budget
    if shards is None:
        shards = review_packets.packet_shards(store, provider, token_budget=budget, **packet_options)
    for attempt in range(2):
        completed = []
        record["shards"] = len(shards)
        record["shardDetails"] = []
        record["truncatedNodes"] = sorted(
            {key for shard in shards for key in shard["truncatedNodes"]}
        )
        for shard in shards:
            shard_record, items = _review_shard(
                store,
                result,
                reviewer,
                shard,
                executable=executable,
                runner=runner,
                timeout=timeout,
                capture=capture,
                **({"before_launch": before_launch} if before_launch is not None else {}),
            )
            record["shardDetails"].append(shard_record)
            completed.append((shard_record, items))
            if shard_record.get("primaryError", {}).get("promptTooLong"):
                break
            if stop_on_invalid and shard_record["invalid"]:
                yield from completed
                return
        else:
            yield from completed
            return
        if attempt == 1:
            yield from completed
            return
        record["reshardedFrom"] = {
            "budget": budget,
            "shards": len(shards),
            "shardDetails": record["shardDetails"],
            "reason": shard_record["primaryError"]["reason"],
        }
        if abandon is not None:
            abandon()
            capture = None
            abandon = None
        budget = max(1, budget // 2)
        record["reshardedFrom"]["newBudget"] = budget
        try:
            shards = review_packets.packet_shards(store, provider, token_budget=budget, **packet_options)
        except ValueError as error:
            # A header that cannot fit must not hide the actual provider failure.
            record["reshardedFrom"]["reshardError"] = str(error)
            yield from completed
            return
        prior = record["reshardedFrom"]["shardDetails"][0]
        for shard in shards:
            shard["reviewRoundId"] = prior["reviewRoundId"]
        bind_review_dossiers(store, {provider: shards}, [reviewer],
            candidate_run={"id": prior["acceptanceRunId"]} if prior["acceptanceRunId"] else None)
        record["reshardedFrom"]["newShardCount"] = len(shards)

def _admit_review(store, reviewer, run_id, revision, *, mocked=False):
    with store._transaction():
        if store.manifest_revision() != revision:
            raise PlanStoreError("roundtable-plan-changed", "Review the new plan revision")
        if not mocked:
            require_approval(store, "B", dict(reviewer=reviewer))
        _table(store)
        store.connection.execute(
            "CREATE TABLE IF NOT EXISTS review_attempts (id TEXT PRIMARY KEY, "
            "plan_revision TEXT, reviewer TEXT, provider TEXT)"
        )
        store.connection.execute(
            "INSERT OR IGNORE INTO review_attempts VALUES (?, ?, ?, ?)",
            (run_id, revision, reviewer["name"], reviewer["provider"]),
        )

def run_roundtable(
    store: PlanStore,
    *,
    reviewers=None,
    runner=None,
    executable_for=None,
    timeout=900,
    token_budgets=None,
) -> dict:
    """Run provider-normal reviews, then atomically store findings and the result.

    The injected runner receives (launch, empty temporary cwd, timeout). Stale
    reviews are refused if the canonical plan changes before persistence. Injected
    callbacks are always mocked and cannot earn provider-execution credit.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    spine = store.spine()
    revision = store.manifest_revision()
    if revision is None:
        raise ValueError(
            "Review requires a compilable manifest: add validation and requirementFiles"
        )
    report = quality_report(store)
    selected = (
        require_approval(store, "B")["subject"]["reviewers"]
        if reviewers is None
        else list(reviewers)
    )
    result = {
        "roundId": uuid4().hex,
        "planRevision": revision,
        "reviewers": [],
        "crossSubscription": len({reviewer["provider"] for reviewer in selected}) > 1,
        "findings": 0,
        "invalid": 0,
    }
    runs = []
    identities = [(item["name"], item["provider"]) for item in selected]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate reviewer in roundtable")
    if runner is None:
        require_reviewers(store, selected)
    dossiers = {
        provider: review_packets.packet_shards(
            store, provider, report=report, token_budget=(token_budgets or {}).get(provider)
        )
        for provider in dict.fromkeys(item["provider"] for item in selected)
    }
    bind_review_dossiers(store, dossiers, selected, invocation_id=result["roundId"])
    result.update(coverage(selected))
    for reviewer in selected:
        started_at = _now()
        record = {**reviewer, "exitCode": 0, "findings": 0, "invalid": 0, "rejected": []}
        run_id = uuid4().hex

        result["reviewers"].append(record)
        provider = reviewer["provider"]
        for shard_record, items in _review_dossier(
            store,
            result,
            reviewer,
            record,
            executable=review_provider._executable(provider, executable_for),
            runner=runner,
            timeout=timeout,
            token_budget=(token_budgets or {}).get(provider),
            report=report,
            shards=dossiers[provider],
            before_launch=lambda: _admit_review(store, reviewer, run_id, revision, mocked=runner is not None),
        ):
            if shard_record["invalid"] and not record["rejected"]:
                record["exitCode"] = shard_record["exitCode"]
            shard_record["invalid"] = len(shard_record["rejected"])
            record["rejected"].extend(shard_record["rejected"])
            for name in ("artifactPath", "stderrPreview", "primaryError", "cleanupWarning"):
                if name in shard_record:
                    record.setdefault(name, shard_record[name])
        record["outOfPacketTools"] = list(
            dict.fromkeys(
                tool for shard in record["shardDetails"] for tool in shard["outOfPacketTools"]
            )
        )
        record["packetBound"] = not record["outOfPacketTools"]
        record["isMocked"] = runner is not None
        record["didRunProvider"] = bool(record["shardDetails"]) and all(
            shard["didRunProvider"] for shard in record["shardDetails"]
        )
        record["findings"] = sum(row.get("round_id") == result["roundId"] and row["reviewer"] == reviewer["name"]
                                 and row["provider"] == provider for row in finding_records(store))
        record["invalid"] = len(record["rejected"])
        result["findings"] += record["findings"]
        result["invalid"] += record["invalid"]
        runs.append(
            (
                run_id,
                result["roundId"],
                revision,
                reviewer["name"],
                provider,
                reviewer.get("model"),
                record["exitCode"],
                record["findings"],
                record["invalid"],
                started_at,
                _now(),
                record["packetBound"],
                json.dumps(record["outOfPacketTools"]),
                record["shards"],
                json.dumps(record["shardDetails"]),
                json.dumps(record["truncatedNodes"]),
                json.dumps(record.get("reshardedFrom")),
                record["isMocked"],
                record["didRunProvider"],
            )
        )
    with store._transaction():
        if store.manifest_revision() != revision:
            raise PlanStoreError("roundtable-plan-changed", "Review the new plan revision")
        _table(store)
        snapshot = json.dumps(store.review_nodes_digest()) if spine.semantic else None
        store.connection.executemany(
            "INSERT INTO review_runs "
            "(id, round_id, plan_revision, reviewer, provider, model, exit_code, "
            "findings, invalid, started_at, finished_at, packetBound, outOfPacketTools, "
            "shards, shardDetails, truncatedNodes, reshardedFrom, isMocked, didRunProvider, "
            "nodesDigest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(*run, snapshot) for run in runs],
        )
        result["clusters"] = store.cluster_review_findings(result["roundId"])
        result["packetBound"] = all(item["packetBound"] for item in result["reviewers"])
        result["outOfPacketTools"] = sorted(
            {tool for item in result["reviewers"] for tool in item["outOfPacketTools"]}
        )
        result["isMocked"] = runner is not None
        result["didRunProvider"] = bool(result["reviewers"]) and all(
            item["didRunProvider"] for item in result["reviewers"]
        )
        result["successfulReviews"] = store.successful_reviews(revision)
        result["crossSubscription"] = len({row["provider"] for row in result["successfulReviews"]}) > 1
        result["convergence"] = store.review_convergence(prepare=True)
        _event(store, "roundtable-completed", result)
    return result
