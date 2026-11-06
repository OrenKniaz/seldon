"""Transactional project planning authority; files are published projections only."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile, TemporaryDirectory
from uuid import uuid4

from codex_harness.core import intake, waves
from codex_harness.core.graph import load_plan
from codex_harness.core.planner_schema import Judgment, judgment_example, judgment_response
from codex_harness.core.reviewer_evidence import finding_records, native_reviewer, review_binding
from codex_harness.core.spine import (
    SCHEMA,
    Spine,
    SpineError,
    compile_manifest,
    impact,
    load_spine,
    requirement_file_sources,
    save_spine,
)
from codex_harness.core.spine_render import render_markdown
from codex_harness.core.test_audit import ORACLE_LABEL, PROSPECTIVE_LABEL, PROSPECTIVE_WARNING


PHASES = (
    "align",
    "expand",
    "vision-lock",
    "features",
    "requirements",
    "tech-stack",
    "plan",
    "roundtable",
    "publish",
    "execute",
    "accept",
)


class PlanStoreError(SpineError):
    """A stable refusal code from the planning store."""


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _nonempty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PlanStoreError("plan-input-invalid", f"{label} must be a nonempty string")


class PlanStore:
    """One SQLite authority per existing workspace, with revision-bound gates.

    Commands reserve writes before reading and commit before returning.
    Reads preserve their caller's transaction; new stores begin without nodes.
    """

    def __init__(self, workspace: str | Path, name: str | None = None) -> None:
        workspace = Path(workspace).resolve()
        if not workspace.is_dir():
            raise PlanStoreError("plan-store-workspace-missing", str(workspace))
        if name is not None:
            _nonempty(name, "name")
        self.workspace = workspace
        self.path = workspace / ".codex-harness" / "plan.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        with self._transaction():
            for sql in (
                "CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                "title TEXT NOT NULL, text TEXT NOT NULL, fields_json TEXT NOT NULL, "
                "parents_json TEXT NOT NULL, revision TEXT NOT NULL, updated_at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS meta (name TEXT NOT NULL, phase TEXT NOT NULL, "
                "vision_locked_revision TEXT, published_revision TEXT)",
                "CREATE TABLE IF NOT EXISTS clarifications (id TEXT PRIMARY KEY, "
                "question TEXT NOT NULL, answer TEXT, status TEXT NOT NULL "
                "CHECK(status IN ('open','answered','waived')), node_ids_json TEXT NOT NULL, "
                "asked_at TEXT NOT NULL, answered_at TEXT)",
                "CREATE TABLE IF NOT EXISTS assumptions (id TEXT PRIMARY KEY, text TEXT NOT NULL, "
                "status TEXT NOT NULL CHECK(status IN ('open','confirmed','rejected')), "
                "node_ids_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, gate TEXT NOT NULL, "
                'plan_revision TEXT NOT NULL, "by" TEXT NOT NULL, statement TEXT NOT NULL, '
                "at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS revisions (plan_revision TEXT PRIMARY KEY, "
                "published_at TEXT NOT NULL, snapshot_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "kind TEXT NOT NULL, at TEXT NOT NULL, payload_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS audit_runs (id TEXT PRIMARY KEY, "
                "manifest_revision TEXT NOT NULL, passed INTEGER NOT NULL, "
                "result_json TEXT NOT NULL, at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS audit_attempts (id TEXT PRIMARY KEY, "
                "attempt_key TEXT UNIQUE NOT NULL, owner TEXT NOT NULL, state TEXT NOT NULL, "
                "manifest_revision TEXT NOT NULL, input_digest TEXT NOT NULL, "
                "inputs_json TEXT NOT NULL, result_json TEXT, audit_id TEXT, error TEXT, "
                "did_run_validation INTEGER, engine_identity TEXT)",
                "CREATE UNIQUE INDEX IF NOT EXISTS audit_one_inflight ON "
                "audit_attempts ((1)) WHERE state IN ('queued','running')",
                "CREATE TABLE IF NOT EXISTS readiness_reviews (id TEXT PRIMARY KEY, "
                "batch_id TEXT NOT NULL, manifest_revision TEXT NOT NULL, "
                "bead_id TEXT NOT NULL, packet_hash TEXT NOT NULL, "
                "result_json TEXT NOT NULL, at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS acceptance_scenarios (id TEXT PRIMARY KEY, "
                "scenario_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS acceptance_attempts (id TEXT PRIMARY KEY, "
                "attempt_key TEXT UNIQUE NOT NULL, owner TEXT NOT NULL, state TEXT NOT NULL, "
                "inputs_json TEXT NOT NULL, progress_json TEXT NOT NULL, result_json TEXT, "
                "error TEXT, started_at TEXT, deadline_at TEXT, superseded_run_id TEXT)",
                "CREATE UNIQUE INDEX IF NOT EXISTS acceptance_one_inflight ON "
                "acceptance_attempts ((1)) WHERE state IN "
                "('queued','running','unknown-interrupted')",
                *[
                    f"CREATE TABLE IF NOT EXISTS acceptance_{table} (id TEXT PRIMARY KEY, "
                    "result_json TEXT NOT NULL)"
                    for table in ("runs", "reviews", "findings", "approvals")
                ],
                "CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events "
                "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END",
                "CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events "
                "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END",
            ):
                self.connection.execute(sql)
            for table, column, declaration in (
                ("meta", "vision_lock_stale", "INTEGER NOT NULL DEFAULT 0"),
                ("meta", "locked_vision_json", "TEXT"),
                ("assumptions", "rationale", "TEXT"),
                ("assumptions", "resolved_by", "TEXT"),
                ("audit_attempts", "engine_identity", "TEXT"),
            ):
                columns = {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    self.connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
                    )
            if self.connection.execute("SELECT 1 FROM meta").fetchone() is None:
                self.connection.execute(
                    "INSERT INTO meta (name, phase) VALUES (?, 'align')",
                    (name if name is not None else workspace.name,),
                )
                self._event("opened", {"name": self._meta()["name"]})

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> PlanStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def _transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            before = self._data() if self.connection.execute("PRAGMA table_info(nodes)").fetchone() else {"nodes": []}
            yield
            after = self._data()
            if before["nodes"] and before != after:
                from codex_harness.core.spine import stale_evidence
                current = Spine(after)
                for node in after["nodes"]:
                    if node["kind"] == "vision" and node not in before["nodes"]:
                        self._classify_vision_edit(node)
                normalized = stale_evidence(Spine(before), current)
                submitted = {node["id"]: node for node in after["nodes"]}
                changed = [key for key in normalized.ids() if normalized.node(key) != submitted[key]]
                for node_id in changed:
                    self._write_node(normalized, node_id)
                if changed:
                    self._event("trace-evidence-staled", {"ids": changed, "plan_revision": normalized.plan_revision()})
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _event(self, kind: str, payload: dict) -> None:
        self.connection.execute(
            "INSERT INTO events (kind, at, payload_json) VALUES (?, ?, ?)",
            (kind, _now(), _json(payload)),
        )

    def _meta(self) -> sqlite3.Row:
        return self.connection.execute("SELECT * FROM meta").fetchone()

    def _data(self) -> dict:
        rows = self.connection.execute(
            "SELECT meta.name, nodes.* FROM meta LEFT JOIN nodes ON 1 ORDER BY nodes.id"
        ).fetchall()
        nodes = [
            {
                "id": row["id"],
                "kind": row["kind"],
                "title": row["title"],
                "text": row["text"],
                "fields": json.loads(row["fields_json"]),
                "parents": json.loads(row["parents_json"]),
            }
            for row in rows
            if row["id"] is not None
        ]
        return {"schema": SCHEMA, "name": rows[0]["name"], "nodes": nodes}

    def spine(self) -> Spine:
        return Spine(self._data())

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

            def refs(ids):
                return [dict(id=key, revision=spine.revision(key), node=spine.node(key))
                        for key in sorted(set(ids))]
            subjects, evidence = refs(subject_ids), refs(evidence_ids or [])
            sources = []
            for key in sorted(set(source_event_ids or [])):
                row = self.connection.execute("SELECT * FROM events WHERE id=?", (key,)).fetchone()
                if row is None:
                    raise PlanStoreError("judgment-source-missing", "Supply an existing source event ID")
                sources.append(dict(id=key, kind=row["kind"], at=row["at"],
                                    payload=json.loads(row["payload_json"])))
            candidates = {ref["node"]["fields"].get("evidence", {}).get("candidate")
                          for ref in evidence} - {None}
            if candidate is None and len(candidates) == 1:
                candidate = next(iter(candidates))
            if candidates and candidate not in candidates:
                raise PlanStoreError("judgment-candidate-unbound", "Select a candidate from the captured evidence")
            record: Judgment = dict(
                id=uuid4().hex,
                question=question,
                subjectRefs=subjects,
                evidenceRefs=evidence,
                sourceRefs=sources,
                candidate=candidate,
            )
            record["binding"] = hashlib.sha256(_json(record).encode("utf-8")).hexdigest()
            record.update(status="pending-judgment", verdict=None, rationale=None, meaning={},
                gaps=[], fixesIssued=0, disposition="judge", owner=None, source=None,
                sourceVerified=False, error=None, **judgment_example(record))
            self._event("judgment-recorded", record)
        return record

    def judgment(self, judgment_id: str, *, candidate=None) -> dict:
        """Credit requires current bindings, including explicit review/run facts for C."""
        record = self._judgment_record(judgment_id)
        snapshot = getattr(self, "_review_read_snapshot", None)
        if snapshot is not None and "judgmentSpine" not in snapshot:
            snapshot["judgmentSpine"] = self.spine()
        spine = snapshot["judgmentSpine"] if snapshot is not None else self.spine()
        current = not record.get("supersededBy") and all(ref["id"] in spine.ids() and spine.revision(ref["id"]) == ref["revision"]
                      for ref in record["subjectRefs"] + record["evidenceRefs"])
        if current:
            current = all(binding is not None
                and binding == review_binding(self, binding["memberIds"], convergence=binding["convergence"], run_id=binding["runId"])
                and ("acceptanceRun" not in binding or binding["acceptanceRun"] is not None and binding["acceptanceRun"]["id"] == binding["runId"])
                and (facts.get("subject", {}).get("kind") != "C" or binding["runId"] is not None and binding["runId"] == facts["subject"].get("run", {}).get("id"))
                for facts in (ref["payload"].get("facts", {}) for ref in record["sourceRefs"])
                if (binding := facts.get("reviewBinding")) is not None or facts.get("subject", {}).get("kind") == "C")
        candidate_current = record["candidate"] is None or candidate == record["candidate"]
        reason = record["error"] if record.get("supersededBy") else None if current and candidate_current else (
            "Refresh changed subject/evidence bindings" if not current else "Supply the current bound candidate")
        judged = current and candidate_current and record["status"] == "judged"
        return dict(record, status="judged" if judged else "pending-judgment",
                    current=current and candidate_current, positiveCredit=judged and record["verdict"] == "positive",
                    concreteGap=judged and record["verdict"] == "negative", refreshReason=reason)

    def submit_judgment(self, *, judgment_id, response, source, review_context=None) -> dict:
        """An initial response gets exactly two exact fixes; every return releases ownership."""
        _nonempty(source, "source")
        with self._transaction():
            record = self._judgment_record(judgment_id)
            if record["disposition"] in {"steer", "complete"}:
                raise PlanStoreError("judgment-attempt-closed", "Read the retained judgment; prepare a fresh request if steering requires one")
            if not self.judgment(judgment_id, candidate=record["candidate"])["current"]:
                record.update(disposition="steer", error="Bindings changed; prepare a fresh judgment")
            else:
                try:
                    parsed = judgment_response(response, record)
                except (ValueError, TypeError) as error:
                    record.update(error=str(error), disposition="exact-fix" if record["fixesIssued"] < 2 else "steer")
                    if record["disposition"] == "exact-fix":
                        record["fixesIssued"] += 1
                else:
                    record.update(parsed, error=None)
                    decided = parsed["verdict"] in {"positive", "negative"}
                    record.update(status="judged" if decided else "pending-judgment",
                                  disposition="complete" if decided else "judge")
            record.update(source=source, sourceVerified=False, owner=None)
            self._event("judgment-response", dict(id=judgment_id, binding=record["binding"],
                        response=response, source=source, sourceVerified=False))
            self._event("judgment-recorded", record)
            facts = next((r["payload"]["facts"] for r in record["sourceRefs"] if "facts" in r["payload"]), {})
            if "reviewBinding" in facts and record["error"] is None and (record["question"] == "plan-review" and facts["reviewBinding"]["runId"] is None
                    or record["question"] != "plan-review" and record["verdict"] == "negative"):
                from codex_harness.core.roundtable import review_members
                reviewer = facts.get("reviewer", facts.get("review", {}))
                for item in review_members(self, record if record["question"] == "plan-review" else dict(record, meaning={}), facts):
                    self._event("finding-recorded", dict(item, **(review_context or {}), id=uuid4().hex,
                        reviewer=reviewer.get("name", reviewer.get("reviewer", "judge")), provider=reviewer.get("provider", "judge"),
                        plan_revision=facts.get("manifestRevision", facts.get("revision")), round_id=facts.get("roundId", facts.get("invocationId")),
                        created_at=_now(), stage="acceptance" if facts["reviewBinding"]["runId"] else "roundtable", runId=facts["reviewBinding"]["runId"]))
            if record["question"] == "vision-intent":
                self._classify_vision_edit(self.spine().node(self.spine().ids("vision")[0]))
        result = self.judgment(judgment_id)
        if record["question"] == "review-reuse" and result["positiveCredit"]:
            result["convergence"] = self.review_convergence(prepare=True)
        return result

    def list_nodes(self, kind: str | None = None) -> list[dict]:
        """Read the current nodes without writing planning truth."""
        return [node for node in self._data()["nodes"] if kind is None or node["kind"] == kind]

    def show_node(self, node_id, *, body=False, test=None, include_source=False) -> dict:
        from codex_harness.core.planner_schema import inspect_node

        return inspect_node(self, node_id, body=body, test=test, include_source=include_source)

    def _revision(self) -> str:
        data = self._data()
        if not data["nodes"]:
            return hashlib.sha256(b"").hexdigest()
        return Spine(data).plan_revision()

    def _write_node(self, spine: Spine, node_id: str) -> None:
        node = spine.node(node_id)
        if node["kind"] == "vision":
            from codex_harness.core.planner_vision import design_references

            design_references(node["fields"], self.workspace)
            if spine.semantic:
                from codex_harness.core.spine import semantic_spec

                supplied = set(node["fields"]) & {"scenarios", "targets"}
                if supplied:
                    semantic_spec(spine, draft=True)
            prior = self.list_nodes("vision")
            if prior and prior[0]["fields"].get("planMode", "legacy") != node["fields"].get(
                "planMode", "legacy"
            ):
                raise ValueError("planMode is immutable; choose semantic-v1 at plan creation")
        self.connection.execute(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "kind=excluded.kind, title=excluded.title, text=excluded.text, "
            "fields_json=excluded.fields_json, parents_json=excluded.parents_json, "
            "revision=excluded.revision, updated_at=excluded.updated_at",
            (
                node_id,
                node["kind"],
                node["title"],
                node["text"],
                _json(node["fields"]),
                _json(node["parents"]),
                spine.revision(node_id),
                _now(),
            ),
        )

    def _classify_vision_edit(self, node: dict) -> None:
        if node["kind"] != "vision" or not self._meta()["vision_locked_revision"]:
            return
        from codex_harness.core.approvals import intent, intent_assessment
        locked = json.loads(self._meta()["locked_vision_json"])
        if intent(locked) == intent(node):
            self.connection.execute("UPDATE meta SET vision_lock_stale=0")
            return
        judgment = intent_assessment(self, node)
        preserved = judgment["positiveCredit"] and judgment["meaning"].get("impact") == "preserved"
        self.connection.execute("UPDATE meta SET vision_lock_stale=?", (int(not preserved),))
        self._event("vision-intent-assessed", dict(id=node["id"], judgmentId=judgment["id"],
                    state="preserved" if preserved else "pending-judgment"))

    def upsert_node(self, node: dict, *, reopen_vision_lock: bool = False) -> None:
        with self._transaction():
            data = self._data()
            node_id = node.get("id") if isinstance(node, dict) else None
            data["nodes"] = [item for item in data["nodes"] if item["id"] != node_id]
            data["nodes"].append(node)
            spine = Spine(data)
            self._write_node(spine, node_id)
            self._event("node-upserted", {"id": node_id, "plan_revision": spine.plan_revision(), "reopenRequested": reopen_vision_lock})

    def revise_test(
        self,
        *,
        node_id: str,
        path: str,
        expected_manifest_revision: str,
        expected_file_sha256: str,
        source: str,
        reason: str,
    ) -> dict:
        """Replace one linked Python proof; current file bytes own manifest/evidence freshness.

        The store reservation serializes supported writers. Interruption after replacement
        may leave changed bytes without a completed event: inspect before retrying; old
        evidence still loses credit through the existing content-bound manifest.
        """
        _nonempty(reason, "reason")
        if not isinstance(source, str) or not source.strip():
            raise PlanStoreError("plan-test-source-invalid", "Provide complete Python source")
        content = source.encode("utf-8")
        if len(content) > 256 * 1024:
            raise PlanStoreError("plan-test-source-too-large", "256 KiB source budget")
        ast.parse(source, filename=path)
        relative = PurePosixPath(path)
        if (
            relative.is_absolute()
            or relative.as_posix() != path
            or ".." in relative.parts
            or "\\" in path
            or ":" in path
            or relative.suffix != ".py"
        ):
            raise PlanStoreError("plan-test-path-invalid", "Use a template-relative Python path")
        template = self.workspace / "template"
        target = template / relative

        def checked_bytes():
            # Reject every reparse component, including Windows junctions, before reading.
            for item in (template, *target.relative_to(template).parents):
                candidate = item if item == template else template / item
                info = candidate.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise PlanStoreError("plan-test-path-invalid", "Reparse paths are refused")
            info = target.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
                or target.resolve() != target
            ):
                raise PlanStoreError("plan-test-path-invalid", "Use an existing regular proof file")
            raw = target.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected_file_sha256:
                raise PlanStoreError("plan-test-preimage-stale", "Read the current linked source")
            return raw

        with self._transaction():
            before = checked_bytes()
            spine = Spine(self._data())
            from codex_harness.core.graph import test_dir

            manifest = compile_manifest(spine, template)
            test_root = PurePosixPath(test_dir(manifest))
            if not relative.is_relative_to(test_root) or spine.node(node_id)["kind"] != "test":
                raise PlanStoreError("plan-test-path-invalid", "Only linked tests under test_dir")
            if manifest["planRevision"] != expected_manifest_revision:
                raise PlanStoreError("plan-test-preimage-stale", "Refresh the current manifest")
            if path not in {item["path"] for item in self.show_node(node_id)["testFiles"]}:
                raise PlanStoreError(
                    "plan-test-path-invalid", "Path is not linked to this test node"
                )
            if before == content:
                raise PlanStoreError("plan-test-unchanged", "Replacement is byte-identical")
            staged = None
            try:
                with NamedTemporaryFile(
                    dir=target.parent, prefix=".proof-", delete=False
                ) as stream:
                    staged = Path(stream.name)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                checked_bytes()
                os.replace(staged, target)
            finally:
                if staged is not None:
                    staged.unlink(missing_ok=True)
            result = {
                "nodeId": node_id,
                "path": path,
                "reason": reason,
                "beforeSha256": expected_file_sha256,
                "afterSha256": hashlib.sha256(content).hexdigest(),
                "previousManifestRevision": expected_manifest_revision,
                "manifestRevision": compile_manifest(spine, template)["planRevision"],
                "provenance": "caller-authored-protected-test",
                "nextAction": "harness_compile, then current audit and authorized review",
            }
            self._event("protected-test-revised", result)
        return result

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
        identities = {node["id"]: after.revision(node["id"]) for node in nodes}
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

    def record_reviewer_selection(
        self, node: dict, expected_spine_revision: str, *, author_sessions=None
    ) -> dict:
        """Optional author_sessions is the producer shape, retained in the selection event."""
        with self._transaction():
            before = self._draft_preimage(expected_spine_revision)
            node = self._draft_node(node, "decision", "dot-reviewer-selection-v1")
            observation = self._latest_draft_event("reviewer-catalog-recorded")
            sessions = (
                author_sessions
                if author_sessions is not None
                else {"implementationSessionIds": [], "reviewerSessionIds": {}}
            )
            assurance = intake.validate_selection(
                observation["catalog"] if observation else None, node["fields"], sessions
            )
            node["fields"] = assurance["selection"]
            result = self._save_draft_nodes(
                before,
                [node],
                "reviewer-selection-recorded",
                {"nodeId": node["id"], "authorSessions": sessions},
            )
            return {
                **result,
                **assurance,
                "nextAction": "Resolve unmet assurance before existing review gates",
            }

    def confirm_reviewer_selection(self, node_id, expected_spine_revision, **response):
        raise PlanStoreError(
            "plan-b-consent-required", "Reviewer choices are drafts; approve the complete B subject"
        )

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
            if lock and event["from"] == "plan" and event["to"] == "roundtable":
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
            "Record actual displayed-plan confirmation at plan -> roundtable",
        )

    def wave_boundary_context(self, run_dir, through_wave_id=None, *, require_exit=True) -> dict:
        """Consume the integration owner's verdict once; no producer verification."""
        from codex_harness.core.acceptance import integrated_revision
        from codex_harness.core.dispatch import _changed_claim_inputs, _claim_inputs
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
            snapshot.get("waveProductLock") == lock and snapshot["manifest"] == manifest,
            "Publication binding changed",
            "wave-publication-stale",
        )
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
            nodeRevisions={k: spine.revision(k) for k in spine.ids()},
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
            "nodeRevisions": {key: spine.revision(key) for key in spine.ids()},
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
                    current = node is not None and event["nodeRevisions"][
                        node["id"]
                    ] == spine.revision(node["id"])
                    item = {"node": node, "state": "draft" if current else "stale"}
                    if current and field == "reviewerSelection":
                        catalog = self._latest_draft_event("reviewer-catalog-recorded")
                        try:
                            item.update(
                                intake.validate_selection(
                                    catalog["catalog"] if catalog else None,
                                    node["fields"],
                                    event["authorSessions"],
                                )
                            )
                        except ValueError as error:
                            item.update(state="stale", unmetRequirements=[str(error)])
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
                nodeRevisions={key: spine.revision(key) for key in spine.ids()},
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
            changed = any(
                key not in nodes or spine.revision(key) != revision
                for key, revision in event["nodeRevisions"].items()
            )
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

    def remove_node(self, node_id: str) -> None:
        with self._transaction():
            data = self._data()
            if not any(node["id"] == node_id for node in data["nodes"]):
                raise PlanStoreError("plan-node-missing", node_id)
            if any(node_id in node["parents"] for node in data["nodes"]):
                raise PlanStoreError("plan-node-has-children", node_id)
            data["nodes"] = [node for node in data["nodes"] if node["id"] != node_id]
            spine = Spine(data)  # Also protects dependency and lesson references.
            self.connection.execute("DELETE FROM nodes WHERE id=?", (node_id,))
            self._event("node-removed", {"id": node_id, "plan_revision": spine.plan_revision()})

    def import_spine(self, path: str | Path) -> None:
        spine = load_spine(path)
        with self._transaction():
            prior = self.list_nodes("vision")
            if prior and Spine(self._data()).semantic != spine.semantic:
                raise ValueError("planMode is immutable; no conversion of existing plans")
            self.connection.execute("DELETE FROM nodes")
            self.connection.execute("UPDATE meta SET name=?", (spine.name,))
            for node_id in spine.ids():
                self._write_node(spine, node_id)
            self._event("spine-imported", {"plan_revision": spine.plan_revision()})

    def export_spine(self, path: str | Path) -> None:
        save_spine(self.spine(), path)

    def _node_ids(self, node_ids) -> str:
        ids = list(node_ids)
        known = {node["id"] for node in self._data()["nodes"]}
        if any(not isinstance(item, str) or item not in known for item in ids):
            raise PlanStoreError("plan-node-missing", "Unknown clarification/assumption node")
        return _json(ids)

    def ask(self, question: str, node_ids=()) -> str:
        _nonempty(question, "question")
        item_id = uuid4().hex
        with self._transaction():
            self.connection.execute(
                "INSERT INTO clarifications VALUES (?, ?, NULL, 'open', ?, ?, NULL)",
                (item_id, question, self._node_ids(node_ids), _now()),
            )
            self._event("clarification-asked", {"id": item_id, "question": question})
        return item_id

    def _resolve_question(self, item_id: str, text: str, status: str) -> None:
        _nonempty(text, "answer/reason")
        with self._transaction():
            row = self.connection.execute(
                "SELECT status FROM clarifications WHERE id=?", (item_id,)
            ).fetchone()
            if row is None:
                raise PlanStoreError("plan-clarification-missing", item_id)
            if row["status"] != "open":
                raise PlanStoreError("plan-clarification-closed", item_id)
            self.connection.execute(
                "UPDATE clarifications SET answer=?, status=?, answered_at=? WHERE id=?",
                (text, status, _now(), item_id),
            )
            self._event(f"clarification-{status}", {"id": item_id, "answer": text})

    def answer(self, item_id: str, answer: str) -> None:
        self._resolve_question(item_id, answer, "answered")

    def waive(self, item_id: str, reason: str) -> None:
        self._resolve_question(item_id, reason, "waived")

    def add_assumption(self, text: str, node_ids=()) -> str:
        _nonempty(text, "assumption")
        item_id = uuid4().hex
        with self._transaction():
            self.connection.execute(
                "INSERT INTO assumptions (id, text, status, node_ids_json) "
                "VALUES (?, ?, 'open', ?)",
                (item_id, text, self._node_ids(node_ids)),
            )
            self._event("assumption-added", {"id": item_id, "text": text})
        return item_id

    def _resolve_assumption(self, item_id: str, status: str, *, rationale: str, by: str) -> None:
        _nonempty(rationale, "rationale")
        _nonempty(by, "by")
        if by not in ("user", "planner") and not (by.startswith("proxy:") and by[6:].strip()):
            raise PlanStoreError("plan-input-invalid", "by must be user, planner or proxy:<name>")
        with self._transaction():
            row = self.connection.execute(
                "SELECT status FROM assumptions WHERE id=?", (item_id,)
            ).fetchone()
            if row is None:
                raise PlanStoreError("plan-assumption-missing", item_id)
            if row["status"] != "open":
                raise PlanStoreError("plan-assumption-closed", item_id)
            self.connection.execute(
                "UPDATE assumptions SET status=?, rationale=?, resolved_by=? WHERE id=?",
                (status, rationale, by, item_id),
            )
            self._event(f"assumption-{status}", {"id": item_id, "rationale": rationale, "by": by})

    def confirm_assumption(self, item_id: str, *, rationale: str, by: str = "planner") -> None:
        self._resolve_assumption(item_id, "confirmed", rationale=rationale, by=by)

    def reject_assumption(self, item_id: str, *, rationale: str, by: str = "planner") -> None:
        self._resolve_assumption(item_id, "rejected", rationale=rationale, by=by)

    def phase(self) -> str:
        return self._meta()["phase"]

    def approve(self, gate: str, *, by: str, statement: str) -> str:
        raise PlanStoreError(
            "plan-confirmation-origin-unavailable", "Display and answer the current A/B subject"
        )

    def _current_approval(self, kind):
        from codex_harness.core.approvals import require_approval

        if not self.connection.execute("SELECT 1 FROM nodes WHERE kind='vision'").fetchone():
            return None
        try:
            record = require_approval(self, kind)
        except PlanStoreError as error:
            if error.code not in {"plan-a-consent-required", "plan-b-consent-required"}:
                raise
            return None
        return dict(
            id=record["id"],
            by=record["channel"],
            statement=record["words"],
            plan_revision=self._revision(),
        )

    def _check_approval(self, gate: str, revision: str) -> None:
        from codex_harness.core.approvals import require_approval

        require_approval(self, "A")

    def compile_template(self, template: Path) -> dict:
        """Materialize the canonical spine before binding protected file contents."""
        template = Path(template)
        if not template.is_dir():
            raise PlanStoreError("spine-template-missing", str(template))
        spine = self.spine()
        from codex_harness.core.planner_vision import design_references
        from codex_harness.core.semantic_schema import path as reference_path

        fields = spine.node(spine.ids("vision")[0])["fields"]
        refs = design_references(fields, self.workspace)
        design_references(fields, template, allow_missing=True)
        generated = render_markdown(spine)
        for ref in refs:
            if ref["path"] in generated:
                raise ValueError(
                    f"Design reference conflicts with planning document: {ref['path']}"
                )
            destination = reference_path(template, ref["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((self.workspace / ref["path"]).read_bytes())
        for name, text in generated.items():
            path = template / name
            if path.is_symlink():
                raise PlanStoreError("spine-requirement-path-invalid", str(path))
            path.write_text(text, encoding="utf-8", newline="\n")
        manifest = compile_manifest(spine, template)
        self.check_requirement_files(manifest, template)
        with self._transaction():
            self._event("manifest-compiled", {"manifest": manifest, "spine": spine.to_dict()})
        return manifest

    @staticmethod
    def check_requirement_files(manifest: dict, template: Path) -> None:
        missing = [name for name in manifest["requirementFiles"] if not (template / name).exists()]
        if missing:
            raise PlanStoreError(
                "plan-requirement-file-missing",
                f"Missing requirement files in {template}: {', '.join(missing)}. "
                "Next: restore listed requirement files, then compile --template template, "
                "audit --oracle oracle, and quality",
            )

    def _manifest(self, spine: Spine) -> dict:
        template = self.workspace / "template"
        manifest = compile_manifest(spine, template if template.is_dir() else None)
        if template.is_dir():
            self.check_requirement_files(manifest, template)
        with TemporaryDirectory(prefix="harness-plan-") as temp:
            path = Path(temp) / "manifest.json"
            path.write_text(_json(manifest), encoding="utf-8")
            load_plan(path)
        return manifest

    def manifest_revision(self) -> str | None:
        snapshot = getattr(self, "_review_read_snapshot", None)
        if snapshot is not None and "manifestRevision" in snapshot:
            return snapshot["manifestRevision"]
        data = self._data()
        if not data["nodes"]:
            return None
        spine = Spine(data)
        fields = spine.node(spine.ids("vision")[0])["fields"]
        if not spine.semantic and ("validation" not in fields or "requirementFiles" not in fields):
            return None
        template = self.workspace / "template"
        try:
            revision = compile_manifest(spine, template if template.is_dir() else None)["planRevision"]
            if snapshot is not None:
                snapshot["manifestRevision"] = revision
            return revision
        except ValueError as error:
            if not (spine.semantic and getattr(self, "_reading_status", False)):
                raise
            self._manifest_error = str(error)
            return None

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

    def audit_history(self, revision):
        table = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='audit_attempts' AND type='table'"
        ).fetchone()
        bound = (
            {
                row[0]
                for row in self.connection.execute(
                    "SELECT audit_id FROM audit_attempts WHERE state='completed'"
                )
            }
            if table
            else set()
        )
        return {
            "manifestRevision": revision,
            "audits": [
                {
                    "id": row["id"],
                    "result": json.loads(row["result_json"]),
                    "at": row["at"],
                    "bindingProvenance": "completed-attempt-linked-history"
                    if row["id"] in bound
                    else "unbound-history",
                }
                for row in self.connection.execute(
                    "SELECT * FROM audit_runs WHERE manifest_revision=? ORDER BY at,id", (revision,)
                )
            ],
            "requestCorrelationKnown": False,
            "serverQuiescenceKnown": False,
            "proofCreditGranted": False,
        }

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

    def proof_level(self, revision: str | None = None) -> str | None:
        audit = self.passing_audit(revision or self.manifest_revision())
        return audit.get("proofLevel", "oracle-verified") if audit else None

    def publication_current(self, revision: str | None) -> bool:
        published = self._meta()["published_revision"]
        if revision is None or published is None:
            return False
        manifest = self.published_manifest(published)
        if manifest.get("planMode") == "semantic-v1":
            return published == revision
        return manifest.get("sourceManifestRevision", published) == revision and manifest.get(
            "proofLevel", "oracle-verified"
        ) == self.proof_level(revision)

    def rendered_docs(self) -> dict[str, str]:
        """Display audit provenance without modifying protected template bytes."""
        rendered = render_markdown(self.spine())
        level = self.proof_level()
        label = PROSPECTIVE_LABEL if level == "prospective" else ORACLE_LABEL if level else None
        if level == "prospective":
            label += "\n" + PROSPECTIVE_WARNING
        readiness = self.readiness_status()
        header = f"Worker readiness (advisory): {readiness['status']}"
        return {
            name: header + "\n\n" + (f"{label}\n\n{text}" if label else text)
            for name, text in rendered.items()
        }

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
            origins = {part["judgmentId"] for row in rows for part in json.loads(row["shardDetails"] or "[]") if part.get("judgmentId")}
            findings = [f for f in self.finding_state()["members"] if f.get("round_id") == rows[0]["round_id"] or f.get("judgmentId") in origins]
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

    def plan_review_policy_state(self, revision: str) -> dict:
        """One derived prospective gate projection; legacy history keeps its old credit.

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
        runs_exist = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_runs'"
        ).fetchone()
        runs = (
            [
                dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM review_runs WHERE plan_revision=? "
                    "ORDER BY finished_at DESC, rowid DESC",
                    (revision,),
                )
            ]
            if runs_exist
            else []
        )
        attempts_exist = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_attempts'"
        ).fetchone()
        attempts = (
            [
                dict(row)
                for row in self.connection.execute(
                    "SELECT rowid AS reservation_order, * FROM review_attempts "
                    "WHERE plan_revision=? ORDER BY rowid DESC",
                    (revision,),
                )
            ]
            if attempts_exist
            else []
        )
        run_ids = {row["id"] for row in runs}
        incomplete = [row for row in attempts if row["id"] not in run_ids]
        declared = {(slot["name"], slot["provider"]) for slot in policy["slots"]}
        undeclared = sorted(
            {(row["reviewer"], row["provider"]) for row in runs + incomplete} - declared
        )
        if undeclared:
            result["reasons"].append("review-policy-undeclared-attempt")
            result["unlistedReviewers"] = [
                dict(name=name, provider=provider) for name, provider in undeclared
            ]
        for slot in policy["slots"]:
            matches = [
                row
                for row in runs
                if (row["reviewer"], row["provider"]) == (slot["name"], slot["provider"])
            ]
            pending = any(
                (row["reviewer"], row["provider"]) == (slot["name"], slot["provider"])
                for row in incomplete
            )
            reserved = [
                row
                for row in attempts
                if (row["reviewer"], row["provider"]) == (slot["name"], slot["provider"])
            ]
            reserved_ids = {row["id"] for row in reserved}
            # Reservations commit before launch; completion timestamps cannot order
            # overlapping attempts. Any unresolved reservation still holds, even
            # when an apparently newer attempt passed. Mixed provenance cannot
            # establish ordering and also holds instead of inventing a winner.
            ambiguous = bool(reserved and any(row["id"] not in reserved_ids for row in matches))
            run = (
                next((row for row in matches if row["id"] == reserved[0]["id"]), None)
                if reserved
                else matches[0]
                if matches
                else None
            )
            success = bool(
                run
                and not pending
                and not ambiguous
                and run["exit_code"] == 0
                and run["invalid"] == 0
                and run.get("isMocked") == 0
                and run.get("didRunProvider") == 1
                and run.get("packetBound") == 1
                and native_reviewer(dict(provider=run["provider"], shards=run.get("shards"),
                                         shardDetails=json.loads(run.get("shardDetails") or "[]")), self)
            )
            observation = dict(
                **slot,
                state="passed" if success else "unverified-or-failed",
                latestReviewId=run["id"] if run else None,
                latestAttemptId=reserved[0]["id"] if reserved else None,
                attemptOrdering="reservation-rowid" if reserved else "legacy-completion-fallback",
                ambiguousAttemptOrder=ambiguous,
                incompleteAttempt=pending,
            )
            result["slots"].append(observation)
            if success:
                result["successfulReviewIds"].append(run["id"])
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
        sources = requirement_file_sources(template, [p.name for p in template.iterdir()]) if template.is_dir() else {}
        return dict(nodes={node["id"]: hashlib.sha256(_json(node).encode()).hexdigest()
                           for node in spine.to_dict()["nodes"]},
                    files={name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sources.items()})

    def review_judgment(self, reviewer, shard, *, prepare=True):
        if "\n## Bound review requests\n" in shard["packet"]:
            requests = json.loads(shard["packet"].split("\n## Bound review requests\n", 1)[1])
            return self.judgment(requests[reviewer["name"]]["id"])
        from codex_harness.core.approvals import assess

        facts = dict(reviewer=reviewer, invocationId=shard["reviewInvocationId"],
            reviewBinding=review_binding(self, run_id=shard.get("acceptanceRunId")), manifestRevision=self.manifest_revision(),
            packet=shard["packet"], packetSha256=shard["hash"], nodeIds=shard["nodeIds"], roundId=shard["reviewRoundId"],
            question="Independently judge whether this plan leads to the whole accepted dream. Judge relevance and meaning of all evidence, regardless of language or instrumentation. Return an explicit verdict and rationale; meaning.findings contains structured findings. Empty or minor-only findings do not imply approval. meaning.depth records the chosen review depth and its consequence-based reason. Keep each reviewer's verdict separate; unavailable evidence stays inconclusive.")
        with nullcontext() if self.connection.in_transaction or not prepare else self._transaction():
            if prepare:
                for row in list(self.connection.execute("SELECT payload_json FROM events WHERE id IN (SELECT max(id) FROM events "
                        "WHERE kind='judgment-recorded' GROUP BY json_extract(payload_json, '$.id'))")):
                    prior = json.loads(row[0])
                    before = next((r["payload"]["facts"] for r in prior["sourceRefs"] if "facts" in r["payload"]), {})
                    if (prior["question"] == "plan-review" and prior["status"] == "pending-judgment" and not prior.get("supersededBy")
                            and before.get("reviewer", {}).get("name") == reviewer["name"] and before["reviewer"]["provider"] == reviewer["provider"]
                            and before.get("invocationId") != facts["invocationId"] and before.get("reviewBinding") == facts["reviewBinding"]):
                        prior.update(disposition="steer", owner=None, supersededBy=facts["invocationId"], error="Dossier superseded; inspect its replacement")
                        self._event("judgment-recorded", prior)
            return assess(self, "plan-review", facts, subject_ids=self.spine().ids(), prepare=prepare)

    def review_reuse(self, row, current, *, prepare=False):
        from codex_harness.core.approvals import assess
        details = json.loads(row.get("shardDetails") or "[]")
        originals = [self.judgment(part["judgmentId"]) for part in details if part.get("judgmentId")]
        member_ids = [r["id"] for r in finding_records(self) if r.get("round_id") == row["round_id"]
                      and r["reviewer"] == row["reviewer"] and r["provider"] == row["provider"]]
        return assess(self, "review-reuse", dict(review=dict(row), before=originals,
            reviewBinding=review_binding(self, member_ids),
            beforeDigest=json.loads(row["nodesDigest"]), afterDigest=current,
            currentNodes=self.list_nodes(), currentRevision=self.manifest_revision(),
            question="Judge the actual revision delta, including repaired and changed evidence. "
            "May this independent prior review still supply approval for this exact revision? "
            "meaning.reuse must be explicit; unchanged field locations or similar text grant no credit."),
            subject_ids=self.spine().ids(), prepare=prepare)

    def successful_reviews(self, revision: str) -> list[dict]:
        """Latest explicitly real successful run per provider; unknown history is excluded."""
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(review_runs)")}
        if not {"plan_revision", "provider", "reviewer", "id", "shardDetails", "shards", "nodesDigest"} <= columns:
            return []
        policy = self.plan_review_policy_state(revision)
        carry = (
            not policy["active"]
            and Spine(self._data()).semantic
            and revision is not None
            and revision == self.manifest_revision()
        )
        snapshot = self.review_nodes_digest() if carry else None
        latest, seen = {}, set()
        for row in self.connection.execute(
            "SELECT * FROM review_runs WHERE (plan_revision=? OR ?) ORDER BY finished_at DESC, rowid DESC",
            (revision, bool(carry)),
        ):
            row = dict(row)
            if policy["active"] and row["id"] not in policy["successfulReviewIds"]:
                continue
            identity = (row["provider"], row["reviewer"])
            if identity in seen:
                continue
            details = json.loads(row.get("shardDetails") or "[]")
            if not native_reviewer(dict(provider=row["provider"], shards=row.get("shards"), shardDetails=details),
                                   self, current=row["plan_revision"] == revision):
                continue
            if row["plan_revision"] != revision:
                if not row.get("nodesDigest"):
                    continue
                reuse = self.review_reuse(row, snapshot)
                if not (reuse["positiveCredit"] and reuse["meaning"].get("reuse") is True):
                    continue
                row.update(carriedForward=True, carriedFromRevision=row["plan_revision"], reuseJudgmentId=reuse["id"])
            latest.setdefault((row["provider"], row["reviewer"]), row)
        return [latest[key] for key in sorted(latest)]

    def review_convergence(self, *, prepare=False):
        from codex_harness.core.approvals import assess
        rows = [dict(row) for row in self.connection.execute("SELECT * FROM review_runs ORDER BY rowid")]
        if len({row["round_id"] for row in rows}) < 3:
            return None
        return assess(self, "plan-convergence", dict(rounds=rows, currentNodes=self.list_nodes(),
            reviewBinding=review_binding(self, convergence=True),
            revision=self.manifest_revision(), defaultRounds=3,
            question="After these independent review rounds, judge convergence and consequence-scaled "
            "depth. meaning.action is ready, repair, continue-review or steer. Three rounds never "
            "grant approval automatically. Explain remaining concrete gaps or readiness."),
            subject_ids=self.spine().ids(), prepare=prepare)

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
        if not self.review_prerequisite(revision)["complete"]:
            missing.append("current positive verdict from every selected reviewer and current convergence judgment")
        if missing:
            raise PlanStoreError(
                "plan-publication-evidence-required",
                f"Missing {' and '.join(missing)} for current manifest revision {revision}",
            )

    @staticmethod
    def _check_coverage(spine: Spine, kind: str) -> None:
        from codex_harness.core.wave_refine import execution_scope

        if spine.semantic and kind == "test":
            from codex_harness.core.spine import semantic_spec

            try:
                semantic_spec(spine)
            except ValueError as error:
                raise PlanStoreError("plan-semantic-scenarios-invalid", str(error)) from error
            return
        scope = execution_scope(spine)
        coverage = spine.coverage_gaps()
        if kind == "bead":
            # Wave scope already requires every executable bead in its detailed prefix.
            # Sketches and unrelated draft nodes are not executable beads.
            unlinked = [gap for gap in coverage if gap["gap"] == "bead-without-vision"]
            if unlinked:
                raise PlanStoreError("plan-bead-without-vision", _json(unlinked))
        gaps = [gap for gap in coverage if gap["gap"] == f"requirement-without-{kind}"]
        if scope:
            gaps = [gap for gap in gaps if gap["id"] in scope["inScopeRequirementIds"]]
        if gaps:
            raise PlanStoreError(f"plan-requirement-without-{kind}", _json(gaps))

    def _open(self, table: str) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                f"SELECT * FROM {table} WHERE status='open' ORDER BY id"
            )
        ]

    def _check_publish(self) -> None:
        self._check_approval("vision-lock", self._revision())
        self.require_finding_dispositions()
        if self._open("clarifications"):
            raise PlanStoreError("plan-clarifications-open", "Resolve open clarifications")

    def advance(self, to_phase: str, *, by: str, statement: str | None = None) -> None:
        _nonempty(by, "by")
        with self._transaction():
            current = self.phase()
            if to_phase not in PHASES:
                raise PlanStoreError("plan-phase-invalid", to_phase)
            index, target = PHASES.index(current), PHASES.index(to_phase)
            if target == index or target > index + 1:
                raise PlanStoreError(
                    "plan-phase-order", "Advance one phase or return to an earlier phase"
                )
            revision = self._revision()
            spine = Spine(self._data())
            if to_phase == "vision-lock" or current == "vision-lock" and target > index:
                from codex_harness.core.planner_vision import expansion_status

                visions = spine.ids("vision")
                status = expansion_status(
                    spine.node(visions[0]) if len(visions) == 1 else {"fields": {}}
                )
                if status["gaps"]:
                    raise PlanStoreError("plan-expansion-required", status["next"])
            if target > index:
                if index > PHASES.index("vision-lock"):
                    self._check_approval("vision-lock", revision)
                if current in ("align", "vision-lock"):
                    if statement is not None:
                        self.approve(current, by=by, statement=statement)
                    self._check_approval(current, revision)
                if current == "requirements":
                    self._check_coverage(spine, "test")
                if current == "plan":
                    self._check_coverage(spine, "bead")
                    self._manifest(spine)
                    from codex_harness.core.approvals import require_approval

                    consent = require_approval(self, "B")
                    statement, by = consent["words"], "user"
                if to_phase == "publish":
                    from codex_harness.core.approvals import require_approval

                    require_approval(self, "B")
                    self._check_publish()
                    self._check_evidence(self._manifest(spine)["planRevision"])
                if current == "publish" and not self.publication_current(
                    self._manifest(spine)["planRevision"]
                ):
                    raise PlanStoreError(
                        "plan-publication-required", "Publish the current revision before execution"
                    )
                if current == "publish":
                    self._check_publish()
                    self._check_evidence(self.manifest_revision())
            self.connection.execute("UPDATE meta SET phase=?", (to_phase,))
            wave_lock = None
            if (
                current == "plan"
                and to_phase == "roundtable"
                and isinstance(statement, str)
                and statement.strip()
            ):
                from codex_harness.core.wave_refine import wave_values

                is_proxy = (
                    by.startswith("proxy:") and bool(by[6:].strip()) or by == "opus-user-proxy"
                )
                values = wave_values(Spine(self._data()))
                if values and (by == "user" or is_proxy):
                    owner, details, _ = values
                    wave_lock = dict(
                        digest=waves.product_digest(
                            spine, owner["fields"], [n["fields"] for n in details]
                        ),
                        mapNodeId=owner["id"],
                        waveMapRevision=spine.revision(owner["id"]),
                        spineRevision=spine.plan_revision(),
                        spine=spine.to_dict(),
                        confirmedBy=by,
                        confirmationOrigin="proxy" if is_proxy else "user",
                        isProxy=bool(is_proxy),
                        statement=statement,
                    )
            self._event(
                "phase-advanced",
                {
                    "from": current,
                    "to": to_phase,
                    "by": by,
                    "statement": statement,
                    "plan_revision": revision,
                    **({"waveProductLock": wave_lock} if wave_lock else {}),
                },
            )

    def publish(
        self,
        *,
        by: str,
        export_dir: str | Path,
        proof_level: str = "oracle-verified",
        reason: str | None = None,
    ) -> str:
        """Freeze a content-bound manifest when workspace/template exists.

        With no known template, publish the spine-only revision. Approval gates
        and status.plan_revision continue to describe the editable spine; the
        returned and published_revision identities describe the frozen manifest.
        """
        _nonempty(by, "by")
        if proof_level not in ("oracle-verified", "prospective"):
            raise PlanStoreError("plan-proof-level-invalid", proof_level)
        if proof_level == "prospective" and (not isinstance(reason, str) or not reason.strip()):
            raise PlanStoreError(
                "plan-prospective-reason-required", "Record why no oracle is available"
            )
        with self._transaction():
            from codex_harness.core.approvals import require_approval

            if self.phase() != "publish":
                raise PlanStoreError(
                    "plan-publish-phase-required", "Enter publish before publishing"
                )
            self._check_publish()
            spine = Spine(self._data())
            self._check_coverage(spine, "test")
            self._check_coverage(spine, "bead")
            manifest = self._manifest(spine)
            require_approval(self, "B")
            wave_lock = self.wave_product_lock() if manifest.get("waveScope") else None
            revision = manifest["planRevision"]
            self._check_evidence(revision)
            level = self.proof_level(revision)
            if not spine.semantic and level != proof_level:
                raise PlanStoreError(
                    "plan-proof-level-required",
                    "Prospective publication requires --proof-level prospective "
                    '--reason "why no oracle"; '
                    "oracle-verified publication requires a passing oracle audit.",
                )
            if not spine.semantic and proof_level == "prospective":
                manifest.update(
                    proofLevel="prospective",
                    proofLevelReason=reason.strip(),
                    sourceManifestRevision=revision,
                )
                # Proof choice is publication identity, not editable test identity.
                revision = hashlib.sha256(_json(manifest).encode("utf-8")).hexdigest()
                manifest["planRevision"] = revision
            row = self.connection.execute(
                "SELECT snapshot_json FROM revisions WHERE plan_revision=?", (revision,)
            ).fetchone()
            if row is None:
                snapshot = {
                    **spine.to_dict(),
                    "plan_revision": revision,
                    "published_at": _now(),
                    "by": by,
                    "open_assumptions": self._open("assumptions"),
                    "manifest": manifest,
                    **({"waveProductLock": wave_lock} if wave_lock else {}),
                }
            else:
                snapshot = json.loads(row["snapshot_json"])
                if Spine(snapshot).to_dict() != spine.to_dict():
                    raise PlanStoreError(
                        "plan-revision-conflict", "Revision already published under another name"
                    )
            if row is None:
                self.connection.execute(
                    "INSERT INTO revisions VALUES (?, ?, ?)",
                    (revision, snapshot["published_at"], _json(snapshot)),
                )
                self._event("published", snapshot)
            if self._meta()["published_revision"] != revision:
                self.connection.execute("UPDATE meta SET published_revision=?", (revision,))
                if row is not None:
                    self._event("publication-selected", {"plan_revision": revision, "by": by})
            self._export(snapshot, Path(export_dir))
        return revision

    @staticmethod
    def _export(snapshot: dict, export_dir: Path) -> None:
        revision = snapshot["plan_revision"]
        files = {
            Path("revisions") / f"{revision}.json": _json(snapshot) + "\n",
            Path(revision) / "manifest.json": _json(snapshot["manifest"]) + "\n",
            **{
                Path(revision) / name: text
                for name, text in render_markdown(Spine(snapshot)).items()
            },
        }
        if snapshot["manifest"].get("proofLevel") == "prospective":
            for name in render_markdown(Spine(snapshot)):
                key = Path(revision) / name
                files[key] = PROSPECTIVE_LABEL + "\n" + PROSPECTIVE_WARNING + "\n\n" + files[key]
        # Never overwrite an immutable export. Missing views can be regenerated
        # after an interrupted filesystem write from the same frozen snapshot.
        for relative, content in files.items():
            path = export_dir / relative
            if path.exists() and path.read_text(encoding="utf-8") != content:
                raise PlanStoreError("plan-export-conflict", str(path))
        export_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".plan-export-", dir=export_dir) as temp:
            staged_files = []
            for index, (relative, content) in enumerate(files.items()):
                path = export_dir / relative
                if path.exists():
                    continue
                staged = Path(temp) / str(index)
                staged.write_text(content, encoding="utf-8", newline="\n")
                staged_files.append((staged, path))
            created = []
            try:
                for staged, path in staged_files:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    staged.replace(path)
                    created.append(path)
            except BaseException:
                for path in reversed(created):
                    path.unlink()
                raise

    def _snapshot(self, revision: str) -> dict:
        row = self.connection.execute(
            "SELECT snapshot_json FROM revisions WHERE plan_revision=?", (revision,)
        ).fetchone()
        if row is None:
            raise PlanStoreError("plan-revision-missing", revision)
        return json.loads(row["snapshot_json"])

    def published_manifest(self, revision: str) -> dict:
        return self._snapshot(revision)["manifest"]

    def impact_since(self, revision: str | None = None) -> dict:
        """Compare to a stored revision/hash prefix or a saved compiled manifest."""
        import re

        if revision is None:
            revision = self._meta()["published_revision"]
        baseline, before = None, None
        if revision is not None:
            path = Path(revision)
            if not path.is_absolute():
                path = self.workspace / path
            if path.is_file():
                load_plan(path)
                baseline = json.loads(path.read_text(encoding="utf-8-sig"))
                revision = baseline.get("planRevision")
        records = {}
        compiled_records = []
        for row in self.connection.execute(
            "SELECT payload_json FROM events WHERE kind='manifest-compiled' ORDER BY id DESC"
        ):
            compiled = json.loads(row[0])
            manifest, snapshot = compiled["manifest"], compiled["spine"]
            compiled_records.append((manifest, snapshot))
            for identity in (manifest["planRevision"], Spine(snapshot).plan_revision()):
                records.setdefault(identity, (manifest, snapshot))
        for row in self.connection.execute("SELECT plan_revision, snapshot_json FROM revisions"):
            snapshot = json.loads(row[1])
            manifest = snapshot.get("manifest")
            for identity in (row[0], Spine(snapshot).plan_revision()):
                records.setdefault(identity, (manifest, snapshot))
        for row in self.connection.execute("SELECT manifest_revision, result_json FROM audit_runs"):
            audit = json.loads(row[1])
            records.setdefault(row[0], (audit.get("manifest"), audit.get("spine")))
        if revision is None:
            if not compiled_records:
                raise PlanStoreError("plan-revision-missing", "Compile a manifest before impact")
            baseline, snapshot = compiled_records[0]
            before = Spine(snapshot)
        elif baseline is None:
            matches = [revision] if revision in records else []
            if not matches and re.fullmatch(r"[0-9a-fA-F]{8,64}", revision):
                matches = [key for key in records if key.startswith(revision.lower())]
            if len(matches) != 1:
                reason = "Ambiguous" if matches else "Unknown"
                raise PlanStoreError(
                    "plan-revision-ambiguous" if matches else "plan-revision-missing",
                    f"{reason} revision {revision}. Next: use a full stored revision hash "
                    "or a saved manifest file.",
                )
            revision = matches[0]
            baseline, snapshot = records[revision]
            if snapshot is not None:
                before = Spine(snapshot)
            if baseline is None and before is None:
                current = self._manifest(self.spine())
                if current["planRevision"] == revision:
                    baseline = current
                else:
                    raise PlanStoreError(
                        "plan-revision-missing",
                        f"Revision {revision} is recorded by an audit "
                        "but its baseline is unavailable. "
                        "Next: pass the saved manifest file for that revision.",
                    )
        else:
            match = next(
                (snapshot for manifest, snapshot in compiled_records if manifest == baseline), None
            )
            if match is not None:
                before = Spine(match)
        if before is not None:
            after = self.spine()
            result = impact(before, after)
            current = self._manifest(after)
            if baseline is not None and baseline.get("requirementFilesRevision") != current.get(
                "requirementFilesRevision"
            ):
                beads = set(before.ids("bead")) | set(after.ids("bead"))
                tests = set(before.ids("test")) | set(after.ids("test"))
                result.update(
                    affected=sorted(set(result["affected"]) | beads | tests),
                    affectedBeads=sorted(beads),
                    affectedTests=sorted(tests),
                    untouchedBeads=[],
                    protectedFilesChanged=True,
                )
            return result
        # A manifest from another planner has no local spine snapshot. Its frozen
        # bead contexts still identify changed contracts and dependent consumers.
        current = self._manifest(self.spine())
        old, new = baseline["beads"], current["beads"]
        changed = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
        protected_changed = baseline.get("requirementFilesRevision") != current.get(
            "requirementFilesRevision"
        )
        if protected_changed:
            changed.update(old.keys() | new.keys())
        affected = set(changed)
        while True:
            consumers = {
                key
                for graph in (old, new)
                for key, bead in graph.items()
                if affected.intersection(bead.get("dependsOn", []))
            }
            if consumers <= affected:
                break
            affected.update(consumers)
        tests = {
            bead["proofModule"]
            for graph in (old, new)
            for key, bead in graph.items()
            if key in affected and bead.get("proofModule")
        }
        return {
            "changed": sorted(changed),
            "affected": sorted(affected),
            "affectedBeads": sorted(affected),
            "affectedTests": sorted(tests),
            "untouchedBeads": sorted(new.keys() - affected),
            "staleDocs": ["PLAN.md"] if changed else [],
            **({"protectedFilesChanged": True} if protected_changed else {}),
        }

    def locked_acceptance_spine(self) -> Spine:
        meta = self._meta()
        if not meta["locked_vision_json"]:
            raise PlanStoreError("acceptance-vision-unlocked", "Confirm the current locked vision")
        self._check_approval("vision-lock", self._revision())
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
        revisions = {
            row["id"]: row["revision"]
            for row in self.connection.execute("SELECT id, revision FROM nodes")
        }
        for scenario in scenarios:
            refs = scenario.get("nodeRevisions", {})
            if not refs or any(revisions.get(key) != value for key, value in refs.items()):
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
            current = Spine(self._data())
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
                        "nodeRevisions": {key: current.revision(key) for key in sorted(refs)},
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
            > roundtable.PROVIDER_TOKEN_BUDGETS[reviewer["provider"]]
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
        records = [json.loads(row[0]) for row in self.connection.execute("SELECT payload_json FROM events WHERE id IN "
                "(SELECT max(id) FROM events WHERE kind='judgment-recorded' "
                "GROUP BY json_extract(payload_json, '$.id')) ORDER BY id")]
        records = {record["id"]: record for record in records}
        snapshot["judgmentRecords"] = records
        sources = finding_records(self)
        linked = {source.get("judgmentId") for source in sources}
        judgments, pending, groups = {}, [], {}
        for record in reversed(list(records.values())):
            facts = next((r["payload"]["facts"] for r in record["sourceRefs"] if "facts" in r["payload"]), {})
            reviewer = facts.get("reviewer", facts.get("review", {}))
            identity = (facts.get("invocationId", facts.get("request_id", record["id"])),
                        (reviewer.get("provider"), reviewer.get("name", reviewer.get("reviewer"))),
                        facts.get("packetSha256") or tuple(facts.get("reviewBinding", {}).get("memberIds") or [r["id"] for r in record["subjectRefs"]]))
            judgment = self.judgment(record["id"]) if (record["status"] == "pending-judgment" or record["id"] in linked or facts.get("reviewBinding", {}).get("memberIds") or record["question"] == "finding-equivalence") else record
            judgments[record["id"]] = judgment
            if record["status"] == "pending-judgment" and not record.get("supersededBy"):
                action = "refresh" if not judgment["current"] else "fresh-invocation" if record["disposition"] == "steer" else "submit"
                pending.append(dict(requestKey=identity, action=action, submitReady=action == "submit", acceptanceRunId=facts.get("reviewBinding", {}).get("runId"),
                                    **{k: judgment[k] for k in ("id", "question", "status", "current", "disposition", "positiveCredit", "concreteGap")}))
            if record["question"] == "finding-equivalence" and judgment["positiveCredit"]:
                for group in judgment["meaning"].get("groups", []):
                    for key in group:
                        groups.setdefault(key, group[0])
        members = []
        for source in sources:
            origin = judgments.get(source.get("judgmentId"), {})
            state = "open" if origin.get("concreteGap") else "pending"
            status, resolution, linked, decision = state, "", [source["judgmentId"]] if source.get("judgmentId") else [], None
            for judgment in judgments.values():
                facts = next((r["payload"]["facts"] for r in judgment["sourceRefs"] if "facts" in r["payload"]), {})
                if source["id"] not in facts.get("reviewBinding", {}).get("memberIds", []):
                    continue
                linked.append(judgment["id"])
                if judgment["question"] == "finding-disposition" and judgment["positiveCredit"] and decision is None:
                    decision = judgment
                    status = judgment["meaning"]["memberDispositions"][source["id"]]
                    state = "closed" if status in {"resolved", "rejected"} else "open" if status == "open" and origin.get("concreteGap") else "pending"
                    resolution = next((p["resolution"] for p in facts["proposals"] if p["id"] == source["id"]), judgment["rationale"])
            if state == "pending" and status != "deferred":
                status = "pending"
            members.append(dict(source, state=state, status=status, resolution=resolution,
                                judgmentIds=list(dict.fromkeys(linked)), cluster_id=groups.get(source["id"])))
        required = [row for row in members if row["state"] != "closed" and row.get("stage") != "readiness"]
        ids = [row["id"] for row in required]
        action = dict(kind="reconcile_findings", state="pending-judgment", question="finding-disposition",
                      findingIds=ids, mcpTool="harness_resolve_findings",
                      message="harness_resolve_findings: prepare finding-disposition for obligation ids "
                              + ", ".join(ids) + "; supply evidence-backed dispositions and reasons, then submit the returned bound judgment.")
        if not required:
            action = None
        if any(row.get("stage") == "acceptance" for row in required):
            from codex_harness.core.acceptance import acceptance_run_prerequisite
            if recovery := acceptance_run_prerequisite(self):
                action = dict(recovery, findingIds=ids, message=recovery["message"] + "; retained obligations: " + ", ".join(ids))
        snapshot["findingState"] = dict(members=members, pendingJudgments=pending,
            dispositionPrerequisite=dict(complete=not required, members=required, nextAction=action))
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
        return dict(complete=bool(expected) and expected <= {(r["provider"], r["reviewer"]) for r in reviews}
                    and not any(r["submitReady"] and r["question"] == "plan-review" and r["acceptanceRunId"] is None for r in self.finding_state()["pendingJudgments"])
                    and (convergence is None or convergence["positiveCredit"] and convergence["meaning"].get("action") == "ready"),
                    reviews=reviews, convergence=convergence)

    def acceptance_findings(self) -> list[dict]:
        return [r for r in self.finding_state()["members"] if r.get("stage") == "acceptance"]

    def cluster_review_findings(self, round_id: str) -> dict:
        from codex_harness.core.approvals import assess
        rows = [row for row in finding_records(self) if row.get("round_id") == round_id]
        if len(rows) < 2:
            return dict(count=len(rows), judgment=None)
        judgment = assess(self, "finding-equivalence", dict(members=rows,
            reviewBinding=review_binding(self, [row["id"] for row in rows]),
            question="Which findings describe the same concrete gap? meaning.groups lists disjoint "
            "groups of exact member IDs. Equivalence grants display grouping only, never closure."),
            subject_ids=self.spine().ids())
        groups = judgment["meaning"].get("groups", []) if judgment["positiveCredit"] else []
        return dict(count=len(rows) - sum(len(group)-1 for group in groups), judgment=judgment)

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
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {"nodeId", "revision"}:
                raise ValueError("review-closure-node-reference-invalid")
            if ref["nodeId"] not in spine.ids() or spine.revision(ref["nodeId"]) != ref["revision"]:
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
                if (set(item) - {"id", "status", "resolution", "closure"}
                    or item.get("id") not in members
                    or item.get("status") not in {"resolved", "rejected", "deferred"}
                    or not isinstance(item.get("resolution"), str) or not item["resolution"].strip()):
                    raise ValueError("Supply known member id, disposition and nonempty resolution")
                if "closure" in item:
                    if members[item["id"]].get("stage") == "acceptance":
                        raise ValueError("Acceptance closure producer unavailable; retain pending judgment")
                    self._closure_declaration(item["closure"], members[item["id"]],
                                              [members[item["id"]]], item["status"])
            judgment = assess(self, "finding-disposition", dict(
                members=[row for row in finding_records(self) if row["id"] in ids], proposals=dispositions,
                reviewBinding=review_binding(self, ids),
                currentNodes=self.list_nodes(), currentEvidence=self.review_nodes_digest(), revision=self.manifest_revision(),
                question="Judge each exact member's proposed disposition against the actual current "
                "repair/counterevidence and revision delta. meaning.memberDispositions maps exact IDs "
                "to resolved, rejected, deferred or open. Missing/unavailable closure evidence stays "
                "pending. A cluster ID never authorizes closure of another member."),
                subject_ids=self.spine().ids())
            judgment = self.judgment(judgment["id"])
            if not judgment["positiveCredit"]:
                return dict(dispositions=0, state="pending-judgment", judgment=judgment, owner=None)
            approved = judgment["meaning"].get("memberDispositions", {})
            reuse = [self.review_reuse(dict(row), self.review_nodes_digest(), prepare=True)
                     for row in self.connection.execute("SELECT * FROM review_runs WHERE nodesDigest IS NOT NULL AND plan_revision!=?", (self.manifest_revision(),))
                     if native_reviewer(dict(provider=row["provider"], shards=row["shards"],
                         shardDetails=json.loads(row["shardDetails"])), self, current=False)]
            return dict(dispositions=sum(approved.get(item["id"]) == item["status"] for item in dispositions),
                        state="judged", judgment=judgment, reuseJudgments=reuse,
                        convergence=self.review_convergence(prepare=True), owner=None)

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
            judgment = assess(self, "user-decision", dict(subject=dict(kind="C", run=run), reviewBinding=review_binding(self, run_id=run["id"]),
                revision=self._revision(), request_id="C:" + run["id"], words=statement, actor=by, channel="caller-message",
                question="Interpret exact owner words for this delivered candidate. "
                "meaning.decision is approve, decline, cancel or changes-requested; ambiguity stays pending."))
            if not judgment["positiveCredit"] or judgment["meaning"].get("decision") != "approve":
                pending = dict(state="pending-judgment" if not judgment["positiveCredit"] else "declined",
                               judgment=judgment, by=by, statement=statement, isProxy=bool(is_proxy), runId=run["id"])
                self._event("acceptance-consent-pending", pending)
                return pending
            approval = {
                "id": uuid4().hex,
                "runId": run["id"],
                "by": by, "statement": statement,
                "isProxy": bool(is_proxy), "judgmentId": judgment["id"], "human_identity_verified": False,
            }
            self.connection.execute(
                "INSERT INTO acceptance_approvals VALUES (?, ?)", (approval["id"], _json(approval))
            )
            self._event("accepted", approval)
            return approval

    def accept_rebind(self, repo) -> dict:
        from codex_harness.core.acceptance import rebind_acceptance

        return rebind_acceptance(self, repo)

    def status(self) -> dict:
        self._reading_status, self._manifest_error = True, None
        self._review_read_snapshot = {}
        try:
            status = self._status()
        finally:
            self._reading_status = False
            self._review_read_snapshot = None
        if self._manifest_error:
            vision = self.spine().ids("vision")[0]
            gap = dict(gap="semantic-scenarios-invalid", id=vision, message=self._manifest_error)
            status["coverage_gaps"] = [*status["coverage_gaps"], gap]
        return status

    def _status(self) -> dict:
        from codex_harness.core.acceptance import acceptance_status
        from codex_harness.core.planner_confirmation_cards import pending_decisions

        self.connection.execute("BEGIN")
        try:
            meta = dict(self._meta())
            meta.pop("locked_vision_json")
            data = self._data()
            index = PHASES.index(meta["phase"])
            pending = {
                "align": "approval:align",
                "vision-lock": "approval:vision-lock",
                "requirements": "requirements-have-tests",
                "plan": "requirements-have-beads-and-valid-manifest",
                "roundtable": "zero-open-clarifications",
                "publish": "current-revision-published",
                "accept": "acceptance:scenarios-review-approval",
            }.get(meta["phase"])
            revision = self._revision()
            approval_gate = (
                "align"
                if meta["phase"] == "align"
                else "vision-lock"
                if index >= PHASES.index("vision-lock")
                else None
            )
            credit = self._current_approval("A") if approval_gate else None
            approval = (
                None
                if not approval_gate
                else {
                    "gate": approval_gate,
                    "granted": bool(credit),
                    "origin": credit["by"] if credit else "unverified-or-unavailable",
                    "approval_id": credit["id"] if credit else None,
                    "plan_revision": credit["plan_revision"] if credit else revision,
                    "human_identity_verified": False,
                }
            )
            current_publication = self.manifest_revision()
            up_to_date = self.publication_current(current_publication)
            level = self.proof_level(current_publication)
            open_clarifications = len(self._open("clarifications"))
            if (
                meta["phase"] == "publish"
                and up_to_date
                and not open_clarifications
                and (Spine(data).semantic or self.passing_audit(current_publication))
                and self.review_prerequisite(current_publication)["complete"]
            ):
                pending = None
            if index > PHASES.index("vision-lock") and not credit:
                pending = "approval:vision-lock"
            acceptance = acceptance_status(self)
            review_policy = self.plan_review_policy_state(current_publication)
            open_rows = [r for r in self.finding_state()["members"] if r["state"] == "open" and r.get("stage") != "acceptance"]
            open_findings, open_members = len({r["cluster_id"] or r["id"] for r in open_rows}), len(open_rows)
            if meta["phase"] == "accept" and credit and acceptance["accepted"]:
                pending = None
            status = {
                **meta,
                **({"planMode": "semantic-v1"} if data["nodes"] and Spine(data).semantic else {}),
                "plan_revision": revision,
                "approval": approval,
                "spine_revision": revision,
                "manifest_revision": current_publication,
                "proofLevel": level,
                "proofLevelLabel": PROSPECTIVE_LABEL
                if level == "prospective"
                else ORACLE_LABEL
                if level
                else "Semantic scenarios; controller Validation pending"
                if data["nodes"] and Spine(data).semantic
                else "Tests not audited",
                "publication": {
                    "published_revision": meta["published_revision"],
                    "current_revision": current_publication,
                    "up_to_date": up_to_date,
                },
                "publishedProofLevel": self.published_manifest(meta["published_revision"]).get(
                    "proofLevel",
                    None if data["nodes"] and Spine(data).semantic else "oracle-verified",
                )
                if meta["published_revision"]
                else None,
                "open_clarifications": open_clarifications,
                "open_assumptions": len(self._open("assumptions")),
                "open_findings": open_findings,
                "open_finding_members": open_members,
                "pending_gate": pending,
                "coverage_gaps": Spine(data).coverage_gaps() if data["nodes"] else [],
                "phase_index": index + 1,
                "acceptance": acceptance,
                "pendingDecisions": pending_decisions(self),
                "pendingSemanticJudgments": self.finding_state()["pendingJudgments"],
                **({"reviewPolicy": review_policy} if review_policy["active"] else {}),
                "readiness": self.readiness_status(current_publication),
            }
            from codex_harness.core.planner_views import review_resolution_state

            resolution = review_resolution_state(self, status)
            status["continuation"] = resolution.pop("continuation", None)
            status["reviewResolution"] = resolution
            # Keep the phase-label alias; the visible gate names the actual prerequisite.
            status["phase_gate"] = pending
            if meta["phase"] in {"roundtable", "publish"}:
                kind = status["continuation"]["planning"]["kind"]
                status["pending_gate"] = {
                    "reconcile_findings": "pending-judgment",
                    "review_policy_hold": "plan-review-policy-unmet",
                    "compile_required": "compile-required",
                    "audit_required": "passing-audit-required",
                    "review_required": "successful-review-required",
                    "attempt_phase_advance": None,
                    "attempt_publication": "current-revision-published",
                    "unavailable_refresh": "continuation-unavailable",
                }.get(kind, "semantic-judgment-required" if kind == "judgment_required" else pending)
            return status
        finally:
            self.connection.rollback()


def open_plan_store(workspace: str | Path, name: str | None = None) -> PlanStore:
    return PlanStore(workspace, name)


class _ReadOnlyPlanStore(PlanStore):
    def _manifest(self, spine: Spine) -> dict:
        template = self.workspace / "template"
        manifest = compile_manifest(spine, template if template.is_dir() else None)
        if template.is_dir():
            self.check_requirement_files(manifest, template)
        return manifest


@contextmanager
def open_plan_store_readonly(workspace: str | Path):
    """Snapshot an existing store without initialization, migration or sidecar creation."""
    workspace = Path(workspace).resolve()
    path = workspace / ".codex-harness/plan.sqlite3"
    if not path.is_file():
        raise FileNotFoundError(path)
    wal = Path(str(path) + "-wal")
    active = wal.is_file() and wal.stat().st_size > 0
    if active and not Path(str(path) + "-shm").is_file():
        raise OSError("WAL index unavailable")
    source = sqlite3.connect(
        path.as_uri() + ("?mode=ro" if active else "?mode=ro&immutable=1"), uri=True
    )
    memory = sqlite3.connect(":memory:", isolation_level=None)
    try:
        source.backup(memory)
        source.close()
        memory.row_factory = sqlite3.Row
        memory.execute("PRAGMA query_only=ON")
        store = _ReadOnlyPlanStore.__new__(_ReadOnlyPlanStore)
        store.workspace, store.path, store.connection = workspace, path, memory
        yield store
    finally:
        source.close()
        memory.close()
