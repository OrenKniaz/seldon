"""Sealed historical finding originals, independent of current execution admission."""

import json
from codex_harness.core import review_provider
from codex_harness.core.acceptance_runs import run_subject


def read_execution_finding_origins(store, finding_ids):
    """Select sealed historical AF originals, without today's candidate validation.

    This does not admit/recover a review or relax final acceptance. Historical
    product identity is preserved separately from the current fulfillment run.
    """
    from codex_harness.core import acceptance_recovery as recovery

    if not finding_ids or len(set(finding_ids)) != len(finding_ids):
        raise ValueError("execution-origin-members-invalid")
    findings = {item["id"]: item for item in store.acceptance_findings()}
    reviews = [
        json.loads(row[0])
        for row in store.connection.execute(
            "SELECT result_json FROM acceptance_reviews ORDER BY rowid"
        )
    ]
    selected = []
    for key in finding_ids:
        finding = findings.get(key)
        owners = [review for review in reviews if key in review.get("findingIds", [])]
        if (
            not finding
            or not key.startswith("AF-")
            or finding.get("stage") != "acceptance"
            or len(owners) != 1
        ):
            raise ValueError("execution-genuine-admitted-af-required")
        review = owners[0]
        attempt = recovery.ReviewAttempt.load(store, review["attemptId"])
        header = attempt.header
        admitted = recovery._events(store, "acceptance-reviewed", attempt.id)
        source = store.connection.execute(
            "SELECT result_json FROM acceptance_runs WHERE id=?", (finding["runId"],)
        ).fetchone()
        if (
            source is None
            or json.loads(source[0]) != header["run"]
            or header["workspace"] != str(store.workspace.resolve())
            or header["repo"] != header["run"]["repo"]
            or header["isMocked"] is not False
            or header["ineligible"]
            or run_subject(header["run"]) != "product"
            or review.get("runId") != finding["runId"]
            or review.get("isMocked") is not False
            or recovery._events(store, recovery.ABANDONED, attempt.id)
            or len(admitted) != 1
            or admitted[0][1] != review
            or any(review.get(name) != value for name, value in attempt.references().items())
        ):
            raise ValueError("execution-af-historical-admission-mismatch")
        if any(
            member not in findings
            or recovery.digest(
                {
                    name: value
                    for name, value in findings[member].items()
                    if name not in {"status", "resolution", "state", "judgmentIds", "cluster_id"}
                }
            )
            != review["findingHashes"].get(member)
            for member in review["findingIds"]
        ):
            raise ValueError("execution-af-original-finding-changed")
        parts = attempt.read_returns()
        parsed_findings = [(owner, item) for owner, _, items, _ in parts for item in items]
        completions = dict(recovery._events(store, recovery.COMPLETED, attempt.id))
        originals = [
            dict(slot=slot, completion=completions[event_id])
            for slot, (_, _, _, event_id) in zip(header["slots"], parts, strict=True)
        ]
        if not any(
            reviewer["name"] == finding["reviewer"]
            and reviewer["provider"] == finding["provider"]
            and all(finding.get(name) == item.get(name) for name in review_provider._FIELDS)
            for reviewer, item in parsed_findings
        ):
            raise ValueError("execution-af-not-in-original-output")
        selected.append(
            dict(
                finding=finding,
                sourceRun=header["run"],
                review=review,
                registration=header,
                originals=originals,
            )
        )
    return selected
