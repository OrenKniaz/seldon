"""Execution-assessment selection, original capture and current support binding."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from codex_harness.core.execution_origins import read_execution_finding_origins
from codex_harness.core import review_provider
from codex_harness.core.acceptance_runs import current_run, run_subject
from codex_harness.core.approvals import require_approval
from codex_harness.core.review_provider import (
    _assessment_file,
    _assessment_hash,
    _execution_session,
    _launch_review,
    _response,
)


def _execution_context(store, request):
    from codex_harness.core import dispatch_attempts as dispatch
    from codex_harness.core.acceptance_recovery import _executable_identity

    members = request["members"]
    if (
        not isinstance(members, list)
        or not members
        or any(
            not isinstance(row, dict) or set(row) != {"memberId", "artifactPaths", "witnessIds"}
            for row in members
        )
        or any(not isinstance(row["memberId"], str) for row in members)
        or len({row["memberId"] for row in members}) != len(members)
    ):
        raise ValueError("execution-member-selection-invalid")
    if store.manifest_revision() != request["expected_manifest_revision"]:
        raise ValueError("execution-manifest-stale")
    run = current_run(store)
    if not run or run["id"] != request["expected_product_run_id"] or run_subject(run) != "product":
        raise ValueError("execution-current-product-run-required")
    publication = store._meta()["published_revision"]
    if not publication or not store.publication_current(request["expected_manifest_revision"]):
        raise ValueError("execution-publication-stale")
    origins = read_execution_finding_origins(store, [row["memberId"] for row in members])
    evidence = dispatch.read_completed_execution_evidence(
        store.workspace,
        request["worker_job_id"],
        request["bead_id"],
        expected_repo=run["repo"],
        expected_publication_revision=publication,
    )
    author = _execution_session(
        evidence["request"], evidence["stdout"], evidence["stderr"], author=True
    )
    from codex_harness.core.runners import execution_witnesses

    tests = {row["id"]: row["observation"] for row in execution_witnesses(evidence["integrated"])}
    files = {}
    selected_witnesses = []
    for member, origin in zip(members, origins, strict=True):
        if origin["sourceRun"]["planRevision"] != run["planRevision"]:
            raise ValueError("execution-origin-requirement-revision-changed")
        refs = [row["id"] for row in evidence["integrated"]["requirementRefs"]]
        if origin["finding"]["node_id"] not in refs:
            raise ValueError("execution-finding-not-owned-by-selected-bead")
        for field in ("artifactPaths", "witnessIds"):
            values = member[field]
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) for value in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError("execution-specific-artifact-and-witness-required")
        if set(member["artifactPaths"]) - set(evidence["ownedFiles"]):
            raise ValueError("execution-artifact-not-owned")
        # Hash selected owned artifacts. Relevance and meaningfulness belong to the judge;
        # Python coverage, source-line and direct-call collection remain optional facts.
        for name in member["artifactPaths"]:
            files[name] = _assessment_file(Path(run["repo"]), name)
        for key in member["witnessIds"]:
            observation = tests.get(key)
            if observation is None:
                raise ValueError("execution-witness-observation-missing")
            source_file = observation.get("source", {}).get("file")
            source_hash = observation.get("source", {}).get("sha256")
            if source_file:
                files[source_file] = _assessment_file(Path(run["repo"]), source_file)
                if source_hash and source_hash != files[source_file]["sha256"]:
                    raise ValueError("execution-witness-source-hash-changed")
            selected_witnesses.append(
                dict(
                    memberId=member["memberId"],
                    witnessId=key,
                    artifactPaths=member["artifactPaths"],
                    observation=observation,
                    sourceFile=source_file,
                    sourceHash=source_hash,
                )
            )
    reviewer = request["reviewer"]
    if (
        not isinstance(reviewer, dict)
        or set(reviewer) != {"name", "provider", "model", "effort"}
        or reviewer["provider"] != "codex"
        or any(not isinstance(value, str) or not value.strip() for value in reviewer.values())
    ):
        raise ValueError("execution-explicit-supported-reviewer-required")
    executable = review_provider._executable("codex", None)
    identity = _executable_identity(executable) if executable else None
    if identity is None:
        raise ValueError("execution-reviewer-executable-unavailable")
    # A bounded attempt deadline never infers reviewer authority from worker grants.
    import math

    if (
        type(request["timeout"]) not in (int, float)
        or not math.isfinite(request["timeout"])
        or request["timeout"] <= 0
    ):
        raise ValueError("execution-finite-review-limits-required")
    return dict(
        manifestRevision=store.manifest_revision(),
        productRun=run,
        origins=origins,
        execution=evidence,
        selectedWitnesses=selected_witnesses,
        relevanceQuestion=(
            "Do these structurally bound observations establish the selected behavior? "
            "Explain relevance and any concrete gaps from the retained artifacts and "
            "observations. Missing Python line tracing or direct ownedCalls is only "
            "missing optional instrumentation. It grants neither success nor failure. "
            "Assess indirect calls, executable tests and unfamiliar evidence on their "
            "actual meaning; do not require a language-specific witness shape."
        ),
        authorObservation=author,
        files=files,
        reviewerExecutable=identity,
        policy=store.plan_review_policy_state(store.manifest_revision()),
    )


def _execution_events(store, kind, registration_id=None):
    rows = [
        (row[0], json.loads(row[1]))
        for row in store.connection.execute(
            "SELECT rowid, payload_json FROM events WHERE kind=? ORDER BY rowid", (kind,)
        )
    ]
    return [
        (key, row)
        for key, row in rows
        if registration_id is None or row.get("registrationId") == registration_id
    ]


def _execution_header(store, registration_id):
    rows = [
        (key, row)
        for key, row in _execution_reservations(store)
        if row["registrationId"] == registration_id
    ]
    if len(rows) != 1 or rows[0][1].get("schema") != 1:
        raise ValueError("execution-registration-missing-or-ambiguous")
    return rows[0][1]


def _execution_reservations(store):
    rows = _execution_events(store, "execution-assessment-reserved")
    required = {"schema", "registrationId", "request", "context", "binding", "packet", "launch"}
    if any(
        not isinstance(row, dict)
        or set(row) != required
        or row["schema"] != 1
        or not isinstance(row["registrationId"], str)
        for _, row in rows
    ):
        raise ValueError("execution-reservation-provenance-unsupported")
    ids = {row["registrationId"] for _, row in rows}
    if len(ids) != len(rows):
        raise ValueError("execution-reservation-order-ambiguous")
    for kind in (
        "execution-assessment-captured",
        "execution-assessment-consumed",
        "execution-assessment-observation-error",
    ):
        if any(
            not isinstance(row, dict)
            or row.get("schema") != 1
            or row.get("registrationId") not in ids
            for _, row in _execution_events(store, kind)
        ):
            raise ValueError("execution-stage-unlisted-or-mixed-provenance")
    return rows


def _execution_directory(store, registration_id):
    from codex_harness.core import acceptance_recovery as recovery

    if not isinstance(registration_id, str) or not re.fullmatch(r"[0-9a-f]{32}", registration_id):
        raise ValueError("execution-registration-id-invalid")
    root = store.path.parent / "reviews" / "execution-assessments" / registration_id
    return recovery._safe(root, root)


def _execution_terminal_original(store, header):
    """A consumed label alone cannot clear an uncertain overlapping attempt."""
    from codex_harness.core import acceptance_recovery as recovery

    key = header["registrationId"]
    captures = _execution_events(store, "execution-assessment-captured", key)
    consumed = _execution_events(store, "execution-assessment-consumed", key)
    reserved = _execution_events(store, "execution-assessment-reserved", key)
    if (
        len(captures) != 1
        or len(consumed) != 1
        or len(reserved) != 1
        or _execution_events(store, "execution-assessment-observation-error", key)
    ):
        return False
    capture = captures[0][1]
    if (
        not reserved[0][0] < captures[0][0] < consumed[0][0]
        or set(capture)
        != {
            "schema",
            "registrationId",
            "headerHash",
            "launch",
            "exitCode",
            "runtime",
            "stdout",
            "stderr",
        }
        or type(capture["exitCode"]) is not int
        or capture["headerHash"] != _assessment_hash(header)
        or capture["launch"] != header["launch"]
        or consumed[0][1].get("captureHash") != _assessment_hash(capture)
    ):
        return False
    try:
        root = _execution_directory(store, key)
        recovery._read(root, "packet", recovery.seal(header["packet"].encode("utf-8")))
        # Invalid UTF-8 is still a captured terminal byte stream; it cannot earn
        # support, but its known process return must not be silently lost.
        return all(
            recovery.seal(recovery._safe(root / name, root).read_bytes()) == capture[name]
            for name in ("stdout", "stderr")
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _execution_capture(store, header, launch, executable, response):
    from codex_harness.core import acceptance_recovery as recovery

    if recovery._launch_identity(launch, executable) != header["launch"]:
        raise ValueError("execution-actual-launch-mismatch")
    code, output, stderr = response
    if type(code) is not int or not isinstance(output, bytes) or not isinstance(stderr, bytes):
        raise ValueError("execution-original-byte-capture-required")
    root = _execution_directory(store, header["registrationId"])
    try:
        observation = _execution_session(
            header["request"]["reviewer"], output.decode("utf-8"), stderr.decode("utf-8")
        )
    except (UnicodeError, ValueError, KeyError, TypeError):
        observation = None
    payload = dict(
        schema=1,
        registrationId=header["registrationId"],
        headerHash=_assessment_hash(header),
        launch=header["launch"],
        exitCode=code,
        runtime=observation,
        stdout=recovery._write(root, "stdout", output),
        stderr=recovery._write(root, "stderr", stderr),
    )
    with store._transaction():
        if _execution_header(store, header["registrationId"]) != header or _execution_events(
            store, "execution-assessment-captured", header["registrationId"]
        ):
            raise ValueError("execution-return-registration-changed-or-duplicate")
        store._event("execution-assessment-captured", payload)


def _execution_parse(store, header):
    from codex_harness.core import acceptance_recovery as recovery

    rows = _execution_events(store, "execution-assessment-captured", header["registrationId"])
    if len(rows) != 1:
        raise ValueError("execution-capture-missing-or-ambiguous")
    capture = rows[0][1]
    root = _execution_directory(store, header["registrationId"])
    recovery._read(root, "packet", recovery.seal(header["packet"].encode("utf-8")))
    if (
        set(capture)
        != {
            "schema",
            "registrationId",
            "headerHash",
            "launch",
            "exitCode",
            "runtime",
            "stdout",
            "stderr",
        }
        or capture["headerHash"] != _assessment_hash(header)
        or capture["launch"] != header["launch"]
        or type(capture["exitCode"]) is not int
        or capture["exitCode"] != 0
    ):
        raise ValueError("execution-capture-failed-or-changed")
    output = recovery._read(root, "stdout", capture["stdout"])
    stderr = recovery._read(root, "stderr", capture["stderr"])
    observation = _execution_session(header["request"]["reviewer"], output, stderr)
    if observation != capture.get("runtime"):
        raise ValueError("execution-sealed-runtime-changed")
    if observation["session"] == header["context"]["authorObservation"]["session"]:
        raise ValueError("execution-author-reviewer-session-not-separated")
    other_captures = [
        row
        for _, row in _execution_events(store, "execution-assessment-captured")
        if row["registrationId"] != header["registrationId"]
    ]
    other_captures.extend(
        json.loads(row[0])
        for row in store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='acceptance-review-completed'"
        )
    )
    for row in other_captures:
        if (row.get("runtime") or {}).get("session") == observation["session"]:
            raise ValueError("execution-reviewer-session-replayed")
    payload = json.loads(_response(output, "codex"))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "snapshotHash", "members"}
        or payload["schema"] != "seldon/execution-member-response-v1"
        or payload["snapshotHash"] != header["binding"]
        or not isinstance(payload["members"], list)
    ):
        raise ValueError("execution-targeted-member-response-required")
    expected = {row["memberId"]: row for row in header["request"]["members"]}
    findings = {row["finding"]["id"]: row["finding"] for row in header["context"]["origins"]}
    members, seen = [], set()
    for item in payload["members"]:
        if (
            not isinstance(item, dict)
            or set(item)
            != {"memberId", "disposition", "judgment", "explanation", "artifactPaths", "witnessIds"}
            or not isinstance(item.get("memberId"), str)
            or item["memberId"] not in expected
            or item["memberId"] in seen
        ):
            raise ValueError("execution-response-membership-invalid")
        key = item["memberId"]
        seen.add(key)
        finding = findings[key]
        if (
            item["disposition"] != finding["status"]
            or item["artifactPaths"] != expected[key]["artifactPaths"]
            or item["witnessIds"] != expected[key]["witnessIds"]
            or not isinstance(item["explanation"], str)
            or not item["explanation"].strip()
            or item["judgment"]
            not in {"supports-repair", "supports-counterevidence", "not-satisfied", "cannot-assess"}
        ):
            raise ValueError("execution-response-binding-or-judgment-invalid")
        supported = (
            item["judgment"]
            == (
                "supports-counterevidence" if finding["status"] == "rejected" else "supports-repair"
            )
            and finding["status"] != "deferred"
        )
        members.append(
            dict(
                **item,
                support="assessed-current" if supported else "unverified",
                retainedObligation=True,
                deferredObligationHeld=finding["status"] == "deferred",
                originalFinding=finding,
                originRunId=finding["runId"],
                productRunId=header["request"]["expected_product_run_id"],
                candidateCommit=header["context"]["execution"]["candidateCommit"],
            )
        )
    if seen != set(expected):
        raise ValueError("execution-response-required-member-omitted")
    return dict(
        members=members, reviewerObservation=observation, captureHash=_assessment_hash(capture)
    )


def assess_execution_findings(store, **request):
    """Complete controlled Python entry; no external receipt/eligibility importer."""
    try:
        header = store.register_execution_assessment(**request)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return dict(
            members=[], fullVisionHeld=True, holdReasons=[str(error)], closureEligible=False
        )
    if header.get("reused"):
        return store.execution_assessment_state()
    from codex_harness.core import acceptance_recovery as recovery

    reviewer = request["reviewer"]
    executable = header["context"]["reviewerExecutable"]["path"]
    launch = None
    attempted = False
    try:
        launch = _launch_review(reviewer, header["packet"], executable)
        if recovery._launch_identity(launch, executable) != header["launch"]:
            raise ValueError("execution-prelaunch-identity-changed")
        if _execution_context(store, request) != header["context"]:
            raise ValueError("execution-prelaunch-selection-changed")
        with TemporaryDirectory(prefix="harness-execution-review-") as cwd:
            if not _admit_execution_assessment(store, header):
                return store.execution_assessment_state()
            attempted = True
            response = review_provider._run(launch, Path(cwd), request["timeout"], raw=True)
            # Preserve the actual return before temporary-directory cleanup.
            _execution_capture(store, header, launch, executable, response)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        if not attempted:
            return dict(
                members=[], fullVisionHeld=True, holdReasons=[str(error)], closureEligible=False
            )
        if not _execution_events(store, "execution-assessment-captured", header["registrationId"]):
            output, stderr = getattr(error, "output", None), getattr(error, "stderr", None)
            try:
                _execution_capture(
                    store,
                    header,
                    launch,
                    executable,
                    (
                        -1,
                        output if isinstance(output, bytes) else b"",
                        stderr if isinstance(stderr, bytes) else b"",
                    ),
                )
            except (OSError, ValueError, TypeError):
                pass  # Retain partial create-once files and the incomplete reservation.
        with store._transaction():
            store._event(
                "execution-assessment-observation-error",
                dict(
                    schema=1,
                    registrationId=header["registrationId"],
                    error=f"{type(error).__name__}: {error}",
                ),
            )
        # Never manufacture terminal certainty, refund, or clear overlap on an
        # ambiguous launch/return/cleanup error. Reads expose this same hold.
        return store.execution_assessment_state()
    return store.commit_execution_assessment(header["registrationId"])


def _admit_execution_assessment(store, header):
    from codex_harness.core import acceptance_recovery as recovery

    request, context = header["request"], header["context"]
    with store._transaction():
        if _execution_context(store, request) != context:
            raise ValueError("execution-selection-raced-before-reservation")
        if any(
            old["request"] == request and old["context"] == context
            for _, old in _execution_events(store, "execution-assessment-reserved")
        ):
            return False
        require_approval(store, "B", dict(reviewer=request["reviewer"]))
        root = _execution_directory(store, header["registrationId"])
        root.mkdir(parents=True, exist_ok=False)
        recovery._write(root, "packet", header["packet"].encode("utf-8"))
        store._event("execution-assessment-reserved", header)
    return True




def selected_execution_support(store, registration_ids, *, repo=None):
    """Reconstruct exact current selected support; references cannot certify themselves."""
    if (
        not isinstance(registration_ids, list)
        or not registration_ids
        or any(not isinstance(x, str) or not x for x in registration_ids)
        or len(set(registration_ids)) != len(registration_ids)
    ):
        raise ValueError("execution-support-selection-invalid")
    state = store.execution_assessment_state()
    by_member = {row["memberId"]: row for row in state["members"]}
    records, seen = [], set()
    for key in registration_ids:
        header = _execution_header(store, key)
        keys = [row["memberId"] for row in header["request"]["members"]]
        if seen.intersection(keys):
            raise ValueError("execution-support-overlapping-selection")
        seen.update(keys)
        members = [by_member.get(member) for member in keys]
        if any(
            not row
            or row.get("registrationId") != key
            or row.get("support") != "assessed-current"
            or row.get("holdReasons")
            for row in members
        ):
            raise ValueError("execution-selected-support-not-current")
        context = header["context"]
        if (
            repo is not None
            and Path(context["productRun"]["repo"]).resolve() != Path(repo).resolve()
        ):
            raise ValueError("execution-selected-support-repo-mismatch")
        captures = _execution_events(store, "execution-assessment-captured", key)
        consumed = _execution_events(store, "execution-assessment-consumed", key)
        if len(captures) != 1 or len(consumed) != 1:
            raise ValueError("execution-selected-support-originals-incomplete")
        witness_ids = {item for row in header["request"]["members"] for item in row["witnessIds"]}
        records.append(
            dict(
                registrationId=key,
                registrationHash=review_provider._assessment_hash(header),
                binding=header["binding"],
                captureHash=review_provider._assessment_hash(captures[0][1]),
                consumptionHash=review_provider._assessment_hash(consumed[0][1]),
                request=header["request"],
                members=members,
                originalFindings=[row["finding"] for row in context["origins"]],
                productRun=context["productRun"],
                authorObservation=context["authorObservation"],
                files=context["files"],
                protectedObservations=[
                    row
                    for row in context["execution"]["integrated"]["validation"]["observations"][
                        "tests"
                    ]
                    if row["id"] in witness_ids
                ],
            )
        )
    if state != store.execution_assessment_state():
        raise ValueError("execution-selected-support-read-raced")
    return dict(
        schema="seldon/ordinary-review-execution-support-v1",
        registrationIds=list(registration_ids),
        records=records,
        closureEligible=False,
        scope=(
            "Sealed evidence input only; no verified closure or native final acceptance authority"
        ),
    )
