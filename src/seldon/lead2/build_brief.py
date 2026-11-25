"""C1/C9/C13: the plain builder brief and flat build answers."""

from ..schema import REFS, TEXT, array, choice, obj
from .graph import PROOF
from .views import _details, _stories, delivery_name

STRING = {"type": "string"}
BOOLEAN = {"type": "boolean"}
WORK = obj(outcome=choice("done", "partial", "blocked"), note=TEXT, acknowledged=BOOLEAN, demos=REFS,
           chunks=array(obj(feature=TEXT, promise=STRING, module=STRING, test=STRING, command=REFS)),
           code_map=array(obj(promise=TEXT, module=STRING, test=STRING)))
STEER = obj(direction=TEXT, questions=array(obj(question=TEXT, recommended=TEXT, recommended_means=TEXT,
                                               alternative=TEXT, alternative_means=TEXT)))
CHUNK = obj(needs_fix=BOOLEAN, finding=STRING)

GUIDE = """Build the whole product in this folder.
Keep the implementation readable, with one clear way to use and run it.
Write and run the planned tests through real behavior. Git-ignore runtime data, caches and test artifacts.
Continue through the build order without waiting for a review or demo.
For each finished feature, report the build-order title, promise result, code paths, test paths and argv.
Name or add missing planned tests. Report selected demos by their exact outcome when ready.
Return done on completion, partial with the next step, or blocked with the actual question.
On done, map each exact promise result to actual relative module and test paths, one pair per row.
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
        lines += [promise["result"], *(decision for key, decision in design.items() if key in related),
                  *(_details(node) for key, node in graph.nodes.items()
                    if key in related and node["kind"] == "check")]
    lines += ["Build order", *(delivery_name(item) for item in order),
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
    if schema is CHUNK and "needs_fix" not in body:
        return {"needs_fix": True, "finding": "The review returned no assessment."}
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
        return text.lower() if text.lower() in field["enum"] else "partial"
    return text or ("Continue the unfinished work." if field.get("minLength") else "")
