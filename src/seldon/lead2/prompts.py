"""C1/C6/C9/C11: the product briefs and typed planning answers."""

from ..questions import labelled
from ..schema import OPTION, REFS, TEXT, array, choice, obj
from .contracts import EDIT_ROWS, NODE_BODIES, STRING, answer_schema

SIZES = {"micro": "Minutes of agent work.", "small": "An hour or two of agent work.",
         "medium": "Days of agent work across sessions.", "big": "Weeks or months of agent work."}
QUESTIONS = array(obj(question=TEXT, options=array(OPTION, 2)))
ALIGN = answer_schema(reason=TEXT, aligned={"type": "boolean"}, size=choice(*SIZES), size_reason=TEXT,
                      people=array(obj(ref=TEXT, role=TEXT)),
                      stories=array(obj(ref=TEXT, story=TEXT, people=REFS, sources=REFS)), retire=REFS,
                      questions=QUESTIONS)
DELIVERY = obj(outcome=TEXT, separate_session_reason=STRING, recommend_demo={"type": "boolean"})
SPEC = answer_schema(reason=TEXT, edits=EDIT_ROWS, build_order=array(DELIVERY, 1))
REVIEW = answer_schema(reason=TEXT, edits=EDIT_ROWS, findings=TEXT, questions=QUESTIONS,
                       build_order=array(DELIVERY))
TASK = {
    "LEAD2-ALIGN": """Help the person shape the product. Keep their exact values. Improve the idea and
include people working together, handoffs, simultaneous actions, and recovery where relevant.
Ask every remaining choice a capable builder could take two ways that the person would care about.
Offer concrete options, best first, including better ideas where useful. The interface adds Other.
Say whether the vision seems ready. The person decides when it is.
Size by agent effort: micro minutes, small 1–2 hours, medium days, big weeks or months.
Honor the person's chosen size without changing their requirements.

Return only people and stories that changed. Use shown references for existing rows or short names
for new rows. For each changed story list the people involved and the words it draws on. Use brief
for the opening words and the shown reply references for decisions. Retire only superseded rows.

Example question: How should the incoming attendant follow up on an overdue key?
- Show the borrower and due time so they can follow up without handover notes.
- Show only the key so staff can use their existing log for borrower details.""",
    "LEAD2-SPEC": """Specify the whole agreed product. Each promise names an action, an observable
result and what counts as failure. Link it through design or interfaces to checks and walks.
Explain design choices and reasons. Model the identities, lifetimes, state transitions and
agreements in the person's world. Preserve accepted defaults and cover them in applicable walks.
Keep the agreed stories and quote the person's choices exactly. Keep checks in plain language.
Give the walker roles, setup, steps and expected results, including overlap,
interruption, resumption and how a new person tries a fresh copy.
For each real-world identity in the data model (a night, a session, an order), include a walk
where an earlier action arrives late or is retried after that identity has changed.
Order working outcomes. Keep one builder session where feasible and explain any real split.
Suggest useful demonstrations. The person chooses which to see. A demonstration can happen within
the same builder session. Leave separate_session_reason empty when the same session continues.""",
    "REVIEW": """Independently trace every promise back to the person's words, and those words forward
to promises. Include chosen options, specifics, constraints, quality and distinct outputs.
Check designs, checks, walks and build order for omissions, misleading proof and likely misreadings.
Repair concrete gaps and keep accepted defaults intact. Leave build_order empty unless it needs revision.
Ask the person only about
material choices their words cannot settle. Offer concrete options with the recommendation first.
The interface adds Other. Explain what you checked and found.""",
}
EDIT_GUIDE = """Each edit is one row. For add/amend, use ref_or_source as a new short name or
shown reference, and node_json as the full node object encoded as text. For retire name the
reference. For link/unlink use ref_or_source and target. Leave unused text empty.
Link words and roles to stories and stories to promises. Give each promise a design and link its
checks and walks to the promise or to its design or interface. Checks can also link to walks.
Stories and roles can link directly to walks for context."""
NODE_FIELDS = "Node fields in addition to kind: " + "; ".join(
    f"{kind} ({', '.join(body['properties'])})" for kind, body in NODE_BODIES.items() if kind != "dream"
)


def question(kind, words, current, **facts):
    task = TASK[kind] + ("\n\n" + EDIT_GUIDE + "\n" + NODE_FIELDS if kind != "LEAD2-ALIGN" else "")
    schema = {"LEAD2-ALIGN": ALIGN, "LEAD2-SPEC": SPEC, "REVIEW": REVIEW}[kind]
    return {"kind": kind, "execution": {}, "schema": schema, "renderContext": {"lead2": True},
            "prompt": task, "facts": {"Owner's words": words, "Current proposal": current, **facts}}


def render(question):
    facts = {**question["facts"]}
    prompt = "\n\n".join((facts.pop("Owner's words"), question["prompt"], labelled(facts)))
    return prompt, "Plan in the typed answer. Do not write product code or open desktop windows."
