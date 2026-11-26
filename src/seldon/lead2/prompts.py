"""C1/C6/C9/C11: the product briefs and typed planning answers."""

from ..questions import labelled
from ..schema import REFS, TEXT, array, choice, obj
from .contracts import OPTION_FIELDS, SPEC_EDITS, SPEC_STORY, YES_NO

SIZES = {"micro": "Minutes of agent work.", "small": "An hour or two of agent work.",
         "medium": "Days of agent work across sessions.", "big": "Weeks or months of agent work."}
QUESTION_FIELDS = dict(question=TEXT, recommended=TEXT, recommended_means=TEXT,
                       alternative=TEXT, alternative_means=TEXT)
QUESTIONS = array(obj(**QUESTION_FIELDS))
MOCKUPS = array(obj(id=TEXT, label=TEXT, means=TEXT))
DELIVERY = array(obj(outcome=TEXT, stories=REFS, try_alone_why=TEXT, would_try_before_rest=YES_NO))
ACCESS = array(obj(need=TEXT, supplier={"type": "string"}, deferred={"type": "boolean"}))
ALIGN_CHOICES = array(obj(question=TEXT, default=TEXT, recommended_means=TEXT, alternative=TEXT,
                          alternative_means=TEXT, her_words_quote={"type": "string"},
                          bet_why=TEXT, would_bet_she_picks_default=YES_NO,
                          wrong_costly_why=TEXT, wrong_costly=YES_NO))
ALIGN = obj(aligned=YES_NO, size=choice(*SIZES), size_reason=TEXT, questions=ALIGN_CHOICES,
            **{kind: SPEC_EDITS[kind] for kind in ("person", "story", "retire", "link", "unlink")})
STACK = obj(stack=SPEC_EDITS["design"], questions=QUESTIONS)
REVIEW = obj(findings=TEXT, unresolved_issue={"type": "string"},
             questions=array(obj(id=TEXT, recommended_id=TEXT, alternative_id=TEXT, **QUESTION_FIELDS)),
             **OPTION_FIELDS)
TASK = {
    "LEAD2-ALIGN": """Start with one real job in the owner's world from start to finish.
Use the walk to cover functions, operation (hosting, users, roles, logins, backups, offline, connections,
cost) and appearance (device, style, key screen, language, direction, accessibility, reference).
Preserve exact values, handoffs, races and recovery. Ask what happens to incomplete records when it
matters. Default to the permissions each role needs for its stated work. Omit immaterial choices.
Use the recommendation as default. In her_words_quote, quote the owner's exact words that settle
this specific default. Leave it empty when in doubt. Give a one-line why before each yes/no.
Bakery order list: would_bet yes when the owner said "show today's pickups first"; no when she
said nothing about who can cancel orders. Wrong_costly yes if guessing could let clerks cancel
paid orders; no if guessing changes the button color.
Size by agent effort, including local checks. Several connected stories can fit one session. Honor the chosen size.
Use short story refs and links brief/reply → story and person → story, e.g. {"source":"brief","targets":["save"]}.
Stories describe product behavior. Leave demonstrations and release decisions out of the spec.""",
    "LEAD2-STACK": """Recommend the stack for the accepted vision. Give each choice a plain one-line reason
and cite the need that drove it in words. Offer alternatives only when the answer changes what is built.
For a plain owner explain the recommendation in business language. Incorporate the owner's latest reply.""",
    "LEAD2-SPEC": """Explain identities, state changes and design. Respect the approved stack and link it to
the promises it serves. For each story: is it part of the app's core job (without it the app is
pointless)? Give core_job_why and a JSON boolean, true or false. Bakery list: saving orders is core;
search and decorative icons are not. After false, explain nice: true for icons, false for search.
For a core story, use nice_why: "", nice: false. Include every story, using its shown ref.
Order build_order by feature. Give each delivery a plain outcome name and its exact story refs.
For a micro or small dream give one product delivery. For larger dreams, explain whether each
is worth trying before the rest. Example: {"outcome":"Record bakery orders","stories":["save"],
"try_alone_why":"Staff can record real orders now.","would_try_before_rest":true}.
Use false for a piece that needs the next delivery to be useful (e.g. just adding customer fields). For a product with a user interface, give a few
quick visual directions for the key screens in mockups: describe layout, interaction and appearance so
the owner can pick or mix them. Use an empty list when there is no visual choice to make.""",
    "REVIEW": """What could disappear while this still passes? Fix missing behaviour and proof from the
brief and owner replies in this answer. If a gap needs another reviewer after your edits, name it in
unresolved_issue. Otherwise return "unresolved_issue": "". Use [] for unused arrays.
Ask only for an unresolved material product choice. Example question IDs: id "pickup", recommended_id
"counter", alternative_id "locker". Use those exact option_id values in option_* edits only. Link
with exact shown refs or new refs declared in those edits. Leave build_order and access empty unless changed.""",
}
SPEC_GUIDE = """Link each promise to planned code modules (design rows) and named tests with observable
results (check rows). List build and judge access with suppliers; empty supplier means can't test here.
Derive access from the planned walk.
Mark deferred only for the specific venue checks the owner explicitly left for later.
Leave access empty to keep a prior list or give the full revised list."""
EDIT_GUIDE = """Use exact refs for edits and fresh refs like order-store for additions. Rows with
owner_choice are locked. Use their refs directly in links. Retiring an editable node removes its links.
Link brief, replies and roles → stories → promises → design, checks and walks. Group by source.
Use [] for unused arrays. Each product question names two concrete options; code adds Other."""
PROMISE_GUIDE = """Give each product behavior its own action, result and failure condition. When a
story or promise describes Seldon's demos, independent walk or release readiness, retire it.
Keep the product behavior and its expected result. Put proof under each behavior it verifies. Preserve exact choices and
walk what each affected person sees through changes, interruptions and recovery. Exercise each usable output."""


def question(kind, words, current, *, qualification=None, **facts):
    if qualification:
        fields = dict(amendments=array(obj(ref=TEXT, result=TEXT, not_met_if=TEXT)),
                      assumptions=array(obj(ref=TEXT, decision=TEXT)))
        prompt = ("Use shown refs for promises and assumptions changed by the B condition. Put the change "
                  "in their result and failure condition or revised decision. Keep unrelated clauses verbatim.")
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
            facts["Size choices by agent effort"] = SIZES
        else:
            extra = REVIEW["properties"] if kind == "REVIEW" else {"mockups": MOCKUPS}
            fields = {**extra, **SPEC_EDITS, "build_order": DELIVERY, "access": ACCESS}
            if kind == "LEAD2-SPEC":
                fields["story"] = SPEC_STORY
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
