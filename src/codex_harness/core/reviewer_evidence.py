"""Review provenance and explicit canonical verdict credit; no admission writes."""


def native_reviewer(reviewer, store=None, *, current=True):
    if not isinstance(reviewer, dict):
        return False
    details = reviewer.get("shardDetails")
    count = reviewer.get("shards")
    return (
        isinstance(reviewer.get("provider"), str)
        and bool(reviewer["provider"].strip())
        and type(count) is int
        and count > 0
        and isinstance(details, list)
        and len(details) == count
        and all(
            isinstance(shard, dict)
            and shard.get("isMocked") is False
            and shard.get("didRunProvider") is True
            and type(shard.get("exitCode")) is int
            and shard["exitCode"] == 0
            and type(shard.get("invalid")) is int
            and shard["invalid"] == 0
            and store is not None
            and bool(shard.get("judgmentId"))
            and (store.judgment(shard["judgmentId"])["positiveCredit"] if current else
                 store._judgment_record(shard["judgmentId"])["status"] == "judged"
                 and store._judgment_record(shard["judgmentId"])["verdict"] == "positive")
            for shard in details
        )
    )


def review_tier(reviewers):
    return ("same-model" if len({(r["provider"], r.get("model")) for r in reviewers}) == 1
            else "same-provider" if len({r["provider"] for r in reviewers}) == 1 else "cross-provider")


def review_binding(store, member_ids=(), *, convergence=False, run_id=None):
    """One live binding for review authority; views never infer its credit."""
    def observed(key, read):
        # One read shares observations; every writer reads live bindings.
        snapshot = getattr(store, "_review_read_snapshot", None)
        if snapshot is None:
            return read()
        if key not in snapshot:
            snapshot[key] = read()
        return snapshot[key]
    binding = dict(memberIds=sorted(member_ids), convergence=convergence, runId=run_id,
                   revision=observed("revision", store._revision), evidence=observed("evidence", store.review_nodes_digest))
    if member_ids:
        members = {row["id"]: row for row in finding_records(store)}
        acceptance = {key: row for key, row in members.items() if row.get("stage") == "acceptance"}
        binding["members"] = [members.get(key, {}) for key in binding["memberIds"]]
        if set(member_ids) & acceptance.keys() and run_id is None:
            from codex_harness.core.acceptance import _latest_run
            latest = _latest_run(store)
            binding["runId"] = latest["id"] if latest else None
        if set(member_ids) & acceptance.keys():
            binding["acceptanceRun"] = None
    if binding["runId"] is not None or "acceptanceRun" in binding:
        from codex_harness.core.acceptance import current_run
        binding["acceptanceRun"] = observed("acceptanceRun", lambda: current_run(store))
    if convergence:
        binding["credits"] = observed("reviewCredits", lambda: [dict(id=row["id"], reuse=row.get("reuseJudgmentId"))
                              for row in store.successful_reviews(store.manifest_revision())])
    return binding


def finding_records(store):
    """Immutable source facts only; archived mutable columns confer no authority."""
    import json
    def read():
        rows = [json.loads(row[0]) for row in store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='finding-recorded' ORDER BY id")]
        tables = {row[0] for row in store.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "review_findings" in tables:
            rows.extend(dict(row) for row in store.connection.execute("SELECT * FROM review_findings ORDER BY rowid"))
        rows.extend(json.loads(row[0]) for row in store.connection.execute("SELECT result_json FROM acceptance_findings ORDER BY rowid"))
        derived = {"status", "resolution", "resolved_at", "cluster_id", "state", "judgmentIds"}
        return [{k: v for k, v in row.items() if k not in derived} for row in rows]
    snapshot = getattr(store, "_review_read_snapshot", None)
    if snapshot is None:
        return read()
    if "findingRecords" not in snapshot:
        snapshot["findingRecords"] = read()
    return snapshot["findingRecords"]
