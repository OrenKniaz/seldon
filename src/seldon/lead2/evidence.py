"""C9/C10/C26: candidate-bound freshness and observation traceability."""

from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from .graph import PROOF, Delta, Graph
from .store import Observation, Snapshot, SpecStore, promise_changes

Status = Literal["planned", "built", "proven-current", "failed", "stale",
                 "not-verifiable-here", "changed-by-person", "under review"]


@dataclass(frozen=True)
class Trace:
    observation: Observation
    snapshot: Snapshot
    graph: Graph
    approvals: dict[tuple[str, int], str]


def _history(spec: SpecStore, snapshot: Snapshot) -> list[Snapshot]:
    history = [snapshot]
    while snapshot.parent is not None:
        snapshot = spec.snapshot(snapshot.parent)
        history.append(snapshot)
    return history[::-1]


def _evidence(spec, snapshot, commit):
    history = _history(spec, snapshot)
    lineage = {item.revision for item in history}
    reopened = {promise: after for before, after in pairwise(history)
                for promise in promise_changes(Delta(before.graph, after.graph))}
    previous, current = set(), {}
    for obs in spec.observations():
        if obs.revision not in lineage:
            continue
        for ref in obs.promises:
            previous.add(ref)
            if (ref in snapshot.graph.nodes and obs.commit == commit
                    and obs.revision >= reopened.get(ref, history[0]).revision):
                current.setdefault(ref, []).append(obs)
    return reopened, previous, current


def current_observations(spec, snapshot, commit):
    """Keep the original witness and revision for evidence on the same candidate."""
    return {obs.id for rows in _evidence(spec, snapshot, commit)[2].values() for obs in rows}


def promise_status(spec: SpecStore, revision: int, commit: str | None) -> dict[str, Status]:
    snapshot = spec.snapshot(revision)
    reopened, previous, current = _evidence(spec, snapshot, commit.lower() if commit else None)
    result: dict[str, Status] = {}
    graph = snapshot.graph
    for promise, node in graph.nodes.items():
        if node["kind"] != "promise":
            continue
        change = reopened.get(promise)
        observations = current.get(promise, [])
        walk = next((obs for obs in reversed(observations) if obs.walk_verdict), None)
        outcomes = ({walk.outcome} if walk else
                    set({obs.witness: obs.outcome for obs in observations if obs.outcome != "built"}.values()))
        statuses = (("pending", "under review"), ("failed", "failed"), ("passed", "proven-current"),
                    ("not-verifiable-here", "not-verifiable-here"))
        if observed := next((status for outcome, status in statuses if outcome in outcomes), None):
            result[promise] = observed
        elif promise in previous and not observations:
            result[promise] = "stale"
        elif change and change.mode == "scope-proposal" and change.accepted:
            result[promise] = "changed-by-person"
        else:
            result[promise] = "built" if observations else "planned"
    return result


def _trace(spec: SpecStore, observation: Observation) -> Trace:
    snapshot = spec.snapshot(observation.revision)
    graph = snapshot.graph
    upstream = graph.follow(observation.witness, PROOF, upstream=True)
    refs = set()
    for promise in observation.promises:
        refs |= graph.follow(promise, upstream=True)
        refs |= graph.follow(promise, PROOF) & upstream
    history = _history(spec, snapshot)
    approvals = {}
    for before, after in pairwise(history):
        if after.mode == "scope-proposal" and promise_changes(Delta(before.graph, after.graph)) & refs:
            approval = spec.decision("scope", after.revision)
            if approval:
                approvals["scope", after.revision] = approval
    lineage = {item.revision for item in history}
    for gate, revision in spec.pins().items():
        if revision in lineage and (approval := spec.decision(gate, revision)):
            approvals[gate, revision] = approval
    return Trace(observation, snapshot, graph.select(refs), approvals)


def trace_observation(spec: SpecStore, observation_id: int) -> Trace:
    for observation in spec.observations():
        if observation.id == observation_id:
            return _trace(spec, observation)
    raise ValueError(f"Unknown observation {observation_id}.")


def trace_words(spec: SpecStore, dream: str) -> tuple[Trace, ...]:
    """Read historical observations using the word links present when each was recorded."""
    return tuple(trace for obs in spec.observations()
                 if (trace := _trace(spec, obs)).graph.nodes.get(dream, {}).get("kind") == "dream")
