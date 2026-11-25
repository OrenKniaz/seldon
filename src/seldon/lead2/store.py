"""C8/C9/C19: the living spec's SQLite writer and decision bindings."""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from ..schema import TEXT, array, choice, obj, validate
from ..store import Store, encode
from .contracts import BATCH_SCHEMA
from .graph import PROOF, Delta, Graph, edited

SNAPSHOTS = """
    CREATE TABLE IF NOT EXISTS lead2_spec (
        id INTEGER PRIMARY KEY, parent INTEGER NOT NULL, graph TEXT NOT NULL,
        mode TEXT NOT NULL, author TEXT NOT NULL, reason TEXT NOT NULL, time TEXT NOT NULL
    )
"""
DECISIONS = """
    CREATE TABLE IF NOT EXISTS lead2_spec_decision (
        gate TEXT NOT NULL, revision INTEGER NOT NULL, approval TEXT NOT NULL UNIQUE,
        PRIMARY KEY(gate, revision)
    )
"""
OBSERVATIONS = """
    CREATE TABLE IF NOT EXISTS lead2_observation (
        id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL
    )
"""

OBSERVATION_BODY = obj(
    promises=array(TEXT, 1), witness=TEXT, commit=TEXT, session=TEXT,
    outcome=choice("built", "passed", "failed", "not-verifiable-here"), result=TEXT,
)


@dataclass(frozen=True)
class Snapshot:
    revision: int = 0
    parent: int | None = None
    graph: Graph = field(default_factory=Graph)
    mode: str = ""
    author: str = ""
    reason: str = ""
    time: str = ""
    accepted: bool = True


@dataclass(frozen=True)
class EditResult:
    revision: int
    accepted: bool
    names: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    id: int
    revision: int
    promises: tuple[str, ...]
    witness: str
    commit: str
    session: str
    outcome: str
    result: str


class Conflict(ValueError):
    def __init__(self, expected: int, current: int, delta: Delta):
        self.current = current
        self.changes = delta.describe()
        changes = "\n".join(self.changes) or "The graph has no net changes."
        super().__init__(f"Revision {expected} is out of date. Read revision {current} and retry.\n{changes}")


class SpecStore:
    def __init__(self, store: Store):
        self._store = store
        with store.transaction():
            for statement in (SNAPSHOTS, DECISIONS, OBSERVATIONS):
                store.db.execute(statement)

    def snapshot(self, revision: int | None = None) -> Snapshot:
        if revision is None:
            revision = self._store.db.execute("""
                SELECT COALESCE(MAX(id), 0) FROM lead2_spec
                WHERE mode != 'scope-proposal' OR id IN
                    (SELECT revision FROM lead2_spec_decision WHERE gate = 'scope')
            """).fetchone()[0]
        if revision == 0:
            return Snapshot()
        row = self._store.db.execute("""
            SELECT parent, graph, mode, author, reason, time,
                   mode != 'scope-proposal' OR id IN
                       (SELECT revision FROM lead2_spec_decision WHERE gate = 'scope')
            FROM lead2_spec WHERE id = ?
        """, (revision,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown spec revision {revision}. Read the current spec before editing.")
        parent, body, mode, author, reason, time, accepted = row
        graph = json.loads(body)
        return Snapshot(revision, parent, Graph(graph["nodes"], tuple(map(tuple, graph["links"]))),
                        mode, author, reason, time, bool(accepted))

    def read_slice(self, node: str, revision: int | None = None) -> tuple[int, Graph]:
        snapshot = self.snapshot(revision)
        graph = snapshot.graph
        kind = graph.nodes.get(node, {}).get("kind")
        if kind not in {"story", "promise"}:
            raise ValueError(f"Choose an active story or promise in revision {snapshot.revision}: {node}")
        refs = graph.follow(node) | graph.follow(node, upstream=True)
        if kind == "story":
            refs.update(target for source, target in graph.links
                        if source == node and graph.nodes[target]["kind"] == "walk")
        return snapshot.revision, graph.select(refs)

    def apply(self, batch: dict, *, author: str) -> EditResult:
        validate(batch, BATCH_SCHEMA, "batch")
        if not batch["reason"].strip() or not author.strip():
            raise ValueError("Supply an author and a reason for the edit.")
        with self._store.transaction():
            current = self.snapshot()
            requested = self.snapshot(batch["revision"])
            self._require_head(requested, current)
            graph, names = edited(current.graph, batch)
            delta = Delta(current.graph, graph)
            mode = batch["mode"]
            if graph == current.graph:
                return EditResult(current.revision, True)
            mode = ("scope-proposal" if self.scope_changes(delta) else
                    "wording-only" if mode == "wording-only" and _wording_only(delta) else "substantive")
            row = self._store.db.execute("""
                INSERT INTO lead2_spec(parent, graph, mode, author, reason, time) VALUES (?, ?, ?, ?, ?, ?)
            """, (current.revision, encode(asdict(graph)), mode, author, batch["reason"],
                  datetime.now(UTC).isoformat()))
            return EditResult(row.lastrowid, mode != "scope-proposal",
                              {name: ref for name, ref in names.items() if ref in graph.nodes})

    def proposals(self) -> tuple[Snapshot, ...]:
        rows = self._store.db.execute("""
            SELECT id FROM lead2_spec WHERE mode = 'scope-proposal'
                AND id NOT IN (SELECT revision FROM lead2_spec_decision WHERE gate = 'scope')
            ORDER BY id
        """)
        return tuple(self.snapshot(revision) for revision, in rows)

    def pins(self) -> dict[str, int]:
        return dict(self._store.db.execute(
            "SELECT gate, revision FROM lead2_spec_decision WHERE gate IN ('A', 'B')"))

    def bind_decision(self, gate: str, revision: int, approval: str) -> None:
        """Bind the displayed revision and materialize A's accepted defaults in the same transaction."""
        if gate not in {"A", "B", "scope"}:
            raise ValueError("Choose A, B or scope.")
        with self._store.transaction():
            if approval and self.decision(gate, revision) == approval:
                return
            request = self._check_approval(gate, revision, approval)
            if self._store.db.execute(
                "SELECT 1 FROM lead2_spec_decision WHERE gate = ? AND approval = ?", (gate, approval),
            ).fetchone():
                return
            snapshot = self.snapshot(revision)
            current = self.snapshot()
            if gate == "scope":
                if snapshot.mode != "scope-proposal" or snapshot.accepted:
                    raise ValueError("Bind scope approval to a pending proposal.")
                self._require_head(self.snapshot(snapshot.parent), current)
            else:
                pins = self.pins()
                if revision == 0 or gate in pins or (gate == "B" and "A" not in pins):
                    raise ValueError("Pin A then B, each once, on a saved spec.")
                self._require_head(snapshot, current)
            if gate == "A" and (edits := _accepted_defaults(request)):
                revision = self.apply(dict(revision=revision, mode="substantive", edits=edits,
                                           reason="Record defaults accepted at A"), author="owner").revision
            self._store.db.execute("INSERT INTO lead2_spec_decision VALUES (?, ?, ?)",
                                   (gate, revision, approval))

    def decision(self, gate: str, revision: int) -> str | None:
        row = self._store.db.execute(
            "SELECT approval FROM lead2_spec_decision WHERE gate = ? AND revision = ?",
            (gate, revision)).fetchone()
        return row[0] if row else None

    def observe(self, revision: int, *, promises: list[str], witness: str, commit: str,
                session: str, outcome: str, result: str) -> Observation:
        body = dict(promises=promises, witness=witness, commit=commit, session=session,
                    outcome=outcome, result=result)
        validate(body, OBSERVATION_BODY, "observation")
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit, re.IGNORECASE):
            raise ValueError("Use the observed candidate's full Git commit ID.")
        body["commit"] = commit.lower()
        with self._store.transaction():
            snapshot = self.snapshot(revision)
            if not snapshot.accepted:
                raise ValueError("Record an observation against an accepted revision.")
            graph = snapshot.graph
            if graph.nodes.get(witness, {}).get("kind") not in {"check", "walk"}:
                raise ValueError("Name a check or walk in the observed spec revision.")
            for promise in promises:
                if (graph.nodes.get(promise, {}).get("kind") != "promise"
                        or witness not in graph.follow(promise, PROOF)):
                    raise ValueError(f"Link {witness} to promise {promise} in the observed revision.")
                origins = graph.follow(promise, upstream=True)
                if not any(graph.nodes[ref]["kind"] == "dream" for ref in origins):
                    raise ValueError(f"Trace promise {promise} to the person's words before recording proof.")
            body["promises"] = list(dict.fromkeys(promises))
            row = self._store.db.execute("INSERT INTO lead2_observation(revision, body) VALUES (?, ?)",
                                         (revision, encode(body)))
            return _observation(row.lastrowid, revision, body)

    def observations(self) -> tuple[Observation, ...]:
        rows = self._store.db.execute("SELECT id, revision, body FROM lead2_observation ORDER BY id")
        return tuple(_observation(ref, revision, json.loads(body)) for ref, revision, body in rows)

    def _require_head(self, requested: Snapshot, current: Snapshot) -> None:
        if requested.revision != current.revision:
            raise Conflict(requested.revision, current.revision, Delta(requested.graph, current.graph))

    def scope_changes(self, delta: Delta) -> set[str]:
        """The exact changes that need separate scope approval."""
        pins = self.pins()
        if "A" not in pins:
            return set()
        locked = self.snapshot(pins["A"]).graph.nodes if "B" not in pins else None
        return {ref for ref in delta.nodes if (locked is None or ref in locked)
                and (delta.after.nodes.get(ref) or delta.before.nodes[ref])["kind"] == "promise"
                and not _presentation_only(delta.before.nodes.get(ref), delta.after.nodes.get(ref))}

    def _check_approval(self, gate: str, revision: int, approval: str) -> dict:
        receipt = self._record("approval", approval)
        request = self._record("decision_request", receipt.get("request"))
        words = self._record("user_words", receipt.get("userWords"))
        subject = request.get("subject", {})
        effects = {"approve", "waive"} if gate == "B" else {"approve"}
        if (request.get("gate") != gate or subject.get("lead2_revision") != revision
                or type(subject.get("lead2_revision")) is not int or not request.get("origin")
                or receipt.get("subject") != subject or receipt.get("effect") not in effects
                or words.get("request") != receipt["request"] or words.get("origin") != request.get("origin")
                or not words.get("words")):
            raise ValueError(f"Use the person's approval bound to {gate} revision {revision}.")
        return request

    def _record(self, kind: str, ref: str | None) -> dict:
        if isinstance(ref, str) and ref.startswith("r") and ref[1:].isascii() and ref[1:].isdigit():
            row = self._store.db.execute("SELECT body FROM record WHERE id = ? AND kind = ?",
                                         (ref[1:], kind)).fetchone()
            if row:
                return json.loads(row[0])
        raise ValueError(f"Use a stored {kind} record, got {ref!r}.")


def _accepted_defaults(request):
    edits = []
    for group in request.get("ask", {}).get("groups", []):
        if not group["id"].startswith("q"):
            continue
        option = next(item for item in group["options"] if item["id"] == group["default"])
        edits.append(dict(op="add", name=None, node=dict(
            kind="design", reason="accepted default", decision=f"{group['question']} {option['label']}",
            words=option["means"],
        )))
    return edits


def _wording_only(delta: Delta) -> bool:
    return not delta.links and all(_presentation_only(delta.before.nodes.get(ref), delta.after.nodes.get(ref))
                                   for ref in delta.nodes)


def _presentation_only(old, new):
    def tokens(text):
        return re.findall(r"\w+(?:[.,'’/-]\w+)*|[^\w\s.,;:!?()\"“”]", text.lower())

    return (old and new and old.get("words") == new.get("words")
            and all(tokens(old[key]) == tokens(value) for key, value in new.items()))


def _observation(ref: int, revision: int, body: dict) -> Observation:
    return Observation(ref, revision, **{**body, "promises": tuple(body["promises"])})
