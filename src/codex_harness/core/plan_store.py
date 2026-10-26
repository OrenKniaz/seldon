"""Transactional project planning authority; files are published projections only."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import subprocess
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from codex_harness.core import intake, waves
from codex_harness.core.graph import load_plan
from codex_harness.core.spine import (
    SCHEMA,
    Spine,
    SpineError,
    compile_manifest,
    impact,
    load_spine,
    save_spine,
)
from codex_harness.core.spine_render import render_markdown
from codex_harness.core.test_audit import ORACLE_LABEL, PROSPECTIVE_LABEL, PROSPECTIVE_WARNING


def cluster_findings(rows: list[dict]) -> list[dict]:
    """Read-only deterministic Jaccard components; input order breaks severity ties.

    A round_id identifies the shared invocation across reviewers and shards.
    Unbound legacy findings remain singletons rather than inventing lineage.
    """
    groups = {}
    for row in sorted(rows, key=lambda row: row.get("created_at", "")):
        key = (row.get("round_id") or row["id"], row["node_id"], row.get("stage"))
        groups.setdefault(key, []).append(row)
    clusters = []
    severities = ("blocker", "major", "minor", "low", "note")
    for group in groups.values():
        tokens = [set(re.findall(r"[^\W_]+", row["finding"].lower())) for row in group]
        parents = list(range(len(group)))

        def root(index, parents=parents):
            while parents[index] != index:
                index = parents[index]
            return index

        for i, left in enumerate(tokens):
            for j in range(i):
                union = left | tokens[j]
                if union and len(left & tokens[j]) / len(union) >= 0.6:
                    parents[root(i)] = root(j)
        components = {}
        for i, row in enumerate(group):
            components.setdefault(root(i), []).append(row)
        for members in components.values():
            canonical = min(members, key=lambda row: severities.index(row["severity"]))
            clusters.append({**canonical, "members": members, "memberCount": len(members)})
    return clusters


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

    Each command takes a write reservation before reading its preimage. Successful
    commands commit before returning; callers never need to commit. A new store
    has no nodes until a vision is upserted or a complete spine is imported.
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
            # Additive migrations preserve existing planning databases.
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
            yield
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
        nodes = [
            {
                "id": row["id"],
                "kind": row["kind"],
                "title": row["title"],
                "text": row["text"],
                "fields": json.loads(row["fields_json"]),
                "parents": json.loads(row["parents_json"]),
            }
            for row in self.connection.execute("SELECT * FROM nodes ORDER BY id")
        ]
        return {"schema": SCHEMA, "name": self._meta()["name"], "nodes": nodes}

    def spine(self) -> Spine:
        self.connection.execute("BEGIN")
        try:
            return Spine(self._data())
        finally:
            self.connection.rollback()

    def list_nodes(self, kind: str | None = None) -> list[dict]:
        """Read the current nodes without writing planning truth."""
        return [node for node in self._data()["nodes"] if kind is None or node["kind"] == kind]

    def show_node(self, node_id: str, *, body: bool = False, test: str | None = None) -> dict:
        """Inspect linked protected Python tests using AST only, never imports."""
        if body and not test:
            raise ValueError("--body requires --test <name>")
        nodes = {node["id"]: node for node in self.list_nodes()}
        node = nodes[node_id]
        result = {
            **node,
            "children": sorted(key for key, child in nodes.items() if node_id in child["parents"]),
            "testFiles": [],
        }
        if node["kind"] not in ("requirement", "test"):
            return result
        linked = {node_id}
        while True:
            descendants = linked | {
                key for key, child in nodes.items() if linked.intersection(child["parents"])
            }
            if descendants == linked:
                break
            linked = descendants
        modules = {nodes[key]["fields"]["module"] for key in linked if nodes[key]["kind"] == "test"}
        template = (self.workspace / "template").resolve()
        protected = set()
        from codex_harness.core.graph import test_dir

        for vision in (item for item in nodes.values() if item["kind"] == "vision"):
            fields = vision["fields"]
            for name in (
                *fields.get("requirementFiles", []),
                test_dir({"validation": fields.get("validation", [])}),
            ):
                path = (template / name).resolve()
                if not path.is_relative_to(template):
                    raise ValueError(f"Protected file outside template: {name}")
                candidates = path.rglob("*.py") if path.is_dir() else [path]
                for candidate in candidates:
                    if not candidate.resolve().is_relative_to(template):
                        raise ValueError(f"Protected file outside template: {candidate}")
                    if candidate.suffix == ".py" and candidate.is_file():
                        protected.add(candidate)
        remaining = 8 * 1024
        found = False
        for path in sorted(protected):
            relative = path.relative_to(template).as_posix()
            dotted = relative[:-3].replace("/", ".")
            if not any(
                module in (relative, path.name, dotted, path.stem) or dotted.endswith("." + module)
                for module in modules
            ):
                continue
            source = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=relative)
            functions = []
            for item in tree.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    functions.append((item.name, item))
                elif isinstance(item, ast.ClassDef):
                    functions.extend(
                        (f"{item.name}.{method.name}", method)
                        for method in item.body
                        if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
                    )
            functions = [(name, item) for name, item in functions if item.name.startswith("test")]
            entry = {"path": relative, "functions": [name for name, _ in functions]}
            if body:
                selected = [(name, item) for name, item in functions if test in (name, item.name)]
                if len(selected) > 1:
                    raise ValueError(f"Ambiguous test {test} in {relative}; use Class.test_name")
                if selected:
                    found = True
                    name, item = selected[0]
                    start = min([item.lineno] + [d.lineno for d in item.decorator_list])
                    text = "".join(source.splitlines(keepends=True)[start - 1 : item.end_lineno])
                    encoded = text.encode("utf-8")
                    clipped = encoded[:remaining].decode("utf-8", errors="ignore")
                    remaining -= len(clipped.encode("utf-8"))
                    entry.update(
                        test=name,
                        body=clipped,
                        truncated=len(encoded) > len(clipped.encode("utf-8")),
                    )
            result["testFiles"].append(entry)
        if body and not found:
            raise ValueError(f"Test not found in linked protected files: {test}")
        return result

    def _revision(self) -> str:
        data = self._data()
        if not data["nodes"]:
            return hashlib.sha256(b"").hexdigest()
        return Spine(data).plan_revision()

    def _write_node(self, spine: Spine, node_id: str) -> None:
        node = spine.node(node_id)
        self._classify_vision_edit(node)
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
        row = self.connection.execute("SELECT * FROM nodes WHERE id=?", (node["id"],)).fetchone()
        if row is None:
            return

        def intent(fields):
            return {
                key: value
                for key, value in fields.items()
                if key not in ("validation", "requirementFiles")
            }

        if (
            row["text"] != node["text"]
            or row["title"] != node["title"]
            or intent(json.loads(row["fields_json"])) != intent(node["fields"])
        ):
            self.connection.execute("UPDATE meta SET vision_lock_stale=1")
            self._event("vision-lock-stale", {"id": node["id"]})

    def upsert_node(self, node: dict) -> None:
        with self._transaction():
            data = self._data()
            node_id = node.get("id") if isinstance(node, dict) else None
            data["nodes"] = [item for item in data["nodes"] if item["id"] != node_id]
            data["nodes"].append(node)
            spine = Spine(data)
            self._write_node(spine, node_id)
            self._event("node-upserted", {"id": node_id, "plan_revision": spine.plan_revision()})

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
                "nextAction": "Record explicit reviewer choice",
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
            if observation is None:
                raise PlanStoreError(
                    "plan-catalog-unavailable", "Record an explicit observation first"
                )
            sessions = (
                author_sessions
                if author_sessions is not None
                else {"implementationSessionIds": [], "reviewerSessionIds": {}}
            )
            assurance = intake.validate_selection(observation["catalog"], node["fields"], sessions)
            node["fields"] = assurance["selection"]
            from codex_harness.core.planner_confirmations import validate_reviewer_record_scope

            validate_reviewer_record_scope(self, node, observation["catalog"], sessions)
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

    def confirm_reviewer_selection(
        self,
        node_id: str,
        expected_spine_revision: str,
        *,
        approve: bool,
        statement: str,
        conditions: str,
    ) -> dict:
        """Record an explicit ordinary caller action, never native or human identity proof."""
        from codex_harness.core.planner_confirmations import (
            REVIEWER_CONFIRMATION,
            reviewer_confirmation_target,
            reviewer_record_authority,
        )

        with self._transaction():
            before = self._draft_preimage(expected_spine_revision)
            node, catalog, sessions, scope = reviewer_confirmation_target(self, before, node_id)
            if (
                approve is not True
                or not isinstance(statement, str)
                or not statement.strip()
                or not isinstance(conditions, str)
                or conditions != ""
            ):
                raise PlanStoreError(
                    "plan-reviewer-confirmation-action-invalid",
                    "Explicit affirmative action, full statement and empty conditions required",
                )
            record = dict(
                recordId=str(uuid4()),
                nodeId=node_id,
                preimageRevision=expected_spine_revision,
                scopeDigest=scope,
                catalogId=catalog["id"],
                catalogDigest=catalog["digest"],
                authorSessions=deepcopy(sessions),
                approve=True,
                statement=statement,
                conditions="",
                origin="user",
                provenance="caller-declared-user-action",
                human_identity_verified=False,
            )
            self._event(REVIEWER_CONFIRMATION, record)
            event_id = self.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            node["fields"]["confirmation"] = reviewer_record_authority(event_id, record)
            assurance = intake.validate_selection(catalog, node["fields"], sessions)
            node["fields"] = assurance["selection"]
            result = self._save_draft_nodes(
                before,
                [node],
                "reviewer-selection-recorded",
                {"nodeId": node_id, "authorSessions": sessions},
            )
            return {**result, **assurance, "confirmationRecord": {**record, "eventId": event_id}}

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
            # Contexts are canonical recorded observations, never caller-supplied authority.
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
            # Canonical compiler/graph validate candidate structure before any writes.
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
                            from codex_harness.core.planner_confirmations import (
                                validate_reviewer_record_scope,
                            )

                            validate_reviewer_record_scope(
                                self, node, catalog["catalog"], event["authorSessions"]
                            )
                            item.update(
                                intake.validate_selection(
                                    catalog["catalog"], node["fields"], event["authorSessions"]
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
            for node_id in spine.ids("vision"):
                self._classify_vision_edit(spine.node(node_id))
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

    def _approve(self, gate: str, by: str, statement: str) -> str:
        if gate not in ("align", "vision-lock"):
            raise PlanStoreError("plan-gate-invalid", gate)
        _nonempty(by, "by")
        _nonempty(statement, "statement")
        revision = Spine(self._data()).plan_revision()
        item_id = uuid4().hex
        self.connection.execute(
            "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, gate, revision, by, statement, _now()),
        )
        self._event(
            "approved",
            {
                "id": item_id,
                "gate": gate,
                "plan_revision": revision,
                "by": by,
                "statement": statement,
            },
        )

        if gate == "vision-lock":
            spine = Spine(self._data())
            vision = _json(spine.node(spine.ids("vision")[0]))
            self.connection.execute("UPDATE meta SET locked_vision_json=?", (vision,))
            self.connection.execute("UPDATE meta SET vision_lock_stale=0")
            if self._meta()["vision_locked_revision"] or PHASES.index(self.phase()) > PHASES.index(
                "vision-lock"
            ):
                self.connection.execute("UPDATE meta SET vision_locked_revision=?", (revision,))
        return item_id

    def approve(self, gate: str, *, by: str, statement: str) -> str:
        if gate not in ("align", "vision-lock"):
            raise PlanStoreError("plan-gate-invalid", gate)
        _nonempty(by, "by")
        _nonempty(statement, "statement")
        raise PlanStoreError(
            "plan-confirmation-origin-unavailable",
            "Supplied words cannot approve a gate. Use a client supporting native MCP elicitation.",
        )

    def _approval_credit(self, gate: str, revision: str) -> dict | None:
        from codex_harness.core.planner_confirmations import approval_credit

        if gate == "vision-lock" and self._meta()["vision_locked_revision"]:
            if self._meta()["vision_lock_stale"]:
                return None
            revision = self._meta()["vision_locked_revision"]
        return approval_credit(self, gate, revision)

    def _check_approval(self, gate: str, revision: str) -> None:
        if self._approval_credit(gate, revision):
            return
        if gate == "vision-lock" and self._meta()["vision_lock_stale"]:
            raise PlanStoreError("plan-approval-stale", "vision-lock needs renewed approval")
        rows = self.connection.execute(
            "SELECT plan_revision FROM approvals WHERE gate=?", (gate,)
        ).fetchall()
        if any(row["plan_revision"] == revision for row in rows) or (
            gate == "vision-lock" and self._meta()["vision_locked_revision"]
        ):
            raise PlanStoreError(
                "plan-confirmation-origin-unavailable",
                "Historical supplied words remain recorded; obtain fresh native confirmation.",
            )
        code = "plan-approval-stale" if rows else "plan-approval-required"
        raise PlanStoreError(code, f"{gate} needs approval of {revision}")

    def compile_template(self, template: Path) -> dict:
        """Materialize the canonical spine before binding protected file contents."""
        template = Path(template)
        if not template.is_dir():
            raise PlanStoreError("spine-template-missing", str(template))
        spine = self.spine()
        for name, text in render_markdown(spine).items():
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
        # compile_manifest validates protected paths before this existence check.
        missing = [name for name in manifest["requirementFiles"] if not (template / name).exists()]
        if missing:
            raise PlanStoreError(
                "plan-requirement-file-missing",
                f"Missing requirement files in {template}: {', '.join(missing)}. "
                "Next: restore listed requirement files, then compile --template template, "
                "audit --oracle oracle, and quality",
            )

    def _manifest(self, spine: Spine) -> dict:
        # A workspace-local template is the only template this store knows.
        # Without one, publication retains the legacy spine-only revision;
        # callers compiling elsewhere must pass their template to compile_manifest.
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
        """Identity of the editable spine plus protected requirement file contents."""
        data = self._data()
        if not data["nodes"]:
            return None
        spine = Spine(data)
        fields = spine.node(spine.ids("vision")[0])["fields"]
        if "validation" not in fields or "requirementFiles" not in fields:
            return None
        template = self.workspace / "template"
        return compile_manifest(spine, template if template.is_dir() else None)["planRevision"]

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
                if row["state"] != "failed" or row["result_json"] is not None:
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
                raise PlanStoreError("audit-owner-mismatch", "Only the running owner may complete")
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
            findings = [
                dict(f)
                for f in self.connection.execute(
                    "SELECT * FROM review_findings WHERE round_id=?", (rows[0]["round_id"],)
                )
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
        member_ids = {row["id"] for row in findings}
        declarations = [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT payload_json FROM events WHERE kind='finding-closure-declared' ORDER BY id"
            )
        ]
        if any(member_ids.intersection(item["memberIds"]) for item in declarations):
            result["closureDeclarations"] = [
                item for item in declarations if member_ids.intersection(item["memberIds"])
            ]
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

    def _prospective_closure_facts(self, adopted_at: str, revision: str) -> list[dict]:
        from codex_harness.core.roundtable import findings

        declarations = [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT payload_json FROM events WHERE kind='finding-closure-declared' ORDER BY id"
            )
        ]
        result = []
        for row in findings(self, expand=True):
            if row.get("stage") == "readiness" or row["created_at"] < adopted_at:
                continue
            declaration = next(
                (item for item in reversed(declarations) if row["id"] in item["memberIds"]), None
            )
            result.append(
                dict(
                    id=row["id"],
                    nodeId=row["node_id"],
                    disposition=row["status"],
                    recordedRevision=row["plan_revision"],
                    closure="unverified",
                    reasons=[
                        "targeted-assessment-producer-unavailable",
                        "observed-separation-producer-unavailable",
                    ],
                    retainedObligation=True,
                    deferred=row["status"] == "deferred",
                    obligationVerification="unverified",
                    declaration=declaration,
                    declarationBindingCurrent=bool(
                        declaration
                        and declaration["expectedManifestRevision"] == revision
                        and declaration["status"] == row["status"]
                    ),
                )
            )
        return result

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
            slots=[],
            requiredFailures=[],
            optionalFailures=[],
            successfulReviewIds=[],
            closureFindings=self._prospective_closure_facts(policies[0]["at"], revision),
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
        if result["closureFindings"]:
            result["reasons"].append("review-closure-unverified")
        result["eligible"] = not result["reasons"]
        return result

    def successful_reviews(self, revision: str) -> list[dict]:
        """Latest explicitly real successful run per provider; unknown history is excluded."""
        exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_runs'"
        ).fetchone()
        if not exists:
            return []
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(review_runs)")}
        if (
            not {
                "plan_revision",
                "provider",
                "exit_code",
                "invalid",
                "finished_at",
                "id",
                "isMocked",
                "didRunProvider",
            }
            <= columns
        ):
            return []
        latest = {}
        for row in self.connection.execute(
            "SELECT * FROM review_runs WHERE plan_revision=? AND exit_code=0 AND invalid=0 "
            "AND isMocked=0 AND didRunProvider=1 ORDER BY finished_at DESC, id DESC",
            (revision,),
        ):
            latest.setdefault(row["provider"], dict(row))
        result = [latest[key] for key in sorted(latest)]
        policy = self.plan_review_policy_state(revision)
        if policy["active"] and revision == self.manifest_revision():
            allowed = set(policy["successfulReviewIds"])
            # Resolve latest per required/optional slot before provider aggregation;
            # older successes cannot mask failed latest attempts after adoption.
            eligible = [
                dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM review_runs WHERE plan_revision=? "
                    "ORDER BY finished_at DESC, rowid DESC",
                    (revision,),
                )
                if row["id"] in allowed
            ]
            result = []
            providers = set()
            for row in eligible:
                if row["provider"] not in providers:
                    providers.add(row["provider"])
                    result.append(row)
        return result

    def _check_evidence(self, revision: str) -> None:
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
        if not self.passing_audit(revision):
            missing.append(
                "passing audit (audit --oracle oracle, or audit --prospective); "
                "audit-binding-required: legacy unbound prospective history "
                "grants no current credit"
            )
        if not self.successful_reviews(revision):
            missing.append("successful review (roundtable)")
        if missing:
            raise PlanStoreError(
                "plan-publication-evidence-required",
                f"Missing {' and '.join(missing)} for current manifest revision {revision}",
            )

    @staticmethod
    def _check_coverage(spine: Spine, kind: str) -> None:
        from codex_harness.core.wave_refine import execution_scope

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
        from codex_harness.core.roundtable import findings

        self._check_approval("vision-lock", self._revision())
        if any(row.get("stage", "roundtable") != "readiness" for row in findings(self, "open")):
            raise PlanStoreError("plan-findings-open", "Resolve open roundtable findings")
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
            if target > index:
                if index > PHASES.index("vision-lock"):
                    self._check_approval("vision-lock", revision)
                spine = Spine(self._data())
                if current in ("align", "vision-lock"):
                    if statement is not None:
                        self.approve(current, by=by, statement=statement)
                    self._check_approval(current, revision)
                if current == "vision-lock":
                    # Re-entering an unchanged lock reuses its original native grant;
                    # metadata/subordinate revisions are not newly approved revisions.
                    grant = self._approval_credit(current, revision)
                    self.connection.execute(
                        "UPDATE meta SET vision_locked_revision=?", (grant["plan_revision"],)
                    )
                if current == "requirements":
                    self._check_coverage(spine, "test")
                if current == "plan":
                    self._check_coverage(spine, "bead")
                    self._manifest(spine)
                if to_phase == "publish":
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
            if self.phase() != "publish":
                raise PlanStoreError(
                    "plan-publish-phase-required", "Enter publish before publishing"
                )
            self._check_publish()
            spine = Spine(self._data())
            self._check_coverage(spine, "test")
            self._check_coverage(spine, "bead")
            manifest = self._manifest(spine)
            wave_lock = self.wave_product_lock() if manifest.get("waveScope") else None
            revision = manifest["planRevision"]
            self._check_evidence(revision)
            level = self.proof_level(revision)
            if level != proof_level:
                raise PlanStoreError(
                    "plan-proof-level-required",
                    "Prospective publication requires --proof-level prospective "
                    '--reason "why no oracle"; '
                    "oracle-verified publication requires a passing oracle audit.",
                )
            if proof_level == "prospective":
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
        if not meta["locked_vision_json"] or meta["vision_lock_stale"]:
            raise PlanStoreError("acceptance-vision-unlocked", "Confirm the current locked vision")
        vision = json.loads(meta["locked_vision_json"])
        data = self._data()
        data["nodes"] = [vision if node["kind"] == "vision" else node for node in data["nodes"]]
        return Spine(data)

    def acceptance_scenarios(self) -> list[dict]:
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
        from codex_harness.core.acceptance import derive_scenarios

        with self._transaction():
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
        max_rounds,
        max_total,
        timeout,
    ):
        """Seal a complete execution-stage request and shared capacity before launch."""
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
                max_rounds=max_rounds,
                max_total=max_total,
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
        root = roundtable._execution_directory(self, item_id)
        # External files are not locked by SQLite; compare the complete projection
        # again inside the writer, immediately before the reservation append.
        with self._transaction():
            if roundtable._execution_context(self, request) != context:
                raise ValueError("execution-selection-raced-before-reservation")
            matching = [
                old
                for _, old in roundtable._execution_events(self, "execution-assessment-reserved")
                if old["request"] == request and old["context"] == context
            ]
            if matching:
                # Duplicate caller delivery cannot launch a second child or
                # abandon an uncertain first launch. A retry needs a new request.
                return dict(matching[-1], reused=True)
            totals = roundtable.review_totals(self, expected_manifest_revision)
            used = next(
                (
                    row
                    for row in totals
                    if row["reviewer"] == reviewer["name"]
                    and row["provider"] == reviewer["provider"]
                ),
                dict(round=0, total=0),
            )
            if used["round"] >= max_rounds or used["total"] >= max_total:
                raise ValueError("execution-shared-review-budget-exhausted")
            root.mkdir(parents=True, exist_ok=False)
            recovery._write(root, "packet", packet.encode("utf-8"))
            self._event("execution-assessment-reserved", header)
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

    def acceptance_findings(self) -> list[dict]:
        return [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT result_json FROM acceptance_findings ORDER BY rowid"
            )
        ]

    def cluster_review_findings(self, round_id: str) -> int:
        """Store membership inside the ingest transaction, retaining every source row."""
        rows = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM review_findings WHERE round_id=? ORDER BY created_at, rowid",
                (round_id,),
            )
        ]
        clusters = cluster_findings(rows)
        for cluster in clusters:
            self.connection.executemany(
                "UPDATE review_findings SET cluster_id=? WHERE id=?",
                [(cluster["id"], member["id"]) for member in cluster["members"]],
            )
        return len(clusters)

    def _closure_declaration(self, closure, row, members, status):
        """Validate links, never infer semantic truth or observed independence."""
        required = {
            "schema",
            "expectedManifestRevision",
            "memberIds",
            "basis",
            "nodeRefs",
            "reviewIds",
            "auditIds",
            "obligationNodeIds",
        }
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
        nodes = {}
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {"nodeId", "revision"}:
                raise ValueError("review-closure-node-reference-invalid")
            saved = self.connection.execute(
                "SELECT revision FROM nodes WHERE id=?", (ref["nodeId"],)
            ).fetchone()
            if not saved or saved[0] != ref["revision"]:
                raise ValueError("review-closure-artifact-stale-or-absent")
            nodes[ref["nodeId"]] = ref["revision"]
        if row["node_id"] != "plan" and row["node_id"] not in nodes:
            raise ValueError("review-closure-affected-node-missing")
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
            saved = self.connection.execute(
                "SELECT kind FROM nodes WHERE id=?", (value,)
            ).fetchone()
            if not saved or saved[0] not in {"requirement", "vision"}:
                raise ValueError("review-closure-obligation-reference-invalid")
        if status == "deferred" and not obligations:
            raise ValueError("review-closure-deferred-obligation-required")
        return {
            **deepcopy(closure),
            "status": status,
            "closure": "unverified",
            "reason": "Targeted assessment and observed separation producers unavailable",
        }

    def resolve_findings(self, dispositions: list[dict]) -> int:
        """Validate the whole batch under one writer lock, then resolve atomically.

        The 1-based item index is also the JSONL line number. Overlapping targets
        are refused so one batch cannot assign conflicting reasons to a cluster.
        """
        from codex_harness.core.roundtable import _table, findings

        if not isinstance(dispositions, list) or not dispositions:
            raise ValueError("At least one finding disposition is required")
        with self._transaction():
            _table(self)
            reviews = {row["id"]: row for row in findings(self, expand=True)}
            acceptance = {row["id"]: row for row in self.acceptance_findings()}
            errors, targets, prepared = [], set(), []
            for line, item in enumerate(dispositions, 1):
                if not isinstance(item, dict) or set(item) not in (
                    {"id", "status", "resolution"},
                    {"id", "status", "resolution", "closure"},
                ):
                    errors.append(f"Line {line}: expected id, status and resolution")
                    continue
                item_id, status, resolution = (item[key] for key in ("id", "status", "resolution"))
                if not isinstance(item_id, str) or item_id not in reviews | acceptance:
                    errors.append(f"Line {line}: Unknown finding: {item_id}")
                    continue
                if status not in ("resolved", "rejected", "deferred"):
                    errors.append(f"Line {line}: Invalid finding resolution status")
                if not isinstance(resolution, str) or not resolution.strip():
                    errors.append(f"Line {line}: A nonempty resolution is required")
                row = reviews.get(item_id)
                canonical_id = (row["cluster_id"] or item_id) if row else item_id
                if canonical_id in targets:
                    errors.append(f"Line {line}: Duplicate cluster disposition: {canonical_id}")
                targets.add(canonical_id)
                declaration = None
                if "closure" in item:
                    if row is None:
                        errors.append(f"Line {line}: acceptance closure producer unavailable")
                    else:
                        members = [
                            member
                            for member in reviews.values()
                            if (member["cluster_id"] or member["id"]) == canonical_id
                        ]
                        try:
                            declaration = self._closure_declaration(
                                item["closure"], row, members, status
                            )
                        except (ValueError, TypeError, KeyError) as error:
                            errors.append(f"Line {line}: {error}")
                prepared.append((canonical_id, status, resolution, row is not None, declaration))
            if errors:
                raise ValueError("Invalid dispositions; no changes applied:\n" + "\n".join(errors))
            for item_id, status, resolution, is_review, declaration in prepared:
                if is_review:
                    members = [
                        row
                        for row in reviews.values()
                        if (row["cluster_id"] or row["id"]) == item_id
                    ]
                    at = _now()
                    for member in members:
                        self.connection.execute(
                            "UPDATE review_findings SET status=?, resolution=?, resolved_at=? "
                            "WHERE id=?",
                            (status, resolution, at, member["id"]),
                        )
                        self._event(
                            "finding-" + status,
                            {
                                "id": member["id"],
                                "resolution": resolution,
                                "clusteredWith": item_id if member["id"] != item_id else None,
                            },
                        )
                    if declaration is not None:
                        self._event("finding-closure-declared", declaration)
                else:
                    finding = {**acceptance[item_id], "status": status, "resolution": resolution}
                    self.connection.execute(
                        "UPDATE acceptance_findings SET result_json=? WHERE id=?",
                        (_json(finding), item_id),
                    )
                    self._event("acceptance-finding-resolved", finding)
        return len(prepared)

    def resolve_acceptance_finding(self, item_id, *, resolution, status="resolved") -> None:
        self.resolve_findings([dict(id=item_id, status=status, resolution=resolution)])

    def acceptance_review_guard(self, run_id: str) -> str | None:
        """Share acceptance/status admission; unknown or synthetic reviews earn no credit."""
        if self._open("clarifications"):
            return "plan-clarifications-open"

        def native_reviewer(reviewer):
            if not isinstance(reviewer, dict):
                return False
            details = reviewer.get("shardDetails")
            count = reviewer.get("shards")
            return (
                isinstance(reviewer.get("provider"), str)
                and bool(reviewer["provider"].strip())
                and type(count) is int
                and count > 0
                and isinstance(details, list)
                and len(details) == count
                and all(
                    isinstance(shard, dict)
                    and shard.get("isMocked") is False
                    and shard.get("didRunProvider") is True
                    and type(shard.get("exitCode")) is int
                    and shard["exitCode"] == 0
                    and type(shard.get("invalid")) is int
                    and shard["invalid"] == 0
                    for shard in details
                )
            )

        def original_review(review):
            # Rebind carries a reference, never a newly executed review. Follow only
            # links recorded by the existing rebind producer, preserving originals.
            seen = set()
            while isinstance(review, dict) and "originalReviewId" in review:
                review_id = review.get("id")
                if not isinstance(review_id, str) or review_id in seen:
                    return None
                seen.add(review_id)
                current_id, previous_id = review.get("runId"), review.get("reboundFrom")
                if (
                    not isinstance(previous_id, str)
                    or current_id == previous_id
                    or review.get("by") != "harness"
                    or review.get("stage") != "acceptance"
                    or review.get("reason") != "revision algorithm upgraded; product unchanged"
                    or "reviewers" in review
                    or "isMocked" in review
                ):
                    return None
                runs = []
                for identity in (current_id, previous_id):
                    saved = self.connection.execute(
                        "SELECT result_json FROM acceptance_runs WHERE id=?", (identity,)
                    ).fetchone()
                    runs.append(json.loads(saved[0]) if saved else None)
                if (
                    any(not isinstance(run, dict) or run.get("passed") is not True for run in runs)
                    or runs[0].get("id") != current_id
                    or runs[1].get("id") != previous_id
                    or runs[0].get("repo") != runs[1].get("repo")
                    or runs[0].get("planRevision") != runs[1].get("planRevision")
                ):
                    return None
                receipts = (
                    json.loads(row[0])
                    for row in self.connection.execute(
                        "SELECT payload_json FROM events WHERE kind='acceptance-rebound'"
                    )
                )
                if not any(
                    receipt.get("run") == runs[0]
                    and receipt.get("reboundFrom") == previous_id
                    and receipt.get("by") == "harness"
                    and review in receipt.get("reviews", [])
                    for receipt in receipts
                ):
                    return None
                original_id = review.get("originalReviewId")
                saved = self.connection.execute(
                    "SELECT result_json FROM acceptance_reviews WHERE id=?", (original_id,)
                ).fetchone()
                review = json.loads(saved[0]) if saved else None
                if (
                    not isinstance(review, dict)
                    or review.get("id") != original_id
                    or review.get("runId") != previous_id
                ):
                    return None
            return review

        for row in self.connection.execute("SELECT result_json FROM acceptance_reviews"):
            review = json.loads(row[0])
            if not isinstance(review, dict) or review.get("runId") != run_id:
                continue
            review = original_review(review)
            if not isinstance(review, dict) or review.get("isMocked") is not False:
                continue
            reviewers = review.get("reviewers")
            if (
                isinstance(reviewers, list)
                and reviewers
                and all(native_reviewer(reviewer) for reviewer in reviewers)
                and len({reviewer["provider"] for reviewer in reviewers}) >= 2
            ):
                return None
        return "acceptance-review-required"

    def accept(self, *, by: str, statement: str) -> dict:
        from codex_harness.core.acceptance import (
            _latest_run,
            _refuse,
            run_invalid_reason,
            run_subject,
        )

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
            run = _latest_run(self)
            if run is not None and run_subject(run) != "product":
                _refuse("acceptance-product-required")
            reason = run_invalid_reason(self, run)
            if reason:
                _refuse(reason, run["repo"] if run else None)
            if not run or not run["passed"]:
                raise PlanStoreError(
                    "acceptance-scenarios-required",
                    "Pass all scenarios on the current integrated revision",
                )
            if any(f["status"] == "open" for f in self.acceptance_findings()):
                raise PlanStoreError("acceptance-findings-open", "Resolve open acceptance findings")
            review_guard = self.acceptance_review_guard(run["id"])
            if review_guard:
                message = (
                    "Resolve open clarifications"
                    if review_guard == "plan-clarifications-open"
                    else "Review the current scenario run with native cross-subscription evidence"
                )
                raise PlanStoreError(review_guard, message)
            approval = {
                "id": uuid4().hex,
                "runId": run["id"],
                "by": by,
                "statement": statement,
                "isProxy": bool(is_proxy),
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
        from codex_harness.core.acceptance import acceptance_status
        from codex_harness.core.roundtable import review_totals

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
            credit = self._approval_credit(approval_gate, revision) if approval_gate else None
            approval = (
                None
                if not approval_gate
                else {
                    "gate": approval_gate,
                    "granted": bool(credit),
                    "origin": "mcp-client-elicitation" if credit else "unverified-or-unavailable",
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
                and self.passing_audit(current_publication)
                and self.successful_reviews(current_publication)
            ):
                pending = None
            if index > PHASES.index("vision-lock") and not credit:
                pending = "approval:vision-lock"
            acceptance = acceptance_status(self)
            review_policy = self.plan_review_policy_state(current_publication)
            open_findings, open_members = 0, 0
            columns = {
                row[1] for row in self.connection.execute("PRAGMA table_info(review_findings)")
            }
            if columns:
                # Legacy rows without stored membership are individual findings.
                cluster_key = "coalesce(cluster_id, id)" if "cluster_id" in columns else "id"
                open_findings, open_members = self.connection.execute(
                    f"SELECT count(DISTINCT {cluster_key}), count(*) FROM review_findings "
                    "WHERE status='open'"
                ).fetchone()
            if meta["phase"] == "accept" and credit and acceptance["accepted"]:
                pending = None
            status = {
                **meta,
                "plan_revision": revision,
                "approval": approval,
                "spine_revision": revision,
                "manifest_revision": current_publication,
                "proofLevel": level,
                "proofLevelLabel": PROSPECTIVE_LABEL
                if level == "prospective"
                else ORACLE_LABEL
                if level
                else "Tests not audited",
                "publication": {
                    "published_revision": meta["published_revision"],
                    "current_revision": current_publication,
                    "up_to_date": up_to_date,
                },
                "publishedProofLevel": self.published_manifest(meta["published_revision"]).get(
                    "proofLevel", "oracle-verified"
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
                "roundtable": review_totals(self, current_publication),
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
                    "reconcile_findings": "plan-findings-open",
                    "review_policy_hold": "plan-review-policy-unmet",
                    "compile_required": "compile-required",
                    "audit_required": "passing-audit-required",
                    "review_required": "successful-review-required",
                    "attempt_phase_advance": None,
                    "attempt_publication": "current-revision-published",
                    "unavailable_refresh": "continuation-unavailable",
                }.get(kind, pending)
            return status
        finally:
            self.connection.rollback()


def open_plan_store(workspace: str | Path, name: str | None = None) -> PlanStore:
    return PlanStore(workspace, name)


class _ReadOnlyPlanStore(PlanStore):
    def _manifest(self, spine: Spine) -> dict:
        # Same canonical compiler/inputs, without _manifest's temporary-file validation.
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
