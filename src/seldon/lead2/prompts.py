"""C1/C6/C9/C11: the product briefs and typed planning answers."""

from ..questions import labelled
from ..schema import TEXT, array, choice, obj
from .contracts import EDIT_FIELDS

SIZES = {"micro": "Minutes of agent work.", "small": "An hour or two of agent work.",
         "medium": "Days of agent work across sessions.", "big": "Weeks or months of agent work."}
QUESTIONS = array(obj(question=TEXT, label=TEXT, means=TEXT))
DELIVERY = array(obj(outcome=TEXT, recommend_demo={"type": "boolean"}))
ALIGN = obj(aligned={"type": "boolean"}, size=choice(*SIZES), size_reason=TEXT, questions=QUESTIONS)
SPEC = obj()
REVIEW = obj(findings=TEXT, questions=QUESTIONS)
TASK = {
    "LEAD2-ALIGN": """Improve the idea while keeping exact values, handoffs, races and recovery.
Ask only consequential unresolved choices. Read prior replies, carry accepted recommendations
into stories, and never repeat answered questions. Offer concrete choices, best first. Say whether
the vision is ready; the owner decides. Honor their chosen size: micro minutes, small 1–2 hours,
medium days, big weeks or months. Return changed roles and stories linked to the owner's words;
start each story with a short title sentence. Fold a ready micro or small spec and build order here;
otherwise leave the detailed spec arrays empty.""",
    "LEAD2-SPEC": """Explain identities, state changes and design. Preserve exact owner choices.
Walk overlap, recovery, fresh setup and stale retries. Order outcomes in one session and suggest demos.""",
    "REVIEW": """Trace owner words to promises and back. Repair bundles, missing deliverables and
material gaps using the existing walks first. Verify accepted defaults and design against checks
and walks; committed retries return their original outcome. Repair without changing accepted defaults.
Leave build_order empty unless changed. Explain findings; ask only consequential unresolved choices.""",
}
EDIT_GUIDE = """Use one row per changed node, in its kind's array. Use a short new ref to add
or a shown ref to amend. Retire only superseded nodes. Group link and unlink targets by source.
For splits, replaces.source is the old promise and targets inherit its proof links; unlink to opt out.
Link brief and reply refs and roles to stories, stories to promises, promises to design and proof.
Leave unused arrays empty. List options separately, recommendation first, label matching meaning."""
PROMISE_GUIDE = """One promise per independently verifiable output or workflow, with its action,
result and failure. Split each separately named resource view, administration editor and report format.
Split board updates from notices and hosting from migrations. Requested artifacts such as automated
integration/E2E tests per workflow are promises, distinct from checks within the plan.
Walk user handoffs with the receiving role observing the result and setting changes through their
effect in the affected workflow. A direct check suffices for a standalone artifact such as a sample.
Use terse B results: output name, exact filters, labels and distinguishing conditions, including
qualified replies. Name sibling formats 'Report name screen — ...', 'Report name print — ...',
and 'Report name CSV — ...'. Start their results with identical shared conditions."""


def question(kind, words, current, **facts):
    fields = {"LEAD2-ALIGN": ALIGN, "LEAD2-SPEC": SPEC, "REVIEW": REVIEW}[kind]["properties"]
    schema = obj(answer=obj(reason=TEXT, **fields, **EDIT_FIELDS, build_order=DELIVERY))
    return {"kind": kind, "execution": {}, "schema": schema, "renderContext": {"lead2": True},
            "prompt": "\n\n".join((TASK[kind], PROMISE_GUIDE, EDIT_GUIDE)),
            "facts": {"Owner's words": words, "Current proposal": current, **facts}}


def render(question):
    facts = {**question["facts"]}
    prompt = "\n\n".join((facts.pop("Owner's words"), question["prompt"], labelled(facts)))
    return prompt, "Plan in the typed answer. Do not write product code or open desktop windows."
