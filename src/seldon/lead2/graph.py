"""C9/C10: typed links, edits, and the impact of a snapshot delta."""

from dataclasses import dataclass, field
from uuid import uuid4

from .contracts import NODE_BODIES

PROOF = {
    ("promise", "design"), ("promise", "interface"), ("design", "interface"),
    ("design", "check"), ("design", "walk"), ("interface", "check"), ("interface", "walk"),
    ("promise", "check"), ("promise", "walk"), ("check", "walk"),
}
LINEAGE = PROOF | {("dream", "story"), ("story", "promise")}
CONTEXT = {("person", "story"), ("person", "walk"), ("story", "walk"), ("design", "person")}


@dataclass(frozen=True)
class Graph:
    nodes: dict[str, dict] = field(default_factory=dict)
    links: tuple[tuple[str, str], ...] = ()

    def kinds(self, link: tuple[str, str]) -> tuple[str, str]:
        source, target = link
        return self.nodes[source]["kind"], self.nodes[target]["kind"]

    def follow(self, node: str, edges=LINEAGE, *, upstream: bool = False) -> set[str]:
        """Follow one direction at a time so a shared check does not pull in sibling promises."""
        neighbors: dict[str, list[str]] = {}
        for link in self.links:
            if self.kinds(link) in edges:
                source, target = reversed(link) if upstream else link
                neighbors.setdefault(source, []).append(target)
        seen = {node}
        pending = [node]
        while pending:
            for target in neighbors.get(pending.pop(), ()):
                if target not in seen:
                    seen.add(target)
                    pending.append(target)
        return seen

    def select(self, refs: set[str]) -> "Graph":
        refs = refs | {source for source, target in self.links
                       if target in refs and self.kinds((source, target)) == ("story", "walk")}
        people = set()
        for link in self.links:
            source, target = link
            kinds = self.kinds(link)
            if target in refs and kinds in {("person", "story"), ("person", "walk")}:
                people.add(source)
            if source in refs and kinds == ("design", "person"):
                people.add(target)
        refs |= people
        return Graph({ref: self.nodes[ref] for ref in sorted(refs)},
                     tuple(link for link in self.links if set(link) <= refs))


def review_issues(graph):
    """Count missing trace relationships separately so review can measure progress."""
    issues = []
    for ref, node in graph.nodes.items():
        if node["kind"] == "story":
            sources = {graph.nodes[source]["kind"] for source, target in graph.links if target == ref}
            missing = {"owner's words": "dream", "role": "person"}
            issues.extend(f"Story has no {label} linked yet: {node['story']}"
                          for label, kind in missing.items() if kind not in sources)
            if graph.follow(ref) == {ref}:
                issues.append(f"Story has no promise linked yet: {node['story']}")
        elif node["kind"] == "promise":
            roots = {graph.nodes[parent]["kind"] for parent in graph.follow(ref, upstream=True)}
            witnesses = {graph.nodes[child]["kind"] for child in graph.follow(ref, PROOF)}
            missing = {"design", "check", "walk"} - witnesses
            if "dream" not in roots:
                missing.add("link to the owner's words")
            issues.extend(f"Promise has no {kind} yet: {node['result']}" for kind in sorted(missing))
    return issues or (["The spec has no stories yet."] if not any(
        node["kind"] == "story" for node in graph.nodes.values()) else [])


def edited(graph: Graph, batch: dict) -> tuple[Graph, dict[str, str]]:
    nodes = graph.nodes.copy()
    links = set(graph.links)
    names: dict[str, str] = {}
    additions = {}
    for index, edit in enumerate(batch["edits"]):
        if edit["op"] == "add":
            ref = uuid4().hex
            additions[index] = ref
            if edit["name"] is not None:
                name = "$" + edit["name"].strip().lstrip("$")
                if name == "$" or name in names:
                    raise ValueError(f"Give each named addition a distinct name, found {name!r}")
                names[name] = ref

    kinds = {ref: node["kind"] for ref, node in nodes.items()}
    kinds.update((ref, batch["edits"][index]["node"]["kind"]) for index, ref in additions.items())

    def resolve(name):
        ref = name if name in kinds else names.get("$" + name.strip().lstrip("$"), name)
        if ref not in kinds:
            raise ValueError(f"Unknown node {name!r}. Use an existing ID or a batch-local $name.")
        return ref

    # Declare additions first so links can refer forward to any named node in the batch.
    nodes.update((ref, batch["edits"][index]["node"]) for index, ref in additions.items())
    for edit in batch["edits"]:
        op = edit["op"]
        if op == "amend":
            ref = resolve(edit["id"])
            if ref not in nodes or kinds[ref] != edit["node"]["kind"]:
                raise ValueError(f"Amend an active node of kind {kinds[ref]} at {edit['id']}.")
            nodes[ref] = edit["node"]
        elif op == "retire":
            nodes.pop(resolve(edit["id"]), None)
        elif op in {"link", "unlink"}:
            source, target = resolve(edit["source"]), resolve(edit["target"])
            pair = (kinds[source], kinds[target])
            allowed = LINEAGE | CONTEXT
            if pair not in allowed:
                if pair[::-1] not in allowed:
                    raise ValueError(f"Connect nodes with a trace relation. {pair[0]} to {pair[1]} has none.")
                source, target = target, source
            if op == "link":
                links.add((source, target))
            else:
                links.discard((source, target))
    return Graph(nodes, tuple(sorted(link for link in links if set(link) <= nodes.keys()))), names


@dataclass(frozen=True)
class Delta:
    before: Graph
    after: Graph

    @property
    def nodes(self) -> set[str]:
        old, new = self.before.nodes, self.after.nodes
        return {ref for ref in old.keys() | new.keys() if old.get(ref) != new.get(ref)}

    @property
    def links(self) -> set[tuple[str, str]]:
        return set(self.before.links) ^ set(self.after.links)

    def describe(self, *, references=True) -> tuple[str, ...]:
        changes = []
        for ref in sorted(self.nodes):
            old, new = self.before.nodes.get(ref), self.after.nodes.get(ref)
            label = f" {ref}" if references else ""
            if old is None:
                changes.append(f"Added {new['kind']}{label}: {_body(new)}")
            elif new is None:
                changes.append(f"Retired {old['kind']}{label}: {_body(old)}")
            else:
                fields = [f"{key} from {old.get(key)!r} to {new.get(key)!r}"
                          for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]
                changes.append(f"Amended {new['kind']}{label}: " + ", ".join(fields))
        for source, target in sorted(self.links):
            verb = "Linked" if (source, target) in self.after.links else "Unlinked"
            graph = self.after if verb == "Linked" else self.before
            left, right = (_label(graph, ref, references) for ref in (source, target))
            changes.append(f"{verb} {left} → {right}")
        return tuple(changes)


def _body(node: dict) -> str:
    return " / ".join(str(value) for key, value in sorted(node.items()) if key != "kind")


def _label(graph: Graph, ref: str, references: bool) -> str:
    node = graph.nodes[ref]
    kind = node["kind"]
    field = next(iter(NODE_BODIES[kind]["properties"]))
    return f"{kind} {node[field]!r}" + (f" ({ref})" if references else "")
