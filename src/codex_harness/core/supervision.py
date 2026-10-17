"""Read-only observations, never execution policy or a supervision-savings claim."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from codex_harness.core.plan_store import open_plan_store_readonly
from codex_harness.core.record_io import open_record_read
from codex_harness.core.spine import Spine, SpineError, impact
from codex_harness.core.steering import steering_report

SOURCE_BASE = "2466c25955900c0d43dca702149a1c83bfd9dc1a"
DIMENSIONS = (
    "changedPaths",
    "redispatchedBeads",
    "revalidatedBeads",
    "reusedBeads",
    "invalidatedClaimIds",
    "retainedClaimIds",
    "docPaths",
)
ORIGINS = ("human", "proxy", "agent-driver", "unknown")


def _json(path):
    with open_record_read(path) as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("report-input-exceeds-1MiB")
    value = json.loads(raw.decode("utf-8"), parse_constant=lambda value: _invalid(value))
    if not isinstance(value, dict):
        raise ValueError("report-input-must-be-object")
    return value, raw


def _invalid(value):
    raise ValueError(f"Nonfinite JSON: {value}")


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except ValueError:
        return None


def _git(repo, *args):
    return subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(repo), *args],
        capture_output=True,
        check=True,
        timeout=15,
    ).stdout


def _commit(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40,64}", value):
        raise ValueError("original-candidate-commit-unavailable")
    return value


def _contained(path, roots):
    path = Path(path).resolve()
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError("evidence-path-outside-canonical-roots")
    return path


def _run(directory):
    directory = Path(directory).resolve()
    path = _contained(directory / "evidence/run.json", [directory])
    record, raw = _json(path)
    return record, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def _candidate(record, repo):
    proof = record.get("integrationValidation") or {}
    if not isinstance(proof, dict):
        raise ValueError("original-integration-candidate-unavailable")
    commit = _commit(proof.get("integrationCommit"))
    tree = _git(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    if tree != proof.get("candidateTree"):
        raise ValueError("original-candidate-tree-mismatch")
    return {"candidateCommit": commit, "candidateTree": tree}


def interventions(path, record):
    """Caller-declared origins/coverage stay declared, never verified-human evidence."""
    result = {
        "knownEvents": None,
        "knownByOrigin": dict.fromkeys(ORIGINS),
        "knownActiveMinutesByOrigin": dict.fromkeys(ORIGINS, None),
        "total": None,
        "activeMinutes": None,
        "idleMinutes": None,
        "wallMinutes": None,
        "coverage": "unavailable",
        "reason": "No explicit intervention log",
        "humanOriginVerified": False,
        "evidenceRefs": [],
    }
    if path is None:
        return result
    log, raw = _json(path)
    result["evidenceRefs"] = [
        {"path": str(Path(path).resolve()), "sha256": hashlib.sha256(raw).hexdigest()}
    ]
    events = log.get("events")
    if not isinstance(events, list) or not all(isinstance(event, dict) for event in events):
        raise ValueError("intervention-events-invalid")
    unique, conflicts = {}, set()
    for index, event in enumerate(events):
        key = event.get("id")
        key = key if isinstance(key, str) and key else ("unidentified", index)
        if key in unique and unique[key] != event:
            conflicts.add(key)
        unique.setdefault(key, event)
    events = [
        ({**event, "origin": "unknown", "activeMinutes": None} if key in conflicts else event)
        for key, event in unique.items()
    ]
    result["knownByOrigin"] = dict.fromkeys(ORIGINS, 0)
    start, end = _time(log.get("windowStart")), _time(log.get("windowEnd"))
    run_start, run_end = _time(record.get("startedAt")), _time(record.get("finishedAt"))
    scoped = log.get("runId") == record.get("runId") and record.get("runId") is not None
    window = bool(
        start
        and end
        and end >= start
        and run_start
        and run_end
        and start <= run_start
        and end >= run_end
    )
    complete = scoped and window and log.get("coverage") == "complete" and not conflicts
    identifiers = [event.get("id") for event in events]
    complete = complete and all(isinstance(key, str) and key for key in identifiers)
    complete = complete and len(set(key for key in identifiers if isinstance(key, str))) == len(
        events
    )
    totals = {origin: [] for origin in ORIGINS}
    intervals, timing_complete = [], True
    for event in events:
        origin = event.get("origin") if event.get("origin") in ORIGINS else "unknown"
        result["knownByOrigin"][origin] += 1
        duration = event.get("activeMinutes")
        valid = (
            isinstance(duration, int | float)
            and not isinstance(duration, bool)
            and math.isfinite(duration)
            and duration >= 0
        )
        totals[origin].append(duration if valid else None)
        active_start, active_end = _time(event.get("activeStart")), _time(event.get("activeEnd"))
        if (
            valid
            and active_start
            and active_end
            and end
            and start
            and start <= active_start <= active_end <= end
            and math.isclose((active_end - active_start).total_seconds() / 60, duration)
        ):
            intervals.append((active_start, active_end))
        else:
            timing_complete = False
        at = _time(event.get("at"))
        complete = complete and bool(at and start and end and start <= at <= end)
    result["knownEvents"] = len(events)
    result["knownActiveMinutesByOrigin"] = {
        origin: sum(value for value in values if value is not None)
        if any(value is not None for value in values)
        else None
        for origin, values in totals.items()
    }
    result["missingDurationEventsByOrigin"] = {
        origin: values.count(None) for origin, values in totals.items()
    }
    result["runBindingMatches"] = scoped
    result["window"] = {
        "start": log.get("windowStart"),
        "end": log.get("windowEnd"),
        "coversRun": window,
    }
    result["activeMinutesMeaning"] = (
        "Recorded duration sum across origins, not operator elapsed time"
    )
    result.update(
        coverage="declared-complete" if complete else "partial-or-unbound",
        reason="Origin and window are recorded labels, not independent origin verification",
    )
    if not scoped:
        result["reason"] += "; log run identity is unbound"
    if not window:
        result["reason"] += "; observation window is missing or incomplete"
    if conflicts:
        result["reason"] += "; conflicting aliased event identities"
    if any(result["missingDurationEventsByOrigin"].values()):
        result["reason"] += "; duration observations are missing or invalid"
    if complete:
        result["total"] = len(events)
        intervals.sort()
        timing_complete = timing_complete and all(
            left[1] <= right[0] for left, right in zip(intervals, intervals[1:], strict=False)
        )
        if not timing_complete:
            result["reason"] += "; active intervals are missing, incompatible or overlapping"
        if timing_complete and all(
            value is not None for values in totals.values() for value in values
        ):
            result["activeMinutes"] = sum(sum(values) for values in totals.values())
        result["wallMinutes"] = (end - start).total_seconds() / 60
    # Idle/overlap are not inferred by subtracting active time from a wall window.
    return result


def _consumers(beads, seeds):
    closure = set(seeds)
    while True:
        affected = {key for key, bead in beads.items() if set(bead["dependsOn"]) & closure}
        if affected <= closure:
            return closure - set(seeds)
        closure.update(affected)


def _retained_claims(record, repo, identifiers):
    by_id, latest = {}, {}
    for claim in record.get("claims", []):
        if not isinstance(claim, dict) or not isinstance(claim.get("claimId"), str):
            raise ValueError("original-claim-identity-unavailable")
        identity = claim["claimId"]
        if identity in by_id and by_id[identity] != claim:
            raise ValueError("conflicting-original-claim-identity")
        by_id[identity] = claim
        latest[claim.get("bead")] = claim
    impacted = {row["claimId"] for row in record["claimSummary"].get("impacted", [])}
    for identity in identifiers:
        claim = by_id.get(identity)
        if claim is None or claim.get("passed") is not True:
            raise ValueError("retained-original-claim-missing-or-failed")
        current = latest[claim.get("bead")]
        if (
            current.get("passed") is not True
            or identity in impacted
            or current["claimId"] in impacted
        ):
            raise ValueError("retained-claim-contradicts-current-failure-or-invalidation")
        commit = _commit(claim.get("candidateCommit"))
        if _git(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip() != claim.get(
            "candidateTree"
        ):
            raise ValueError("retained-original-claim-tree-mismatch")
    return sorted(set(identifiers))


def report(workspace, run_dir, *, baseline_run=None, forecast=None, intervention_log=None):
    workspace, run_dir = Path(workspace).resolve(), Path(run_dir).resolve()
    missing = []
    source = {
        "planRevision": None,
        "publicationRevision": None,
        "runId": None,
        "candidateCommit": None,
        "candidateTree": None,
        "evidenceRefs": [],
        "harnessImplementationBaseCommit": SOURCE_BASE,
    }
    result = {
        "readOnly": True,
        "source": source,
        "forecast": {"state": "unavailable", "reason": "No frozen forecast"},
        "impact": None,
        "actual": dict.fromkeys(DIMENSIONS),
        "reconciliation": None,
        "interventions": interventions(None, {}),
        "supervisionSavings": None,
        "supervisionSavingsReason": "No admitted matched native detailed-prompt baseline",
        "missingDependencies": missing,
        "isLiveProductProof": False,
    }

    def need(code, detail):
        missing.append(
            {"code": code, "detail": str(detail), "evidenceRefs": list(source["evidenceRefs"])}
        )

    try:
        record, ref = _run(run_dir)
    except (OSError, ValueError) as error:
        need("run-original", error)
        return result
    source.update(runId=record.get("runId"), runPublicationRevision=record.get("planRevision"))
    source["evidenceRefs"].append(ref)
    result["actual"].update(
        runId=record.get("runId"),
        claims=record.get("claims"),
        resumeSummary=record.get("resumeSummary"),
        claimSummary=record.get("claimSummary"),
        evidenceRefs=[ref],
    )
    resume = record.get("resumeSummary")
    resume = resume if isinstance(resume, dict) else {}
    for field, key in (("redispatchedBeads", "redispatched"), ("reusedBeads", "reused")):
        observed = resume.get(key)
        result["actual"][field] = (
            sorted(set(observed))
            if isinstance(observed, list) and all(isinstance(item, str) for item in observed)
            else None
        )
    beads = record.get("beads")
    summary = record.get("claimSummary")
    summary = summary if isinstance(summary, dict) else {}
    fresh = summary.get("fresh")
    if isinstance(beads, dict) and isinstance(fresh, list):
        fresh_ids = {
            claim.get("claimId")
            for claim in fresh
            if isinstance(claim, dict) and isinstance(claim.get("claimId"), str)
        }
        result["actual"]["revalidatedBeads"] = sorted(
            key
            for key, bead in beads.items()
            if isinstance(bead, dict)
            and isinstance(bead.get("revalidation"), dict)
            and isinstance(bead["revalidation"].get("claim"), dict)
            and bead["revalidation"]["claim"].get("claimId") in fresh_ids
        )
    for field, key in (("invalidatedClaimIds", "impacted"), ("retainedClaimIds", "retained")):
        rows = summary.get(key)
        if isinstance(rows, list) and all(
            isinstance(row, dict) and isinstance(row.get("claimId"), str) for row in rows
        ):
            result["actual"][field] = sorted({row["claimId"] for row in rows})
    claims = record.get("claims")
    result["actual"]["sourceCandidates"] = (
        [
            {
                key: claim.get(key)
                for key in ("claimId", "bead", "candidateCommit", "candidateTree", "passed")
            }
            for claim in claims
            if isinstance(claim, dict)
        ]
        if isinstance(claims, list)
        else None
    )
    result["actual"].update(
        actualWorkerRedispatchedBeads=None,
        redispatchEvidenceState=(
            "Recorded dispatch bookkeeping; actual-worker execution is not established "
            "by these labels"
        ),
    )
    need(
        "actual-worker-proof-unavailable",
        "Actual-worker redispatch is not established by local provenance or dispatch bookkeeping; "
        "summary, PID and didExecuteRealProvider labels alone are insufficient",
    )
    try:
        result["interventions"] = interventions(intervention_log, record)
        if intervention_log is None:
            need(
                "intervention-original",
                "No original intervention log, observation window or coverage record",
            )
    except (OSError, ValueError, TypeError) as error:
        need("intervention-original", f"{intervention_log}: {error}")
    before = None
    if baseline_run is not None:
        try:
            before, old_ref = _run(baseline_run)
            source["evidenceRefs"].append(old_ref)
        except (OSError, ValueError) as error:
            need("baseline-run-original", error)
    else:
        need("baseline-run-original", "Supply --baseline-run; absent baseline is not zero impact")
    manifest, spine, baseline_plan_revision = None, None, None
    try:
        with open_plan_store_readonly(workspace) as store:
            spine = store.spine()
            manifest = store._manifest(spine)
            source.update(
                planRevision=spine.plan_revision(), publicationRevision=manifest["planRevision"]
            )
            source["evidenceRefs"].append(
                {
                    "path": str(store.path),
                    "kind": "read-only-sqlite-snapshot",
                    "planRevision": source["planRevision"],
                    "publicationRevision": source["publicationRevision"],
                }
            )
            if (
                before
                and isinstance(before.get("planRevision"), str)
                and re.fullmatch(r"[0-9a-f]{64}", before["planRevision"])
            ):
                result["impact"] = store.impact_since(before["planRevision"])
                old_spine = None
                for row in store.connection.execute(
                    "SELECT snapshot_json FROM revisions WHERE plan_revision=?",
                    (before["planRevision"],),
                ):
                    old_spine = Spine(json.loads(row[0]))
                if old_spine is None:
                    for row in store.connection.execute(
                        "SELECT payload_json FROM events WHERE kind='manifest-compiled' "
                        "ORDER BY id DESC"
                    ):
                        compiled = json.loads(row[0])
                        if compiled["manifest"]["planRevision"] == before["planRevision"]:
                            old_spine = Spine(compiled["spine"])
                            break
                if old_spine is not None:
                    baseline_plan_revision = old_spine.plan_revision()
                    semantic = impact(old_spine, spine)
                    direct = set(semantic["affectedBeads"])
                    consumers = _consumers(
                        {key: spine.node(key)["fields"] for key in spine.ids("bead")}, direct
                    )
                    result["impact"]["reportingBreakdown"] = {
                        "directChangedNodes": semantic["changed"],
                        "semanticTraceBeads": sorted(direct),
                        "dependencyConsumers": sorted(consumers),
                        "protectedProofBeads": result["impact"]["affectedBeads"]
                        if result["impact"].get("protectedFilesChanged")
                        else [],
                        "note": "Overlapping dimensions; canonical impact/reuse policy unchanged",
                    }
    except (OSError, sqlite3.Error, SpineError, ValueError, KeyError) as error:
        need("canonical-plan-impact", error)
    aligned = manifest is not None and record.get("planRevision") == manifest["planRevision"]
    if not aligned:
        need(
            "publication-revision-mismatch",
            "Original run and editable canonical publication are distinct or unavailable",
        )
    repo, outcome, baseline = None, None, None
    try:
        candidate_repo = _contained(record["repo"], [workspace, run_dir])
        metadata = candidate_repo / ".git"
        if not metadata.resolve().is_relative_to(candidate_repo) or not metadata.is_dir():
            raise ValueError("candidate-git-directory-outside-canonical-repository")
        indirection = metadata / "commondir"
        if indirection.exists() or indirection.is_symlink():
            if not indirection.resolve().is_relative_to(metadata.resolve()):
                raise ValueError("candidate-git-commondir-record-outside-repository")
            with open_record_read(indirection) as stream:
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("candidate-git-commondir-oversized")
            value = raw.decode("utf-8").rstrip("\r\n")
            if not value or any(character in value for character in "\r\n\x00"):
                raise ValueError("candidate-git-commondir-malformed")
            common = (metadata / value).resolve()
            if not any(common.is_relative_to(root) for root in (workspace, run_dir)):
                raise ValueError("candidate-git-commondir-outside-canonical-roots")
            if not common.is_dir():
                raise ValueError("candidate-git-commondir-unavailable")
        repo = candidate_repo
        source.update(
            candidateCommit=_git(repo, "rev-parse", "HEAD").decode().strip(),
            candidateTree=_git(repo, "rev-parse", "HEAD^{tree}").decode().strip(),
        )
        outcome = _candidate(record, repo)
        result["actual"]["outcomeCandidate"] = outcome
        if before:
            baseline = _candidate(before, repo)
            result["actual"]["baselineCandidate"] = baseline
            paths = (
                _git(
                    repo,
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-renames",
                    "--name-only",
                    "-z",
                    baseline["candidateCommit"],
                    outcome["candidateCommit"],
                    "--",
                )
                .decode()
                .split("\0")
            )
            result["actual"]["changedPaths"] = sorted(path for path in paths if path)
            if aligned:
                owners = {
                    key
                    for key, bead in manifest["beads"].items()
                    if set(bead["ownedFiles"]) & set(result["actual"]["changedPaths"])
                }
                consumers = _consumers(manifest["beads"], owners)
                result["actual"].update(
                    changedSourceOwners=sorted(owners),
                    dependentProofConsumers=sorted(consumers),
                    unaffectedSourceOwners=sorted(set(manifest["beads"]) - owners - consumers),
                )
            result["actual"]["docPaths"] = [
                path for path in result["actual"]["changedPaths"] if path.lower().endswith(".md")
            ]
            result["actual"]["docRevisions"] = [
                {
                    "path": path,
                    "gitTreeEntry": _git(repo, "ls-tree", outcome["candidateCommit"], "--", path)
                    .decode()
                    .strip()
                    or None,
                }
                for path in result["actual"]["docPaths"]
            ]
            if aligned:
                result["steering"] = {
                    "origin": "read-only-recomputation",
                    "report": steering_report(
                        repo=repo,
                        base_commit=baseline["candidateCommit"],
                        head_commit=outcome["candidateCommit"],
                        manifest=manifest,
                        spine=spine,
                    ),
                }
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        need("original-product-candidate", error)
        repo = None  # A failed inferred candidate binding permits no further Git reads.
    retained = result["actual"].get("retainedClaimIds")
    if isinstance(retained, list):
        result["actual"]["retainedClaimIds"] = None
        if repo is None:
            need("original-retained-claim", "Canonical product Git binding is unavailable")
        else:
            try:
                result["actual"]["retainedClaimIds"] = _retained_claims(record, repo, retained)
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                need("original-retained-claim", error)
    result["actual"]["claimObservationMeaning"] = (
        "Recorded dispositions with original Git identity checks; "
        "not independent current-input, receipt or runtime proof"
    )
    if forecast is not None:
        try:
            if not (
                repo and outcome and baseline and before and aligned and baseline_plan_revision
            ):
                raise ValueError("forecast-original-source-bindings-unavailable")
            forecast_path = _contained(forecast, [repo])
            prediction, raw = _json(forecast_path)
            relative = forecast_path.relative_to(repo).as_posix()
            original_baseline = record.get("baseline")
            if not isinstance(original_baseline, dict):
                raise ValueError("original-run-baseline-unavailable")
            frozen_commit = _commit(original_baseline.get("commit"))
            if int(_git(repo, "cat-file", "-s", f"{frozen_commit}:{relative}")) > 1024 * 1024:
                raise ValueError("frozen-forecast-exceeds-1MiB")
            if _git(repo, "show", f"{frozen_commit}:{relative}") != raw:
                raise ValueError("forecast-not-frozen-at-original-run-baseline")
            _git(repo, "merge-base", "--is-ancestor", frozen_commit, outcome["candidateCommit"])
            created, started = _time(prediction.get("createdAt")), _time(record.get("startedAt"))
            if not created or not started or created >= started:
                raise ValueError("forecast-late-or-time-unavailable")
            expected_before = {
                "planRevision": baseline_plan_revision,
                "runId": before.get("runId"),
                "publicationRevision": before.get("planRevision"),
                **baseline,
            }
            expected_target = {
                "planRevision": source["planRevision"],
                "publicationRevision": source["publicationRevision"],
            }
            if (
                prediction.get("baseline") != expected_before
                or prediction.get("target") != expected_target
            ):
                raise ValueError("forecast-baseline-or-target-mismatch")
            predicted = prediction.get("predicted")
            if not isinstance(predicted, dict):
                raise ValueError("forecast-predictions-unavailable")
            result["forecast"] = {
                "state": "bound-records",
                "prediction": prediction,
                "frozenAtCommit": frozen_commit,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "evidenceRefs": [str(Path(forecast).resolve()), *source["evidenceRefs"]],
                "runtimeAdmissionVerified": False,
            }
            comparison = {}
            for field in DIMENSIONS:
                expected, observed = predicted.get(field), result["actual"].get(field)
                if not isinstance(expected, list) or not isinstance(observed, list):
                    comparison[field] = None
                    continue
                if not all(isinstance(item, str) for item in expected + observed):
                    raise ValueError(f"forecast-dimension-invalid:{field}")
                comparison[field] = {
                    "misses": sorted(set(observed) - set(expected)),
                    "extras": sorted(set(expected) - set(observed)),
                    "consistent": sorted(set(expected) & set(observed)),
                }
            result["reconciliation"] = comparison
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            result["forecast"] = {"state": "unavailable", "reason": str(error)}
            need("frozen-forecast-binding", error)
    else:
        need("frozen-forecast-binding", "No frozen forecast; no retrospective prediction score")
    return result
