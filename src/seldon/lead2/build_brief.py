"""C1/C9/C13: the plain builder brief and flat build answers."""

from ..schema import REFS, TEXT, array, choice, obj
from .graph import PROOF
from .views import _details, _stories

STRING = {"type": "string"}
BOOLEAN = {"type": "boolean"}
WORK = obj(outcome=choice("done", "partial", "blocked"), note=TEXT, acknowledged=BOOLEAN, demos=REFS,
           chunks=array(obj(delivery=STRING, command=REFS)),
           code_map=array(obj(promise=STRING, modules=REFS, tests=REFS)))
STEER = obj(direction=TEXT, questions=array(obj(question=TEXT, recommended=TEXT, recommended_means=TEXT,
                                               alternative=TEXT, alternative_means=TEXT)))
VERDICT = choice("works", "broken", "can't-test-here", "pending")
CHUNK = obj(verdict=VERDICT, reason=STRING, limits=REFS)

GUIDE = """Build the whole product in this folder.
Keep the implementation readable, with one clear way to use and run it.
Write and run the planned tests through real behavior. Git-ignore runtime data, caches and test artifacts.
Use local sample data for ordinary interruption, restart and restore checks. For web checks,
use the headless browser and local app helpers in checks/kit. Give the guide the standard
startup command and explain how to choose a loopback address, port and separate sample data location.
Continue through the build order without waiting for a review or demo.
When a delivery is newly finished or fixed, report its delivery ID and runnable check argv, one row per check.
Report selected demos by delivery ID when ready. On done or a selected demo, give the full map of actual
module and test paths for the delivered promises, keyed by promise ID. Include the fresh-copy guide
and a sample job in the product.
Return done on completion, partial with the next step, or blocked with the actual question.
Apply any accepted changes in the message and set acknowledged to true when you have taken them on."""


def brief(graph, order, demos, kickoff):
    words = {n["words"]: None for n in graph.nodes.values() if n["kind"] == "dream" and n["words"] != kickoff}
    lines = [kickoff.rstrip(), *words, "Stories and promises"]
    promises = {ref: node for ref, node in graph.nodes.items() if node["kind"] == "promise"}
    stories = list(_stories(graph))
    for story, roles, children in stories:
        lines += [story, *roles, *(_details(node) for ref, node in promises.items() if ref in children)]
    linked = {ref for _, _, children in stories for ref in children}
    lines += [_details(node) for ref, node in promises.items() if ref not in linked]
    design = {ref: n["decision"] for ref, n in graph.nodes.items() if n["kind"] == "design"}
    lines += ["Design and stack", *(f"{c} — {graph.nodes[ref]['reason']}" for ref, c in design.items()),
              *(n["contract"] for n in graph.nodes.values() if n["kind"] == "interface"),
              "Planned code and tests"]
    for ref, promise in promises.items():
        related = graph.follow(ref, PROOF)
        lines += [f"{ref}: {promise['result']}",
                  *(decision for key, decision in design.items() if key in related),
                  *(_details(node) for key, node in graph.nodes.items()
                    if key in related and node["kind"] == "check")]
    lines += ["Build order", *order,
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
