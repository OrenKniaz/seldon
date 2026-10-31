"""Pure review evidence checks and display text; no store reads or admission writes."""


def native_reviewer(reviewer):
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
            for shard in details
        )
    )


def single_provider_notice(result):
    provider = result["reviewers"][0]["provider"]
    return f"Reviewed by two {provider} reviewers; only one provider was available."
