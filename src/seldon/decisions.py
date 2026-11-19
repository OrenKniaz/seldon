"""C8/C16/C20; R17: planner first, concrete choices, and only dependent work waits."""

from dataclasses import replace
from pathlib import Path
import traceback

from . import features, inference, plan, questions, recovery
from .answers import ConsumptionFailure, recorder
from .providers import InputRejected, ProviderFailure
from .states import advance


class Waiting(Exception):
    """The next words belong to a future chat turn."""


def holds(store):
    cursor = store.cursor()
    if not cursor.decision:
        return False
    item = store.read(cursor.decision)
    if item["gate"] == "UNIT":
        return False
    if item["gate"] == "CHECK-IN":
        return cursor.state in ("EXECUTE", "C")
    return cursor.state == item["subject"].get("step", item["gate"])


def affected(store, ref):
    contracts = plan.select(plan.execution(store), "unit")
    refs = {ref}
    while added := {r for r, u in contracts.items() if r not in refs
                    and refs.intersection(u["dependencies"])}:
        refs.update(added)
    return refs


def again(store, subject, source, direction):
    ref = subject["unit"]
    for owner, unit in store.units().items():
        if unit.state == "ASKED" and recovery.lineage(store, owner) == recovery.lineage(store, ref):
            advance(store, "again", source, unit=owner, thread=None,
                    base=unit.base if unit.worktree else store.cursor().candidate,
                    callFolder=None, callAttempt=None,
                    failure=unit.failure + "\nDirection: " + direction)


def coach(store, provider, subject, frame=False):
    """Runs outside integration and database locks, including when submitted by the scheduler.

    frame: the planner writes the user's options; its guidance becomes one of them (R17)."""
    ref = subject.get("unit")
    coach_contracts = bool(ref) and features.enabled("askContracts")
    document = plan.execution(store) if store.cursor().revision else {}
    facts = {"Unresolved work": subject, "Earlier user answers": questions.user_words(store),
             "Vision": store.read(store.cursor().vision) if store.cursor().vision else None,
             "Decisions": document.get("architecture", {}).get("decisions", []),
             "User answers": [b for _, _, b in store.records("user_words")]}
    if ref:
        facts.update({"Execution facts": questions.execution_facts(store, ref),
            "Runtime": questions.runtime(store.read(store.cursor().kickoff)["setup"]),
            "Requirements": questions.requirement_facts(document,
                plan.select(document, "unit")[ref]["requirements"]),
            **questions.agreement_facts(document, ref)})
    else:
        facts["Requirements and agreements"] = document
    response = None
    def consume(source, answer):
        nonlocal subject, response
        key, subject = recovery.subject(store, "UNIT" if ref else subject["step"], subject)
        history = store.recovery(key)
        contract = answer.get("contract")
        if contract and (contract["resolution"] == "gap" or history["coached"] or frame):
            history["contractGap"] = source
            store.recovery(key, history)
            return
        guidance = contract["text"] if contract else answer.get("guidance")
        if guidance and not history["coached"] and not frame:
            history.update(coached=True, waiting=False)
            store.recovery(key, history)
            direction = store.record("planner_answer", {"subject": subject, "source": source,
                "guidance": guidance, "words": guidance, "origin": "planner"})
            if ref:
                again(store, subject, source, guidance)
            else:
                store.record("approval", {"request": store.cursor().decision, "subject": subject,
                    "words": direction, "choice": "revise", "origin": "planner"})
                store.cursor(replace(store.cursor(), decision=None, requestedChange=direction,
                                     failure=store.cursor().failure if subject.get("inputOwner") else ""))
            return None
        label = ("Change the agreement or approach" if subject.get("checkFailure")
                 else "Try the planner's new approach")
        ask = answer.get("ask") or {"question": subject.get("question", "How should we continue?"),
            "options": [{"label": label, "means": answer["guidance"]}]}
        ask = {"question": ask["question"], "options": [
            {**o, "effect": "direct"} for o in ask["options"]]}
        if frame and subject.get("checkFailure"):
            ask["question"] = subject["question"]
            ask["options"] = [o for o in ask["options"]
                              if o["label"].casefold() not in ("leave this out", "pause")][:2]
        if ref:
            ask["options"].append(option("Leave this out", "Leave out this unit and its dependents", "drop"))
        elif "guidance" in answer:
            ask["options"].append(option("Stop", "Stop the dream", "stop"))
        if frame and "Pause" not in [o["label"] for o in ask["options"]]:
            ask["options"].append(option("Pause", "Pause here; your work is saved and can resume", "pause"))
        response = {**subject, "ask": ask, "planner": source}
    try:
        inference.call(store, provider, questions.build("COACH", facts, coach_contracts=coach_contracts),
                       consume=consume, field="guidance/contract/ask" if coach_contracts else "guidance/ask")
    except ConsumptionFailure as error:
        cause, shape = record_fault(store, error)
        recovery.failed(store, cause, shape=shape)
        response = {**subject, "failure": shape["failure"], "ask": pause_ask(str(error))}
    return response


def contract_gap(store, ref):
    return store.recovery(recovery.lineage(store, ref)).get("contractGap")


def ask_unit(store, provider, ref):
    try:
        row = store.unit(ref)
        tried = [b["failure"] for _, _, b in store.records("unit_failure") if b["unit"] == ref]
        subject = {"unit": ref, "tried": tried, "failure": row.failure,
                   "affects": sorted(affected(store, ref) - {ref}), "recommendation": row.failure}
        checks = recovery.repeated(store, ref)
        if checks and (missing := judged(store, ref)):
            # Builders could not fix what a judge found missing; that is a product choice, not a fault.
            subject["question"] = (f"The builders could not fix what the judge found missing in {ref}, "
                f"even after the approach you chose: {missing} Tried: "
                f"{len(store.recovery(recovery.lineage(store, ref))['attempts'])} rounds of corrections. "
                "How should we continue?")
            if subject := coach(store, provider, subject, frame=True):
                request(store, "UNIT", subject)
        elif checks and features.enabled("checkContract") and check_failure(store, ref):
            subject["checkFailure"] = True
            subject["question"] = (
                f"The builders could not make these checks pass for {ref} after trying the chosen approach: "
                f"{', '.join(checks)}. Expected them to pass. The same checks failed again. "
                "What should we change so the builders can continue?")
            subject["recommendation"] = (
                "Offer a concrete change to the agreement or approach based on the observed failure.")
            if subject := coach(store, provider, subject, frame=True):
                request(store, "UNIT", subject)
        elif checks:
            # Like a repeated step fault: the same approach again would only loop (R15, R17).
            subject["ask"] = {"question": f"This is Seldon's fault: {ref} failed the same checks again "
                f"({', '.join(checks)}) after the approach you chose. Expected them to pass; got the "
                "failure shown. We recommend Pause until Seldon is fixed.",
                "options": [option("Pause", "Pause here; your work is saved and can resume", "pause"),
                            option("Try again", "Retry this unit if its cause has been fixed", "direct"),
                            option("Leave this out", "Leave out this unit and its dependents", "drop")]}
            request(store, "UNIT", subject)
        elif subject := coach(store, provider, subject):
            request(store, "UNIT", subject)
    finally:
        store.close()


def judged(store, ref):
    """What a judge found missing in the unit's latest correction; None for a check or code failure."""
    attempts = store.recovery(recovery.lineage(store, ref))["attempts"]
    body = store.read(attempts[-1]) if attempts else {}
    sources = [store.read(g["source"]) for g in body.get("gaps", []) if g["kind"] != "check"]
    return " ".join(f"{g['target']}: {g['correction']}" for b in sources
                    for g in questions.must_fix(b.get("answer", {"gaps": []}))) or None


def check_failure(store, ref):
    """Repeated proof failures carry check IDs or a check gap, unlike controller faults."""
    attempt = store.recovery(recovery.lineage(store, ref))["attempts"][-1]
    body = store.read(attempt)
    return body.get("checks") or any(g["kind"] == "check" for g in body.get("gaps", []))


def option(label, means, effect):
    return {"label": label, "means": means, "effect": effect}


def record_fault(store, error):
    step = store.cursor().state
    cause = error.__cause__ if isinstance(error.__cause__, ProviderFailure) else error
    failure = f"{type(cause).__name__}: {cause}"
    shape = {"step": step, "failure": failure}
    frames = [f for f in traceback.extract_tb(cause.__traceback__)
              if Path(__file__).parent == Path(f.filename).parent
              and Path(f.filename).name != "processes.py"
              and not (Path(f.filename).name == "gitwork.py" and f.name == "git")]
    location = (f"src/seldon/{Path(frames[-1].filename).name}:{frames[-1].lineno} {frames[-1].name}"
                if frames else None)
    cause = store.record("step_failure", {**shape, "exceptionType": type(cause).__name__,
        "message": str(cause), "location": location})
    return cause, shape


def pause_ask(reason):
    return {"question": "This is Seldon's fault: recovery did not resolve this step. "
        f"We recommend Stop to pause here until Seldon is fixed.\n{reason}",
        "options": [option("Stop", "Pause here; your work is saved and can resume", "pause"),
                    option("Try again", "Retry this step if its cause has been fixed", "direct")]}


def step_fault(store, provider, error):
    cause, shape = record_fault(store, error)
    step, failure = shape["step"], shape["failure"]
    key = recovery.lineage(store)
    repeated = shape in store.recovery(key)["seen"]
    recovery.failed(store, cause, shape=shape)
    subject = {"step": step, "tried": [failure], "failure": failure}
    if step == "PROVE":
        return owner_question(store, "Seldon", failure, retry="Run proof after the fault is fixed")
    if repeated:
        if isinstance(error, InputRejected):
            subject["ask"] = pause_ask(f"{failure} Seldon owns preparing a smaller evidence packet.")
        else:
            subject["ask"] = pause_ask(str(error))
    else:
        subject = coach(store, provider, subject)
    if subject:
        request(store, step, subject)


def owner_question(store, owner, failure, *, retry=None, subject=None):
    step = store.cursor().state
    options = [option("Pause", "Keep this question pending", "pause")]
    if retry:
        options.append(option(retry, "Confirm the named input is ready to try again", "retry"))
    else:
        options.append(option("Provide a fix", "Describe the concrete change that resolves this failure", "direction"))
    request(store, step, {**(subject or {}), "step": step, "inputOwner": owner, "failure": failure}, ask={
        "question": f"{owner} owns the next change.\n{failure}", "options": options})


def route_repair(store, cause, owner, tree, failure, retry):
    cursor = store.cursor()
    repeated = recovery.failed(store, cause, key=f"lead-build:{cursor.leadBuild}", shape=[owner, tree])
    if owner == "Builder" and tree and not repeated:
        return
    if owner == "Planner" and not repeated:
        request(store, cursor.state, {"step": cursor.state, "inputOwner": owner, "failure": failure,
                                      "question": failure, "tried": [failure]})
    else:
        owner_question(store, owner, failure, retry=retry)


def gate_ask(store, gate, subject):
    if "ask" in subject:
        return subject["ask"]
    if gate == "A":
        opts = [option("Build this", "Build the proposed vision", "approve")]
        opts += [{**o, "effect": "direct"} for o in subject["vision"]["alternatives"]]
    elif gate == "B":
        opts = [option("Build this plan", "Approve the plan", "approve"),
                option("Build it and do not stop at check-ins", "Approve and waive later check-ins", "waive")]
    elif gate == "CHECK-IN":
        opts = [option("approve", "Continue with the proposed next work", "approve"),
                option("ask for changes", "Describe the changes you want", "direct"),
                option("stop", "End the dream here", "stop")]
    elif gate == "C":
        opts = [option("accept", "Accept the demonstrated product", "approve"),
                option("ask for changes", "Describe the changes you want", "direct"),
                option("stop", "End without accepting the product", "stop")]
    else:
        return None  # A planning question is coached by decide before it reaches the user.
    return {"question": f"How should we continue at {gate}?", "options": opts}


def stop(store, words):
    with store.transaction():
        outcome = store.record("outcome", {"accepted": False, "verification_complete": False,
                                           "reason": words})
        advance(store, "stop", outcome, decision=None, failure="")


def request(store, gate, subject, *, ask=None):
    with store.transaction():
        cursor = store.cursor()
        waived = gate == "CHECK-IN" and cursor.waived
        if cursor.decision and not waived:
            return
        if gate == "CHECK-IN":
            subject = {**subject, "open gaps": [b for _, _, b in store.records("open_gap")]}
        key = None
        if gate == "UNIT" or "step" in subject:
            key, subject = recovery.subject(store, gate, subject)
        ask = ask if ask is not None else gate_ask(store, gate, subject)
        summary = []
        if gate in ("A", "B"):
            vision = subject.get("round", cursor.vision)
            summary = [b["summary"] for _, _, b in store.records("answer_normalisation")
                       if b.get("answer") == vision]
        if gate in ("A", "B", "CHECK-IN"):
            stages = {"A": {"A"}, "B": {"ARCHITECT", "DETAIL", "REVIEW"},
                      "CHECK-IN": {"ARCHITECT", "DETAIL", "REVIEW", "EXECUTE", "WAVE-VERIFY"}}[gate]
            summary.extend(b["summary"] for _, _, b in store.records("answer_normalisation")
                if b.get("vision") == cursor.vision and b.get("wave") == cursor.wave and b.get("stage") in stages)
        if gate in ("B", "CHECK-IN"):
            summary.extend(b["summary"] for _, _, b in store.records("scope_normalisation")
                           if b.get("wave") == cursor.wave and "summary" in b)
        if summary:
            subject = {**subject, "normalisations": list(dict.fromkeys(summary))}
        ref = store.record("decision_request", {"gate": gate, "subject": subject,
            **({"ask": ask} if ask else {}),
            "origin": store.read(cursor.kickoff)["setup"].get("userOrigin", "user")})
        if waived:
            approval = store.record("approval", {"request": ref, "subject": subject,
                "words": store.record("user_words", {"request": ref, "words": "waived by the user",
                    "origin": "user waiver"}), "choice": "approve"})
            store.cursor(replace(cursor, checkin=approval))
            return
        store.cursor(replace(cursor, decision=ref))
        if key:
            state = store.recovery(key)
            state["question"] = ref
            store.recovery(key, state)


SIZE_CHOICES = {
    "tiny": ("Micro", "Minutes in one plain-agent session. Start with one builder and one runnable delivery."),
    "small": ("Small", "Days of single-agent effort. Keep one owner where possible, with runnable milestones."),
    "medium": ("Medium", "Weeks of single-agent effort. Plan in waves with owners and agreements at real seams."),
    "large": ("Big", "Months of single-agent effort. Plan the full work with staged delivery and named owners."),
}


def round_ask(answer, *, size_first=False):
    def group(key, question, options, affects):
        return {"id": key, "question": question, "default": options[0]["id"],
                "options": options, "affects": affects}

    groups = [group(f"q{i}", item["question"],
                    [{"id": f"o{j}", **o} for j, o in enumerate(item["options"], 1)], item["affects"])
              for i, item in enumerate(answer["questions"], 1)]
    size = answer["vision"]["size"]
    options = []
    for key in (size, *(key for key in SIZE_CHOICES if key != size)):
        rationale = answer["vision"]["sizeWhy"] if key == size else ""
        label, means = SIZE_CHOICES[key] if size_first else (key.capitalize(), f"Plan for a {key} dream")
        if rationale:
            means = f"{means} {rationale}" if size_first else rationale
        options.append({"id": key, "label": label, "means": means})
    question = ("How much coordination should Seldon plan for? You can override the recommendation. "
                "Requested delivery checkpoints still apply." if size_first else "What size should Seldon plan for?")
    groups.append(group("size", question, options, "size"))
    options = [
        {"id": "revise", "label": "Keep shaping this", "means": "Review another proposal before locking A"},
        {"id": "done", "label": "This is it", "means": "Lock the displayed vision with its displayed defaults"},
    ]
    if answer["recommendation"] == "done":
        options.reverse()
    groups.append(group("round", "Are alignment and expansion done?", options, "vision"))
    return {"delta": answer["changed"], "why": answer["why"], "groups": groups, "retained": []}


def picked_options(groups, selections):
    if not selections:
        raise ValueError("Expected at least one selected option; got an empty reply.")
    options = {g["id"]: {o["id"]: o for o in g["options"]} for g in groups}
    picked = {}
    for selection in selections:
        group, choice, text = (selection.get(k, "") for k in ("groupId", "optionId", "text"))
        valid = (isinstance(text, str) and bool(text.strip()) if choice == "other"
                 else text == "" and choice in options.get(group, {}))
        if group not in options or group in picked or not valid:
            raise ValueError(f"Expected one displayed option per group or nonempty Other text. "
                             f"Got group {group!r}, option {choice!r}, text {text!r}.")
        picked[group] = {"optionId": choice, "text": text}
    return picked


def fold_round(store, ref, item, receipt, picked, interpretation=None):
    # For prose this is nested inside inference's answer transaction.
    with store.transaction():
        cursor = store.cursor()
        if cursor.decision != ref:
            return
        ask = item["ask"]
        groups = {g["id"]: g for g in ask["groups"]}
        selected = []
        for key, selection in picked.items():
            choice, text = selection["optionId"], selection["text"]
            if choice == "unclear":
                continue
            group = groups[key]
            option = next((o for o in group["options"] if o["id"] == choice), None)
            selected.append({"groupId": key, "question": group["question"], "affects": group["affects"],
                "default": group["default"], **selection, "label": option["label"] if option else "Other",
                "means": option["means"] if option else text})
        words = receipt.get("words", "\n".join(s["text"] or s["label"] for s in selected))
        raw = store.record("user_words", {**receipt, "words": words, "origin": item["origin"],
                                          "interpretation": interpretation})
        retained = ask["retained"] + [{**s, "source": raw} for s in selected]
        round_choice = next((s["optionId"] for s in retained if s["groupId"] == "round"), None)
        setup = store.read(cursor.kickoff)["setup"]
        answered = {s["groupId"] for s in retained}
        remaining = [g for g in ask["groups"] if g["id"] not in answered]
        reestimate = (setup.get("sizeFirst", False) and [g["id"] for g in remaining] == ["round"]
                      and any(s["groupId"] == "size" and s["optionId"] != s["default"] for s in retained))
        if not round_choice and not reestimate:
            failure = "A is still open. Answer the remaining choices or explicitly lock the displayed defaults."
            if selected:
                store.cursor(replace(cursor, decision=None, failure=failure))
                request(store, "A", item["subject"], ask={**ask, "retained": retained,
                    "groups": remaining})
            else:
                store.cursor(replace(cursor, failure=failure))
            return

        approve = round_choice == "done" and all(s["optionId"] == s["default"]
                  for s in retained if s["groupId"] != "round")
        approval = store.record("approval", {"request": ref, "subject": item["subject"],
            "words": raw, "userWords": raw, "interpretation": interpretation, "selections": retained,
            "scripted": setup.get("scriptedUser", False),
            "choice": "done" if approve else "revise", "effect": "approve" if approve else "direct"})
        if approve:
            advance(store, "approve", approval, decision=None, failure="", A=approval,
                    vision=item["subject"]["round"])
        else:
            advance(store, "revise", approval, decision=None, failure="", requestedChange=raw)


def decide_round(store, provider, ref, item, reply):
    if not isinstance(reply, dict) or reply.get("request") != ref or not reply.get("replyId"):
        with store.transaction():
            store.cursor(replace(store.cursor(), failure=f"A is still open. Use answer request {ref} "
                                 "with a fresh replyId and your words or selected options."))
        raise Waiting()
    if any(row["request"] == ref and row.get("replyId") == reply["replyId"]
           for _, _, row in store.records("user_words")):
        raise Waiting()

    groups = item["ask"]["groups"]
    try:
        if ("words" in reply) == ("selections" in reply):
            raise ValueError("Expected either verbatim words or selections; got both or neither.")
        if "words" in reply:
            words = reply["words"]
            if not isinstance(words, str) or not words.strip():
                raise ValueError("Expected the user's words; got an empty or non-text reply.")
            brief = questions.batch_decide_brief(groups, words)
            brief["prompt"] += (" A request to change the proposal belongs in a product group, or in "
                "the round's Other when it fits no product group. If a material request or the user's "
                "intent to accept the remaining defaults is ambiguous, do not select done.")
            brief["facts"]["Retained choices"] = item["ask"]["retained"]
            inference.call(store, provider, brief, check=lambda a: questions.check_batch_decide(a, words, record=recorder(store)),
                consume=lambda source, a: fold_round(store, ref, item, reply, a["selections"], source),
                binding=ref, field="selections")
        else:
            fold_round(store, ref, item, reply, picked_options(groups, reply["selections"]))
    except ValueError as error:
        with store.transaction():
            store.record("user_words", {**reply, "words": reply.get("words", ""), "origin": item["origin"],
                                       "rejected": True})
            store.cursor(replace(store.cursor(), failure=f"Could not use the A reply: {error} "
                                 f"Use answer request {ref} with a fresh replyId and the displayed selections."))
        raise Waiting() from error
    if store.cursor().state == "A":
        raise Waiting()


def decide(store, provider, user):
    cursor = store.cursor()
    ref = cursor.decision
    item = store.read(ref)
    if "ask" not in item:
        subject = coach(store, provider, item["subject"])
        if subject is None:
            return
        with store.transaction():
            store.cursor(replace(store.cursor(), decision=None))
            request(store, item["gate"], subject)
        return decide(store, provider, user)
    words = user({"request": ref, **item})
    if "groups" in item["ask"]:
        return decide_round(store, provider, ref, item, words)
    scripted = store.read(cursor.kickoff)["setup"].get("scriptedUser", False)
    input_owner = item["subject"].get("inputOwner")
    if (scripted or input_owner) and item["gate"] not in ("A", "B", "CHECK-IN", "C") and isinstance(words, dict):
        raise Waiting()
    raw = store.record("user_words", {"request": ref, "words": words, "origin": item["origin"]})
    direction, interpretation = str(words), None
    if isinstance(words, dict):
        if (set(words) != {"request", "subject", "choice"} or words["request"] != ref
                or words["subject"] != item["subject"]
                or words["choice"] not in ("approve", "revise", "decline")):
            raise ValueError("Decision response must bind to this displayed request and subject")
        choice = words["choice"]
        effect = {"approve": "approve", "revise": "direct", "decline": "stop"}[choice]
    else:
        options = item["ask"]["options"]
        normal = lambda text: "".join(text.casefold().split())
        picked = next((o for o in options if normal(o["label"]) == normal(words)), None)
        if picked is None:
            selection = {}
            def consume(source, answer):
                choice = answer["choice"]
                picked = next((o for o in options if o["label"] == choice), None)
                effect = picked["effect"] if picked else {
                    "Other": "direct", "Stop": "stop", "Waive": "waive", "Unclear": None}[choice]
                selection.update(picked=picked, choice=choice, effect=effect, why=answer["why"])
            question = questions.build("DECIDE", {
                "Decision": item["gate"], "Displayed subject": item["subject"],
                "Displayed options": item["ask"], "User's words": words},
                targets=[o["label"] for o in options] + (["Waive"] if item["gate"] == "CHECK-IN" else []))
            if input_owner:
                question["prompt"] += (" Choose Other only for a concrete change to the named input. "
                                       "Acknowledgement, approval and a request to continue are Unclear.")
            interpretation, _ = inference.call(store, provider, question,
                consume=consume, field="choice")
            picked, choice, effect = (selection[k] for k in ("picked", "choice", "effect"))
            if effect is None:
                if not input_owner:
                    with store.transaction():
                        store.cursor(replace(store.cursor(), failure=selection["why"]))
                raise Waiting()
        else:
            choice, effect = picked["label"], picked["effect"]
        if picked:
            direction = picked["label"] + ": " + picked["means"]
    if effect in ("pause", "direction") or (input_owner and effect == "direct" and
            (choice != "Other" or any(
                b.get("effect") == "direct" and b["subject"].get("inputOwner") == input_owner
                and b["subject"].get("failure") == item["subject"]["failure"]
                and store.read(b["userWords"])["words"] == words
                for _, _, b in store.records("approval") if "userWords" in b))):
        raise Waiting()  # Keep the displayed request and step intact for a later answer.
    with store.transaction():
        cursor = store.cursor()
        if cursor.decision != ref:
            raise ValueError("Decision changed; obtain a fresh response")
        directed = (store.record("decision_direction", {"words": direction, "verbatim": raw})
                    if effect == "direct" and direction != str(words) else raw)
        approval = store.record("approval", {"request": ref, "subject": item["subject"],
            "words": directed, "userWords": raw, "interpretation": interpretation, "scripted": scripted,
            "choice": "revise" if effect == "direct" else choice, "effect": effect})
        recovery.answered(store, item["subject"], raw)
        if effect == "stop":
            stop(store, words)
            return
        updates = {"decision": None, "failure": ""}
        if input_owner:
            updates["failure"] = item["subject"]["failure"]
        if effect == "waive":
            updates["waived"] = True
        if item["gate"] == "UNIT":
            if effect == "drop":
                for owner in sorted(affected(store, item["subject"]["unit"])):
                    row = store.unit(owner)
                    if row.state == "WAITING":
                        advance(store, "asked", approval, unit=owner)
                    if store.unit(owner).state == "ASKED":
                        advance(store, "drop", approval, unit=owner, waiting="")
            else:
                again(store, item["subject"], raw, direction)
            store.cursor(replace(store.cursor(), **updates))
        elif "step" in item["subject"]:
            store.cursor(replace(cursor, **updates, requestedChange=directed))
        elif effect == "direct":
            advance(store, "revise" if item["gate"] in ("A", "B") else "steer", raw,
                    **updates, requestedChange=directed)
        elif item["gate"] in ("A", "B", "CHECK-IN"):
            field = {"A": "A", "B": "B", "CHECK-IN": "checkin"}[item["gate"]]
            if cursor.state == item["gate"]:
                advance(store, "approve", approval, **updates, **{field: approval})
            else:
                store.cursor(replace(cursor, **updates, **{field: approval}))
        elif item["gate"] == "C":
            from .run import eligible
            from .judgment import not_judged
            eligible(store)
            deferred = not_judged(store)
            outcome = store.record("outcome", {"accepted": not scripted, "scriptedUser": scripted,
                "verification_complete": not bool(deferred), "notJudged": deferred,
                "candidate": cursor.candidate, "approval": approval,
                "reason": "Scripted C, not owner acceptance" if scripted else "User accepted the demo commit",
                "nextAction": None, "failed": [], "blocked": []})
            advance(store, "scripted" if scripted else "approve", outcome, decision=None)
