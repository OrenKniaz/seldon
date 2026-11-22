"""C3/C4/I10; R2/R8/R12: human-readable briefs bounded by one unit or one feature."""

import json
import re
from difflib import SequenceMatcher

from . import features, observations, proof
from .answers import Mismatch, index, links, normalised, render_constraint_values
from .schema import ALIGN_ROUND, answer_schema, batch_decision, obj, validate, vision_body

GUIDE_PATH = "docs/try-it.md"
GUIDE_SCOPE = (GUIDE_PATH, "docs/try-it/**")
GUIDE_WALKTHROUGH = ("In docs/try-it.md, show how to try every accepted user surface, with one runnable "
                     "example per CLI command when there is a CLI. Supply inputs or show how to create them. "
                     "Declare a check under docs/try-it/ that uses the same commands in the reader's shell "
                     "from a clean checkout wherever safe, including input setup, launch and stop. Where "
                     "practical, have the reader and the check use the same runnable script. Assert the "
                     "promised results and stop what you start. For checks and local walkthrough probes, use the "
                     "supplied SELDON_EVIDENCE_DIR for generated evidence; do not override it or write "
                     "generated evidence into the product checkout. In SELDON_EVIDENCE_DIR/operation-lifecycle.json, "
                     "record a JSON array linking each exercised step to the guide with its actual command "
                     "or interaction and observed result. Use only approved runtimes already available. "
                     "If the exact runtime is unavailable, preserve it in the guide, record observed=false "
                     "with the reason for its steps, and exercise the other supported steps.")

# Owner decision: ALIGN improves craft within scope, with observable quality and a distinctive UI direction.
ALIGN_DESIGN = (
    "Improve the dream toward the most beautiful, simplest, most delightful version of what was asked, "
    "within scope. Beauty means craft, not extra requirements. Every quality criterion must be observable "
    "with the supplied runtime. A product with a user interface states one distinctive visual direction "
    "as a quality criterion.")

# Owner decision: retain visual craft guidance; removing the avoid list regressed measured page quality.
CRAFT = (
    "For a UI, follow the accepted visual direction with deliberate typography, spacing, contrast, "
    "clear language and purposeful accessible interaction. Inspect the real result. "
    "A UI must look designed by someone with a point of view, following the accepted visual direction. "
    "Avoid the default AI look: a system-font stack as the whole typography, a centered column on off-white, "
    "a generic pill button, purple gradients, cards in cards, gray text on color, bounce easing. "
    "Instead: deliberate type with real hierarchy from fonts available offline; a confident palette with "
    "strong contrast; generous rhythm in spacing; motion that is brief, purposeful and honors "
    "reduced-motion; "
    "words a person would actually say. No TODOs, no partial implementations. "
    "A finished page opens with a clean browser console: give it an icon (for example "
    "<link rel=\"icon\" href=\"data:,\">) and serve every file it requests.")

# Owner decision: accepted-experience leeway applies to unit and final judgments.
AVAILABLE_TOOLS = (
    "Available tools and skills: checks/kit/browser.cjs exports browser(url, async ({page, shot, errors}) "
    "=> { /* exercise the UI; await shot(name) saves evidence */ }); "
    "checks/kit/serve.cjs exports serve(argv, async ({url, port, dataDir}) => { /* exercise product */ }); "
    "argv substitutes {port} and {data}. In a Node check: const browser = require('./kit/browser.cjs'); "
    "const serve = require('./kit/serve.cjs'); await serve(['node','app/server.js'], "
    "async ({url}) => browser(url, async ({page, shot}) => { await page.goto(url); await shot('ready'); })); "
    "Run with node checks/example.cjs. Adjust require paths relative to your scoped check file.")

DEFECT_HANDOFF = (
    "A defect in a file outside your write scope is its owner's to fix: leave the file alone, "
    "keep your check honest, name the owner and the correction in discoveries and return done. "
    "Seldon delivers it and reruns your checks on the integrated product.")

WORK_COACH = (
    "Use workerQuestion when supplied. Use the receipt's summary, why and discoveries to resolve "
    "the actual issue in an older receipt. Ask the user only for a concrete fact or decision they own."
)

SIZE_ESTIMATE = (
    "Estimate how long one capable plain agent would need to build and verify the grown scope and quality "
    "bar with the same starting repo, tools and access. Micro means minutes in one session: a single-file "
    "CLI, a local web app with a small API, SQLite and an importer, or a standard-library signal-processing "
    "bench with a page, CLI and local storage server. Small means days. Medium means weeks with decisions "
    "to carry across sessions, as in a multi-service product with auth and migrations. Big means months. "
    "A technical-sounding domain or several local interfaces alone do not imply more effort. In sizeWhy "
    "state the closest comparable work or its absence, expected effort, and what would make it bigger. "
    "Follow an Owner size override and explain its planning consequences. "
    "Keep explicitly requested delivery stages and exact constraints at every size.")

PROMPTS = {
    "ALIGN": "What improved dream should we agree to? Recommend size and an observable quality bar. "
             "Keep every exact value the user stated verbatim in the scopeIn item it belongs to; "
             "improving never paraphrases an exact value. "
             "Name 1-3 alternatives the user may prefer to this vision: smaller, bigger, or another "
             "answer to what their words leave open. "
             + ALIGN_DESIGN,
    "COACH": "Answer this worker's question using the accepted requirements, contracts and tools. "
             "Give practical guidance so the worker can continue within its scope. "
             "Answer yes or no first when the question allows it. "
             "If the vision, decisions and results do not settle it, return ask: one question, "
             "2-4 options, your recommendation first. " + DEFECT_HANDOFF,
    "DECIDE": "Which displayed option do the user's verbatim words choose? Other for their own "
              "direction, Stop to end the dream, Unclear when you cannot tell. When Waive is available, "
              "use it only when the user approves continuing and says later check-ins need no answer.",
}


def question(kind, prompt, facts, schema, examples, targets=(), execution=None, images=(), *, context=None):
    examples = [{"answer": example} for example in examples]
    for example in examples:
        validate(example, schema)
    return {"kind": kind, "prompt": prompt, "facts": facts, "targets": list(targets), "schema": schema,
            "examples": examples, "execution": execution or {}, "images": list(images),
            **({"renderContext": context} if context else {})}


def build(kind, facts, targets=(), *, coach_contracts=False):
    if kind == "ALIGN":
        facts = {**facts, "Design guidance used by UI workers": CRAFT}
    elif kind == "COACH":
        facts = {**facts, "Available tools and skills": AVAILABLE_TOOLS}
    example = {
        "ALIGN": {"size": "tiny", "understanding": "A personal greeting", "users": ["CLI user"],
                  "scopeIn": ["Greet a supplied name"], "scopeOut": [], "improvements": [],
                  "quality": [{"criterion": "Clear output", "verification": "Run with a name"}],
                  "alternatives": [{"label": "Greet everyone", "means": "Use a greeting without a name"}]},
        "DECIDE": {"choice": next(iter(targets), "Unclear")},
        "COACH": {"guidance": "Use the declared contract and write a check of the acceptance sentence."},
    }[kind]
    example["why"] = "The observed interaction and check sources support this answer"
    prompt = PROMPTS[kind]
    if kind == "COACH":
        if "receipt" in facts.get("Unresolved work", {}):
            prompt += "\n" + WORK_COACH
        if coach_contracts:
            prompt += (
                "\nUse the contract response for questions about peer agreements. Answer with the "
                "applicable plan text. If the text is missing, incomplete, disputed, or the previous "
                "guidance did not resolve it, describe the gap and what the planner must settle. "
                "Use ask for decisions that require the user.")
    return question(kind, prompt, facts, answer_schema(kind, targets, coach_contracts=coach_contracts), [example], targets)


def align_round_brief(facts, *, sources, size_first=False):
    """Build one ALIGN round. Sources maps recorded handles to their exact user words."""
    return question("ALIGN",
        "What product would you put in front of this person now? Name who makes what decision, "
        "what could mislead them, and what they would need tomorrow to repeat it. Rank the stories "
        "by value. Keep a story's handle as it moves or changes so its earlier choices stay traceable. "
        "Keep exact required values once, linked to their source and affected outcomes. Runtime "
        "inventory tells you what is available. Carry values into scope only when the user required them. "
        "Cite the supplied Sources for choices that introduced or changed a story, criterion or exclusion. "
        "Keep earlier citations while they explain the current outcome. "
        "Ask together only the choices whose answers materially change the proposed product. "
        "Name the affected commitment or scope, show the proposed default first and explain what each "
        "option changes for the user. Give two to four concrete options; Seldon supplies Other. "
        "Give the changed outcome and why. Recommend done when the displayed defaults settle the product. "
        + ALIGN_DESIGN + ("\n" + SIZE_ESTIMATE if size_first else ""),
        {**facts, "Sources": sources, "Design guidance used by UI workers": CRAFT},
        obj(answer=ALIGN_ROUND), [tiny_round_example(), consequential_round_example()],
        context={"sizeFirst": size_first})


def check_align_round(answer, sources):
    vision = vision_body(answer)
    dropped, changes = set(), []
    def record(kind, body):
        changes.append(body)

    constraints = index(vision["constraints"], "vision.constraints", record=record)
    for field in ("stories", "quality"):
        vision[field][:] = index(vision[field], f"vision.{field}", record=record).values()
    goals = commitments(vision)
    for ref, entry in goals.items():
        entry["constraints"] = links(entry["constraints"], constraints, record=record,
                                     field="vision.constraints", subject=ref)
    cited = {ref for entry in goals.values() for ref in entry.get("constraints", [])}
    for handle, constraint in constraints.items():
        source, value = constraint["source"], constraint["value"]
        canonical = source if source in sources else re.split(r"[:;]", source, maxsplit=1)[0].strip()
        words = sources.get(canonical, "")
        if not words.strip():
            if value in sources["kickoff"]:
                canonical, words = "kickoff", sources["kickoff"]
            elif handle not in cited:
                dropped.add(handle)
                changes.append({"subject": handle, "action": "dropped_constraint", "source": source,
                                "value": value, "summary": f"Removed unsourced constraint {handle!r}."})
                continue
        constraint["source"] = canonical if words.strip() else "kickoff"
        if constraint["source"] != source:
            changes.append({"subject": handle, "action": "source_handle", "original": source,
                            "source": constraint["source"],
                            "summary": f"Updated the source reference for constraint {handle!r}."})
        if value in words:
            continue
        # Preserve the exact source spelling when wrapping or JSON escaping was the only difference.
        parts = value.replace("\\\\", "\\").split()
        pattern = r"\s+".join(re.escape(part).replace(r"\\", r"\\{1,2}") for part in parts)
        if match := (re.search(pattern, words) if pattern else None):
            constraint["value"] = match.group()
            changes.append({"subject": handle, "action": "exact_source_text", "value": value,
                            "source": canonical, "sourceSpan": match.group(),
                            "summary": f"Restored the exact wording of constraint {handle!r}."})
            continue
        tokens = list(re.finditer(r"\w+", words))
        closest = SequenceMatcher(None, re.findall(r"\w+", value.casefold()),
                                  [token.group().casefold() for token in tokens]).find_longest_match()
        span = (words[tokens[closest.b].start():tokens[closest.b + closest.size - 1].end()]
                if closest.size else None)
        changes.append({"subject": handle, "action": "paraphrased",
                        "source": canonical if words.strip() else None,
                        "value": value, "sourceSpan": span,
                        "summary": f"Kept constraint {handle!r} as a paraphrase." if words.strip()
                                   else f"Kept proposed requirement {handle!r} without a source quote."})
    answer["vision"]["constraints"] = [c for h, c in constraints.items() if h not in dropped]

    for entry in [*goals.values(), *vision["scopeOut"]]:
        if unknown := set(entry.get("constraints", [])) - constraints.keys():
            raise Mismatch("vision.constraints", f"Unknown constraint handles {sorted(unknown)}. "
                           f"Use these declared handles: {list(constraints)}.")
        valid = list(dict.fromkeys(ref for ref in entry["sources"] if ref in sources))
        if valid != entry["sources"] or not valid:
            label = entry.get("handle", entry.get("outcome"))
            kept = valid or ["kickoff"]
            changes.append({"subject": label, "action": "citation", "sources": kept,
                            "removed": [ref for ref in entry["sources"] if ref not in sources],
                            "summary": f"Updated citations for {label!r} to use {', '.join(kept)}."})
            entry["sources"] = kept
    subjects = {"vision", "size", "scopeOut", *goals, *constraints}
    for ask in answer["questions"]:
        ask["affects"], = links([ask["affects"]], subjects, record=record,
                                field="questions.affects", subject=ask["question"])
        if ask["affects"] not in subjects or ask["affects"] in dropped:
            changes.append({"subject": ask["affects"], "action": "question", "question": ask["question"],
                            "summary": "Kept the open question attached to the vision."})
            ask["affects"] = "vision"
    return changes


def batch_decide_brief(groups, words):
    """Map verbatim chat to request-bound group IDs and actual option IDs in one DECIDE."""
    example = {"selections": {group["id"]: {"optionId": "unclear", "text": ""} for group in groups},
               "why": "The words do not settle these choices"}
    return question("DECIDE",
        "What selections do the user's exact words make in the displayed groups? "
        "Resolve each unambiguous choice using its option ID. Use 'other' with the exact quoted "
        "direction, or 'unclear' for an unanswered or ambiguous group. Leave text empty except for 'other'. "
        "Only an explicit answer to the round decision can select done.",
        {"Displayed groups": groups, "User's verbatim words": words},
        obj(answer=batch_decision(groups)), [example])


def check_batch_decide(answer, words, *, record=None):
    for group, selection in answer["selections"].items():
        text = selection["text"]
        if selection["optionId"] == "other":
            if text.strip() and text in words:
                continue
            raise Mismatch("selections", f"Other for {group!r} contains {text!r}. "
                           "Copy the exact direction from the user's words, or use Unclear.")
        selection["text"] = normalised(record, "selections", text, "",
            f"Removed redundant text from option {selection['optionId']!r} for {group!r}.")


def tiny_round_example():
    return {"vision": {"understanding": "Greet the supplied name", "size": "tiny",
        "sizeWhy": "A single-file CLI takes one plain agent minutes", "users": ["CLI user"], "scopeOut": [],
        "stories": [{"handle": "greeting", "feature": "Personal greeting", "story": "Greet a visitor by name",
                     "outcome": "The reply contains the supplied name", "constraints": [], "sources": []}],
        "constraints": [], "quality": [{"handle": "clear", "criterion": "Readable greeting",
            "verification": "Run with a name and read the greeting", "constraints": [], "sources": []}]},
        "changed": "The supplied name appears in the greeting.", "questions": [],
        "recommendation": "done", "why": "One clear interaction covers the dream"}


def consequential_round_example():
    return {"vision": {"understanding": "Compare two noise filters on repeatable input", "size": "tiny",
        "sizeWhy": "Like a local simulation bench, one agent can build and verify it in minutes using shared "
                   "local data; adding live hardware calibration would increase the effort.",
        "users": ["Audio engineer choosing a filter"], "constraints": [{"handle": "launch",
            "value": "python server.py --port PORT --data DIR", "meaning": "Start the local bench",
            "source": "kickoff"}], "scopeOut": [{"outcome": "Hardware playback", "sources": []}],
        "stories": [{"handle": "compare", "feature": "Filter comparison",
            "story": "Choose a filter after comparing two runs of yesterday's reference input",
            "outcome": "The engineer sees the band differences and which measurements span unequal intervals",
            "constraints": ["launch"], "sources": []},
            {"handle": "inspect", "feature": "Saved runs",
             "story": "An engineer can reopen a stopped run and see what was measured",
             "outcome": "A saved run shows its settings and measured interval",
             "constraints": ["launch"], "sources": ["example-reply"]}],
        "quality": [{"handle": "repeatable", "criterion": "Repeatable band readings",
            "verification": "Reopen a saved input with identical settings and compare the readings",
            "constraints": [], "sources": []}]},
        "changed": "A stopped run retains the measured interval so a colleague can inspect it.",
        "questions": [{"affects": "scopeIn:compare", "question": "How may stopped runs be compared?",
            "options": [{"label": "Inspect only", "means": "Only complete runs contribute a band comparison"},
                        {"label": "Compare shared interval",
                         "means": "Compare overlapping measurements and mark the result partial"}]}],
        "recommendation": "revise", "why": "A partial comparison trades measurement time for confidence"}


def user_words(store):
    cursor = store.cursor()
    answer = store.read(cursor.vision) if cursor.vision else None
    if answer and "scopeIn" in answer["answer"]:
        attempt = store.read(answer["attempt"])
        dream = store.read(attempt["question"])["facts"]["Dream"]
    else:
        dream = store.read(cursor.kickoff)["words"]
    changes = [store.read(b["words"])["words"] for _, _, b in store.records("approval")
               if b.get("choice") == "revise" or "step" in b["subject"]]
    return {"Dream": dream, **({"Requested change": changes} if changes else {})}


def labelled(facts):
    return "\n\n".join(label + "\n" + (value if isinstance(value, str) else
                          json.dumps(value, ensure_ascii=False, indent=2)) for label, value in facts.items())


def take(facts, *keys):
    return {key: facts.pop(key) for key in keys if key in facts}


def checkin_wave_task(wave):
    return (f"Plan wave {wave} from the finished delivery and the user's check-in direction. Give the "
            "follow-on wave its own goal and cite the accepted commitments it advances, including ones "
            "that also had work in an earlier wave. List the upcoming waves in order. Seldon carries "
            "forward completed waves. Keep their owners and report any needed changes to responsibilities "
            "or agreements by handle. If the direction changes accepted scope, return scope for the user.")


CONTINUING_ASSIGNMENT = ("For work on the continuing owner's paths, leave scope out of that assignment. "
                         "Seldon keeps the owner and its approved write paths. Give new work that needs "
                         "different paths an explicit scope.")


SEVERITY_GUIDANCE = (
    "Use must-fix for missing accepted behavior or quality, nit for minor polish or a small residual proof "
    "limit in an otherwise delivered experience, and pre-existing for a defect the unit did not introduce. "
    "When essential evidence is missing, use uncertain and name the smallest observation that would decide "
    "it. State machine or device limits in why when the available evidence already establishes the meaning.")


def reviewed_decision(question, facts, context):
    kind = question["kind"]
    example = question["examples"][0]
    opening = {"Accepted audience": context["audience"]} if context.get("audience") else {}
    output = "Return one JSON receipt with the decision and its reason."
    if kind == "ALIGN":
        follow_up = "Previous proposal" in facts
        grown = "vision" in question["schema"]["properties"]["answer"].get("properties", {})
        if grown:
            mission = ("Here is what the person chose. What changed for them since the last proposal?"
                       if follow_up else
                       "Who would use this, what action or decision matters most to them, and what would "
                       "a noticeably better version let them do?")
            opening.update(take(facts, "Selected choices", "Requested change", "Sources"))
            if "Sources" in opening and facts.get("Dream") == opening["Sources"].get("kickoff"):
                facts.pop("Dream")
            opening.update(take(facts, "Dream"))
            task = ("Rank stories by value. Carry forward settled stories, handles and citations. Attach "
                    "exact required values to their supplied source and affected outcomes. Give each "
                    "quality promise something observable, including a distinctive direction for a UI. "
                    "Ask together only choices that materially change the product. Give two to four "
                    "concrete options with consequences, recommendation first. Seldon supplies Other. "
                    "Recommend done when settled. "
                    "The user decides gate A. For an exact correction, address the rejected value and "
                    "its source.")
            if context.get("sizeFirst", SIZE_ESTIMATE in question["prompt"]):
                task += ("\n\n" + SIZE_ESTIMATE + " Display micro/small/medium/big and use the schema values "
                         "tiny/small/medium/large in the receipt.")
            if follow_up:
                example = question["examples"][-1]
        else:
            mission = "What improved experience can this person agree to?"
            opening.update(take(facts, "Dream", "Requested change"))
            task = ("Preserve exact values in their scope items. Name observable quality, recommend size "
                    "and show one to three genuinely different alternatives where the words leave room. "
                    + ALIGN_DESIGN)
    elif kind == "ARCHITECT":
        if "Next wave" in facts:
            mission = "What follow-on work delivers the user's check-in direction?"
            opening.update(take(facts, "User check-in", "Finished wave"))
            task = checkin_wave_task(facts.pop("Next wave"))
        elif "Finished wave" in facts:
            mission = "What must change in the unfinished work to deliver the accepted outcome?"
            opening.update(take(facts, "Finished wave", "User check-in"))
            task = ("Preserve completed waves and owners. Describe only changed agreements, responsibilities "
                    "or remaining waves. Return scope for a change requiring the user's decision.")
            output += (" List the full wave horizon or only the remaining waves. An empty waves list keeps "
                       "the horizon. Areas and contracts merge by handle, and empty lists keep them.")
        else:
            mission = "What is the first runnable delivery for this user, and who owns the work?"
            vision = facts.get("Approved vision", {})
            if vision.get("users"):
                opening["Accepted audience"] = vision["users"]
            goals = facts.get("Commitment handles", {})
            if goals:
                first = next((key for key in goals if key.startswith("scopeIn:")), next(iter(goals)))
                # The approved vision supplies ordering, even when replay loads a sorted JSON record.
                if vision.get("stories"):
                    first = "scopeIn:" + vision["stories"][0]["handle"]
                if first in goals:
                    opening["First accepted outcome"] = {first: goals[first]}
            task = ("Honor the accepted stages and size. Give one capable builder a coherent area when it "
                    "fits. Split at independent responsibilities and write the agreements that enable "
                    "concurrent work. Keep scopes disjoint with room for product and checks. Keep every "
                    "commitment and exact constraint on the horizon. Detail next-wave contracts and leave "
                    "later ones as handles and a sentence. In a multi-wave plan start with a runnable slice.")
    elif kind == "DETAIL":
        mission = "What should the user be able to do through this area in the current wave?"
        opening.update(take(facts, "Current wave", "User check-in", "Area", "Applicable constraints",
                            "Walkthrough ownership", "Writing the brief", "Separate walkthrough"))
        task = ("Describe the features and give each requirement one observable acceptance sentence. Trace "
                "the accepted commitments. Seldon supplies linked exact values for execution and review. "
                "Give the work and its checks to one session-sized owner "
                "unless another unit can build an independent piece now. Reserve dependencies for actual "
                "file prerequisites. Return contract_gap for a missing agreement or subdivide at a real boundary.")
        if "Continuing owner" in facts:
            opening.update(take(facts, "Continuing owner"))
            task += " " + CONTINUING_ASSIGNMENT
        output += (" Cite Accepted commitments when the schema asks for them. Feature links use feature "
                   "targetHandle values. Unit requirements use local requirement handles and dependencies "
                   "use one-based unit positions.")
    elif kind == "REVIEW":
        mission = ("If the builders delivered exactly this plan, "
                   "which accepted promise could the user still miss?")
        opening.update(take(facts, "Review subject", "Area", "Accepted commitments"))
        task = ("Trace each relevant commitment to a responsibility, an agreement where needed and an "
                "observable acceptance sentence. For a material omission name the promise and the smallest "
                "plan correction. Judge the supplied plan from plan facts.")
        example = {"answer": {"verdict": "gap", "gaps": [{"target": "remember-preference",
            "correction": ("Assign persistence to one owner and require the saved value "
                           "to appear after restart."),
            "severity": "must-fix"}], "why": "Saving within one session leaves reopening unassigned."}}
    elif kind == "COACH":
        mission = "What can this worker do next inside their scope?"
        opening.update(take(facts, "Unresolved work"))
        task = ("Answer yes or no first when possible. Resolve the question from accepted requirements, "
                "decisions, the actual runtime and contract text. Escalate unresolved user choices with "
                "two to four concrete options, recommendation first.")
        if "receipt" in opening.get("Unresolved work", {}):
            task += " " + WORK_COACH
        if any("contract" in branch.get("properties", {})
               for branch in question["schema"]["properties"]["answer"]["anyOf"]):
            task += (" For a peer agreement, return its applicable text or identify the gap "
                     "the planner must settle when the text is missing, incomplete or disputed.")
        example = {"answer": {"guidance": "Yes. Reload through the documented command from the product root.",
                             "why": "The recorded command accepts the saved profile path."}}
    elif kind == "FLOW":
        mission = "Follow the feature story from the person's first action to the accepted result."
        opening.update(take(facts, "Feature"))
        task = ("What do the observations and screenshots show at each handoff? Use the traced requirements, "
                "crossed contracts, baseline and final observations and source judgments. Identify the "
                "actual repair owner, including a unit or agreement outside this feature where responsible. "
                "Use uncertain for a missing decisive observation.")
    elif kind == "PRODUCT":
        mission = "Does this delivered experience meet the named criterion for its intended audience?"
        opening.update(take(facts, "Criteria"))
        task = ("Use final observations and attached evidence to describe what the person sees or does. "
                "For a visual criterion inspect the accepted visual direction.")
        if context.get("productMode") == "commitment" or "Feature verdicts" in facts:
            task += " Trace the join between feature results for the cross-area promise."
        if context.get("productMode") == "guide" or "Guide file" in facts:
            task += " Compare the delivered instructions with the observed clean-start journey."
        task += " Use the supplied criterion and responsible owner for any correction. " + SEVERITY_GUIDANCE
    else:  # DECIDE has either displayed options or a group-specific schema.
        mission = "Which displayed option does the person's exact wording settle?"
        opening.update(take(facts, "User's verbatim words", "User's words", "Decision", "Displayed subject",
                            "Displayed groups", "Displayed options"))
        if "Displayed groups" in opening:
            task = ("Map each group to its own displayed option ID. Use other with the person's exact words "
                    "for their own direction and leave text empty for every other selection. Mark unanswered "
                    "or ambiguous groups unclear. Select done only on an explicit round decision.")
        else:
            task = ("Use Other for their own direction, Stop for an explicit stop and Unclear for ambiguity. "
                    "Waive requires permission to continue and to skip answers at later check-ins, "
                    "and is available only when offered.")
            example = {"answer": {"choice": "Unclear", "why": "The words leave the displayed choice open."}}
    if kind in ("FLOW", "PRODUCT"):
        example = {"answer": {"verdict": "uncertain", "neededFacts": ["The value after reopening the app."],
                             "why": "The delivered observation ends before the reader reopens the app."}}
    if kind in ("ARCHITECT", "DETAIL", "COACH"):
        facts.pop("Available tools and skills", None)  # Leave the builder tutorial with WORK.
    return mission, opening, task, output, example


def reviewed_brief(question, stdin_instruction, image_directions):
    """Render the reviewed arm from recorded facts and presentation context only."""
    from .store import encode

    facts = dict(question["facts"])
    kind = question["kind"]
    context = question.get("renderContext", {})
    if kind in ("WORK", "JUDGE"):
        from . import unit_questions
        render = unit_questions.reviewed_work if kind == "WORK" else unit_questions.reviewed_judge
        mission, opening, task, output, example = render(question, facts, context)
    else:
        mission, opening, task, output, example = reviewed_decision(question, facts, context)
    validate(example, question["schema"])
    if repairs := take(facts, "Exact corrections", "Exact failure"):
        opening.update(repairs)
    keys = ("Write scope", "Requirements", "Subjects", "Contract authority", "Contracts provided",
            "Contracts consumed", "Area contracts", "Planned contracts", "Decisions", "Runtime", "Runtime assumptions",
            "Execution facts", "Existing checks", "Correction owners", "Crossed contracts",
            "Wave-start observations", "Observations", "Final observations", "Check artifacts")
    evidence = {**take(facts, *keys), **dict(sorted(facts.items()))}
    label = ("Assignment facts" if kind == "WORK" else
             "Evidence" if kind in ("JUDGE", "FLOW", "PRODUCT") else "Decision facts")
    caption = ("Illustration from another assignment. Use the paths, handles and observations from yours."
               if kind == "WORK" else "Illustrative receipt. Use the supplied facts and handles.")
    return "\n\n".join(filter(None, (
        mission,
        "Call instruction\n" + stdin_instruction if stdin_instruction else "",
        labelled(opening), task,
        label + "\n\n" + labelled(evidence) if evidence else "",
        image_directions.strip(), output, caption + "\n" + encode(example),
        "Answer schema\n" + encode(question["schema"]))))


def runtime(setup):
    return {k: setup[k] for k in ("python", "node", "nodeVersion") if k in setup}


def observed(store, refs):
    return [{**{k: b[k] for k in ("commit", "check", "requirements", "purpose", "command",
                                 "exitCode", "stdout", "stderr")}, **observations.judge_fields(b)}
            for b in (store.read(ref) for ref in refs)]


def images(store, refs):
    from pathlib import Path
    return list(dict.fromkeys(p for ref in refs for p in store.read(ref)["evidenceFiles"]
                             if proof.image(Path(p).read_bytes())))


class AnswerRejected(ValueError):
    def __init__(self, message, answer):
        super().__init__(message)
        self.answer = answer


def ask(provider, question, record, retries=2):
    original = question
    rejected = []
    for attempt in range(retries + 1):
        raw = provider(question)
        errors = []
        answer = None
        try:
            decoded = json.loads(raw)
            changes = render_constraint_values(decoded) if original["kind"] == "ALIGN" else []
            validate(decoded, question["schema"])
            answer = decoded["answer"]
        except (ValueError, KeyError) as error:
            errors = str(error).splitlines()
        record({"rawAnswer": raw, "errors": errors,
                **({"normalisations": changes} if not errors and changes else {})})
        if not errors:
            return answer
        rejected.append({"Rejected answer": raw, "Errors": errors})
        if attempt == retries:
            raise AnswerRejected(f"{original['kind']}: {retries} exact-fix retries exhausted; "
                                 + "; ".join(errors), answer)
        question = {**original, "facts": {**original["facts"], "Exact corrections": rejected}}


def must_fix(answer):
    return [g for g in answer["gaps"] if features.blocking(g)]


def satisfied(answer):
    return answer.get("verdict") in ("satisfied", "gap") and not must_fix(answer)


def commitments(vision):
    vision = vision_body(vision)
    if "stories" in vision:
        return {f"{prefix}:{handle}": value
                for field, prefix in (("stories", "scopeIn"), ("quality", "quality"))
                for handle, value in index(vision[field], f"vision.{field}").items()}
    return {f"{field}:{i}": value for field in ("scopeIn", "improvements", "quality")
            for i, value in enumerate(vision[field], 1)}
