"""Canonical drafts writers; composed by the PlanStore transaction owner."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from codex_harness.core import intake, waves
from codex_harness.core.graph import load_plan
from codex_harness.core.spine import (
    Spine,
    compile_manifest,
)


from codex_harness.core.plan_types import PlanStoreError, _json


class PlanDrafts:
    def _draft_preimage(self, expected_spine_revision: str) -> Spine:
        spine = Spine(self._data())
        if spine.plan_revision() != expected_spine_revision:
            raise PlanStoreError(
                "plan-spine-stale",
                "Refresh status and reconcile against the current spine revision",
            )
        return spine

    def _latest_draft_event(self, kind: str) -> dict | None:
        row = self.connection.execute(
            "SELECT payload_json FROM events WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _obligation_register(spine: Spine) -> list[dict]:
        register = {key: True for kind in ("requirement", "acceptance") for key in spine.ids(kind)}
        for key in spine.ids("decision"):
            fields = spine.node(key)["fields"]
            if fields.get("schema") == "dot-wave-map-v1":
                for row in fields["obligations"]:
                    register[row["id"]] = (
                        register.get(row["id"], False) or row["requiredForFullVision"]
                    )
        return [
            dict(id=key, requiredForFullVision=value) for key, value in sorted(register.items())
        ]

    def _save_draft_nodes(
        self, before: Spine, nodes: list[dict], kind: str, provenance: dict
    ) -> dict:
        if len({node["id"] for node in nodes}) != len(nodes):
            raise PlanStoreError("plan-node-duplicate", "Draft node identities must be unique")
        merged = {key: before.node(key) for key in before.ids()}
        for node in nodes:
            old = merged.get(node["id"])
            if old and (old["kind"], old["fields"].get("schema")) != (
                node["kind"],
                node["fields"].get("schema"),
            ):
                raise PlanStoreError("plan-node-identity-conflict", node["id"])
            merged[node["id"]] = node
        after = Spine({**before.to_dict(), "nodes": list(merged.values())})
        changed = [
            node
            for node in nodes
            if node["id"] not in before.ids() or after.node(node["id"]) != before.node(node["id"])
        ]
        identities = after.dependency_revisions(node["id"] for node in nodes)
        event = {"nodeRevisions": identities, **deepcopy(provenance)}
        if changed or self._latest_draft_event(kind) != event:
            for node in changed:
                self._write_node(after, node["id"])
            self._event(kind, event)
        return {
            "nodeRevisions": identities,
            "spineRevision": after.plan_revision(),
            "state": "draft",
            "lockState": "unconfirmed",
            "approvalGranted": False,
        }

    @staticmethod
    def _draft_node(node: dict, kind: str, schema: str) -> dict:
        node = deepcopy(node)
        if not isinstance(node, dict) or node.get("kind") != kind:
            raise PlanStoreError("plan-draft-kind-invalid", f"Expected {kind} node")
        if not isinstance(node.get("fields"), dict):
            raise PlanStoreError("plan-draft-fields-invalid", "Node fields must be an object")
        if node["fields"].get("schema", schema) != schema:
            raise PlanStoreError("plan-draft-schema-invalid", schema)
        return node

    def record_intake(self, node: dict, expected_spine_revision: str, *, baseline=None) -> dict:
        """Fields are propose's request; baseline is null or an existing publication/revision pair.

        Obligations come from the complete canonical nodes/map, never the caller's delta.
        Supplied confirmation provenance is retained without authenticating approval.
        """
        with self._transaction():
            before = self._draft_preimage(expected_spine_revision)
            node = self._draft_node(node, "decision", "dot-intake-v1")
            resolved = None
            if baseline is not None:
                if not isinstance(baseline, dict) or set(baseline) != {
                    "publishedRevision",
                    "spineRevision",
                }:
                    raise PlanStoreError(
                        "plan-baseline-unresolved", "Supply the exact revision pair"
                    )
                snapshot = self._snapshot(baseline["publishedRevision"])
                source = Spine(snapshot)
                if source.plan_revision() != baseline["spineRevision"]:
                    raise PlanStoreError(
                        "plan-baseline-unresolved", "Published spine does not match"
                    )
                recipes = [
                    source.node(key)["fields"]["recipe"]
                    for key in source.ids("decision")
                    if source.node(key)["fields"].get("schema") == "dot-intake-v1"
                ]
                if len(set(recipes)) > 1:
                    raise PlanStoreError(
                        "plan-baseline-unresolved", "Published recipe is ambiguous"
                    )
                resolved = {
                    **baseline,
                    "obligationIds": [
                        row["id"]
                        for row in self._obligation_register(source)
                        if row["requiredForFullVision"]
                    ],
                    "recipe": recipes[0] if recipes else None,
                }
            node["fields"] = intake.propose(
                node["fields"], resolved, self._obligation_register(before)
            )
            result = self._save_draft_nodes(
                before, [node], "intake-recorded", {"nodeId": node["id"]}
            )
            return {
                **result,
                "intake": node["fields"],
                "routing": intake.routing(self, node),
            }

    def record_reviewer_catalog(self, catalog: dict, expected_spine_revision: str) -> dict:
        """Record a supplied observation, not discovered entitlement or provider execution."""
        with self._transaction():
            before = self._draft_preimage(expected_spine_revision)
            catalog = intake.validate_catalog(catalog)
            observations = [
                json.loads(row[0])
                for row in self.connection.execute(
                    "SELECT payload_json FROM events "
                    "WHERE kind='reviewer-catalog-recorded' ORDER BY id"
                )
            ]
            prior = next(
                (row for row in observations if row["catalog"]["id"] == catalog["id"]), None
            )
            if prior and prior["catalog"] != catalog:
                raise PlanStoreError("plan-observation-conflict", catalog["id"])
            if prior is None:
                self._event("reviewer-catalog-recorded", {"catalog": catalog})
            return {
                "catalog": catalog,
                "state": "draft",
                "approvalGranted": False,
                "spineRevision": before.plan_revision(),
                "nextAction": (
                    "Choose exact observed reviewer tuples; observation is supplied evidence"
                ),
            }

    def record_reviewer_selection(self, node: dict, expected_spine_revision: str) -> dict:
        """Record code-selected tuples; P/B and native receipts own permission/credit."""
        with self._transaction():
            before = self._draft_preimage(expected_spine_revision)
            node = self._draft_node(node, "decision", "dot-reviewer-selection-v1")
            return self._save_draft_nodes(before, [node], "reviewer-selection-recorded", {"nodeId": node["id"]})

    def record_wave_map(
        self,
        map_node: dict,
        detail_nodes: list[dict],
        expected_spine_revision: str,
        *,
        historical_measurements=None,
    ) -> dict:
        """Record a vision-owned decision map and child detail designs; history only appends."""
        with self._transaction():
            before = self._draft_preimage(expected_spine_revision)
            map_node = self._draft_node(map_node, "decision", "dot-wave-map-v1")
            details = [
                self._draft_node(node, "design", "dot-wave-detail-v1") for node in detail_nodes
            ]
            if map_node.get("parents") != before.ids("vision") or any(
                node.get("parents") != [map_node["id"]] for node in details
            ):
                raise PlanStoreError(
                    "plan-wave-owner-invalid",
                    "Map decision must belong to the vision; "
                    "detail designs must belong to that map",
                )
            prior = self._latest_draft_event("wave-map-recorded")
            if prior and prior["mapNodeId"] != map_node["id"]:
                raise PlanStoreError(
                    "plan-map-identity-conflict", "Reuse the current logical map ID"
                )
            old_required = {
                row["id"]
                for row in self._obligation_register(before)
                if row["requiredForFullVision"]
            }
            proposed = {key: before.node(key) for key in before.ids()}
            proposed.update({node["id"]: node for node in [map_node, *details]})
            candidate = Spine({**before.to_dict(), "nodes": list(proposed.values())})
            validated = waves.validate_map(
                candidate, map_node["fields"], [n["fields"] for n in details], None
            )
            # Source IDs have producer-validated sourceRefs; required map IDs must survive.
            prior_obligations = {
                row["id"]
                for key in before.ids("decision")
                if before.node(key)["fields"].get("schema") == "dot-wave-map-v1"
                for row in before.node(key)["fields"]["obligations"]
            }
            required = {
                row["id"]
                for row in validated["waveMap"]["obligations"]
                if row["requiredForFullVision"]
            }
            if (old_required & prior_obligations) - required:
                raise PlanStoreError(
                    "plan-obligation-loss", "Required map obligations must survive"
                )
            if historical_measurements is not None and not isinstance(
                historical_measurements, list
            ):
                raise PlanStoreError("plan-history-invalid", "Expected historical measurement list")
            history = [
                *(prior["historicalMeasurements"] if prior else []),
                *(historical_measurements if historical_measurements is not None else []),
            ]
            snapshot = self._wave_snapshot(before, validated, history)
            accounting = waves.accounting(snapshot, [], [], [])
            observations = {}
            normalized = {}
            for record, projected in zip(
                history, accounting["historicalMeasurements"], strict=True
            ):
                identity = record["sourceRef"]
                if identity in observations and intake.canonical_digest(
                    observations[identity]
                ) != intake.canonical_digest(record):
                    raise PlanStoreError("plan-history-observation-conflict", identity)
                observations[identity] = record
                normalized[identity] = projected
            history = list(observations.values())
            accounting["historicalMeasurements"] = list(normalized.values())
            result = self._save_draft_nodes(
                before,
                [map_node, *details],
                "wave-map-recorded",
                {
                    "mapNodeId": map_node["id"],
                    "detailNodeIds": [n["id"] for n in details],
                    "historicalMeasurements": history,
                },
            )
            return {
                **result,
                "accounting": accounting,
                "nextAction": "Read full draft with waves; publication and execution remain gated",
            }

    def wave_product_lock(self) -> dict:
        """Resolve only an original map-containing explicit plan confirmation."""
        from codex_harness.core.wave_refine import need, wave_values

        spine = Spine(self._data())
        values = wave_values(spine)
        need(values is not None, "No wave map", "wave-product-lock-unavailable")
        owner, details, _ = values
        current = waves.product_digest(spine, owner["fields"], [n["fields"] for n in details])
        publication = self._meta()["published_revision"]
        retained = self._snapshot(publication).get("waveProductLock") if publication else None
        preferred = (
            retained["sourceRef"]
            if retained
            and retained["digest"] == current
            and retained["waveMapRevision"] == spine.revision(owner["id"])
            else None
        )
        rows = list(
            self.connection.execute(
                "SELECT id, payload_json FROM events WHERE kind='phase-advanced' ORDER BY id DESC"
            )
        )
        rows.sort(key=lambda row: f"plan-event:{row['id']}" != preferred)
        for row in rows:
            event = json.loads(row["payload_json"])
            lock = event.get("waveProductLock")
            if lock and (event["from"], event["to"]) in {
                ("plan", "roundtable"),
                ("roundtable", "publish"),
            }:
                original = Spine(lock["spine"])
                original_values = wave_values(original)
                need(
                    original_values is not None
                    and lock["spineRevision"] == event["plan_revision"] == original.plan_revision()
                    and lock["statement"] == event["statement"]
                    and bool(lock["statement"].strip())
                    and lock["confirmedBy"] == event["by"],
                    "Confirmation source mismatch",
                    "wave-product-lock-unavailable",
                )
                need(
                    lock["digest"]
                    == waves.product_digest(
                        original,
                        original_values[0]["fields"],
                        [n["fields"] for n in original_values[1]],
                    ),
                    "Confirmation digest mismatch",
                    "wave-product-lock-unavailable",
                )
                need(
                    lock["digest"] == current
                    and lock["waveMapRevision"] == spine.revision(owner["id"]),
                    "Protected product changed",
                    "wave-product-lock-stale",
                )
                return {**lock, "sourceRef": f"plan-event:{row['id']}"}
        raise PlanStoreError(
            "wave-product-lock-unavailable",
            "Record actual B after review, before publication",
        )

    def wave_boundary_context(self, run_dir, through_wave_id=None, *, require_exit=True) -> dict:
        """Consume the integration owner's verdict once; no producer verification."""
        from codex_harness.core.acceptance import integrated_revision
        from codex_harness.core.dispatch_repository import _changed_claim_inputs, _claim_inputs
        from codex_harness.core.wave_refine import digest, git, need, wave_values

        spine = Spine(self._data())
        manifest = self._manifest(spine)
        scope = manifest.get("waveScope")
        need(scope is not None, "Scoped wave publication required", "wave-scope-unavailable")
        need(
            through_wave_id in (None, scope["throughWaveId"]),
            "Select the canonical current boundary",
            "wave-scope-mismatch",
        )
        publication = self._meta()["published_revision"]
        need(
            self.publication_current(manifest["planRevision"]),
            "Publish the current scope",
            "wave-publication-stale",
        )
        lock = self.wave_product_lock()
        snapshot = self._snapshot(publication)
        need(
            snapshot.get("waveProductLock") == lock
            and snapshot["manifest"].get("sourceManifestRevision", publication)
            == manifest["planRevision"],
            "Publication binding changed",
            "wave-publication-stale",
        )
        manifest = snapshot["manifest"]
        run_dir = Path(run_dir).resolve()
        run_path = run_dir / "evidence/run.json"
        need(
            run_path.is_file()
            and not run_path.is_symlink()
            and run_path.resolve().is_relative_to(run_dir),
            "Canonical run evidence required",
            "wave-integration-unavailable",
        )
        with run_path.open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        need(len(raw) <= 16 * 1024 * 1024, "Run record exceeds bound")
        run = json.loads(raw.decode("utf-8"))
        repo = (run_dir / "repo").resolve()
        need(
            run.get("schema") == "codex-harness/plan-run-v1"
            and Path(run.get("repo", "")).resolve() == repo
            and repo.is_relative_to(run_dir)
            and git(repo, "rev-parse", "--show-toplevel").replace("\\", "/").lower()
            == repo.as_posix().lower(),
            "Foreign run/repository",
            "wave-integration-mismatch",
        )
        need(
            run.get("planRevision") == publication,
            "Run belongs to another publication",
            "wave-integration-stale",
        )
        verdict = run.get("integrationValidation") or {}
        claim = verdict.get("claim") or {}
        head, tree = git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "HEAD^{tree}")
        need(
            not git(repo, "status", "--porcelain", "--untracked-files=all"),
            "Candidate must be clean",
            "wave-candidate-dirty",
        )
        need(
            verdict.get("passed") is True
            and claim.get("passed") is True
            and claim.get("bead") == "integration"
            and claim in run.get("claims", [])
            and claim.get("proofModule") == scope["boundaryIntegrationModule"],
            "Missing/failed/mismatched integration verdict",
            "wave-integration-unavailable",
        )
        need(
            head == verdict.get("integrationCommit") == claim.get("candidateCommit")
            and tree == verdict.get("candidateTree") == claim.get("candidateTree"),
            "Integration candidate changed",
            "wave-candidate-stale",
        )
        with TemporaryDirectory(prefix="wave-verdict-") as temp:
            path = Path(temp) / "manifest.json"
            path.write_text(_json(manifest), encoding="utf-8")
            plan = load_plan(path)
            need(
                not _changed_claim_inputs(claim, _claim_inputs(repo, plan, "integration", head)),
                "Integration inputs invalidated",
                "wave-integration-stale",
            )
        values = wave_values(spine)
        ids = sorted(
            {
                sid
                for w in values[0]["fields"]["waves"]
                if w["id"] in scope["waveIds"]
                for sid in w["exitScenarioIds"]
            }
        )
        scenarios = [s for s in self.acceptance_scenarios() if s["id"] in ids]
        need(
            {s["id"] for s in scenarios} == set(ids)
            and all(
                s.get("executable") is True
                and s.get("status") != "stale"
                and s.get("nodeRevisions", {}).get(s["id"]) == spine.revision(s["id"])
                for s in scenarios
            ),
            "Current executable bound cumulative scenarios required",
            "wave-exits-unavailable",
        )
        result = dict(
            publicationRevision=publication,
            productLockRevision=lock["digest"],
            productLockSource=lock["sourceRef"],
            waveMapRevision=scope["waveMapRevision"],
            nodeRevisions=spine.dependency_revisions(spine.ids()),
            throughWaveId=scope["throughWaveId"],
            waveIds=scope["waveIds"],
            scenarioIds=ids,
            scenarioRevision=digest(scenarios),
            scenarios=scenarios,
            runSourceRef=str(run_path),
            runSha256=hashlib.sha256(raw).hexdigest(),
            integrationVerdictDigest=digest(verdict),
            repo=str(repo),
            candidateCommit=head,
            candidateTree=tree,
            productBytesRevision=integrated_revision(repo),
            evidenceClass="source-test-only"
            if run.get("isMocked") is True
            else "local-integration-only",
            isMocked=run.get("isMocked"),
            didExecuteRealProvider=run.get("didExecuteRealProvider"),
        )
        if require_exit:
            exits = self._latest_draft_event("wave-cumulative-exit")
            need(
                exits is not None
                and exits.get("passed") is True
                and exits.get("binding") == result,
                "Fresh passing cumulative exits required",
                "wave-exits-stale",
            )
            result["cumulativeExitDigest"] = digest(exits)
        return result

    def wave_refinement_context(self, wave_id, run_dir) -> dict:
        from codex_harness.core.wave_refine import (
            MAX_CONTEXT_BYTES,
            digest,
            extract_interfaces,
            need,
            wave_values,
        )

        spine = Spine(self._data())
        boundary = self.wave_boundary_context(run_dir)
        owner, details, _ = wave_values(spine)
        order = owner["fields"]["order"]
        index = order.index(boundary["throughWaveId"]) + 1
        need(
            index < len(order) and order[index] == wave_id,
            "Refine the next canonical wave",
            "wave-target-invalid",
        )
        target = next(n for n in details if n["fields"]["waveId"] == wave_id)
        need(
            target["fields"]["detail"] == "sketch",
            "Target must contain genuine sketches",
            "wave-target-invalid",
        )
        producers = {k for s in target["fields"]["sketches"] for k in s["pendingProducerIds"]}
        paths = [p for key in producers for p in spine.node(key)["fields"]["ownedFiles"]]
        body = dict(
            schema="dot-wave-refinement-context-v1",
            spineRevision=spine.plan_revision(),
            targetWaveId=wave_id,
            targetDetail=target,
            productLock=self.wave_product_lock(),
            boundary=boundary,
            requiredObligations=owner["fields"]["obligations"],
            interfaces=extract_interfaces(boundary["repo"], boundary["candidateCommit"], paths),
        )
        need(
            len(_json(body).encode("utf-8")) <= MAX_CONTEXT_BYTES,
            "Refinement context exceeds size bound",
            "wave-interface-unavailable",
        )
        return {**body, "contextDigest": digest(body)}

    def record_wave_refinement(
        self, expected_spine_revision, context_digest, captured_response: bytes
    ) -> dict:
        from codex_harness.core.wave_refine import need, proposal

        with self._transaction():
            spine = self._draft_preimage(expected_spine_revision)
            saved = self._latest_draft_event("wave-refinement-prepared")
            need(
                saved is not None and saved["contextDigest"] == context_digest,
                "Prepare current context first",
                "wave-context-stale",
            )
            context = self.wave_refinement_context(
                saved["targetWaveId"], Path(saved["boundary"]["runSourceRef"]).parent.parent
            )
            need(context == saved, "Context sources changed", "wave-context-stale")
            result = proposal(spine, context, captured_response)
            self._event("wave-refinement-recorded", result)
            return result

    def prepare_wave_refinement(self, wave_id, run_dir) -> dict:
        with self._transaction():
            context = self.wave_refinement_context(wave_id, run_dir)
            self._event("wave-refinement-prepared", context)
            return context

    def apply_wave_refinement(self, proposal_id, expected_spine_revision, *, wave_id=None) -> dict:
        from codex_harness.core.wave_refine import digest, need, wave_values

        with self._transaction():
            records = [
                json.loads(row[0])
                for row in self.connection.execute(
                    "SELECT payload_json FROM events WHERE kind='wave-refinement-recorded'"
                )
            ]
            matches = [r for r in records if r["proposalId"] == proposal_id]
            need(
                bool(matches) and all(r == matches[0] for r in matches),
                "Unknown/conflicting proposal",
                "wave-proposal-unavailable",
            )
            result = matches[0]
            need(
                wave_id in (None, result["context"]["targetWaveId"]),
                "Proposal target mismatch",
                "wave-target-invalid",
            )
            need(
                digest({k: v for k, v in result.items() if k != "proposalId"}) == proposal_id,
                "Proposal content changed",
            )
            applied = [
                json.loads(row[0])
                for row in self.connection.execute(
                    "SELECT payload_json FROM events WHERE kind='wave-refinement-applied'"
                )
            ]
            prior = next((r for r in applied if r["proposalId"] == proposal_id), None)
            if prior:
                need(
                    expected_spine_revision == result["context"]["spineRevision"]
                    and Spine(self._data()).plan_revision() == prior["spineRevision"],
                    "Applied draft changed",
                    "wave-context-stale",
                )
                return prior
            before = self._draft_preimage(expected_spine_revision)
            need(
                result["state"] == "complete" and result["proposedDetail"] is not None,
                "Whole target must resolve before apply",
                "wave-proposal-unapplied",
            )
            saved = result["context"]
            context = self.wave_refinement_context(
                saved["targetWaveId"], Path(saved["boundary"]["runSourceRef"]).parent.parent
            )
            need(context == saved, "Context sources changed", "wave-context-stale")
            nodes = [*result["proposedNodes"], result["proposedDetail"]]
            replaced = {n["id"] for n in nodes}
            after = Spine(
                {
                    **before.to_dict(),
                    "nodes": [before.node(k) for k in before.ids() if k not in replaced] + nodes,
                }
            )
            owner, details, _ = wave_values(after)
            need(
                waves.product_digest(after, owner["fields"], [n["fields"] for n in details])
                == context["productLock"]["digest"],
                "Protected product contradiction",
                "wave-proposal-unapplied",
            )
            self._check_coverage(after, "bead")
            self._check_coverage(after, "test")
            self._manifest(after)
            for node in nodes:
                self._write_node(after, node["id"])
            receipt = dict(
                proposalId=proposal_id,
                spineRevision=after.plan_revision(),
                editedNodeIds=sorted(replaced),
                impact=result["impact"],
                state="draft",
                readiness="not-run",
                review="required",
                publication="required",
                completedRealDelta=0,
            )
            self._event("wave-refinement-applied", receipt)
            return receipt

    @staticmethod
    def _wave_snapshot(spine: Spine, validated: dict, history: list) -> dict:
        return {
            "publicationRevision": None,
            "productLockRevision": None,
            "waveMap": validated["waveMap"],
            "waveDetails": validated["waveDetails"],
            "nodeRevisions": spine.dependency_revisions(spine.ids()),
            "selectedWaveIds": validated["waveMap"]["order"][:1],
            "historicalMeasurements": history,
            "beadDependencies": {
                key: spine.node(key)["fields"]["dependsOn"] for key in spine.ids("bead")
            },
        }

    def outcome_waves(self) -> dict:
        """Read a consistent draft projection. No runtime evidence is bound in this slice."""
        self.connection.execute("BEGIN")
        try:
            data = self._data()
            result = {
                "state": "unavailable",
                "lockState": "unconfirmed",
                "approvalGranted": False,
                "reason": "Outcome map not recorded",
                "intake": None,
                "reviewerSelection": None,
            }
            if not data["nodes"]:
                return result
            spine = Spine(data)
            nodes = {key: spine.node(key) for key in spine.ids()}
            for kind, field in (
                ("intake-recorded", "intake"),
                ("reviewer-selection-recorded", "reviewerSelection"),
            ):
                event = self._latest_draft_event(kind)
                if event:
                    node = nodes.get(event["nodeId"])
                    current = node is not None and spine.bindings_current(event["nodeRevisions"])
                    item = {"node": node, "state": "draft" if current else "stale"}
                    result[field] = item
            event = self._latest_draft_event("wave-map-recorded")
            if event is None:
                return result
            map_node = nodes.get(event["mapNodeId"])
            details = [nodes.get(key) for key in event["detailNodeIds"]]
            result.update(
                mapNode=map_node,
                detailNodes=details,
                nodes=nodes,
                nodeRevisions=spine.dependency_revisions(spine.ids()),
            )
            if map_node is None or any(node is None for node in details):
                return {**result, "state": "stale", "reason": "Recorded map/detail node missing"}
            try:
                validated = waves.validate_map(
                    spine, map_node["fields"], [n["fields"] for n in details], None
                )
                accounting = waves.accounting(
                    self._wave_snapshot(spine, validated, event["historicalMeasurements"]),
                    [],
                    [],
                    [],
                )
            except ValueError as error:
                return {**result, "state": "stale", "reason": str(error)}
            changed = not spine.bindings_current(event["nodeRevisions"])
            applied = self._latest_draft_event("wave-refinement-applied")
            if applied and applied["spineRevision"] == spine.plan_revision():
                changed = False
            try:
                lock = self.wave_product_lock()
            except ValueError:
                lock = None
            try:
                scope = compile_manifest(spine).get("waveScope")
            except ValueError:
                scope = None
            return {
                **result,
                **validated,
                "state": "stale" if changed else "draft",
                "reason": "Map/detail changed outside its validated recording"
                if changed
                else "Draft only; current publication, lock and runtime evidence unavailable",
                "accounting": accounting,
                "waveProductLock": lock,
                "lockState": "current" if lock else validated["lockState"],
                "executionScope": scope,
            }
        finally:
            self.connection.rollback()
