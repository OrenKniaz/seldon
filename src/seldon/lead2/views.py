"""C9/C19: person-facing and planner-facing views of the same spec."""

from ..store import encode
from .evidence import promise_status
from .graph import Delta
from .store import SpecStore


def render_person(spec: SpecStore, *, revision: int | None = None, commit: str | None = None,
                  since: int | None = None) -> str:
    snapshot = spec.snapshot(revision)
    graph = snapshot.graph
    status = promise_status(spec, snapshot.revision, commit)
    heading = "Spec" if snapshot.accepted else "Scope proposal"
    lines = [f"# {heading} · revision {snapshot.revision}"]
    if commit is not None:
        lines.append(f"Candidate {commit}")
    shown = set()

    def promise_line(ref):
        node = graph.nodes[ref]
        return f"- **{node['action']}** — {node['result']} ({status[ref]})"

    for ref, node in graph.nodes.items():
        if node["kind"] == "story":
            lines += ["", f"## {node['story']}"]
            roles = [graph.nodes[source]["role"] for source, target in graph.links
                     if target == ref and graph.nodes[source]["kind"] == "person"]
            if roles:
                lines.append(" · ".join(roles))
            promises = [target for source, target in graph.links if source == ref and target in status]
            shown.update(promises)
            lines.extend(promise_line(promise) for promise in promises)
    if unlinked := status.keys() - shown:
        lines += ["", "## Promises without a story"]
        lines.extend(promise_line(promise) for promise in sorted(unlinked))
    if since is not None:
        changes = Delta(spec.snapshot(since).graph, graph).describe()
        lines += ["", f"## Changes since revision {since}"]
        lines += [f"- {change}" for change in changes] or ["No spec changes."]
    return "\n".join(lines)


def render_prompt(spec: SpecStore, *, revision: int | None = None, node: str | None = None,
                  commit: str | None = None) -> str:
    snapshot = spec.snapshot(revision)
    graph = snapshot.graph if node is None else spec.read_slice(node, snapshot.revision)[1]
    status = promise_status(spec, snapshot.revision, commit)
    # List words first, with stable references and each link rendered only once.
    nodes = dict(sorted(graph.nodes.items(), key=lambda pair: pair[1]["kind"] != "dream"))
    lines = [f"Revision {snapshot.revision} ({'accepted' if snapshot.accepted else 'proposed'})"]
    for ref, body in nodes.items():
        state = f" [{status[ref]}]" if ref in status else ""
        lines.append(f"{ref}{state} {encode(body)}")
    lines.extend(f"{source} -> {target}" for source, target in graph.links)
    return "\n".join(lines)
