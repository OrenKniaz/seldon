"""C9/C10/C26: candidate-bound freshness and observation traceability."""

from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from .graph import PROOF, Delta, Graph
from .store import Observation, Snapshot, SpecStore

Status = Literal["planned", "built", "proven-current", "failed", "stale",
                 "not-verifiable-here", "changed-by-person"]


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


def promise_status(spec: SpecStore, revision: int, commit: str | None) -> dict[str, Status]:
    if commit is not None:
        commit = commit.lower()
    snapshot = spec.snapshot(revision)
    history = _history(spec, snapshot)
    position = {item.revision: index for index, item in enumerate(history)}
    reopened: dict[str, Snapshot] = {}
    for before, after in pairwise(history):
        if after.mode != "wording-only":
            for promise in Delta(before.graph, after.graph).reopens():
                reopened[promise] = after

    by_promise: dict[str, list[Observation]] = {}
    for observation in spec.observations():
        if observation.revision in position:
            for promise in observation.promises:
                by_promise.setdefault(promise, []).append(observation)

    result: dict[str, Status] = {}
    graph = snapshot.graph
    for promise, node in graph.nodes.items():
        if node["kind"] != "promise":
            continue
        change = reopened.get(promise, history[0])
        links = graph.follow(promise, PROOF)
        previous = by_promise.get(promise, [])
        current = [obs for obs in previous if obs.commit == commit and obs.witness in links
                   and position[obs.revision] >= position[change.revision]]
        # Re-observing a witness supersedes its old result. Build records carry no verdict.
        latest = {obs.witness: obs.outcome for obs in current if obs.outcome != "built"}
        outcomes = set(latest.values())
        if "failed" in outcomes:
            result[promise] = "failed"
        elif "passed" in outcomes:
            result[promise] = "proven-current"
        elif "not-verifiable-here" in outcomes:
            result[promise] = "not-verifiable-here"
        elif previous and not current:
            result[promise] = "stale"
        elif change.mode == "scope-proposal" and change.accepted:
            result[promise] = "changed-by-person"
        else:
            result[promise] = "built" if current else "planned"
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
        if after.mode == "scope-proposal" and Delta(before.graph, after.graph).reopens() & refs:
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
