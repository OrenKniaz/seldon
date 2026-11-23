"""C9/C19: person-facing and planner-facing views of the same spec."""

import re

from ..store import encode
from .contracts import NODE_BODIES
from .evidence import promise_status
from .graph import Delta
from .store import SpecStore


def render_person(spec: SpecStore, *, revision: int | None = None, commit: str | None = None,
                  since: int | None = None, detailed=False) -> str:
    snapshot = spec.snapshot(revision)
    graph = snapshot.graph
    status = promise_status(spec, snapshot.revision, commit)
    heading = "Spec" if snapshot.accepted else "Scope proposal"
    lines = [f"# {heading} · revision {snapshot.revision}"]
    if commit is not None:
        lines.append(f"Candidate {commit}")
    first = spec.snapshot(1).graph.nodes if snapshot.revision > 1 else graph.nodes
    replies = [n["words"] for ref, n in graph.nodes.items() if n["kind"] == "dream" and ref not in first]
    if replies:
        lines.extend(("", "## Your answers", *replies))
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
    if detailed:
        lines += ["", "## Spec details"]
        lines.extend(f"- {_details(node)}" for node in graph.nodes.values()
                     if node["kind"] not in {"dream", "story"})
    if since is not None:
        changes = Delta(spec.snapshot(since).graph, graph).describe()
        lines += ["", f"## Changes since revision {since}"]
        lines += [f"- {change}" for change in changes] or ["No spec changes."]
    return "\n".join(lines)


def scope_summary(spec, before, after):
    delta = Delta(before.graph, after.graph)
    nodes, links = spec.scope_changes(delta)
    lines = []
    for ref in sorted(nodes):
        old, new = delta.before.nodes.get(ref), delta.after.nodes.get(ref)
        if old is None:
            lines.append(f"- Added {_details(new)}")
        elif new is None:
            lines.append(f"- Removed {_details(old)}")
        else:
            for field, value in new.items():
                if old[field] != value:
                    label = field.replace("_", " ")
                    lines.append(f"- Changed {label} from “{old[field]}” to “{value}”")
    for source, target in sorted(links):
        added = (source, target) in delta.after.links
        graph = delta.after if added else delta.before
        start, end = graph.nodes[source], graph.nodes[target]
        verb = "Added" if added else "Removed"
        lines.append(f"- {verb} a link from {_short_description(start)} to {_short_description(end)}")
    return "\n".join(lines)


def _short_description(node):
    if node["kind"] == "dream":
        return "the owner’s words"
    field = next(iter(NODE_BODIES[node["kind"]]["properties"]))
    return f"“{node[field]}”"


def b_summary(graph, order, picked):
    def first_sentence(value):
        return re.split(r"(?<=[.!?])\s+", " ".join(value.split()), maxsplit=1)[0]

    promises = [f"- {first_sentence(node['result'])}" for node in graph.nodes.values()
                if node["kind"] == "promise"]
    outcomes = [first_sentence(item["outcome"]) for item in order]
    demos = [f"- Delivery {index}" for index, _ in enumerate(outcomes, 1)
             if picked.get(f"delivery-{index}", {}).get("optionId") == "show"]
    defaults = [f"- {' '.join(node['decision'].split('? ', 1)[-1].split())}" for node in graph.nodes.values()
                if node.get("reason") == "accepted default"]
    return "\n".join(["### Promises", *promises, "", "### Build order",
                      *(f"{index}. {outcome}" for index, outcome in enumerate(outcomes, 1)),
                      "", "### Demonstrations", *(demos or ["None selected."]),
                      "", "### Accepted defaults", *(defaults or ["None."])])


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


def planning_facts(graph, kickoff):
    """Render call-local handles. The corresponding IDs never leave the store."""
    prefixes = {"person": "p", "story": "s", "dream": "reply-", "promise": "v",
                "design": "d", "interface": "i", "check": "c", "walk": "w"}
    counts, aliases, replies, proposal = {}, {}, [], []
    for ref, node in graph.nodes.items():
        prefix = prefixes[node["kind"]]
        if node.get("words") == kickoff and prefix == "reply-":
            aliases[ref] = "brief"
            continue
        counts[prefix] = counts.get(prefix, 0) + 1
        handle = aliases[ref] = f"{prefix}{counts[prefix]}"
        if prefix == "reply-":
            replies.append(f"{handle}\n{node['words']}")
        else:
            proposal.append(f"[{handle}] {encode(node)}")
    children = {}
    for source, target in graph.links:
        children.setdefault(aliases[source], []).append(aliases[target])
    proposal.extend(f"{source} → {', '.join(targets)}" for source, targets in children.items())
    refs = {label: ref for ref, label in aliases.items()}
    return "\n".join(proposal) or "New product.", "\n\n".join(replies), refs


def planning_view(graph, *, detailed=False):
    lines = []
    for ref, story in graph.nodes.items():
        if story["kind"] != "story":
            continue
        roles = [graph.nodes[source]["role"] for source, target in graph.links
                 if target == ref and graph.nodes[source]["kind"] == "person"]
        lines.append(f"### {story['story']}")
        if roles:
            lines.append(" · ".join(roles))
        if detailed:
            following = graph.follow(ref) | {target for source, target in graph.links if source == ref}
            for child, node in graph.nodes.items():
                if child in following and node["kind"] != "story":
                    lines.append(f"- {_details(node)}")
    return "\n".join(lines)


def _details(node):
    fields = NODE_BODIES[node["kind"]]["properties"]
    details = " · ".join(f"{field.replace('_', ' ').capitalize()} — {value}"
                         for field in fields if (value := node[field]).strip())
    return f"**{node['kind'].capitalize()}** · {details}"
