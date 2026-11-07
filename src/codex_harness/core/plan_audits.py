"""Canonical audits writers; composed by the PlanStore transaction owner."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4


from codex_harness.core.plan_types import PlanStoreError, _json, _now


class PlanAudits:
    def record_audit(self, revision: str, result: dict) -> str:
        with self._transaction():
            if self.manifest_revision() != revision:
                raise PlanStoreError("audit-plan-changed", "Audit the current manifest revision")
            return self._insert_audit(revision, result)

    def _insert_audit(self, revision, result):
        item_id = uuid4().hex
        result = {**result, "id": item_id}
        self.connection.execute(
            "INSERT INTO audit_runs VALUES (?, ?, ?, ?, ?)",
            (
                item_id,
                revision,
                int(result["ok"] is True),
                json.dumps(result, ensure_ascii=False)
                if result.get("prospectiveAuditContract")
                else _json(result),
                _now(),
            ),
        )
        self._event("audit-completed", {"manifestRevision": revision, **result})
        return item_id


    def audit_attempt(self, attempt_id, owner=None):
        from codex_harness.core.planner_audits import CONTRACT

        exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_attempts'"
        ).fetchone()
        row = (
            self.connection.execute(
                "SELECT * FROM audit_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if exists
            else None
        )
        if row is None:
            raise PlanStoreError("audit-attempt-unknown", str(attempt_id))
        state = row["state"]
        if state in {"queued", "running"} and owner != row["owner"]:
            state = "unknown-interrupted"
        result = json.loads(row["result_json"]) if row["result_json"] else None
        current = self.passing_audit(row["manifest_revision"]) if row["audit_id"] else None
        eligible = bool(current and current["id"] == row["audit_id"])
        return {
            "attemptId": row["id"],
            "state": state,
            "ownerInstance": row["owner"],
            "prospectiveAuditContract": CONTRACT,
            "mode": "prospective",
            "manifestRevision": row["manifest_revision"],
            "inputDigest": row["input_digest"],
            "engineIdentity": row["engine_identity"] if "engine_identity" in row.keys() else None,
            "auditId": row["audit_id"],
            "result": result,
            "error": row["error"],
            "didRunValidation": (
                bool(row["did_run_validation"]) if row["did_run_validation"] is not None else None
            ),
            "completedAllRequiredSteps": bool(state == "completed" and result and result["ok"]),
            "isLiveProof": False,
            "proofCreditGranted": eligible,
            "reason": "owner-not-observed; no replay" if state == "unknown-interrupted" else None,
        }

    def admit_audit(self, owner, revision, input_digest, files, engine_identity):
        from codex_harness.core.planner_audits import CONTRACT

        key = hashlib.sha256(
            _json([CONTRACT, "prospective", revision, input_digest, engine_identity]).encode()
        ).hexdigest()
        with self._transaction():
            row = self.connection.execute(
                "SELECT id, attempt_key FROM audit_attempts WHERE state IN ('queued','running')"
            ).fetchone()
            if row:
                attempt = self.audit_attempt(row["id"], owner)
                attempt["reason"] = "audit-busy: existing attempt holds this plan"
                return attempt, False
            while row := self.connection.execute(
                "SELECT id,state,result_json FROM audit_attempts WHERE attempt_key=?", (key,)
            ).fetchone():
                if row["state"] not in {"failed", "abandoned"} or row["result_json"] is not None:
                    return self.audit_attempt(row["id"], owner), False
                # A terminal infrastructure failure has no verdict to reuse. Preserve
                # its immutable row; derive the next admission slot under this lock.
                key = hashlib.sha256(_json([key, "retry", row["id"]]).encode()).hexdigest()
            if self.manifest_revision() != revision:
                raise PlanStoreError("audit-plan-changed", "Capture the current manifest")
            attempt_id = uuid4().hex
            self.connection.execute(
                "INSERT INTO audit_attempts (id,attempt_key,owner,state,manifest_revision,"
                "input_digest,inputs_json,result_json,audit_id,error,"
                "did_run_validation,engine_identity) "
                "VALUES (?,?,?,'queued',?,?,?,NULL,NULL,NULL,0,?)",
                (attempt_id, key, owner, revision, input_digest, _json(files), engine_identity),
            )
            self._event("audit-admitted", {"attemptId": attempt_id, "manifestRevision": revision})
        return self.audit_attempt(attempt_id, owner), True

    def run_audit_attempt(self, attempt_id, owner):
        with self._transaction():
            changed = self.connection.execute(
                "UPDATE audit_attempts SET state='running', did_run_validation=NULL "
                "WHERE id=? AND owner=? AND state='queued'",
                (attempt_id, owner),
            ).rowcount
            if changed != 1:
                raise PlanStoreError("audit-owner-mismatch", "Only the admitted owner may dispatch")

    def complete_audit_attempt(
        self, attempt_id, owner, result, error=None, *, did_run_validation=True
    ):
        from codex_harness.core.planner_audits import CONTRACT, digest, input_files

        with self._transaction():
            row = self.connection.execute(
                "SELECT * FROM audit_attempts WHERE id=? AND owner=? AND state='running'",
                (attempt_id, owner),
            ).fetchone()
            if row is None:
                return  # Released attempts never publish late audit credit.
            state, audit_id = "failed", None
            if result is not None:
                result = {
                    **result,
                    "prospectiveAuditContract": CONTRACT,
                    "attemptId": attempt_id,
                    "manifestRevision": row["manifest_revision"],
                    "inputDigest": row["input_digest"],
                    "engineIdentity": row["engine_identity"],
                }
                try:
                    fresh = (
                        self.manifest_revision() == row["manifest_revision"]
                        and digest(input_files(self.workspace / "template")) == row["input_digest"]
                    )
                except (OSError, ValueError):
                    fresh = False
                state = "completed" if fresh else "stale"
                if fresh:
                    audit_id = self._insert_audit(row["manifest_revision"], result)
                    result["id"] = audit_id
                else:
                    error = "audit-inputs-stale: captured inputs no longer current; no credit"
            self.connection.execute(
                "UPDATE audit_attempts SET state=?, result_json=?, audit_id=?, error=?, "
                "did_run_validation=? WHERE id=?",
                (
                    state,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    audit_id,
                    error,
                    int(did_run_validation),
                    attempt_id,
                ),
            )
            self._event("audit-attempt-completed", {"attemptId": attempt_id, "state": state})

    def passing_audit(self, revision: str) -> dict | None:
        rows = self.connection.execute(
            "SELECT result_json FROM audit_runs WHERE manifest_revision=? AND passed=1 "
            "ORDER BY at DESC, id DESC",
            (revision,),
        ).fetchall()
        # A stronger audit cannot be downgraded by a later prospective check.
        # Legacy oracle audit envelopes remain byte-identical.
        audits = [json.loads(row[0]) for row in rows]
        from codex_harness.core.planner_audits import CONTRACT, digest, engine_identity, input_files

        eligible = []
        table = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_attempts'"
        ).fetchone()
        for audit in audits:
            if audit.get("proofLevel", "oracle-verified") != "prospective":
                eligible.append(audit)
                continue
            if not table or audit.get("prospectiveAuditContract") != CONTRACT:
                continue  # Legacy unbound rows remain history, never new-contract credit.
            attempt = self.connection.execute(
                "SELECT * FROM audit_attempts WHERE id=? AND state='completed' AND audit_id=?",
                (audit.get("attemptId"), audit.get("id")),
            ).fetchone()
            if (
                attempt
                and attempt["manifest_revision"] == revision
                and audit.get("inputDigest") == attempt["input_digest"]
                and "engine_identity" in attempt.keys()
                and audit.get("engineIdentity") == attempt["engine_identity"] == engine_identity()
            ):
                try:
                    fresh = (
                        self.manifest_revision() == revision
                        and digest(input_files(self.workspace / "template"))
                        == attempt["input_digest"]
                    )
                except (OSError, ValueError):
                    fresh = False
                if fresh:
                    eligible.append(audit)
        audits = eligible
        return next(
            (
                audit
                for audit in audits
                if audit.get("proofLevel", "oracle-verified") == "oracle-verified"
            ),
            next((audit for audit in audits if audit.get("proofLevel") == "prospective"), None),
        )
