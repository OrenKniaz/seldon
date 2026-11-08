"""Transactional project planning authority; files are published projections only."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from codex_harness.core.graph import load_plan
from codex_harness.core.spine import (
    SCHEMA,
    Spine,
    compile_manifest,
    impact,
    load_spine,
)
from codex_harness.core.spine_render import render_markdown
from codex_harness.core.test_audit import ORACLE_LABEL, PROSPECTIVE_LABEL


from codex_harness.core.plan_types import PHASES, PlanStoreError, _json, _now, _nonempty

from codex_harness.core.plan_judgments import PlanJudgments
from codex_harness.core.plan_drafts import PlanDrafts
from codex_harness.core.plan_audits import PlanAudits
from codex_harness.core.plan_reviews import PlanReviews
from codex_harness.core.plan_publication import PlanPublication
from codex_harness.core.plan_acceptance import PlanAcceptance


class PlanStore(
    PlanJudgments, PlanDrafts, PlanAudits, PlanReviews, PlanPublication, PlanAcceptance
):
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
        # Concurrent activations wait for the existing bounded admission writer.
        self.connection = sqlite3.connect(self.path, isolation_level=None, timeout=30)
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
                "CREATE INDEX IF NOT EXISTS events_by_kind ON events(kind, id)",
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
        if self.connection.in_transaction:
            yield
            return
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            before = (
                self._data()
                if self.connection.execute("PRAGMA table_info(nodes)").fetchone()
                else {"nodes": []}
            )
            yield
            after = self._data()
            if before["nodes"] and before != after:
                from codex_harness.core.spine import stale_evidence

                for node in after["nodes"]:
                    if node["kind"] == "vision" and node not in before["nodes"]:
                        self._classify_vision_edit(node)
                normalized = stale_evidence(Spine(before), Spine(after))
                changed = [
                    node["id"] for node in after["nodes"] if normalized.node(node["id"]) != node
                ]
                for node_id in changed:
                    self._write_node(normalized, node_id)
                if changed:
                    self._event(
                        "trace-evidence-staled",
                        {"ids": changed, "plan_revision": normalized.plan_revision()},
                    )
            if before != after:
                from codex_harness.core.approvals import scope_assessment

                scope_assessment(self, prepare=True)
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
        self._event(
            "vision-intent-assessed",
            dict(
                id=node["id"],
                judgmentId=judgment["id"],
                state="preserved" if preserved else "pending-judgment",
            ),
        )

    def upsert_node(self, node: dict, *, reopen_vision_lock: bool = False) -> None:
        with self._transaction():
            previous = self._data()
            node_id = node.get("id") if isinstance(node, dict) else None
            data = {**previous, "nodes": [item for item in previous["nodes"] if item["id"] != node_id] + [node]}
            spine = Spine(data, previous=previous)
            self._write_node(spine, node_id)
            self._event(
                "node-upserted",
                {
                    "id": node_id,
                    "plan_revision": spine.plan_revision(),
                    "reopenRequested": reopen_vision_lock,
                },
            )

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
            if error.code not in {
                "plan-a-consent-required",
                "plan-b-consent-required",
                "pending-judgment",
            }:
                raise
            return None
        return record

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
            from codex_harness.core.approvals import scope_assessment

            scope_assessment(self, prepare=True)
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
            revision = compile_manifest(spine, template if template.is_dir() else None)[
                "planRevision"
            ]
            if snapshot is not None:
                snapshot["manifestRevision"] = revision
            return revision
        except ValueError as error:
            if not (spine.semantic and getattr(self, "_reading_status", False)):
                raise
            self._manifest_error = str(error)
            return None

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

        own_read = not self.connection.in_transaction
        if own_read:
            self.connection.execute("BEGIN")
        try:
            meta = dict(self._meta())
            meta.pop("locked_vision_json")
            data = self._data()
            index = PHASES.index(meta["phase"])
            from codex_harness.core.status_api import run_baton

            run = run_baton(self)
            pending = (
                (run or {}).get("reason")
                if run and run["state"] not in {"DONE", "STOPPED"}
                else None
            )
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
                    "origin": credit["channel"] if credit else "unverified-or-unavailable",
                    "approval_id": credit["id"] if credit else None,
                    "plan_revision": revision,
                    "human_identity_verified": False,
                }
            )
            current_publication = self.manifest_revision()
            up_to_date = self.publication_current(current_publication)
            level = self.proof_level(current_publication)
            open_clarifications = len(self._open("clarifications"))
            acceptance = acceptance_status(self)
            review_policy = self.plan_review_policy_state(current_publication)
            open_rows = [
                r
                for r in self.finding_state()["members"]
                if r["state"] == "open" and r.get("stage") != "acceptance"
            ]
            open_findings, open_members = (
                len({r["cluster_id"] or r["id"] for r in open_rows}),
                len(open_rows),
            )
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
            status["run"] = run
            status["continuation"] = status["run"]
            status["phase_gate"] = pending
            return status
        finally:
            if own_read:
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
