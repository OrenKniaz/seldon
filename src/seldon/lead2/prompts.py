"""C1/C6/C9/C11: the product briefs and typed planning answers."""

from ..questions import labelled
from ..schema import TEXT, array, choice, obj
from .contracts import EDIT_FIELDS, OPTION_EDITS, SPEC_EDITS

SIZES = {"micro": "Minutes of agent work.", "small": "An hour or two of agent work.",
         "medium": "Days of agent work across sessions.", "big": "Weeks or months of agent work."}
QUESTIONS = array(obj(question=TEXT, recommended=TEXT, recommended_means=TEXT,
                      alternative=TEXT, alternative_means=TEXT))
DELIVERY = array(obj(outcome=TEXT, recommend_demo={"type": "boolean"}))
ACCESS = array(obj(need=TEXT, supplier={"type": "string"}))
ALIGN = obj(aligned={"type": "boolean"}, size=choice(*SIZES), size_reason=TEXT, questions=QUESTIONS,
            stack={**EDIT_FIELDS["design"], "description": "Provisional stack choices and reasons."})
REVIEW = obj(findings=TEXT, questions=QUESTIONS, option_edits=OPTION_EDITS)
TASK = {
    "LEAD2-ALIGN": """Start reason with one real job in the owner's world from start to finish.
Use the walk to cover functions, operation (hosting, users, roles, logins, backups, offline, connections,
cost) and appearance (device, style, key screen, language, direction, accessibility, reference).
Preserve exact values, handoffs, races and recovery. Ask only unanswered consequential choices.
Use the size fields, never a product question about size. Honor the chosen size and scope.
Link changed roles and short-titled stories to owner words. The owner decides readiness.
Fold a ready micro or small spec and build order here; otherwise leave detailed spec arrays empty.""",
    "LEAD2-SPEC": """Explain identities, state changes and design. Preserve exact owner choices.
Walk overlap, recovery, fresh setup and stale retries. Order outcomes in one session and suggest demos.""",
    "REVIEW": """What could disappear while this still passes? Repair trace, bundles, outputs, modules,
tests and walks from the brief and replies while preserving accepted defaults and original retry outcomes.
In findings list each notice and setting once. Quote the walk step where the recipient opens the notice
(including reminders after restart) or another person sees the changed setting take effect. Add missing steps.
Ask only for unresolved material product outcomes. Resolve implementation and proof using your judgment.
Give each outcome option_edits marked with the exact question and recommended or alternative.
Put complete node values in regular field order. For link/unlink/replaces use ref for source and
values for targets; retire has no values. Keep distinct questions on separate nodes and keep options
out of common edits. Leave build_order and access empty unless changed.""",
}
OWNER_GUIDE = """Use business questions for plain or unclear owners. In first ALIGN fill stack with
each recommended choice and a plain one-line reason they can override. Show unresolved stack choices
to technical owners with defaults. Link stack decisions to relevant promises when present."""
SPEC_GUIDE = """Link each promise to planned code modules (design rows) and named tests with observable
results (check rows). List build and judge access with suppliers; empty supplier means can't test here.
Derive access from the walks, including printer and paper for print and the channel for each notice.
Leave access empty to keep a prior list or give the full revised list."""
EDIT_GUIDE = """Use a short new ref to add or a shown ref to amend, one row per changed node.
Retire only superseded nodes. Group links by source; replacements inherit old proof unless unlinked.
Link words and roles to stories, stories to promises, promises to design and proof.
Leave unused arrays empty. Each question names two concrete options; code adds Other."""
PROMISE_GUIDE = """Give each output its own action, result and failure, including portal views, admin
functions, board updates, notices, hosting, migrations, and report formats. Promised workflow tests
are distinct from planning checks. Walk recipient and setting effects; standalone artifacts can use
direct checks. Keep B results terse with exact distinguishing conditions and owner qualifications.
Name sibling results 'Report name screen — ...', 'Report name print — ...' and 'Report name CSV — ...',
starting each with identical shared conditions."""


def question(kind, words, current, *, qualification=None, **facts):
    if qualification:
        fields = dict(amendments=array(obj(ref=TEXT, result=TEXT, not_met_if=TEXT)))
        prompt = ("Amend only existing promises explicitly named by the B condition. Keep every other "
                  "clause verbatim. Put the condition in the promise result and failure condition.")
        facts = {"B condition": qualification}
    else:
        extra = {"LEAD2-ALIGN": ALIGN, "REVIEW": REVIEW}.get(kind, obj())["properties"]
        edits = SPEC_EDITS if kind == "LEAD2-SPEC" else EDIT_FIELDS
        fields = {**extra, **edits, "build_order": DELIVERY, "access": ACCESS}
        task = TASK[kind]
        if kind == "LEAD2-SPEC":
            task += "\nGive a 3–8 word title per story/promise/walk; use an empty title if unavailable."
        prompt = "\n\n".join((task, OWNER_GUIDE, SPEC_GUIDE, PROMISE_GUIDE, EDIT_GUIDE))
    return {"kind": kind, "execution": {}, "schema": obj(answer=obj(reason=TEXT, **fields)),
            "renderContext": {"lead2": True},
            "prompt": prompt,
            "facts": {"Owner's words": words, "Current proposal": current, **facts}}


def render(question):
    facts = {**question["facts"]}
    prompt = "\n\n".join((facts.pop("Owner's words"), question["prompt"], labelled(facts)))
    return prompt, "Plan in the typed answer. Do not write product code or open desktop windows."
