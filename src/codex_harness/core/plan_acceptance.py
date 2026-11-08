"""Canonical acceptance writers; composed by the PlanStore transaction owner."""

from __future__ import annotations

import json
import subprocess
from contextlib import nullcontext
from copy import deepcopy
from uuid import uuid4

from codex_harness.core import worker_packet_budget
from codex_harness.core.reviewer_evidence import finding_records, native_reviewer, review_binding
from codex_harness.core.spine import Spine


from codex_harness.core.plan_types import PlanStoreError, _json, _nonempty


class PlanAcceptance:
    def locked_acceptance_spine(self) -> Spine:
        from codex_harness.core.approvals import require_approval

        meta = self._meta()
        if not meta["locked_vision_json"]:
            raise PlanStoreError("acceptance-vision-unlocked", "Confirm the current locked vision")
        require_approval(self, "A")
        return self.spine()

    def acceptance_scenarios(self) -> list[dict]:
        if self._data()["nodes"] and (spine := self.spine()).semantic:
            from codex_harness.core.acceptance import derive_scenarios

            return derive_scenarios(spine)
        scenarios = [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT scenario_json FROM acceptance_scenarios ORDER BY id"
            )
        ]
        spine = self.spine()
        for scenario in scenarios:
            refs = scenario.get("nodeRevisions", {})
            if not spine.bindings_current(refs):
                scenario["status"] = "stale"
        return scenarios

    def save_acceptance_scenarios(self, scenarios: list[dict]) -> None:
        if self.spine().semantic:
            raise ValueError(
                "Semantic scenarios belong to vision.fields.scenarios; use upsert_node"
            )
        from codex_harness.core.acceptance import derive_scenarios

        with nullcontext() if self.connection.in_transaction else self._transaction():
            spine = self.locked_acceptance_spine()
            required = {item["id"] for item in derive_scenarios(spine)}
            ids = []
            stored = []
            for scenario in scenarios:
                if (
                    not isinstance(scenario, dict)
                    or not all(
                        isinstance(scenario.get(key), str) and scenario[key].strip()
                        for key in ("id", "title", "given")
                    )
                    or not isinstance(scenario.get("steps"), list)
                    or not isinstance(scenario.get("expect"), dict)
                ):
                    raise PlanStoreError(
                        "acceptance-scenario-invalid", "Expected id/title/given/steps/expect"
                    )
                for step in scenario["steps"]:
                    command = step.get("command") if isinstance(step, dict) else step
                    if not isinstance(command, str) or not command.strip():
                        raise PlanStoreError(
                            "acceptance-scenario-invalid", "Steps require shell commands"
                        )
                ids.append(scenario["id"])
                refs = set(scenario.get("nodeIds", [])) | set(spine.ids("vision"))
                if refs - set(spine.ids()):
                    raise PlanStoreError("plan-node-missing", "Unknown acceptance scenario node")
                steps = scenario["steps"]
                executable = bool(steps) and bool(
                    scenario["expect"] or isinstance(steps[-1], dict) and steps[-1].get("expect")
                )
                stored.append(
                    {
                        **scenario,
                        "status": "planned" if executable else "draft",
                        "executable": executable,
                        "nodeRevisions": spine.dependency_revisions(refs),
                    }
                )
            if len(set(ids)) != len(ids) or not required <= set(ids):
                raise PlanStoreError(
                    "acceptance-coverage-required", "Cover first-moment and every scope item"
                )
            self.connection.execute("DELETE FROM acceptance_scenarios")
            self.connection.executemany(
                "INSERT INTO acceptance_scenarios VALUES (?, ?)",
                [(s["id"], _json(s)) for s in stored],
            )
            self._event("acceptance-scenarios-saved", {"ids": ids})

    def register_execution_assessment(
        self,
        *,
        members,
        worker_job_id,
        bead_id,
        expected_manifest_revision,
        expected_product_run_id,
        reviewer,
        timeout,
    ):
        """Prepare captured execution inputs; admission occurs only at real launch."""
        from codex_harness.core import acceptance_recovery as recovery
        from codex_harness.core import roundtable

        request = deepcopy(
            dict(
                members=members,
                worker_job_id=worker_job_id,
                bead_id=bead_id,
                expected_manifest_revision=expected_manifest_revision,
                expected_product_run_id=expected_product_run_id,
                reviewer=reviewer,
                timeout=timeout,
            )
        )
        context = roundtable._execution_context(self, request)
        item_id = uuid4().hex
        binding = roundtable._assessment_hash(
            dict(registrationId=item_id, request=request, context=context)
        )
        response = dict(
            schema="seldon/execution-member-response-v1", snapshotHash=binding, members=[]
        )
        originals = {row["finding"]["id"]: row["finding"] for row in context["origins"]}
        for member in members:
            response["members"].append(
                dict(
                    **member,
                    disposition=originals[member["memberId"]]["status"],
                    judgment="cannot-assess",
                    explanation=(
                        "Address the exact original concern against these specific "
                        "protected observations."
                    ),
                )
            )
        packet = json.dumps(
            dict(
                schema="seldon/canonical-execution-packet-v1",
                binding=binding,
                context=context,
                responseContract=response,
                instructions=(
                    "Read only this packet. No tools. Return the exact response object, "
                    "once per member. Positive judgment requires relevant protected behavior "
                    "or checkable counterevidence, not a hash/link. Use cannot-assess when "
                    "unsupported. Rejection may use counterevidence without edits. Deferral "
                    "retains the locked obligation."
                ),
            ),
            ensure_ascii=False,
        )
        if (
            roundtable.estimated_tokens(packet)
            > worker_packet_budget.PROVIDER_TOKEN_BUDGETS[reviewer["provider"]]
        ):
            raise ValueError("execution-complete-packet-too-large")
        executable = context["reviewerExecutable"]["path"]
        launch = roundtable._launch_review(reviewer, packet, executable)
        header = dict(
            schema=1,
            registrationId=item_id,
            request=request,
            context=context,
            binding=binding,
            packet=packet,
            launch=recovery._launch_identity(launch, executable),
        )
        return header

    def commit_execution_assessment(self, registration_id):
        """Consume only the producer's registered original, once, under the writer."""
        from codex_harness.core import roundtable

        with self._transaction():
            header = roundtable._execution_header(self, registration_id)
            captured = roundtable._execution_events(
                self, "execution-assessment-captured", registration_id
            )
            if len(captured) != 1:
                # A caller cannot turn an uncertain live/absent return into a
                # terminal fact and thereby release an overlapping later pass.
                raise ValueError("execution-producer-capture-missing-or-ambiguous")
            if roundtable._execution_events(
                self, "execution-assessment-observation-error", registration_id
            ):
                raise ValueError("execution-producer-terminal-observation-unverified")
            completed = roundtable._execution_events(
                self, "execution-assessment-consumed", registration_id
            )
            capture_hash = (
                roundtable._assessment_hash(captured[0][1]) if len(captured) == 1 else None
            )
            if completed:
                if len(completed) != 1 or completed[0][1].get("captureHash") != capture_hash:
                    raise ValueError("execution-replay-changed-or-ambiguous")
                roundtable._execution_parse(self, header)
            else:
                try:
                    if roundtable._execution_context(self, header["request"]) != header["context"]:
                        raise ValueError("execution-selection-stale-before-consumption")
                    result = roundtable._execution_parse(self, header)
                    if (
                        roundtable._execution_context(self, header["request"]) != header["context"]
                        or roundtable._execution_parse(self, header) != result
                    ):
                        raise ValueError("execution-selection-raced-before-consumption")
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    subprocess.SubprocessError,
                ) as error:
                    result = dict(members=[], holdReasons=[str(error)], captureHash=capture_hash)
                self._event(
                    "execution-assessment-consumed",
                    dict(schema=1, registrationId=registration_id, **result),
                )
        return self.execution_assessment_state()

    def execution_assessment_state(self, finding_ids=None):
        """Same stage projection used by the entry and acceptance status; no final credit."""
        from codex_harness.core import roundtable

        try:
            rows = roundtable._execution_reservations(self)
        except (ValueError, KeyError, TypeError) as error:
            return dict(
                schema="seldon/execution-assessed-support-v1",
                members=[],
                fullVisionHeld=True,
                closureEligible=False,
                holdReasons=[str(error)],
            )
        latest, incomplete = {}, set()
        for _, header in rows:
            keys = [row["memberId"] for row in header["request"]["members"]]
            for key in keys:
                latest[key] = header
            if not roundtable._execution_terminal_original(self, header):
                incomplete.update(keys)
        members = []
        for key, header in latest.items():
            if finding_ids is not None and key not in finding_ids:
                continue
            reasons = []
            reasons.extend(
                row["error"]
                for _, row in roundtable._execution_events(
                    self, "execution-assessment-observation-error", header["registrationId"]
                )
            )
            results = roundtable._execution_events(
                self, "execution-assessment-consumed", header["registrationId"]
            )
            selected = None
            if key in incomplete:
                reasons.append("execution-overlapping-incomplete-reservation")
            try:
                if roundtable._execution_context(self, header["request"]) != header["context"]:
                    raise ValueError("execution-current-selection-changed")
                if len(results) != 1:
                    raise ValueError("execution-latest-attempt-not-consumed")
                parsed = roundtable._execution_parse(self, header)
                if parsed != {name: results[0][1][name] for name in parsed}:
                    raise ValueError("execution-consumed-result-changed")
                selected = next(row for row in parsed["members"] if row["memberId"] == key)
                if selected["support"] != "assessed-current":
                    reasons.append("execution-review-negative-cannot-assess-or-deferred")
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                StopIteration,
                subprocess.SubprocessError,
            ) as error:
                reasons.append(str(error))
            member = dict(selected or {"memberId": key, "retainedObligation": True})
            member.update(registrationId=header["registrationId"], holdReasons=reasons)
            if reasons:
                member["support"] = "unverified"
            members.append(member)
        return dict(
            schema="seldon/execution-assessed-support-v1",
            members=members,
            fullVisionHeld=True,
            closureEligible=False,
            scope="current registered AF execution support only; native final acceptance unchanged",
        )

    def finding_state(self) -> dict:
        """The sole effective obligation state and outstanding-request projection."""
        snapshot = getattr(self, "_review_read_snapshot", None)
        if snapshot is None:
            self._review_read_snapshot = {}
            try:
                return self.finding_state()
            finally:
                self._review_read_snapshot = None
        if "findingState" in snapshot:
            return snapshot["findingState"]
        records = [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT payload_json FROM events WHERE id IN "
                "(SELECT max(id) FROM events WHERE kind='judgment-recorded' "
                "GROUP BY json_extract(payload_json, '$.id')) ORDER BY id"
            )
        ]
        records = {record["id"]: record for record in records}
        snapshot["judgmentRecords"] = records
        sources = finding_records(self)
        linked = {source.get("judgmentId") for source in sources}
        judgments, pending, groups = {}, [], {}
        for record in reversed(list(records.values())):
            facts = next(
                (r["payload"]["facts"] for r in record["sourceRefs"] if "facts" in r["payload"]), {}
            )
            reviewer = facts.get("reviewer", facts.get("review", {}))
            identity = (
                facts.get("invocationId", facts.get("request_id", record["id"])),
                (reviewer.get("provider"), reviewer.get("name", reviewer.get("reviewer"))),
                facts.get("packetSha256")
                or tuple(
                    facts.get("reviewBinding", {}).get("memberIds")
                    or [r["id"] for r in record["subjectRefs"]]
                ),
            )
            judgment = (
                self.judgment(record["id"])
                if (
                    record["status"] == "pending-judgment"
                    or record["id"] in linked
                    or facts.get("reviewBinding", {}).get("memberIds")
                    or record["question"] == "finding-equivalence"
                )
                else record
            )
            judgments[record["id"]] = judgment
            if record["status"] == "pending-judgment" and not record.get("supersededBy"):
                action = (
                    "refresh"
                    if not judgment["current"]
                    else "fresh-invocation"
                    if record["disposition"] == "steer"
                    else "submit"
                )
                pending.append(
                    dict(
                        requestKey=identity,
                        action=action,
                        submitReady=action == "submit",
                        acceptanceRunId=facts.get("reviewBinding", {}).get("runId"),
                        **{
                            k: judgment[k]
                            for k in (
                                "id",
                                "question",
                                "status",
                                "current",
                                "disposition",
                                "positiveCredit",
                                "concreteGap",
                            )
                        },
                    )
                )
            if record["question"] == "finding-equivalence" and judgment["positiveCredit"]:
                for group in judgment["meaning"].get("groups", []):
                    for key in group:
                        groups.setdefault(key, group[0])
        members = []
        for source in sources:
            origin = judgments.get(source.get("judgmentId"), {})
            state = "open" if origin.get("concreteGap") else "pending"
            status, resolution, linked, decision = (
                state,
                "",
                [source["judgmentId"]] if source.get("judgmentId") else [],
                None,
            )
            for judgment in judgments.values():
                facts = next(
                    (
                        r["payload"]["facts"]
                        for r in judgment["sourceRefs"]
                        if "facts" in r["payload"]
                    ),
                    {},
                )
                if source["id"] not in facts.get("reviewBinding", {}).get("memberIds", []):
                    continue
                linked.append(judgment["id"])
                if (
                    judgment["question"] == "finding-disposition"
                    and judgment["positiveCredit"]
                    and decision is None
                ):
                    decision = judgment
                    status = judgment["meaning"]["memberDispositions"][source["id"]]
                    state = (
                        "closed"
                        if status in {"resolved", "rejected"}
                        else "open"
                        if status == "open" and origin.get("concreteGap")
                        else "pending"
                    )
                    resolution = next(
                        (p["resolution"] for p in facts["proposals"] if p["id"] == source["id"]),
                        judgment["rationale"],
                    )
            if state == "pending" and status != "deferred":
                status = "pending"
            members.append(
                dict(
                    source,
                    state=state,
                    status=status,
                    resolution=resolution,
                    judgmentIds=list(dict.fromkeys(linked)),
                    cluster_id=groups.get(source["id"]),
                )
            )
        required = [
            row for row in members if row["state"] != "closed" and row.get("stage") != "readiness"
        ]
        ids = [row["id"] for row in required]
        action = dict(
            kind="reconcile_findings",
            state="pending-judgment",
            question="finding-disposition",
            findingIds=ids,
            mcpTool="harness_chat",
            message="Answer the current typed question with content.findings for obligation ids "
            + ", ".join(ids)
            + "; supply evidence-backed dispositions and reasons. The controller prepares the bound judgment.",
        )
        if not required:
            action = None
        if any(row.get("stage") == "acceptance" for row in required):
            from codex_harness.core.acceptance import acceptance_run_prerequisite

            if recovery := acceptance_run_prerequisite(self):
                action = dict(
                    recovery,
                    findingIds=ids,
                    message=recovery["message"] + "; retained obligations: " + ", ".join(ids),
                )
        snapshot["findingState"] = dict(
            members=members,
            pendingJudgments=pending,
            dispositionPrerequisite=dict(
                complete=not required, members=required, nextAction=action
            ),
        )
        return snapshot["findingState"]

    def require_finding_dispositions(self):
        prerequisite = self.finding_state()["dispositionPrerequisite"]
        if not prerequisite["complete"]:
            raise PlanStoreError("pending-judgment", prerequisite["nextAction"]["message"])

    def review_prerequisite(self, revision):
        from codex_harness.core.planner_review import delivery_review_selection, selection_inputs

        reviews = self.successful_reviews(revision)
        _, node, _ = selection_inputs(self)
        selected = delivery_review_selection(node["fields"])[0] if node else []
        expected = {(r["provider"], r["name"]) for r in selected}
        convergence = self.review_convergence() if reviews else None
        return dict(
            complete=bool(expected)
            and expected <= {(r["provider"], r["reviewer"]) for r in reviews}
            and not any(
                r["submitReady"] and r["question"] == "plan-review" and r["acceptanceRunId"] is None
                for r in self.finding_state()["pendingJudgments"]
            )
            and (
                convergence is None
                or convergence["positiveCredit"]
                and convergence["meaning"].get("action") == "ready"
            ),
            reviews=reviews,
            convergence=convergence,
        )

    def acceptance_findings(self) -> list[dict]:
        return [r for r in self.finding_state()["members"] if r.get("stage") == "acceptance"]

    def cluster_review_findings(self, round_id: str) -> dict:
        from codex_harness.core.approvals import assess

        rows = [row for row in finding_records(self) if row.get("round_id") == round_id]
        if len(rows) < 2:
            return dict(count=len(rows), judgment=None)
        judgment = assess(
            self,
            "finding-equivalence",
            dict(
                members=rows,
                reviewBinding=review_binding(self, [row["id"] for row in rows]),
                question="Which findings describe the same concrete gap? meaning.groups lists disjoint "
                "groups of exact member IDs. Equivalence grants display grouping only, never closure.",
            ),
            subject_ids=self.spine().ids(),
        )
        groups = judgment["meaning"].get("groups", []) if judgment["positiveCredit"] else []
        return dict(count=len(rows) - sum(len(group) - 1 for group in groups), judgment=judgment)

    def _closure_declaration(self, closure, row, members, status):
        """Validate links, never infer semantic truth or observed independence."""
        required = set(
            (
                "schema expectedManifestRevision memberIds basis nodeRefs "
                "reviewIds auditIds obligationNodeIds"
            ).split()
        )
        if not isinstance(closure, dict) or set(closure) != required:
            raise ValueError(
                "review-closure-contract-invalid: expected " + ", ".join(sorted(required))
            )
        revision = self.manifest_revision()
        if closure["schema"] != "seldon/finding-closure-declaration-v1":
            raise ValueError("review-closure-schema-invalid")
        if closure["expectedManifestRevision"] != revision:
            raise ValueError("review-closure-revision-stale")
        if closure["basis"] != {
            "resolved": "repair",
            "rejected": "counterevidence",
            "deferred": "defer",
        }.get(status):
            raise ValueError("review-closure-basis-disposition-mismatch")
        ids = [member["id"] for member in members]
        if not isinstance(closure["memberIds"], list) or sorted(closure["memberIds"]) != sorted(
            ids
        ):
            raise ValueError("review-closure-member-binding-invalid")
        refs = closure["nodeRefs"]
        if not isinstance(refs, list) or not refs:
            raise ValueError("review-closure-node-evidence-required")
        spine = self.spine()
        if any(not isinstance(ref, dict) or set(ref) != {"nodeId", "revision"} for ref in refs):
            raise ValueError("review-closure-node-reference-invalid")
        if not spine.bindings_current({ref["nodeId"]: ref["revision"] for ref in refs}):
            raise ValueError("review-closure-artifact-stale-or-absent")
        for field, table, binding in (
            ("reviewIds", "review_runs", "plan_revision"),
            ("auditIds", "audit_runs", "manifest_revision"),
        ):
            values = closure[field]
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError("review-closure-record-reference-invalid")
            for value in values:
                saved = self.connection.execute(
                    f"SELECT {binding} FROM {table} WHERE id=?", (value,)
                ).fetchone()
                if not saved or saved[0] != revision:
                    raise ValueError("review-closure-record-stale-or-absent: " + field)
        obligations = closure["obligationNodeIds"]
        if not isinstance(obligations, list) or any(
            not isinstance(value, str) for value in obligations
        ):
            raise ValueError("review-closure-obligation-reference-invalid")
        for value in obligations:
            if value not in spine.ids():
                raise ValueError("review-closure-obligation-reference-invalid")
        if status == "deferred" and not obligations:
            raise ValueError("review-closure-deferred-obligation-required")

    def resolve_findings(self, dispositions: list[dict]) -> dict:
        """Judge each named member against current evidence; never close a cluster implicitly."""
        from codex_harness.core.approvals import assess

        if not isinstance(dispositions, list) or not dispositions:
            raise ValueError("At least one finding disposition is required")
        with self._transaction():
            members = {row["id"]: row for row in self.finding_state()["members"]}
            ids = [item.get("id") for item in dispositions]
            if len(set(ids)) != len(ids):
                raise ValueError("Supply each finding member exactly once")
            for item in dispositions:
                if (
                    set(item) - {"id", "status", "resolution", "closure"}
                    or item.get("id") not in members
                    or item.get("status") not in {"resolved", "rejected", "deferred"}
                    or not isinstance(item.get("resolution"), str)
                    or not item["resolution"].strip()
                ):
                    raise ValueError("Supply known member id, disposition and nonempty resolution")
                if "closure" in item:
                    if members[item["id"]].get("stage") == "acceptance":
                        raise ValueError(
                            "Acceptance closure producer unavailable; retain pending judgment"
                        )
                    self._closure_declaration(
                        item["closure"], members[item["id"]], [members[item["id"]]], item["status"]
                    )
            judgment = assess(
                self,
                "finding-disposition",
                dict(
                    members=[row for row in finding_records(self) if row["id"] in ids],
                    proposals=dispositions,
                    reviewBinding=review_binding(self, ids),
                    currentNodes=self.list_nodes(),
                    currentEvidence=self.review_nodes_digest(),
                    revision=self.manifest_revision(),
                    question="Judge each exact member's proposed disposition against the actual current "
                    "repair/counterevidence and revision delta. meaning.memberDispositions maps exact IDs "
                    "to resolved, rejected, deferred or open. Missing/unavailable closure evidence stays "
                    "pending. A cluster ID never authorizes closure of another member.",
                ),
                subject_ids=self.spine().ids(),
            )
            judgment = self.judgment(judgment["id"])
            if not judgment["positiveCredit"]:
                return dict(dispositions=0, state="pending-judgment", judgment=judgment, owner=None)
            approved = judgment["meaning"].get("memberDispositions", {})
            reuse = self.prepare_review_reuse()
            return dict(
                dispositions=sum(
                    approved.get(item["id"]) == item["status"] for item in dispositions
                ),
                state="judged",
                judgment=judgment,
                reuseJudgments=reuse,
                convergence=self.review_convergence(prepare=True),
                owner=None,
            )

    def resolve_acceptance_finding(self, item_id, *, resolution, status="resolved") -> None:
        self.resolve_findings([dict(id=item_id, status=status, resolution=resolution)])

    def acceptance_review_guard(self, run_id: str) -> str | None:
        """Share acceptance/status admission; unknown or synthetic reviews earn no credit."""
        if self._open("clarifications"):
            return "plan-clarifications-open"

        for row in self.connection.execute("SELECT result_json FROM acceptance_reviews"):
            review = json.loads(row[0])
            if not isinstance(review, dict) or review.get("runId") != run_id:
                continue
            if not isinstance(review, dict) or review.get("isMocked") is not False:
                continue
            reviewers = review.get("reviewers")
            if (
                isinstance(reviewers, list)
                and reviewers
                and all(native_reviewer(reviewer, self) for reviewer in reviewers)
                and (
                    len({reviewer["provider"] for reviewer in reviewers}) >= 2
                    or review.get("reviewIndependence") == "single-provider"
                    and len({reviewer.get("name", "") for reviewer in reviewers} - {""}) >= 2
                )
            ):
                return None
        return "acceptance-review-required"

    def accept(self, *, by: str, statement: str) -> dict:
        from codex_harness.core import acceptance

        _nonempty(by, "by")
        _nonempty(statement, "statement")
        is_proxy = by.startswith("proxy:") and bool(by[6:].strip()) or by == "opus-user-proxy"
        if by != "user" and not is_proxy:
            raise PlanStoreError(
                "acceptance-approver-invalid", "Use user or an explicitly labelled proxy:<name>"
            )
        with self._transaction():
            if self.phase() != "accept":
                raise PlanStoreError("acceptance-phase-required", "Advance to accept first")
            self.locked_acceptance_spine()
            if any(item.get("status") == "stale" for item in self.acceptance_scenarios()):
                raise PlanStoreError(
                    "acceptance-scenarios-stale", "Review and save scenarios for the current nodes"
                )
            run = acceptance._latest_run(self)
            if run is not None and acceptance.run_subject(run) != "product":
                acceptance._refuse("acceptance-product-required")
            reason = acceptance.run_invalid_reason(self, run)
            if reason:
                acceptance._refuse(reason, run["repo"] if run else None)
            acceptance.require_c_evidence(self, run)
            from codex_harness.core.approvals import assess

            judgment = assess(
                self,
                "user-decision",
                dict(
                    subject=dict(kind="C", run=run),
                    reviewBinding=review_binding(self, run_id=run["id"]),
                    revision=self._revision(),
                    request_id="C:" + run["id"],
                    words=statement,
                    actor=by,
                    channel="caller-message",
                    question="Interpret exact owner words for this delivered candidate. "
                    "meaning.decision is approve, changes_requested, decline or unclear; ambiguity stays pending.",
                ),
            )
            if not judgment["positiveCredit"] or judgment["meaning"].get("decision") != "approve":
                pending = dict(
                    state="pending-judgment"
                    if not judgment["positiveCredit"]
                    else (
                        "changes-requested"
                        if judgment["meaning"].get("decision") == "changes_requested"
                        else "declined"
                    ),
                    judgment=judgment,
                    by=by,
                    statement=statement,
                    isProxy=bool(is_proxy),
                    runId=run["id"],
                )
                self._event("acceptance-consent-pending", pending)
                return pending
            approval = {
                "id": uuid4().hex,
                "runId": run["id"],
                "by": by,
                "statement": statement,
                "isProxy": bool(is_proxy),
                "judgmentId": judgment["id"],
                "human_identity_verified": False,
            }
            self.connection.execute(
                "INSERT INTO acceptance_approvals VALUES (?, ?)", (approval["id"], _json(approval))
            )
            self._event("accepted", approval)
            return approval

    def accept_rebind(self, repo) -> dict:
        from codex_harness.core.acceptance import rebind_acceptance

        return rebind_acceptance(self, repo)
