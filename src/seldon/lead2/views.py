"""C9/C19: person-facing and planner-facing views of the same spec."""

import re
from itertools import groupby
from os.path import commonprefix

from ..store import encode
from .contracts import NODE_BODIES
from .evidence import promise_status
from .graph import Delta, review_issues
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
        detail = f"\n  Not met if {node['not_met_if']}" if detailed else ""
        return f"- **{node['action']}** — {node['result']} ({status[ref]}){detail}"

    for story, roles, children in _stories(graph):
        lines += ["", f"## {story}", *roles]
        promises = children & status.keys()
        shown.update(promises)
        lines.extend(promise_line(ref) for ref in graph.nodes if ref in promises)
    if unlinked := status.keys() - shown:
        lines += ["", "## Promises without a story"]
        lines.extend(promise_line(promise) for promise in sorted(unlinked))
    if detailed:
        lines += ["", "## Spec details"]
        lines.extend(f"- {_details(node)}" for node in graph.nodes.values()
                     if node["kind"] not in {"dream", "story", "promise"})
    if since is not None:
        changes = Delta(spec.snapshot(since).graph, graph).describe()
        lines += ["", f"## Changes since revision {since}"]
        lines += [f"- {change}" for change in changes] or ["No spec changes."]
    return "\n".join(lines)


def scope_summary(before, after):
    delta = Delta(before.graph, after.graph)
    lines = []
    for ref in sorted(delta.nodes, key=lambda ref: (ref in delta.before.nodes, ref)):
        old, new = delta.before.nodes.get(ref), delta.after.nodes.get(ref)
        if (new or old)["kind"] != "promise":
            continue
        label = "Added" if old is None else "Removed" if new is None else "Updated"
        lines.append(f"- {label} {_details(new or old)}")
        if old and new:
            lines.append(f"  - Previously {_details(old)}")
    return "\n".join(lines)


def _outputs(nodes, revised):
    for node in sorted(nodes, key=lambda node: node["result"].casefold()):
        line = " ".join(node["result"].split())
        match = re.fullmatch(r"(.+?) (screen|print(?: view)?|CSV|export)\s*[:—]\s*(.+)", line, re.I)
        heading, label, body = match.groups() if match else ("", "", line)
        if revised:
            body += f" · Not met if {' '.join(node['not_met_if'].split())}"
        yield heading, label, body


def b_summary(graph, order, picked, *, revised=False):
    results = {ref: node for ref, node in graph.nodes.items() if node["kind"] == "promise"}
    promises = []
    for story, _, children in (*_stories(graph), ("Promises without a story", [], results.keys())):
        if refs := children & results.keys():
            promises.extend(("", f"#### {story.split('. ', 1)[0]}"))
            outputs = _outputs(map(results.pop, refs), revised)
            for heading, group in groupby(outputs, key=lambda row: row[0]):
                rows = [(label, body) for _, label, body in group]
                if heading and len(rows) == 1:
                    label, body = rows[0]
                    promises.append(f"- {heading} {label} — {body}")
                    continue
                shared = commonprefix([body for _, body in rows]).rpartition(" ")[0] if len(rows) > 1 else ""
                if heading:
                    promises.append(f"- **{heading}**{f' · {shared}' if shared else ''}")
                for label, body in rows:
                    tail = body[len(shared):].lstrip() if heading else body
                    promises.append(f"  - {label}{f' · {tail}' if tail else ''}" if heading else f"- {tail}")
    demos = [f"- Delivery {index}" for index in range(1, len(order) + 1)
             if picked.get(f"delivery-{index}", {}).get("optionId") == "show"]
    defaults = [f"- {' '.join(node['decision'].split('? ', 1)[-1].split())}" for node in graph.nodes.values()
                if node.get("reason") == "accepted default"]
    gaps = review_issues(graph)
    return "\n".join(["### Promises", *promises, "", "### Build order",
                      *(f"{i}. {' '.join(item['outcome'].split())}" for i, item in enumerate(order, 1)),
                      "", "### Demonstrations", *(demos or ["None selected."]),
                      "", "### Accepted defaults", *(defaults or ["None."]),
                      *(["", "### Trace gaps", *(f"- {gap}" for gap in gaps)] if gaps else [])])


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


def planning_view(graph):
    lines = []
    for story, roles, children in _stories(graph):
        lines += [f"### {story}", *roles]
        lines.extend(f"- {_details(node)}" for ref, node in graph.nodes.items() if ref in children)
    return "\n".join(lines)


def _stories(graph):
    for ref, story in graph.nodes.items():
        if story["kind"] == "story":
            roles = [graph.nodes[source]["role"] for source, target in graph.links
                     if target == ref and graph.nodes[source]["kind"] == "person"]
            direct = {target for source, target in graph.links if source == ref}
            yield story["story"], [" · ".join(roles)] if roles else [], (graph.follow(ref) - {ref}) | direct


def _details(node):
    fields = NODE_BODIES[node["kind"]]["properties"]
    details = " · ".join(f"{field.replace('_', ' ').capitalize()} — {value}"
                         for field in fields if (value := node[field]).strip())
    return f"**{node['kind'].capitalize()}** · {details}"
