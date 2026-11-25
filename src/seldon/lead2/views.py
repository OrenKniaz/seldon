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


def changed_promises(prior, current, *, detailed=False):
    for ref in sorted(Delta(prior, current).nodes, key=lambda ref: (ref in prior.nodes, ref)):
        old, new = prior.nodes.get(ref), current.nodes.get(ref)
        if (new or old)["kind"] != "promise":
            continue
        verb = "Added" if old is None else "Removed" if new is None else "Updated"
        yield f"- {verb} · " + (_details(new or old) if detailed else (new or old)["result"])
        if old and new:
            if detailed:
                yield f"  - Previously {_details(old)}"
            elif new["not_met_if"] != old["not_met_if"]:
                yield f"  - Not met if {new['not_met_if']}"


def b_changes(before, after, prior, current):
    changes = [f"- Your request · {node['words']}" for ref, node in after.graph.nodes.items()
               if node["kind"] == "dream" and ref not in before.graph.nodes]
    changes.extend(changed_promises(before.graph, after.graph))
    labels = {"build_order": "Build order", "access": "Access needs"}
    changes.extend(f"- {label} updated." for field, label in labels.items()
                   if prior.get(field, []) != current.get(field, []))
    return "\n".join(changes) or "No changes to promises, build order or access needs."


def _outputs(nodes):
    results = sorted((" ".join(node["result"].split()) for node in nodes), key=str.casefold)
    pattern = re.compile(r"(.+?) (screen|print(?: view)?|CSV|export)\s*[:—]\s*(.+)", re.I)
    parts = (match.groups() if (match := pattern.fullmatch(line)) else ("", "", line) for line in results)
    for heading, group in groupby(parts, key=lambda row: row[0]):
        rows = [(label, body) for _, label, body in group]
        if not heading or len(rows) == 1:
            yield from (f"- {heading} {label} — {body}" if heading else f"- {body}" for label, body in rows)
            continue
        shared = commonprefix([body for _, body in rows]).rpartition(" ")[0]
        yield f"- **{heading}**" + (f" · {shared}" if shared else "")
        for label, body in rows:
            tail = body[len(shared):].lstrip()
            yield f"  - {label}" + (f" · {tail}" if tail else "")


def delivery_name(delivery):
    return re.sub(r"^\d+[.)]\s+", "", " ".join(delivery["outcome"].split()))


def b_summary(graph, plan, groups, picked, *, changes=""):
    order = plan["build_order"]
    results = {ref: node for ref, node in graph.nodes.items() if node["kind"] == "promise"}
    promises = []
    for story, _, children in (*_stories(graph), ("Promises without a story", [], results.keys())):
        if refs := children & results.keys():
            promises += ["", f"#### {story.split('. ', 1)[0]}", *_outputs(map(results.pop, refs))]
    demos = [f"- Delivery {g['id'].removeprefix('delivery-')}" for g in groups
             if picked.get(g["id"], {}).get("optionId", g["default"]) == "show"]
    defaults = [f"- {' '.join(node['decision'].split('? ', 1)[-1].split())}" for node in graph.nodes.values()
                if node.get("reason") == "accepted default"]
    gaps = review_issues(graph)
    limit = "Can't test here"
    access = [f"- {row['need']} — {row['supplier'].strip() or limit}"
              for row in plan.get("access", [])]
    return "\n".join(["### Promises", *promises, "", "### Build order",
                      *(f"{i}. {delivery_name(item)}" for i, item in enumerate(order, 1)),
                      "", "### Demonstrations", *(demos or ["None selected."]),
                      "", "### Access needs", *(access or ["None identified."]),
                      "", "### Accepted defaults", *(defaults or ["None."]),
                      *(["", "### Changes since the last offer", changes] if changes else []),
                      *(["", "### Trace gaps", *(f"- {gap}" for gap in gaps)] if gaps else [])])


def render_prompt(spec: SpecStore, *, revision: int | None = None, node: str | None = None,
                  commit: str | None = None) -> str:
    snapshot = spec.snapshot(revision)
    graph = snapshot.graph if node is None else spec.read_slice(node, snapshot.revision)[1]
    status = promise_status(spec, snapshot.revision, commit)
    # List words first, with stable references and each link rendered only once.
    lines = [f"Revision {snapshot.revision} ({'accepted' if snapshot.accepted else 'proposed'})"]
    for ref, body in sorted(graph.nodes.items(), key=lambda pair: pair[1]["kind"] != "dream"):
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
        lines.extend(f"- {_details(node)}" for ref, node in graph.nodes.items()
                     if ref in children and node["kind"] != "design")
    designs = [f"- {node['decision']} — {node['reason']}" for node in graph.nodes.values()
               if node["kind"] == "design" and node["reason"] != "accepted default"]
    if designs:
        lines += ["", "### Design choices", *designs]
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
