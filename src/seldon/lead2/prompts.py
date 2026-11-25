"""C1/C6/C9/C11: the product briefs and typed planning answers."""

from ..questions import labelled
from ..schema import TEXT, array, choice, obj
from .contracts import OPTION_FIELDS, SPEC_EDITS

SIZES = {"micro": "Minutes of agent work.", "small": "An hour or two of agent work.",
         "medium": "Days of agent work across sessions.", "big": "Weeks or months of agent work."}
QUESTION_FIELDS = dict(question=TEXT, recommended=TEXT, recommended_means=TEXT,
                       alternative=TEXT, alternative_means=TEXT)
QUESTIONS = array(obj(**QUESTION_FIELDS))
MOCKUPS = array(obj(id=TEXT, label=TEXT, means=TEXT))
DELIVERY = array(obj(outcome=TEXT, recommend_demo={"type": "boolean"}))
ACCESS = array(obj(need=TEXT, supplier={"type": "string"}))
ALIGN = obj(aligned={"type": "boolean"}, size=choice(*SIZES), size_reason=TEXT, questions=QUESTIONS,
            **{kind: SPEC_EDITS[kind] for kind in ("person", "story", "retire", "link", "unlink")})
STACK = obj(stack=SPEC_EDITS["design"], questions=QUESTIONS)
REVIEW = obj(findings=TEXT, unresolved_issue={"type": "string"},
             questions=array(obj(id=TEXT, recommended_id=TEXT, alternative_id=TEXT, **QUESTION_FIELDS)),
             **OPTION_FIELDS)
TASK = {
    "LEAD2-ALIGN": """Start reason with one real job in the owner's world from start to finish.
Use the walk to cover functions, operation (hosting, users, roles, logins, backups, offline, connections,
cost) and appearance (device, style, key screen, language, direction, accessibility, reference).
Preserve exact values, handoffs, races and recovery. Ask only unanswered consequential choices.
Use the size fields, never a product question about size. Honor the chosen size and scope.
Link brief and relevant earlier reply refs to each short-titled story. Link people to their stories.
The owner decides readiness.""",
    "LEAD2-STACK": """Recommend the stack for the accepted vision. Give each choice a plain one-line reason
and cite the need that drove it in words. For a technical owner offer concrete alternatives with defaults.
For a plain owner explain the recommendation in business language. Incorporate the owner's latest reply.""",
    "LEAD2-SPEC": """Explain identities, state changes and design. Respect the approved stack and link it to
the promises it serves. Order outcomes and suggest demos. For a product with a user interface, give a few
quick visual directions for the key screens in mockups: describe layout, interaction and appearance so
the owner can pick or mix them. Use an empty list when there is no visual choice to make.""",
    "REVIEW": """What could disappear while this still passes? Fix missing behaviour and proof from the
brief and owner replies. If given a concrete unresolved issue, focus on that issue. Leave unresolved_issue
empty when no further independent review is needed. Otherwise name one concrete gap still to resolve.
Ask only for unresolved material product choices. Give each question and each option a distinct ID.
Put ordinary named edits for each option in the option_* rows using its option_id. Keep those edits
out of common edits. Leave build_order and access empty unless changed.""",
}
SPEC_GUIDE = """Link each promise to planned code modules (design rows) and named tests with observable
results (check rows). List build and judge access with suppliers; empty supplier means can't test here.
Derive access from the planned walk.
Leave access empty to keep a prior list or give the full revised list."""
EDIT_GUIDE = """Use a short new ref to add or a shown ref to amend, one row per changed node.
Retire only superseded nodes. Group links by source; replacements inherit old proof unless unlinked.
Link brief, relevant earlier replies and roles to stories.
Link stories to promises and promises to design and proof.
Leave unused arrays empty. Each question names two concrete options; code adds Other."""
PROMISE_GUIDE = """Give each promised outcome its own action, result and failure condition. Preserve exact
owner choices. Plan concrete walks that show what each affected person can see and do through relevant
interruptions and recovery. Exercise separately usable outputs individually. When one person changes a
setting, have the affected person verify what changes downstream."""


def question(kind, words, current, *, qualification=None, **facts):
    if qualification:
        fields = dict(amendments=array(obj(ref=TEXT, result=TEXT, not_met_if=TEXT)))
        prompt = ("Amend only existing promises explicitly named by the B condition. Keep every other "
                  "clause verbatim. Put the condition in the promise result and failure condition.")
        facts = {"B condition": qualification}
    elif "Resolve only this review reply" in facts:
        fields = SPEC_EDITS
        prompt = ("Apply only the owner's review reply to the current spec. Preserve recorded choices "
                  "and unrelated nodes and links.\n\n" + EDIT_GUIDE)
    else:
        prompt = TASK[kind]
        if kind == "LEAD2-STACK":
            fields = STACK["properties"]
        elif kind == "LEAD2-ALIGN":
            fields = ALIGN["properties"]
            prompt += "\nUse short refs for new rows, shown refs for changes and links, and business terms."
        else:
            extra = REVIEW["properties"] if kind == "REVIEW" else {"mockups": MOCKUPS}
            fields = {**extra, **SPEC_EDITS, "build_order": DELIVERY, "access": ACCESS}
            prompt = "\n\n".join((prompt, SPEC_GUIDE, PROMISE_GUIDE, EDIT_GUIDE))
    return {"kind": kind, "execution": {}, "schema": obj(answer=obj(reason=TEXT, **fields)),
            "renderContext": {"lead2": True},
            "prompt": prompt,
            "facts": {"Owner's words": words, "Current proposal": current, **facts}}


def render(question):
    if question["renderContext"].get("flatAnswer"):
        prompt, facts = question["prompt"], labelled(question["facts"])
        if question["kind"] == "WORK":
            from ..desktop import WRITE_RULE
            rule = "" if question["execution"].get("session") else WRITE_RULE
            return "\n\n".join(filter(None, (prompt, facts, rule))), (
                "Work here. Keep checks headless and generated files out of Git. Do not commit.")
        if question["kind"] == "LEAD2-WALK":
            return prompt + "\n\n" + facts, (
                "Walk the real product headlessly and return the typed verdict. "
                "Use the disposable checkout. Never modify product source, open a visible window or commit.")
        return prompt + "\n\n" + facts, "Read the supplied facts and return one typed decision."
    facts = {**question["facts"]}
    prompt = "\n\n".join((facts.pop("Owner's words"), question["prompt"], labelled(facts)))
    return prompt, "Plan in the typed answer. Do not write product code or open desktop windows."
