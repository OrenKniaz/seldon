"""Single-use native and caller-mediated chat worker-execution decisions.

Native responses remain bridge-correlated. Chat authority explicitly has lower,
caller-mediated assurance. Neither authenticates a human or verifies subscription
capacity. Plan approval remains separate from execution admission.
"""

import json
from pathlib import Path
from uuid import uuid4

from codex_harness.core.plan_quality import quality_report
from codex_harness.core.planner_confirmations import (
    CHAT,
    NATIVE,
    _chat_evidence,
    _chat_question,
    _response_channel,
    combined_credit,
)
from codex_harness.core.planner_views import review_resolution_state
from codex_harness.core.spine import Spine, compile_manifest

REQUEST = "worker-execution-requested"
RESPONSE = "worker-execution-responded"
CONSUMED = "worker-execution-consumed"


def _records(store, kind):
    return [
        json.loads(row[0])
        for row in store.connection.execute(
            "SELECT payload_json FROM events WHERE kind=? ORDER BY id DESC", (kind,)
        )
    ]


def _canonical_store(store):
    databases = store.connection.execute("PRAGMA database_list").fetchall()
    if store.connection.execute("PRAGMA query_only").fetchone()[0] or not any(
        row[1] == "main" and row[2] and Path(row[2]).resolve() == store.path.resolve()
        for row in databases
    ):
        raise ValueError("worker-execution-canonical-store-required")
    if store.path.resolve() != (store.workspace / ".codex-harness/plan.sqlite3").resolve():
        raise ValueError("worker-execution-canonical-store-required")


def _control_digest(store, exclude_event=None, *, execution_transition=False):
    """Raw canonical prerequisites; safe inside an existing writer transaction."""
    from codex_harness.core.planner_jobs import _digest, _files

    originals = {}
    for table in (
        "meta",
        "approvals",
        "clarifications",
        "audit_runs",
        "revisions",
        "review_runs",
        "review_findings",
        "events",
    ):
        exists = store.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            originals[table] = None
            continue
        query = f"SELECT * FROM {table}"
        if table == "events":
            query += (
                " WHERE kind NOT IN ('worker-execution-requested', "
                "'worker-execution-responded', 'worker-execution-consumed')"
            )
            if exclude_event is not None:
                query += f" AND id != {int(exclude_event)}"
            if execution_transition:
                query += (
                    " AND NOT (kind='phase-advanced' "
                    "AND json_extract(payload_json, '$.from')='publish' "
                    "AND json_extract(payload_json, '$.to')='execute')"
                )
        originals[table] = [
            dict(row, phase="publish") if table == "meta" and execution_transition else list(row)
            for row in store.connection.execute(query + " ORDER BY rowid")
        ]
    if exclude_event is not None:
        template = store.workspace / "template"
        originals["template"] = _files(template) if template.is_dir() else None
    return _digest(originals)


def execution_scope(
    store, expected_revision, workers, *, max_parallel=2, timeout=900, call_cap, _candidate=False
):
    """Read canonical scope; no event, approval, launch or provider query."""
    from codex_harness.core import planner_jobs as jobs

    if _candidate:
        _canonical_store(store)
        manifest, template = store._manifest(Spine(store._data())), store.workspace / "template"
        if (
            manifest.get("planMode") != "semantic-v1"
            or manifest["planRevision"] != expected_revision
            or store.phase() != "plan"
        ):
            raise ValueError("worker-execution-frozen-semantic-candidate-required")
    else:
        manifest, template = jobs._publication(store, expected_revision)
    semantic = manifest.get("planMode") == "semantic-v1"
    if semantic and max_parallel != 1:
        raise ValueError("Parallel Build unsupported in semantic-v1; use max_parallel=1")
    selected = jobs._workers(manifest, workers)
    if type(call_cap) is not int or call_cap < len(selected) or call_cap < 1:
        raise ValueError("worker-job-call-cap-required")
    if type(max_parallel) is not int or max_parallel < 1 or not jobs._valid_timeout(timeout):
        raise ValueError("worker-execution-limits-invalid")
    if not _candidate:
        status = store.status()
        resolution = review_resolution_state(store, status)
        check = next(c for c in quality_report(store)["checks"] if c["id"] == "roundtable")
        if (
            (status["vision_lock_stale"] if "vision_lock_stale" in status else False)
            or status["open_clarifications"]
            or resolution["blockingOpenFindings"] != []
            or (
                (not semantic and resolution["audit"]["current"] is not True)
                or resolution["review"]["current"] is not True
                or check["status"] not in {"pass", "warn"}
            )
        ):
            raise ValueError("worker-execution-canonical-prerequisite")
    scope = dict(
        workspace=str(store.workspace.resolve()),
        publicationRevision=expected_revision,
        spineRevision=store._revision(),
        sourceManifestRevision=manifest.get("sourceManifestRevision", expected_revision),
        phase=store.phase(),
        manifestDigest=jobs._digest(manifest),
        templateFiles=jobs._files(template),
        workers=selected,
        maxParallel=max_parallel,
        timeout=timeout,
        callCap=call_cap,
        canonicalControlDigest=_control_digest(store),
    )
    if (
        not _candidate
        and store.phase() in {"publish", "execute"}
        and combined_credit(store, "execution")
    ):
        scope.pop("phase")
        scope["canonicalControlDigest"] = _control_digest(store, execution_transition=True)
    return scope


def bind_candidate_execution(store, manifest):
    """Seal the checked publication control preimage; retain the original consent preimage."""
    from codex_harness.core import planner_jobs as jobs
    from codex_harness.core.planner_confirmations import combined_credit

    bundle = combined_credit(store, "execution")
    if bundle is None:
        return
    candidate = bundle["scopes"]["execution"]
    if candidate["manifestDigest"] != jobs._digest(manifest):
        raise ValueError("worker-execution-candidate-stale")
    if any(r["request_id"] == bundle["request_id"] for r in _records(store, REQUEST)):
        return
    scope = {
        **candidate,
        "canonicalControlDigest": _control_digest(store, execution_transition=True),
    }
    scope.pop("phase")
    request = dict(
        request_id=bundle["request_id"],
        scope=scope,
        channel=bundle.get("channel", NATIVE),
        response_revision=bundle["plan_revision"],
    )
    store._event(REQUEST, request)
    store._event(RESPONSE, {**bundle["response"], "scope": scope})


def _recheck(store, scope):
    """Transaction-safe: never call status/spine, which own their read transactions."""
    from codex_harness.core import planner_jobs as jobs

    meta = store._meta()
    current = compile_manifest(Spine(store._data()), store.workspace / "template")
    publication = store.connection.execute(
        "SELECT snapshot_json FROM revisions WHERE plan_revision=?", (scope["publicationRevision"],)
    ).fetchone()
    if (
        str(store.workspace.resolve()) != scope["workspace"]
        or meta["published_revision"] != scope["publicationRevision"]
        or (
            meta["phase"] != scope["phase"]
            if "phase" in scope
            else meta["phase"] not in {"publish", "execute"}
        )
        or store._revision() != scope["spineRevision"]
        or current["planRevision"] != scope["sourceManifestRevision"]
        or jobs._files(store.workspace / "template") != scope["templateFiles"]
        or _control_digest(store, execution_transition="phase" not in scope)
        != scope["canonicalControlDigest"]
        or publication is None
        or jobs._digest(json.loads(publication[0])["manifest"]) != scope["manifestDigest"]
    ):
        raise ValueError("worker-execution-scope-stale")


def prepare_execution(
    store,
    expected_revision,
    workers,
    *,
    max_parallel=2,
    timeout=900,
    call_cap,
    _chat=False,
    _expected_request_id=None,
):
    _canonical_store(store)
    scope = execution_scope(
        store,
        expected_revision,
        workers,
        max_parallel=max_parallel,
        timeout=timeout,
        call_cap=call_cap,
    )
    manifest = store.published_manifest(expected_revision)
    tasks = []
    for worker in scope["workers"]:
        bead = manifest["beads"][worker["bead"]]
        tasks.append(
            f"- {worker['bead']}: {bead['title']}\n"
            f"  {worker['provider']} / {worker['model']} / effort {worker['effort']}\n"
            + "  Owns: "
            + ", ".join(bead["ownedFiles"])
        )
    card = (
        f"Start {len(tasks)} worker tasks for {store._meta()['name']}?\n\n"
        + "\n".join(tasks)
        + f"\n\nUp to {call_cap} provider attempts total, including failed or uncertain starts. "
        + f"At most {max_parallel} tasks in parallel; "
        f"timeout {timeout} seconds per worker attempt. "
        "Total completion time is unknown. This local ceiling does not verify subscription quota.\n"
        "This approves worker execution separately from plan approval. Conditions, decline, "
        "cancel or changed scope grant no execution.\n"
        + f"Project: {scope['workspace']}\nPublication: {expected_revision}\n"
        + f"Protected inputs: {scope['manifestDigest']}\n"
    )
    request = dict(request_id=uuid4().hex, card=card, scope=scope)
    if _chat:
        _chat_question(request, expected_revision)
    with store._transaction():
        _recheck(store, scope)
        if _expected_request_id is not None:
            latest = next(iter(_records(store, REQUEST)), None)
            if latest is None or latest["request_id"] != _expected_request_id:
                raise ValueError("confirmation-request-superseded")
        store._event(REQUEST, request)
    return request


def prepare_chat_execution(
    store,
    expected_revision,
    workers,
    *,
    max_parallel=2,
    timeout=900,
    call_cap,
    _expected_request_id=None,
):
    """Fresh question over the same canonical worker scope, without native credit."""
    return prepare_execution(
        store,
        expected_revision,
        workers,
        max_parallel=max_parallel,
        timeout=timeout,
        call_cap=call_cap,
        _chat=True,
        _expected_request_id=_expected_request_id,
    )


def complete_execution(store, request_id, action, content=None):
    return _complete_execution(store, request_id, action, content)


def complete_chat_execution(
    store, request_id, *, expected_revision, quoted_message, quote_sha256, actor_label
):
    action, content, evidence = _chat_evidence(
        request_id, expected_revision, quoted_message, quote_sha256, actor_label
    )
    return _complete_execution(store, request_id, action, content, chat=evidence)


def _complete_execution(store, request_id, action, content=None, *, chat=None):
    _canonical_store(store)
    requests = _records(store, REQUEST)
    request = next((r for r in requests if r["request_id"] == request_id), None)
    if request is None:
        raise ValueError("worker-execution-request-unknown")
    scope = request["scope"]
    channel = CHAT if chat is not None else NATIVE
    if request.get("channel", NATIVE) != channel:
        raise ValueError("worker-execution-channel-mismatch")
    if chat is not None and chat["revision"] != scope["publicationRevision"]:
        raise ValueError("worker-execution-revision-mismatch")
    if action not in {"accept", "decline", "cancel"} or (
        action == "accept"
        and (
            not isinstance(content, dict)
            or set(content) != {"approve", "statement", "conditions"}
            or type(content["approve"]) is not bool
            or not isinstance(content["statement"], str)
            or not content["statement"].strip()
            or not isinstance(content["conditions"], str)
        )
    ):
        raise ValueError("worker-execution-response-invalid")
    try:
        current = execution_scope(
            store,
            scope["publicationRevision"],
            scope["workers"],
            max_parallel=scope["maxParallel"],
            timeout=scope["timeout"],
            call_cap=scope["callCap"],
        )
    except ValueError:
        current = None
    with store._transaction():
        if any(r["request_id"] == request_id for r in _records(store, RESPONSE)):
            raise ValueError("worker-execution-already-responded")
        latest = _records(store, REQUEST)[0]
        stale = latest != request or current != scope
        if not stale:
            try:
                _recheck(store, scope)
            except ValueError:
                stale = True
        state = "stale" if stale else action
        if not stale and action == "accept":
            state = (
                "conditional"
                if content["conditions"]
                else "approved"
                if content["approve"]
                else "declined"
            )
        result = dict(
            request_id=request_id,
            state=state,
            scope=scope,
            action=action,
            content=content,
            provenance=channel,
            human_identity_verified=False,
        )
        if chat is not None:
            result["chat"] = chat
        store._event(RESPONSE, result)
    return result


def require_execution(store, scope):
    from codex_harness.core.planner_confirmations import _b_required, combined_credit

    _canonical_store(store)
    bundle = combined_credit(store, "execution")
    if bundle and any(
        scope[k] != bundle["scopes"]["execution"][k]
        for k in "workers callCap maxParallel timeout manifestDigest publicationRevision".split()
    ):
        raise _b_required("worker-execution-grant-required: outside approved B")
    requests, responses = _records(store, REQUEST), _records(store, RESPONSE)
    if not requests:
        raise ValueError("worker-execution-grant-required")
    request = requests[0]
    response = next((r for r in responses if r.get("request_id") == request["request_id"]), {})
    content = response.get("content") or {}
    if (
        request.get("scope") != scope
        or response.get("scope") != scope
        or response.get("state") != "approved"
        or response.get("action") != "accept"
        or not _response_channel(request, response, scope["publicationRevision"])
        or content.get("approve") is not True
        or content.get("conditions") != ""
        or not isinstance(content.get("statement"), str)
        or not content["statement"].strip()
    ):
        raise ValueError("worker-execution-grant-required")
    if any(r.get("request_id") == request["request_id"] for r in _records(store, CONSUMED)):
        raise ValueError("worker-execution-grant-consumed")
    return request["request_id"]


def consume_execution(store, scope, job_id):
    _canonical_store(store)
    with store._transaction():
        request_id = require_execution(store, scope)
        _recheck(store, scope)
        store._event(CONSUMED, dict(request_id=request_id, jobId=job_id, scope=scope))
    return request_id


def consumed_execution(store, request_id, scope, job_id):
    """Read the original consumption binding; later prompts do not remint it."""
    request = next((r for r in _records(store, REQUEST) if r.get("request_id") == request_id), {})
    response = next((r for r in _records(store, RESPONSE) if r.get("request_id") == request_id), {})
    content = response.get("content") or {}
    original = (
        request.get("scope") == scope
        and response.get("scope") == scope
        and response.get("state") == "approved"
        and response.get("action") == "accept"
        and _response_channel(request, response, scope["publicationRevision"])
        and content.get("approve") is True
        and content.get("conditions") == ""
        and isinstance(content.get("statement"), str)
        and bool(content["statement"].strip())
    )
    return original and any(
        r.get("request_id") == request_id and r.get("jobId") == job_id and r.get("scope") == scope
        for r in _records(store, CONSUMED)
    )


def consumed_execution_provenance(store, request_id, scope, job_id):
    """Project the verified canonical original for the job writer; never infer from an ID."""
    if not consumed_execution(store, request_id, scope, job_id):
        raise ValueError("worker-execution-original-unproved")
    return next(
        row["provenance"]
        for row in _records(store, RESPONSE)
        if row.get("request_id") == request_id
    )
