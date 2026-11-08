"""User words enter once; the loop and canonical consent owner attach structure."""

import hashlib
from uuid import uuid4

from codex_harness.core import run_loop
from codex_harness.core.plan_types import PlanStoreError
from codex_harness.core.status_api import run_baton


def chat(store, text, reply_to=None):
    from codex_harness.core import run_setup

    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must contain the user's actual words")
    run = run_loop.cursor(store)
    if run is None:
        if reply_to is not None:
            raise PlanStoreError("user-subject-stale", "Start with the dream, without a reply token")
        setup = run_setup.kickoff(store.workspace)
        run_setup.validate(store, setup)
        if not store._data()["nodes"]:
            fields = {} if setup["planMode"] == "legacy" else dict(planMode="semantic-v1")
            if setup["project"]:
                fields["setup"] = setup["project"]
            store.upsert_node(dict(id="V1", kind="vision", title=store.workspace.name,
                                   text=text, parents=[], fields=fields))
        run_setup.initialize(store, setup=setup)
        with store._transaction():
            store._event("chat-message", dict(id=uuid4().hex, role="user", text=text,
                         replyTo=None, provenance="external-chat", humanIdentityVerified=False))
        return run_loop.run_loop(store)
    if text not in {"/pause", "/stop"}:
        run_setup.frozen_setup(run)
    request = run.get("userRequest")
    if text not in {"/pause", "/stop", "/resume"} and reply_to is not None and (not request or reply_to != request["request_id"]):
        raise PlanStoreError("user-subject-stale", "Reply to the current displayed subject token")
    message_id = uuid4().hex
    with store._transaction():
        store._event("chat-message", dict(id=message_id, role="user", text=text,
                     replyTo=reply_to,
                     provenance="external-chat", humanIdentityVerified=False))
    if text in {"/pause", "/stop", "/resume"}:
        return run_loop.control(store,text,message_id)
    if request and reply_to is not None and request["gate"] == "ask_user" and run["state"] != "PAUSED":
        with store._transaction():
            run = run_loop.cursor(store)
            store.answer(request["clarificationId"], text)
            run_loop._interrupt(store, run)
            run.update(state="PLAN", step=request["returnStep"], userRequest=None)
            run_loop.save(store, run)
        return run_loop.run_loop(store)
    if request and reply_to is not None and run["state"] != "PAUSED" and not (run.get("semanticHeld") or (run.get("question") or {}).get("family") == "user-interpretation"):
        words = dict(request_id=request["request_id"], expected_revision=request["plan_revision"],
                     quoted_message=text, quote_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                     actor_label="proxy:external-chat", message_id=message_id)
        return run_loop.user_reply(store, words)
    if run["state"] in run_loop.TERMINAL:
        return run_baton(store)
    with store._transaction():
        run = run_loop.cursor(store)
        run_loop._reconcile(store, run)
        run_loop.save(store, run)
        if request:
            run["returnAuthorStep"] = "understanding" if request["gate"] == "align+vision-lock" else "trace"
        if run.get("semanticHeld") and (run.get("question") or {}).get("purpose") == "scope-intent":
            run["freshConsent"] = True
        pending = next((r for r in run_loop.approvals.records(store,"confirmation-pending-judgment") if r["gate"] == "plan-execution" and not any(c["request_id"] == r["request_id"] for c in run_loop.approvals.records(store,"confirmation-responded"))), None)
        if pending:
            run["pendingConsent"] = pending["request_id"]
        run_loop._interrupt(store, run)
        run.update(state="STEER", semanticHeld=False, reason="New actual user words; interpret control or requested changes")
        run_loop._steering(store,run,"user-direction")
        run_loop.save(store, run)
    return run_loop.run_loop(store)


def activate(workspace, notify, wake):
    """Reconnect wakes the existing leased loop; inspection stays read-only."""
    if not (workspace / ".codex-harness/plan.sqlite3").exists():
        return
    import threading
    from codex_harness.core.plan_store import open_plan_store, open_plan_store_readonly
    with open_plan_store_readonly(workspace) as observed:
        current = run_loop.cursor(observed)
        if not current or current["state"] in run_loop.TERMINAL and not (current.get("effect") or current.get("settling")):
            return
    def resume():
        with open_plan_store(workspace) as store:
            if run_loop.cursor(store):
                try:
                    run_loop.start(store)
                    state = run_loop.wait_for_question(store)
                except PlanStoreError as error:
                    state = dict(refusal=dict(code=error.code, reason=str(error),
                        nextAction=getattr(error, "nextAction", str(error))))
                notify(dict(jsonrpc="2.0",method="notifications/message",params=dict(
                    level="error" if "refusal" in state else "info",logger="seldon",data=state)))
        if callable(wake):
            wake()
    threading.Thread(target=resume,daemon=True).start()
