"""Acceptance run lifecycle, revision validity and shared C evidence gate."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from codex_harness.core import spine as plan_spine, candidate_delivery
from codex_harness.core.acceptance_revision import _hash, _metadata_remaining, integrated_revision
from codex_harness.core.acceptance_scenarios import derive_scenarios, run_scenarios
from codex_harness.core.plan_store import PlanStoreError, _now
from codex_harness.core.planner_candidate import current_job_run, judgments_for
from codex_harness.core.record_io import read_record_json
from codex_harness.core.semantic_runner import web_toolchain

def run_subject(run) -> str:
    return run["subject"]


def acceptance_inputs(store, repo, *, subject=None) -> dict:
    if subject not in ("product", "oracle"):
        raise PlanStoreError(
            "acceptance-subject-required",
            "Specify product or oracle as the acceptance subject; the run loop owns admission.",
        )
    spine = store.locked_acceptance_spine()
    spine = plan_spine.Spine(store._data()) if spine.semantic else spine
    scenarios = store.acceptance_scenarios()
    if spine.semantic:
        from codex_harness.core.runners import executable_scenarios
        document, targets, _ = plan_spine.semantic_spec(spine)
        scenarios = executable_scenarios(spine.to_dict(), document)["scenarios"]
    if not scenarios:
        store.save_acceptance_scenarios(derive_scenarios(spine))
        scenarios = store.acceptance_scenarios()
    return dict(
        **({"semantic": targets} if spine.semantic else {}),
        **({"judgments": judgments_for(store, repo)} if spine.semantic else {}),
        repo=str(Path(repo).absolute()),
        subject=subject,
        planRevision=store._revision(),
        scenarioRevision=_hash([scenarios, targets] if spine.semantic else scenarios),
        scenarios=scenarios,
        stepTimeoutSeconds=60,
        maxExecutionSeconds=sum(
            60 * len(item.get("steps", item.get("actions", []))) + 1.7 for item in scenarios
        ),
    )

def capture_acceptance(inputs, *, deadline=None, should_stop=None) -> dict:
    """Worker-side actual product/accepted-candidate metadata, never a supplied answer."""
    _metadata_remaining(deadline, should_stop)
    repo, subject = inputs["repo"], inputs["subject"]
    product = Path(repo).resolve()
    dispatch_digest = None
    if subject == "product":
        dispatch_record = product.parent / "evidence" / "run.json"
        if dispatch_record.is_file():
            from codex_harness.core.dispatch_shared import STATUS_MOCK_ONLY
            run = read_record_json(dispatch_record)
            _metadata_remaining(deadline, should_stop)
            dispatch_digest = _hash(run)
            if (
                run.get("schema") != "codex-harness/plan-run-v1"
                or not isinstance(run.get("beads"), dict)
                or Path(run.get("repo", "")).resolve() != product
            ):
                raise PlanStoreError("acceptance-dispatch-record-invalid", str(dispatch_record))
            accepted = any(
                bead.get("status")
                in {
                    "requirement-validated",
                    "already-satisfied",
                    "reused",
                    STATUS_MOCK_ONLY,
                }
                and (bead.get("acceptedPlanRevision") or bead.get("planRevision"))
                == run.get("planRevision")
                and (bead.get("revalidation") or bead.get("integrated") or {})
                .get("validation", {})
                .get("passed")
                is True
                for bead in run["beads"].values()
            )
            if not accepted:
                raise PlanStoreError(
                    "acceptance-no-delivered-candidate",
                    "No accepted dispatch candidate is available for product scenarios. "
                    "Partial integrated artifacts remain retained; they are not accepted beads. "
                    f"Inspect {dispatch_record} and recover the failed/blocked work first.",
                )
    revision = (
        integrated_revision(product)
        if deadline is None and should_stop is None
        else integrated_revision(product, deadline=deadline, should_stop=should_stop)
    )
    return {
        **inputs,
        **(
            {"webToolchain": web_toolchain(None, False)}
            if inputs.get("semantic")
            and any(t.get("kind") == "web" for t in inputs["semantic"].values())
            else {}
        ),
        "resolvedRepo": str(product),
        "revision": revision,
        "dispatchDigest": dispatch_digest,
    }

def complete_acceptance(store, captured, results) -> dict:
    """Only a current captured input set may publish a consumable run receipt."""
    inputs = acceptance_inputs(store, captured["repo"], subject=captured["subject"])
    if capture_acceptance(inputs) != captured:
        raise PlanStoreError("acceptance-revision-changed", "Product changed during acceptance")
    with store._transaction():
        return record_acceptance(store, captured, results)

def record_acceptance(store, captured, results, *, deadline=None) -> dict:
    """Record under the canonical caller's existing write transaction."""
    current = acceptance_inputs(store, captured["repo"], subject=captured["subject"])
    if any(captured[key] != value for key, value in current.items()):
        raise PlanStoreError("acceptance-revision-changed", "Rerun on the integrated revision")
    tools = captured.get("webToolchain")
    if tools is not None and tools != web_toolchain(None, False):
        raise PlanStoreError("acceptance-revision-changed", "Web tools changed during acceptance")
    result = {
        "id": uuid4().hex,
        "repo": captured["resolvedRepo"],
        "subject": captured["subject"],
        "revision": captured["revision"],
        "revisionAlgorithm": "tree-bytes-v2",
        "planRevision": captured["planRevision"],
        "scenarioRevision": captured["scenarioRevision"],
        "results": results,
        **({"webToolchain": captured["webToolchain"]} if "webToolchain" in captured else {}),
        "passed": bool(results) and all(r["passed"] for r in results),
        "state": "FAIL" if any(r["passed"] is False for r in results) else
                 "pending-judgment" if not results or any(r["passed"] is None for r in results) else "PASS",
    }
    _metadata_remaining(deadline)
    store.connection.execute(
        "INSERT INTO acceptance_runs VALUES (?, ?)", (result["id"], json.dumps(result))
    )
    store._event("acceptance-run", result)
    return result

def run_acceptance(store, repo, env=None, *, subject=None) -> dict:
    if store.connection.execute(
        "SELECT 1 FROM acceptance_attempts "
        "WHERE state IN ('queued','running','unknown-interrupted') LIMIT 1"
    ).fetchone():
        raise PlanStoreError("acceptance-busy", "Existing acceptance owner holds this workspace")
    captured = capture_acceptance(acceptance_inputs(store, repo, subject=subject))
    results = run_scenarios(
        repo,
        captured["scenarios"],
        env,
        semantic=captured.get("semantic"),
        judgments=captured.get("judgments"),
    )
    return complete_acceptance(store, captured, results)

def run_wave_exits(store, run_dir, through_wave_id) -> dict:
    """Fresh cumulative exits on the current clean scoped integration candidate."""
    from codex_harness.core.wave_refine import digest, need
    binding = store.wave_boundary_context(run_dir, through_wave_id, require_exit=False)
    results = run_scenarios(binding["repo"], binding["scenarios"])
    with store._transaction():
        need(
            binding == store.wave_boundary_context(run_dir, through_wave_id, require_exit=False),
            "Candidate/publication/scenarios changed during exits",
            "wave-exits-stale",
        )
        result = dict(
            schema="dot-wave-cumulative-exit-v1",
            binding=binding,
            **{k: v for k, v in binding.items() if k != "scenarios"},
            results=results,
            passed=bool(results) and all(r["passed"] for r in results),
            executionKind="canonical-clean-scenarios-v1",
            sourceRefs=[binding["runSourceRef"], binding["productLockSource"]],
        )
        result["id"] = digest(result)
        store._event("wave-cumulative-exit", result)
    return result

def _latest_run(store) -> dict | None:
    row = store.connection.execute(
        "SELECT result_json FROM acceptance_runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    run = json.loads(row[0])
    if run.get("revisionAlgorithm") != "tree-bytes-v2" or "subject" not in run:
        from codex_harness.core.plan_types import refuse_old_format
        refuse_old_format("Acceptance run")
    return run

def run_invalid_reason(store, run) -> str | None:
    if store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='acceptance_attempts'"
    ).fetchone():
        attempt = store.connection.execute(
            "SELECT state,superseded_run_id FROM acceptance_attempts ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if attempt and attempt["state"] in {"queued", "running", "unknown-interrupted"}:
            return "acceptance-execution-pending"
        if attempt and run and run["id"] == attempt["superseded_run_id"]:
            return "acceptance-execution-required"
    try:
        store.locked_acceptance_spine()
    except PlanStoreError:
        return "vision-unlocked"
    if run is None:
        return "acceptance-scenarios-required"
    if run["planRevision"] != store._revision():
        return "plan-changed"
    if not Path(run["repo"]).is_dir():
        return "product-changed"
    current_revision = integrated_revision(run["repo"])
    if run["revision"] != current_revision:
        return "product-changed"
    if "webToolchain" in run:
        try:
            if run["webToolchain"] != web_toolchain(None, False):
                return "web-toolchain-changed"
        except (OSError, ValueError):
            return "web-toolchain-changed"
    scenarios = store.acceptance_scenarios()
    if plan_spine.Spine(store._data()).semantic:
        if (
            run["scenarioRevision"]
            != acceptance_inputs(store, run["repo"], subject=run_subject(run))["scenarioRevision"]
        ):
            return "scenarios-changed"
    elif run["scenarioRevision"] != _hash(scenarios):
        return "scenarios-changed"
    return None


def current_run(store) -> dict | None:
    run = _latest_run(store)
    return run if run_invalid_reason(store, run) is None and (not store.spine().semantic or current_job_run(store)[0]["complete"]) else None

def require_c_evidence(store, run):
    """The shared C gate for the writer and every status surface."""
    if run and run.get("state") == "pending-judgment":
        raise PlanStoreError("pending-judgment", "Prepare and submit a current bound execution judgment")
    if not run or not run["passed"]:
        _refuse("acceptance-scenarios-required")
    if store.spine().semantic:
        try:
            candidate_delivery.delivered_candidate(store, run["repo"])
            if run.get("webToolchain") and not judgments_for(store, run["repo"]).get(
                "visualQuality", {}
            ).get("passed"):
                raise ValueError("delivery: visualQuality human judgment remains unproven")
        except ValueError as error:
            raise PlanStoreError("acceptance-delivery-invalid", str(error)) from error
    store.require_finding_dispositions()
    if reason := store.acceptance_review_guard(run["id"]):
        _refuse(reason)

INVALID_MESSAGES = {
    "acceptance-execution-pending": (
        "A newer acceptance attempt is pending or uncertain; prior credit is held."
    ),
    "acceptance-execution-required": (
        "A newer explicit acceptance attempt superseded this run; inspect its retained outcome."
    ),
    "acceptance-product-required": (
        "Scenarios verified against the reference implementation (dry run); "
        "product acceptance is required."
    ),
    "product-changed": "The product revision has changed or its repository is unavailable.",
    "web-toolchain-changed": "The web toolchain changed; rerun with the current tools.",
    "plan-changed": "The plan has changed since the acceptance run.",
    "scenarios-changed": "The acceptance scenarios have changed since the run.",
    "vision-unlocked": "The current vision is not locked.",
    "acceptance-scenarios-required": "All current acceptance scenarios must pass.",
    "acceptance-approval-required": "Acceptance approval is required for the current run.",
    "acceptance-review-required": "An acceptance review is required for the current run.",
    "plan-clarifications-open": "Open questions must be answered before acceptance.",
}

def acceptance_next(reason, repo=None) -> str:
    return "Read the durable run baton; answer its bound question or use harness_chat with recorded direction."


def _refuse(reason, repo=None):
    raise PlanStoreError(
        reason, INVALID_MESSAGES[reason] + " Next: " + acceptance_next(reason, repo)
    )

def acceptance_run_prerequisite(store):
    """Canonical run recovery facts; finding_state owns prerequisite ordering."""
    run = _latest_run(store)
    candidate = current_job_run(store)[0] if run and store.spine().semantic else dict(complete=True)
    if not candidate["complete"]:
        return dict(kind="acceptance_run_required", state="pending-judgment", reason="retained-candidate-required", message=candidate["next"])
    reason = run_invalid_reason(store, run) or (
        "acceptance-product-required" if run and run_subject(run) != "product" else None)
    return dict(kind="acceptance_run_required", state="pending-judgment", reason=reason,
                message=acceptance_next(reason)) if reason else None
