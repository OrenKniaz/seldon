"""Canonical reviews writers; composed by the PlanStore transaction owner."""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext

from codex_harness.core.reviewer_evidence import finding_records, native_reviewer, review_binding
from codex_harness.core.spine import (
    Spine,
    requirement_file_sources,
)


from codex_harness.core.plan_types import PlanStoreError, _json, _nonempty


class PlanReviews:
    def latest_review_failure(self) -> dict | None:
        row = self.connection.execute(
            "SELECT payload_json FROM events WHERE kind='acceptance-review-failed' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return json.loads(row[0]) if row else None

    def review(self, review_id: str) -> dict:
        """Read recorded roundtable (round or reviewer id) or acceptance evidence."""
        saved = self.connection.execute(
            "SELECT result_json FROM acceptance_reviews WHERE id=?", (review_id,)
        ).fetchone()
        if saved:
            result = json.loads(saved[0])
            findings = [f for f in self.acceptance_findings() if f.get("runId") == result["runId"]]
            result["findingCountsScope"] = "scenario run (findings do not record review ids)"
        else:
            exists = self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_runs'"
            ).fetchone()
            rows = (
                list(
                    self.connection.execute(
                        "SELECT * FROM review_runs WHERE round_id=? OR id=? ORDER BY rowid",
                        (review_id, review_id),
                    )
                )
                if exists
                else []
            )
            if not rows:
                raise PlanStoreError("plan-review-not-found", review_id)
            result = {"id": review_id, "reviewers": []}
            for saved in rows:
                row = dict(saved)
                for key in ("shardDetails", "reshardedFrom", "outOfPacketTools"):
                    row[key] = json.loads(row[key]) if row.get(key) else None
                for key in ("isMocked", "didRunProvider"):
                    row[key] = bool(row[key]) if row.get(key) is not None else None
                row["name"] = row["reviewer"]
                row["packetBound"] = (
                    bool(row["packetBound"]) if row.get("packetBound") is not None else None
                )
                result["reviewers"].append(row)
            origins = {
                part["judgmentId"]
                for row in rows
                for part in json.loads(row["shardDetails"] or "[]")
                if part.get("judgmentId")
            }
            findings = [
                f
                for f in self.finding_state()["members"]
                if f.get("round_id") == rows[0]["round_id"] or f.get("judgmentId") in origins
            ]
        for row in result["reviewers"]:
            counts = {}
            for finding in findings:
                if (finding["reviewer"], finding["provider"]) == (row["name"], row["provider"]):
                    counts[finding["status"]] = counts.get(finding["status"], 0) + 1
            row["findingCounts"] = counts
            row["findings"] = sum(counts.values())
        policy = self.plan_review_policy_state(self.manifest_revision())
        if policy["active"]:
            result["reviewPolicy"] = policy
        return result

    def readiness_reviews(self, revision: str | None = None) -> list[dict]:
        query = "SELECT result_json FROM readiness_reviews"
        params = () if revision is None else (revision,)
        if revision is not None:
            query += " WHERE manifest_revision=?"
        return [
            json.loads(row[0]) for row in self.connection.execute(query + " ORDER BY rowid", params)
        ]

    def readiness_status(self, revision: str | None = None) -> dict:
        from codex_harness.core.readiness import readiness_status

        return readiness_status(
            self, revision if revision is not None else self.manifest_revision()
        )

    def record_plan_review_policy(self, slots: list[dict], expected_revision: str) -> dict:
        """Prospective plan-stage roles only; no identity, permission or review credit.

        Existing records cannot be reclassified. Model/effort and independence
        observations require their owning producers, not supplied role metadata.
        """
        from codex_harness.core.roundtable import _table

        if not isinstance(slots, list) or not slots:
            raise ValueError("review-policy-slots-required")
        names = set()
        normalized = []
        for slot in slots:
            if not isinstance(slot, dict) or set(slot) != {"name", "provider", "required"}:
                raise ValueError("review-policy-slot-requires-name-provider-required")
            _nonempty(slot["name"], "reviewer name")
            if slot["provider"] not in {"codex", "claude", "copilot"}:
                raise ValueError("review-policy-provider-invalid")
            if type(slot["required"]) is not bool or slot["name"] in names:
                raise ValueError("review-policy-role-or-name-invalid")
            names.add(slot["name"])
            normalized.append(dict(slot))
        if not any(slot["required"] for slot in normalized):
            raise ValueError("review-policy-required-slot-missing")
        payload = dict(
            schema="seldon/plan-review-policy-v1",
            manifestRevision=expected_revision,
            stage="plan",
            slots=sorted(normalized, key=lambda row: row["name"]),
        )
        with self._transaction():
            if not expected_revision or self.manifest_revision() != expected_revision:
                raise PlanStoreError("review-policy-revision-stale", "Compile and refresh first")
            prior = self._plan_review_policies()
            same = [row for row in prior if row["payload"]["manifestRevision"] == expected_revision]
            if same:
                if same[-1]["payload"] == payload:
                    return payload
                raise PlanStoreError("review-policy-fixed", "Existing reviewer roles are immutable")
            _table(self)
            for table in ("review_runs", "review_attempts"):
                exists = self.connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if (
                    exists
                    and self.connection.execute(
                        f"SELECT 1 FROM {table} WHERE plan_revision=? LIMIT 1", (expected_revision,)
                    ).fetchone()
                ):
                    raise PlanStoreError(
                        "review-policy-attempts-exist", "Declare roles before any review attempts"
                    )
            self._event("plan-review-policy-declared", payload)
        return payload

    def _plan_review_policies(self) -> list[dict]:
        return [
            dict(id=row["id"], at=row["at"], payload=json.loads(row["payload_json"]))
            for row in self.connection.execute(
                "SELECT id, at, payload_json FROM events "
                "WHERE kind='plan-review-policy-declared' ORDER BY id"
            )
        ]

    def _review_credit_rows(self, revision, *, carry=False):
        """Select the latest reservation before eligibility; never fall back to an older pass."""
        tables = {
            row[0]
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        runs = (
            [
                dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM review_runs WHERE (plan_revision=? OR ?) "
                    "ORDER BY rowid DESC",
                    (revision, carry),
                )
            ]
            if "review_runs" in tables
            else []
        )
        attempts = (
            [
                dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM review_attempts WHERE (plan_revision=? OR ?) ORDER BY rowid DESC",
                    (revision, carry),
                )
            ]
            if "review_attempts" in tables
            else []
        )
        identities = {(r["reviewer"], r["provider"]) for r in runs + attempts}
        result = {}
        for identity in identities:
            matches = [r for r in runs if (r["reviewer"], r["provider"]) == identity]
            reserved = [r for r in attempts if (r["reviewer"], r["provider"]) == identity]
            if not reserved:
                from codex_harness.core.plan_types import refuse_old_format
                refuse_old_format("Plan review without a reservation")
            reservation = reserved[0]
            run = next((r for r in matches if r["id"] == reservation["id"]), None)
            success = bool(
                run
                and run["exit_code"] == 0
                and run["invalid"] == 0
                and run.get("isMocked") == 0
                and run.get("didRunProvider") == 1
                and run.get("packetBound") == 1
                and native_reviewer(
                    dict(
                        provider=run["provider"],
                        shards=run.get("shards"),
                        shardDetails=json.loads(run.get("shardDetails") or "[]"),
                    ),
                    self,
                    current=run["plan_revision"] == revision,
                )
            )
            result[identity] = dict(
                run=run,
                success=success,
                latestReviewId=run["id"] if run else None,
                latestAttemptId=reservation["id"],
                attemptOrdering="reservation-rowid",
                ambiguousAttemptOrder=False,
                incompleteAttempt=bool(
                    not run and reservation.get("state") != "abandoned"
                ),
            )
        return result

    def plan_review_policy_state(self, revision: str) -> dict:
        """One derived prospective gate projection over shared reservation-ordered credit.

        No general observed-separation, clause-witness or targeted-assessment
        producer exists here. Author references cannot fill those gaps.
        """
        policies = self._plan_review_policies()
        if not policies:
            return {"active": False, "mode": "legacy", "eligible": None}
        latest = policies[-1]
        policy = latest["payload"]
        result = dict(
            active=True,
            schema="seldon/plan-review-policy-state-v1",
            stage="plan",
            workspace=str(self.workspace),
            policyEventId=latest["id"],
            manifestRevision=revision,
            declaredRevision=policy["manifestRevision"],
            eligible=False,
            state="pending-judgment",
            owner=None,
            slots=[],
            requiredFailures=[],
            optionalFailures=[],
            successfulReviewIds=[],
            producerAvailability=dict(
                observedSeparation="unavailable",
                targetedAssessment="unavailable",
                clauseWitness="unavailable",
            ),
            reasons=[],
            roleBinding=(
                "Persisted reviewer/provider only; no observed model/effort or author identity"
            ),
        )
        if policy["manifestRevision"] != revision:
            result["reasons"] = ["review-policy-revision-stale"]
            return result
        credits = self._review_credit_rows(revision)
        declared = {(slot["name"], slot["provider"]) for slot in policy["slots"]}
        undeclared = sorted(
            {identity for identity, row in credits.items() if row["run"] or row["incompleteAttempt"]} - declared
        )
        if undeclared:
            result["reasons"].append("review-policy-undeclared-attempt")
            result["unlistedReviewers"] = [
                dict(name=name, provider=provider) for name, provider in undeclared
            ]
        for slot in policy["slots"]:
            credit = credits.get((slot["name"], slot["provider"]), {})
            success = credit.get("success", False)
            observation = dict(
                **slot,
                state="passed" if success else "unverified-or-failed",
                **{key: credit.get(key) for key in ("latestReviewId", "latestAttemptId", "attemptOrdering")},
                ambiguousAttemptOrder=credit.get("ambiguousAttemptOrder", False),
                incompleteAttempt=credit.get("incompleteAttempt", False),
            )
            result["slots"].append(observation)
            if success:
                result["successfulReviewIds"].append(credit["latestReviewId"])
            else:
                result["requiredFailures" if slot["required"] else "optionalFailures"].append(
                    slot["name"]
                )
        if result["requiredFailures"]:
            result["reasons"].append("review-required-latest-attempt-not-passed")
        result["eligible"] = not result["reasons"]
        result["state"] = "judged" if result["eligible"] else "pending-judgment"
        return result

    def review_nodes_digest(self) -> dict:
        """Capture exact planning nodes and template bytes without semantic repair exceptions."""
        spine = self.spine()
        template = self.workspace / "template"
        sources = (
            requirement_file_sources(template, [p.name for p in template.iterdir()])
            if template.is_dir()
            else {}
        )
        return dict(
            nodes=spine.dependency_revisions(spine.ids()),
            files={
                name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in sources.items()
            },
        )

    def review_judgment(self, reviewer, shard, *, prepare=True):
        if "\n## Bound review requests\n" in shard["packet"]:
            requests = json.loads(shard["packet"].split("\n## Bound review requests\n", 1)[1])
            return self.judgment(requests[reviewer["name"]]["id"])
        from codex_harness.core.approvals import assess

        facts = dict(
            reviewer=reviewer,
            invocationId=shard["reviewInvocationId"],
            reviewBinding=review_binding(self, run_id=shard.get("acceptanceRunId")),
            manifestRevision=self.manifest_revision(),
            packet=shard["packet"],
            packetSha256=shard["hash"],
            nodeIds=shard["nodeIds"],
            roundId=shard["reviewRoundId"],
            question="Independently judge whether this plan leads to the whole accepted dream. Judge relevance and meaning of all evidence, regardless of language or instrumentation. Return an explicit verdict and rationale; meaning.findings contains structured findings. Empty or minor-only findings do not imply approval. meaning.depth records the chosen review depth and its consequence-based reason. Keep each reviewer's verdict separate; unavailable evidence stays inconclusive.",
        )
        with (
            nullcontext() if self.connection.in_transaction or not prepare else self._transaction()
        ):
            if prepare:
                for row in list(
                    self.connection.execute(
                        "SELECT payload_json FROM events WHERE id IN (SELECT max(id) FROM events "
                        "WHERE kind='judgment-recorded' GROUP BY json_extract(payload_json, '$.id'))"
                    )
                ):
                    prior = json.loads(row[0])
                    before = next(
                        (
                            r["payload"]["facts"]
                            for r in prior["sourceRefs"]
                            if "facts" in r["payload"]
                        ),
                        {},
                    )
                    if (
                        prior["question"] == "plan-review"
                        and prior["status"] == "pending-judgment"
                        and not prior.get("supersededBy")
                        and before.get("reviewer", {}).get("name") == reviewer["name"]
                        and before["reviewer"]["provider"] == reviewer["provider"]
                        and before.get("invocationId") != facts["invocationId"]
                        and before.get("reviewBinding") == facts["reviewBinding"]
                    ):
                        prior.update(
                            disposition="steer",
                            owner=None,
                            supersededBy=facts["invocationId"],
                            error="Dossier superseded; inspect its replacement",
                        )
                        self._event("judgment-recorded", prior)
            return assess(
                self, "plan-review", facts, subject_ids=self.spine().ids(), prepare=prepare
            )

    def review_reuse(self, row, current, *, prepare=False):
        from codex_harness.core.approvals import assess

        details = json.loads(row.get("shardDetails") or "[]")
        originals = [
            self.judgment(part["judgmentId"]) for part in details if part.get("judgmentId")
        ]
        member_ids = [
            r["id"]
            for r in finding_records(self)
            if r.get("round_id") == row["round_id"]
            and r["reviewer"] == row["reviewer"]
            and r["provider"] == row["provider"]
        ]
        return assess(
            self,
            "review-reuse",
            dict(
                review=dict(row),
                before=originals,
                reviewBinding=review_binding(self, member_ids),
                beforeDigest=json.loads(row["nodesDigest"]),
                afterDigest=current,
                currentNodes=self.list_nodes(),
                currentRevision=self.manifest_revision(),
                question="Judge the actual revision delta, including repaired and changed evidence. "
                "May this independent prior review still supply approval for this exact revision? "
                "meaning.reuse must be explicit; unchanged field locations or similar text grant no credit.",
            ),
            subject_ids=self.spine().ids(),
            prepare=prepare,
        )

    def successful_reviews(self, revision: str) -> list[dict]:
        """Credit only the latest reservation, with native identity and explicit judged reuse."""
        if not self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='review_runs'").fetchone():
            return []
        policy = self.plan_review_policy_state(revision)
        from codex_harness.core.planner_review import selection_inputs, delivery_review_selection
        from codex_harness.core.approvals import reviewer_tuple

        selected = selection_inputs(self)[1]
        allowed = delivery_review_selection(selected["fields"])[0] if selected else []
        carry = (
            not policy["active"]
            and Spine(self._data()).semantic
            and revision is not None
            and revision == self.manifest_revision()
        )
        snapshot = self.review_nodes_digest() if carry else None
        latest = {}
        for credit in self._review_credit_rows(revision, carry=carry).values():
            row = credit["run"]
            if not credit["success"]:
                continue
            if policy["active"] and row["id"] not in policy["successfulReviewIds"]:
                continue
            details = json.loads(row.get("shardDetails") or "[]")
            profiles = [
                ref["payload"].get("facts", {}).get("reviewer")
                for part in details
                if part.get("judgmentId")
                for ref in self._judgment_record(part["judgmentId"])["sourceRefs"]
            ]
            profiles = [reviewer_tuple(profile) for profile in profiles if profile]
            if not profiles or any(profile not in allowed for profile in profiles):
                continue
            if row["plan_revision"] != revision:
                if not row.get("nodesDigest"):
                    continue
                reuse = self.review_reuse(row, snapshot)
                if not (reuse["positiveCredit"] and reuse["meaning"].get("reuse") is True):
                    continue
                row.update(
                    carriedForward=True,
                    carriedFromRevision=row["plan_revision"],
                    reuseJudgmentId=reuse["id"],
                )
            latest.setdefault((row["provider"], row["reviewer"]), row)
        return [latest[key] for key in sorted(latest)]

    def review_convergence(self, *, prepare=False):
        from codex_harness.core.approvals import assess

        rows = [dict(row) for row in self.connection.execute("SELECT * FROM review_runs ORDER BY rowid")]
        spine = self.spine()
        rounds = 1 if spine.node(spine.ids("vision")[0])["fields"].get("planSize") in {"tiny", "small"} else 3
        if len({row["round_id"] for row in rows}) < rounds:
            return None
        return assess(
            self,
            "plan-convergence",
            dict(
                rounds=rows,
                currentNodes=self.list_nodes(),
                reviewBinding=review_binding(self, convergence=True),
                revision=self.manifest_revision(),
                defaultRounds=rounds,
                question="After these independent review rounds, judge convergence and consequence-scaled "
                "depth (tiny/small: one short independent round unless a concrete gap needs repair). "
                "meaning.action is ready, repair, continue-review or steer. Three rounds never "
                "grant approval automatically. Explain remaining concrete gaps or readiness.",
            ),
            subject_ids=self.spine().ids(),
            prepare=prepare,
        )

    def _check_evidence(self, revision: str) -> None:
        from codex_harness.core.approvals import require_approval

        require_approval(self, "B")
        policy = self.plan_review_policy_state(revision)
        if policy["active"] and not policy["eligible"]:
            raise PlanStoreError(
                "plan-review-policy-unmet",
                "Prospective review policy held: "
                + _json(policy)
                + ". Failed required reviews need a current retry within existing authorization; "
                "unavailable assessment/identity producers cannot be replaced by author claims.",
            )
        missing = []
        if not Spine(self._data()).semantic and not self.passing_audit(revision):
            missing.append(
                "passing audit (audit --oracle oracle, or audit --prospective); "
                "audit-binding-required: legacy unbound prospective history "
                "grants no current credit"
            )
        prerequisite = self.review_prerequisite(revision)
        if not (prerequisite["complete"] or prerequisite["unavailable"]):
            missing.append(
                "current positive verdict from every selected reviewer and current convergence judgment"
            )
        if missing:
            raise PlanStoreError(
                "plan-publication-evidence-required",
                f"Missing {' and '.join(missing)} for current manifest revision {revision}",
            )

    def prepare_review_reuse(self):
        """The existing reuse owner judges actual deltas before another paid review."""
        if (
            not self.spine().semantic
            or not self.connection.execute("PRAGMA table_info(review_runs)").fetchone()
        ):
            return []
        reuse = []
        for credit in self._review_credit_rows(self.manifest_revision(), carry=True).values():
            row = credit["run"]
            if not credit["success"]:
                continue
            if row["plan_revision"] == self.manifest_revision() or not row.get("nodesDigest"):
                continue
            reuse.append(self.review_reuse(row, self.review_nodes_digest(), prepare=True))
        return reuse

    def completed_review(self, effect):
        """Read the exact completed round recorded by this effect's admissions."""
        ids = [
            r.split(":", 1)[1] for r in effect.get("attemptIds", []) if r.startswith("plan-review:")
        ]
        rows = [
            self.connection.execute("SELECT * FROM review_runs WHERE id=?", (key,)).fetchone()
            for key in ids
        ]
        if not rows or any(
            row is None or row["plan_revision"] != self.manifest_revision() for row in rows
        ):
            return None
        rounds = {row["round_id"] for row in rows}
        if len(rounds) != 1:
            return None
        return next(
            (
                json.loads(row[0])
                for row in self.connection.execute(
                    "SELECT payload_json FROM events WHERE kind='roundtable-completed' ORDER BY id DESC"
                )
                if json.loads(row[0])["roundId"] in rounds
            ),
            None,
        )
