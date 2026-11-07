"""Canonical judgments writers; composed by the PlanStore transaction owner."""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from uuid import uuid4

from codex_harness.core.planner_schema import (
    Judgment,
    judgment_example,
    judgment_refs,
    judgment_response,
)
from codex_harness.core.reviewer_evidence import review_binding


from codex_harness.core.plan_types import PlanStoreError, _json, _now, _nonempty


class PlanJudgments:
    def _judgment_record(self, judgment_id: str) -> Judgment:
        snapshot = getattr(self, "_review_read_snapshot", None)
        if snapshot is not None and judgment_id in snapshot.get("judgmentRecords", {}):
            return snapshot["judgmentRecords"][judgment_id]
        row = self.connection.execute(
            "SELECT payload_json FROM events WHERE kind='judgment-recorded' "
            "AND json_extract(payload_json, '$.id')=? ORDER BY id DESC LIMIT 1",
            (judgment_id,),
        ).fetchone()
        if row is None:
            raise PlanStoreError("judgment-missing", "Prepare a judgment before submitting a reply")
        return json.loads(row[0])

    def prepare_judgment(
        self,
        *,
        question,
        subject_ids,
        evidence_ids=None,
        source_event_ids=None,
        candidate=None,
    ) -> Judgment:
        """Capture canonical facts, not a default verdict or a provider launch."""
        _nonempty(question, "question")
        if not subject_ids:
            raise PlanStoreError("judgment-subject-required", "Supply current subject IDs")
        if candidate is not None:
            _nonempty(candidate, "candidate")
        with nullcontext() if self.connection.in_transaction else self._transaction():
            spine = self.spine()

            subjects, evidence = (
                judgment_refs(spine, subject_ids),
                judgment_refs(spine, evidence_ids or []),
            )
            sources = []
            for key in sorted(set(source_event_ids or [])):
                row = self.connection.execute("SELECT * FROM events WHERE id=?", (key,)).fetchone()
                if row is None:
                    raise PlanStoreError(
                        "judgment-source-missing", "Supply an existing source event ID"
                    )
                sources.append(
                    dict(
                        id=key,
                        kind=row["kind"],
                        at=row["at"],
                        payload=json.loads(row["payload_json"]),
                    )
                )
            candidates = {
                ref["node"]["fields"].get("evidence", {}).get("candidate") for ref in evidence
            } - {None}
            if candidate is None and len(candidates) == 1:
                candidate = next(iter(candidates))
            if candidates and candidate not in candidates:
                raise PlanStoreError(
                    "judgment-candidate-unbound", "Select a candidate from the captured evidence"
                )
            record: Judgment = dict(
                id=uuid4().hex,
                question=question,
                subjectRefs=subjects,
                evidenceRefs=evidence,
                sourceRefs=sources,
                candidate=candidate,
            )
            record["binding"] = hashlib.sha256(_json(record).encode("utf-8")).hexdigest()
            record.update(
                status="pending-judgment",
                verdict=None,
                rationale=None,
                meaning={},
                gaps=[],
                fixesIssued=0,
                disposition="judge",
                owner=None,
                source=None,
                sourceVerified=False,
                error=None,
                **judgment_example(record),
            )
            self._event("judgment-recorded", record)
        return record

    def judgment(self, judgment_id: str, *, candidate=None) -> dict:
        """Credit requires current bindings, including explicit review/run facts for C."""
        from codex_harness.core.planner_jobs import execution_inputs

        record = self._judgment_record(judgment_id)
        snapshot = getattr(self, "_review_read_snapshot", None)
        if snapshot is not None and "judgmentSpine" not in snapshot:
            snapshot["judgmentSpine"] = self.spine()
        spine = snapshot["judgmentSpine"] if snapshot is not None else self.spine()
        current = (
            not record.get("supersededBy")
            and all(
                ref["id"] in spine.ids() and spine.revision(ref["id"]) == ref["revision"]
                for ref in record["subjectRefs"] + record["evidenceRefs"]
            )
            and (
                record["question"] != "scope-intent"
                or all(
                    ref["payload"].get("facts", {}).get("after", {}).get("digest")
                    == execution_inputs(self)["digest"]
                    for ref in record["sourceRefs"]
                )
            )
        )
        if current:
            current = all(
                binding is not None
                and binding
                == review_binding(
                    self,
                    binding["memberIds"],
                    convergence=binding["convergence"],
                    run_id=binding["runId"],
                )
                and (
                    "acceptanceRun" not in binding
                    or binding["acceptanceRun"] is not None
                    and binding["acceptanceRun"]["id"] == binding["runId"]
                )
                and (
                    facts.get("subject", {}).get("kind") != "C"
                    or binding["runId"] is not None
                    and binding["runId"] == facts["subject"].get("run", {}).get("id")
                )
                for facts in (ref["payload"].get("facts", {}) for ref in record["sourceRefs"])
                if (binding := facts.get("reviewBinding")) is not None
                or facts.get("subject", {}).get("kind") == "C"
            )
        candidate_current = record["candidate"] is None or candidate == record["candidate"]
        reason = (
            record["error"]
            if record.get("supersededBy")
            else None
            if current and candidate_current
            else (
                "Refresh changed subject/evidence bindings"
                if not current
                else "Supply the current bound candidate"
            )
        )
        judged = current and candidate_current and record["status"] == "judged"
        return dict(
            record,
            status="judged" if judged else "pending-judgment",
            current=current and candidate_current,
            positiveCredit=judged and record["verdict"] == "positive",
            concreteGap=judged and record["verdict"] == "negative",
            refreshReason=reason,
        )

    def submit_judgment(
        self, *, judgment_id, response, source, review_context=None, apply_meaning=None
    ) -> dict:
        """An initial response gets exactly two exact fixes; every return releases ownership."""
        _nonempty(source, "source")
        with self._transaction():
            from codex_harness.core.run_ownership import ensure_effect

            ensure_effect(self)
            record = self._judgment_record(judgment_id)
            if record["disposition"] in {"steer", "complete"}:
                raise PlanStoreError(
                    "judgment-attempt-closed",
                    "Read the retained judgment; prepare a fresh request if steering requires one",
                )
            if not self.judgment(judgment_id, candidate=record["candidate"])["current"]:
                record.update(
                    disposition="steer", error="Bindings changed; prepare a fresh judgment"
                )
            else:
                try:
                    parsed = judgment_response(response, record)
                    if apply_meaning is not None:
                        self.connection.execute("SAVEPOINT judgment_payload")
                        try:
                            apply_meaning(parsed["meaning"])
                        except BaseException:
                            self.connection.execute("ROLLBACK TO judgment_payload")
                            self.connection.execute("RELEASE judgment_payload")
                            raise
                        self.connection.execute("RELEASE judgment_payload")
                except (ValueError, TypeError) as error:
                    record.update(
                        error=str(error),
                        disposition="exact-fix" if record["fixesIssued"] < 2 else "steer",
                    )
                    if record["disposition"] == "exact-fix":
                        record["fixesIssued"] += 1
                else:
                    record.update(parsed, error=None)
                    decided = parsed["verdict"] in {"positive", "negative"}
                    record.update(
                        status="judged" if decided else "pending-judgment",
                        disposition="complete" if decided else "judge",
                    )
            record.update(source=source, sourceVerified=False, owner=None)
            self._event(
                "judgment-response",
                dict(
                    id=judgment_id,
                    binding=record["binding"],
                    response=response,
                    source=source,
                    sourceVerified=False,
                ),
            )
            self._event("judgment-recorded", record)
            facts = next(
                (r["payload"]["facts"] for r in record["sourceRefs"] if "facts" in r["payload"]), {}
            )
            if (
                "reviewBinding" in facts
                and record["error"] is None
                and (
                    record["question"] == "plan-review"
                    and facts["reviewBinding"]["runId"] is None
                    or record["question"] != "plan-review"
                    and record["verdict"] == "negative"
                )
            ):
                from codex_harness.core.roundtable import review_members

                reviewer = facts.get("reviewer", facts.get("review", {}))
                for item in review_members(
                    self,
                    record if record["question"] == "plan-review" else dict(record, meaning={}),
                    facts,
                ):
                    self._event(
                        "finding-recorded",
                        dict(
                            item,
                            **(review_context or {}),
                            id=uuid4().hex,
                            reviewer=reviewer.get("name", reviewer.get("reviewer", "judge")),
                            provider=reviewer.get("provider", "judge"),
                            plan_revision=facts.get("manifestRevision", facts.get("revision")),
                            round_id=facts.get("roundId", facts.get("invocationId")),
                            created_at=_now(),
                            stage="acceptance" if facts["reviewBinding"]["runId"] else "roundtable",
                            runId=facts["reviewBinding"]["runId"],
                        ),
                    )
            if record["question"] == "vision-intent":
                self._classify_vision_edit(self.spine().node(self.spine().ids("vision")[0]))
        result = self.judgment(judgment_id)
        if record["question"] == "review-reuse" and result["positiveCredit"]:
            result["convergence"] = self.review_convergence(prepare=True)
        return result
