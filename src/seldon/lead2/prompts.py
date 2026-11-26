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
DELIVERY = array(obj(outcome=TEXT, stories=REFS))
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
Preserve exact values, handoffs, races and recovery. Default to the permissions each role needs.
For micro and small dreams, also include the simplest stack as a choice here. Use the same doubt rule
for it: ask only if a wrong guess is costly and her words don't settle it. Put it in questions even
when assumed so it becomes a correctable assumption at B. Use words the owner understands.
Use the recommendation as default. In her_words_quote, quote the owner's exact words that settle
this specific default. Leave it empty when in doubt. Give a one-line why before each yes/no.
Bakery order list: would_bet yes when the owner said "show today's pickups first"; no when she
said nothing about who can cancel orders. Wrong_costly yes if guessing could let clerks cancel
paid orders; no if guessing changes the button color.
Size by builder effort. A local bakery queue with two staff phones, one database, safe retries,
restart recovery and CSV reports is small. Medium needs several days of build work. Honor the owner's size.
Use short story refs and links brief/reply → story and person → story, e.g. {"source":"brief","targets":["save"]}.
Stories describe product behavior. Leave demonstrations and release decisions out of the spec.""",
    "LEAD2-STACK": """Recommend the stack for the accepted vision. Give each choice a plain one-line reason
and cite the need that drove it in words. Offer alternatives only when the answer changes what is built.
For a plain owner explain the recommendation in business language. Incorporate the owner's latest reply.""",
    "LEAD2-SPEC": """Use the fewest stories that fully cover the owner's words. Keep connected steps, roles and
recovery cases under the same job. Preserve their exact requirements as promises and checks.
For each story: is it part of the app's core job (without it the app is pointless)?
Give core_job_why and a JSON boolean. Bakery list: saving orders is core;
search and decorative icons are not. After false, explain nice: true for icons, false for search.
For a core story, use nice_why: "", nice: false. Include every story, using its shown ref.
A small app usually needs one delivery, including when its workflows share a screen or data.
Split only when the person could use an outcome on its own before the rest. Group every story
exactly once, in delivery order. Example: {"outcome":"Run the bakery order list","stories":["save","pickup"]}.
Use mockups for distinct visual choices the owner can pick or mix. Give [] if the look is settled.""",
    "REVIEW": """Fix behaviour and proof gaps now. Put future build and venue verification in checks or access.
Use unresolved_issue only for a plan gap still preventing the build after your edits. If ready, give "".
Ask only a material choice. Example: id "pickup", recommended_id "counter", alternative_id "locker".
Use "counter" or "locker" as option_id. New refs belong to their option. Keep access [] unless changed.""",
}
SPEC_GUIDE = """Link each promise to planned modules and observable checks. Derive access from the walk:
give the full list with suppliers ("" means unavailable), or [] to keep it. Defer only by agreement."""
EDIT_GUIDE = """Use exact refs to edit and new refs to add. Keep Seldon's process out of stories.
Link brief/replies/roles → stories → promises → design/checks/walks. Locked refs go only in links.
Retire once and leave old links alone. Each product question names two options; code adds Other.
For an existing story s1, revise it in story or retire s1 and link the new story to its promises.
Use replaces only to copy proof links from a shown promise: {"source":"v1","targets":["new-save"]}.
Declare new-save in promise. Never invent v1 if the proposal shows no such promise. Otherwise use []."""
PROMISE_GUIDE = """Give each behavior an action, result and failure condition. Walk each role through interruptions.
For handoffs, check the other open session before reload. On phones, fit the result and 44px tap targets
at 390 × 844 after a message. For an interrupted device action, plan a real restart and account switch:
the next person can sign out, and only the original person can resolve the pending action once."""


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
            fields = {**extra, **SPEC_EDITS, "access": ACCESS}
            if kind == "LEAD2-SPEC":
                fields.update(story=SPEC_STORY, build_order=DELIVERY)
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
