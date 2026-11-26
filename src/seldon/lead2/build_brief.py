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

GUIDE = """Build the whole product in this folder.
Keep the code readable. A documented local start is enough unless the spec asks for an installer.
Test your own work as you go through the real user paths. Git-ignore runtime data, caches and test artifacts.
Use local samples for interruption, restart and restore. Report real-site limits with done. For web checks,
use the headless browser and local app helpers in checks/kit. Give the guide a tracked Python or Node
startup command that accepts loopback address, port and separate sample data as command-line arguments.
If a demo is the next return point, build through that delivery and return partial with its ID
before starting the remaining work. Seldon shows the saved build and resumes you automatically.
Report selected demos by delivery ID. Give one code_map row for each delivered promise ID, with actual
tracked module and test paths. Repeat shared paths across rows. Include the fresh-copy guide
and a sample job in the product.
Return done when the stories work in your own checks. Seldon walks the product independently after
`done`. That is Seldon's job, so keep building through local checks and never wait for it. Use partial
only for a chosen demo. Blocked requires a question the person must answer. Apply accepted changes and acknowledge them."""


def promise_refs(graph):
    refs = [ref for ref, node in graph.nodes.items() if node["kind"] == "promise"]
    prefixes = Counter(ref[:8] for ref in refs)
    return {ref[:8] if prefixes[ref[:8]] == 1 else ref: ref for ref in refs}


def brief(graph, order, demos, kickoff):
    words = {n["words"]: None for n in graph.nodes.values() if n["kind"] == "dream" and n["words"] != kickoff}
    lines = [kickoff.rstrip(), *words, "Stories and promises"]
    promises = {ref: node for ref, node in graph.nodes.items() if node["kind"] == "promise"}
    stories = list(_stories(graph))
    for story, roles, children in stories:
        lines += [story, *roles, *(_details(node) for ref, node in promises.items() if ref in children)]
    linked = {ref for _, _, children in stories for ref in children}
    lines += [_details(node) for ref, node in promises.items() if ref not in linked]
    design = {ref: n for ref, n in graph.nodes.items() if n["kind"] == "design"}
    lines += ["Design and stack", *(f"{n['decision']} — {n['words'] if 'owner_choice' in n else n['reason']}"
                                    for n in design.values()),
              *(n["contract"] for n in graph.nodes.values() if n["kind"] == "interface"),
              "Planned code and tests"]
    for label, ref in promise_refs(graph).items():
        related = graph.follow(ref, PROOF)
        lines += [f"{label}: {promises[ref]['result']}",
                  *(node["decision"] for key, node in design.items() if key in related),
                  *(_details(node) for key, node in graph.nodes.items()
                    if key in related and node["kind"] == "check")]
    lines += ["Build order", *(f"{ref}: {name}" for ref, name in order.items()),
              "Chosen demos", *(demos or ["None chosen."]), "How we work", GUIDE]
    return "\n\n".join(lines)


def question(kind, schema, prompt, **facts):
    return {"kind": kind, "schema": schema, "prompt": prompt, "facts": facts, "execution": {},
            "renderContext": {"lead2": True, "flatAnswer": True}}


def normalise(body, schema):
    """Use reported fields that carry meaning and default omissions before consumption."""
    if not isinstance(body, dict):
        body = {"note": str(body or "").strip()}
    if isinstance(body.get("answer"), dict):
        body = body["answer"]
    return {key: _field(body.get(key), field) for key, field in schema["properties"].items()}


def _field(value, field):
    if field["type"] == "array":
        rows = value if isinstance(value, list) else [value]
        item = field["items"]
        kind = dict if item["type"] == "object" else str
        return [normalise(row, item) if kind is dict else row.strip()
                for row in rows if isinstance(row, kind) and (kind is dict or row.strip())]
    if field["type"] == "boolean":
        return str(value).lower() == "true"
    text = value.strip() if isinstance(value, str) else ""
    if "enum" in field:
        default = "partial" if "partial" in field["enum"] else "pending"
        return text.lower() if text.lower() in field["enum"] else default
    return text or ("Continue the unfinished work." if field.get("minLength") else "")
