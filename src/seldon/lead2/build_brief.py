"""C1/C9/C13: the plain builder brief and flat build answers."""

from collections import Counter

from ..schema import REFS, TEXT, array, choice, obj
from .graph import PROOF
from .views import _details, _stories

STRING = {"type": "string"}
BOOLEAN = {"type": "boolean"}
WORK = obj(outcome=choice("done", "partial", "blocked"), note=TEXT, acknowledged=BOOLEAN, demos=REFS,
           code_map=array(obj(promise=STRING, modules=REFS, tests=REFS)))
STEER = obj(direction=TEXT, questions=array(obj(question=TEXT, recommended=TEXT, recommended_means=TEXT,
                                               alternative=TEXT, alternative_means=TEXT)))
END_REVIEW = obj(notes=REFS)

GUIDE = """Build and test the whole product here with disposable data and headless checks from checks/kit.
Example complete handoff: README.md includes a sample job and `python app.py --host 127.0.0.1 --port 3000 --data demo.db`.
The tracked entry point accepts loopback, port and sample data. Boot from a clean copy without terminal input.
Try that exact route on a fresh copy. If dependencies or compiled assets are needed, the preview entry
point prepares them and the guide's clean install works with the committed lockfile (e.g. npm ci).
Then return {"outcome":"done","note":"README.md and fresh-copy start checked.","acknowledged":true,
"demos":[],"code_map":[{"promise":"abcd1234","modules":["app.py"],"tests":["checks/saves.cjs"]}]}.
Use the shown promise IDs and tracked paths. Repeat shared paths. Apply and acknowledge changes.
Use partial only at a chosen demo, before later work, with its delivery ID. Blocked needs an owner's question.
Return done after your own local checks, with any real-site limits. Seldon owns the independent walk."""


def promise_refs(graph):
    refs = [ref for ref, node in graph.nodes.items() if node["kind"] == "promise"]
    prefixes = Counter(ref[:8] for ref in refs)
    return {ref[:8] if prefixes[ref[:8]] == 1 else ref: ref for ref in refs}


def brief(graph, order, demos, kickoff):
    words = {n["words"]: None for n in graph.nodes.values() if n["kind"] == "dream" and n["words"] != kickoff}
    lines = [kickoff.rstrip(), *words, "Stories and promises"]
    promises = {ref: node for ref, node in graph.nodes.items() if node["kind"] == "promise"}
    linked = set()
    for story, roles, children in _stories(graph):
        lines += [story, *roles, *(_details(node) for ref, node in promises.items() if ref in children)]
        linked.update(children)
    lines += [_details(node) for ref, node in promises.items() if ref not in linked]
    hard = {case for ref, node in graph.nodes.items()
            if node["kind"] == "story" and node.get("priority", "must") == "must"
            for case in graph.follow(ref) | {b for a, b in graph.links if a == ref}
            if graph.nodes[case]["kind"] in ("check", "walk")}
    if hard:
        lines += ["Test these yourself before you return done", *(_details(n) for r, n in graph.nodes.items() if r in hard)]
    lines += ["Design and stack", *(f"{n['decision']} — {n['words'] if 'owner_choice' in n else n['reason']}"
                                    for n in graph.nodes.values() if n["kind"] == "design"),
              *(n["contract"] for n in graph.nodes.values() if n["kind"] == "interface"),
              "Promise IDs for your code map"]
    for label, ref in promise_refs(graph).items():
        related = graph.follow(ref, PROOF)
        lines += [f"{label}: {promises[ref]['result']}",
                  *(_details(node) for key, node in graph.nodes.items()
                    if key in related - hard and node["kind"] == "check")]
    lines += ["Build order", *(f"{ref}: {name}" for ref, name in order.items()),
              "Chosen demos", *(demos or ["None chosen."]), "How we work", GUIDE]
    return "\n\n".join(lines)


def question(kind, schema, prompt, **facts):
    return {"kind": kind, "schema": schema, "prompt": prompt, "facts": facts, "execution": {},
            "renderContext": {"lead2": True, "flatAnswer": True}}


def normalise(body, schema):
    """Use reported fields that carry meaning and default omissions before consumption."""
    body = body if isinstance(body, dict) else {"note": str(body or "").strip()}
    body = body["answer"] if isinstance(body.get("answer"), dict) else body
    return {key: _field(body.get(key), field) for key, field in schema["properties"].items()}


def _field(value, field):
    if field["type"] == "array":
        rows = value if isinstance(value, list) else [value]
        item = field["items"]
        if item["type"] == "object":
            return [normalise(row, item) for row in rows if isinstance(row, dict)]
        return [row.strip() for row in rows if isinstance(row, str) and row.strip()]
    if field["type"] == "boolean":
        return str(value).lower() == "true"
    text = value.strip() if isinstance(value, str) else ""
    if "enum" in field:
        default = "partial" if "partial" in field["enum"] else "pending"
        return text.lower() if text.lower() in field["enum"] else default
    return text or ("Continue the unfinished work." if field.get("minLength") else "")
