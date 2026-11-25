"""C9/C14/C15: thin acceptance through actual worker processes, Git and final reviews."""

import json
import sys
import time
from copy import deepcopy
from pathlib import Path
from threading import Barrier

import pytest

from seldon import unit_questions
from seldon import gitwork, plan, questions
from seldon.providers import ROLES, Codex, ProviderFailure
from seldon.run import Run




@pytest.mark.fast
@pytest.mark.parametrize("provider_name,lead_mode", [("codex", False), ("claude", True)])
def test_provider_works_without_disturbing_desktop(tmp_path, monkeypatch, provider_name, lead_mode):
    """A scripted builder commits through the real provider launch without opening an editor.

    An inherited failing editor catches a missing override; prompt and browser checks protect
    desktop use, which the existing scripted provider journeys do not exercise.
    """
    from seldon import providers

    product = tmp_path / "product"
    product.mkdir()
    gitwork.git(product, "init", "--quiet")
    (product / "item.txt").write_text("work", encoding="utf-8")
    hook = product / ".git/hooks/prepare-commit-msg"
    hook.write_text('#!/bin/sh\nprintf "Scripted work\\n" > "$1"\n', encoding="utf-8")
    hook.chmod(0o755)
    gitwork.git(product, "add", "item.txt")
    monkeypatch.setenv("GIT_EDITOR", "nonexistent-desktop-editor")
    monkeypatch.setenv("BROWSER", "nonexistent-desktop-browser %s")
    native = tmp_path / "native.py"
    native.write_text(
        "import json, subprocess, sys, webbrowser\n"
        "prompt = sys.stdin.read()\n"
        "assert 'The person is using this computer' in prompt\n"
        "assert 'never open files or pages in desktop apps' in prompt\n"
        "assert 'checks/kit/browser.cjs' in prompt and 'save screenshots as evidence' in prompt\n"
        "assert webbrowser.open('https://example.invalid/')\n"
        "subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@local', "
        "'commit'], check=True, timeout=15, capture_output=True)\n"
        + ("print(json.dumps({'type':'result','subtype':'success','result':'done'}))\n"
           if provider_name == "claude" else
           "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'done'}}))\n"
           "print(json.dumps({'type':'turn.completed'}))\n"), encoding="utf-8")
    setup = {"providers": providers.roles(provider_name), provider_name + "Command": [sys.executable, str(native)]}
    calls = tmp_path / "calls"
    question = {"kind": "WORK", "prompt": "Build", "facts": {"Milestone brief": "Build"},
                "schema": {"type": "object"}, "examples": [{"answer": {}}],
                "execution": {"cwd": str(product), "lead": lead_mode}}
    answer, _ = providers.Provider(calls, setup, timeout=30).invoke(question, lambda _: None)
    assert answer == "done"
    assert gitwork.git(product, "log", "-1", "--format=%s") == "Scripted work"


@pytest.mark.fast
def test_fresh_lead_native_liveness_journey(tmp_path, monkeypatch):
    """A silent writer is recovered once while an active writer can outlive the quiet window."""
    from seldon import inference, providers
    from seldon.lead2 import build_brief, chat

    home = tmp_path / "native"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    provider_script = home / "provider.py"
    provider_script.write_text(
        "import json, os, sys, time\n"
        "from pathlib import Path\n"
        "thread = 'cee7ba47-baef-4134-85b4-cd2358eb1de0'\n"
        "def send(**event): print(json.dumps(event), flush=True)\n"
        "send(type='thread.started', thread_id=thread)\n"
        "log = Path(os.environ['CODEX_HOME']) / 'sessions' / ('rollout-' + thread + '.jsonl')\n"
        "log.parent.mkdir(exist_ok=True)\n"
        "def usage(fresh):\n"
        " total = {'input_tokens': fresh + 18, 'cached_input_tokens': 20, 'output_tokens': 2}\n"
        " log.write_text(json.dumps({'payload': {'type':'token_count', "
        "'info':{'total_token_usage':total}}}, separators=(',', ':')) + '\\n')\n"
        "if 'resume' not in sys.argv:\n"
        " usage(12)\n"
        " time.sleep(10)\n"
        "else:\n"
        " assert sys.argv[sys.argv.index('resume') + 1] == thread\n"
        " assert 'Continue from the existing files' in sys.stdin.read()\n"
        " for _ in range(7):\n"
        "  send(type='item.completed', item={'type':'reasoning'})\n"
        "  time.sleep(.22)\n"
        " usage(19)\n"
        " send(type='item.completed', item={'type':'agent_message','text':json.dumps(dict(outcome='done'))})\n"
        " send(type='turn.completed')\n", encoding="utf-8")
    setup = {"providers": providers.roles(), "codexCommand": [sys.executable, str(provider_script)],
             "lead2SilenceSeconds": {"build": .9}}
    provider = providers.Provider(tmp_path / "calls", setup, timeout=.1)
    run = Run(tmp_path / "product", tmp_path / "workers", "Build a small tool.", setup, provider, None, mode="lead2")
    try:
        question = build_brief.question("WORK", build_brief.WORK, "Build the tool.")
        question["execution"]["cwd"] = str(run.project)
        _, answer = inference.call(run.store, provider, question, normalise=build_brief.normalise)
        assert answer["outcome"] == "done"
        first, second = chat.state(run.store, run.store.cursor(), run.project, False)["calls"]
        assert first["frozen"] and not second["frozen"] and second["elapsedSeconds"] > 1.4
        assert [first["freshPlusOutputTokens"], second["freshPlusOutputTokens"]] == [12, 7]
    finally:
        run.close()


@pytest.mark.fast
def test_quiet_bench_size_calibration_journey(tmp_path, monkeypatch):
    """ALIGN coaches effort on the recorded unstaged bench and honors the owner's size at A.

    The staged override journey checks checkpoint retention, so it misses a prompt
    that treats several local surfaces as weeks of work for one agent.
    """
    from seldon import features, frontdoor, schema

    monkeypatch.setattr(features, "enabled", lambda name: name != "plannerBrief")
    monkeypatch.setattr(features, "planning_setup", lambda: {"sizeFirst": True, "split": "auto"})
    fixture = Path(__file__).with_name("fixtures") / "quietbench-q01-align.json"
    recorded = json.loads(fixture.read_text(encoding="utf-8"))
    facts, medium = recorded["facts"], recorded["answer"]
    micro = deepcopy(medium)
    micro["vision"].update(size="tiny", sizeWhy="The owner chose one plain session for the local bench.")
    approved = []

    def provider(q):
        strict_schema(q["schema"])
        if q["kind"] == "ALIGN":
            assert q["facts"]["Dream"] == facts["Dream"]
            assert q["facts"]["Runtime"] == facts["Runtime"]
            assert q["facts"]["Sources"]["kickoff"] == facts["Sources"]["kickoff"]
            prompt = q["prompt"].lower()
            assert "how long one capable plain agent" in prompt
            micro_anchor = prompt.split("micro means minutes", 1)[1].split("small means days", 1)[0]
            assert all(example in micro_anchor for example in
                       ("single-file cli", "sqlite and an importer", "signal-processing bench"))
            medium_anchor = prompt.split("medium means weeks", 1)[1].split("big means months", 1)[0]
            assert "multi-service product with auth and migrations" in medium_anchor
            assert "what would make it bigger" in prompt
            example = next(e["answer"]["vision"] for e in q["examples"]
                           if "noise filters" in e["answer"]["vision"]["understanding"])
            assert example["size"] == "tiny"
            override = q["facts"].get("Owner size override")
            assert override in (None, "tiny")
            answer = micro if override else medium
        else:
            assert q["kind"] == "ARCHITECT"
            approved.append(q["facts"]["Approved vision"])
            commitments = list(q["facts"]["Commitment handles"])
            answer = {"areas": [{"handle": "bench", "responsibility": "Deliver the local bench",
                                "scope": ["bench/**"], "commitments": commitments}],
                      "waves": [{"goal": "Deliver the local bench", "commitments": commitments}],
                      "contracts": [], "decisions": [], "why": "One owner can deliver the bench."}
        response = {"answer": answer}
        schema.validate(response, q["schema"])
        return json.dumps(response)

    def choose(item, group, option):
        return {"request": item["request"], "replyId": option,
                "selections": [{"groupId": group, "optionId": option}]}

    product = tmp_path / "quiet-bench"
    run = Run(product, tmp_path / "workers", facts["Dream"], facts["Runtime"], provider, lambda _: None)
    try:
        assert run.step() == "A"
        waiting = frontdoor.state(product)["waitingFor"]
        size = next(g for g in waiting["ask"]["groups"] if g["id"] == "size")
        assert size["default"] == "medium" and size["options"][0]["label"] == "Medium"
        assert medium["vision"]["sizeWhy"] in size["options"][0]["means"]
        run.user = lambda item: choose(item, "size", "tiny")
        assert run.step() == "ALIGN"
        run.close()
        run = Run.reopen(product, provider, lambda item: choose(item, "round", "done"))
        assert run.step() == "A"
        revised = frontdoor.state(product)["waitingFor"]
        assert revised["subject"]["vision"] == micro["vision"]
        size = next(g for g in revised["ask"]["groups"] if g["id"] == "size")
        assert size["default"] == "tiny" and size["options"][0]["label"] == "Micro"
        assert not approved
        assert run.step() == "ARCHITECT"
        assert run.step() == "DETAIL"
        assert approved == [micro["vision"]]
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("guide_only", [False, True])
def test_recorded_quiet_bench_guide_scope_journey(tmp_path, monkeypatch, guide_only):
    """Replay q01's mixed scope and guide-only replies through progressive planning.

    Clean scopes in the existing wave journey miss lost page work and a repeated guide
    claim with no product paths to keep. Replay the wave-one replan replies at the
    wave-two planning boundary, where the established guide owner must survive.
    """
    from dataclasses import replace
    from seldon import decisions, features, schema

    monkeypatch.setattr(features, "enabled", lambda name: name != "plannerBrief")
    replies = json.loads((Path(__file__).parent / "fixtures/quietbench-q01-guide-scopes.json")
                         .read_text(encoding="utf-8"))
    round_answer = questions.tiny_round_example()
    vision = round_answer["vision"]
    vision.update(understanding="Run and revisit a local bench measurement", size="small")
    vision["quality"][0]["handle"] = "visual"
    vision["stories"][0].update(handle="sessions", outcome="Save the local bench session")
    page_scope = ["web/**", "checks/page/**"]
    guide_scope = ["docs/try-it.md", "docs/try-it/**"]
    exact, coached, asked = [], [], []

    def provider(q):
        strict_schema(q["schema"])
        kind, facts = q["kind"], q["facts"]
        if kind == "ALIGN":
            answer = round_answer
        elif kind == "ARCHITECT":
            answer = {"areas": [
                {"handle": "session", "responsibility": "Save bench sessions",
                 "scope": ["bench/**"], "commitments": ["scopeIn:sessions"]},
                {"handle": "page", "responsibility": "Display and revisit the session",
                 "scope": page_scope + (guide_scope if "Previous architecture" in facts else []),
                 "commitments": ["quality:visual"]}],
                "contracts": [], "decisions": [],
                "waves": [{"goal": "Save the session", "commitments": ["scopeIn:sessions"]},
                          {"goal": "Revisit the session", "commitments": ["quality:visual"]}]}
            if "Previous architecture" in facts:
                answer["areas"][1]["commitments"].append("scopeIn:sessions")
                answer["waves"] = []
        elif kind == "DETAIL" and facts["Current wave"]["goal"] == "Save the session":
            answer = {"verdict": "planned", "edits": [
                {"kind": "feature", "targetHandle": "session", "typedBody": {
                    "story": "Save a bench run", "acceptance": "The run can be reopened",
                    "commitments": ["scopeIn:sessions"]}},
                {"kind": "requirement", "targetHandle": "save",
                 "typedBody": {"acceptance": "Reload the stored settings", "features": ["session"]}}],
                "units": [{"scope": ["bench/**"], "requirements": ["save"], "dependencies": [],
                           "instructions": "Persist the session"}]}
        elif kind == "DETAIL":
            if "Exact corrections" in facts:
                exact.extend(facts["Exact corrections"][0]["Errors"])
            answer = replies["r385" if guide_only else "r239"]
        elif kind == "COACH":
            coached.append(str(facts["Unresolved work"]))
            answer = {"guidance": "Give the page builder a product scope."}
        else:
            assert kind == "REVIEW"
            answer = {"verdict": "adequate", "gaps": []}
        response = {"answer": {"why": "Plan the accepted bench experience", **answer}}
        schema.validate(response, q["schema"])
        return json.dumps(response)

    def user(item):
        if "groups" in item["ask"]:
            return {"request": item["request"], "replyId": "guide-scope-lock",
                    "selections": [{"groupId": "round", "optionId": "done"}]}
        if "step" in item["subject"]:
            asked.append(item["ask"])
            return item["ask"]["options"][0]["label"]
        return approve(item)

    run = Run(tmp_path / "product", tmp_path / "workers", "Save and revisit a bench measurement",
              {}, provider, user)
    try:
        for phase in ("A", "ARCHITECT", "DETAIL", "REVIEW", "B", "EXECUTE"):
            assert run.step() == phase
        cursor = run.store.cursor()
        before = run.store.plan(cursor.revision)
        owner = plan.guide_owner(before)
        original = plan.select(before, "unit")[owner]
        run.store.cursor(replace(cursor, state="CHECK-IN"))
        assert run.step() == "ARCHITECT"
        run.step()  # Consume the check-in reply.
        assert run.step() == "DETAIL"
        if guide_only:
            stopped = run.run()
            assert stopped.state == "DETAIL" and stopped.decision
            assert len(exact) == 2 and exact[0] == exact[1]
            assert all(word in exact[0] for word in (
                "unit:1", "expected", "got", *guide_scope, "Send", "walkthrough owner"))
            assert len(coached) == 1 and exact[0] in coached[0]
            request, = asked
            assert exact[0] in request["question"]
            assert request["options"][0]["effect"] == "pause"
            assert run.store.cursor().B == cursor.B
            return
        assert run.step() == "REVIEW"
        assert run.step() == "EXECUTE"
        assert not exact and not asked
        after = plan.execution(run.store)
        assert plan.guide_owner(after) == owner
        units = plan.select(after, "unit")
        assert (units[owner]["area"], units[owner]["scope"]) == (original["area"], original["scope"])
        page, = [unit for unit in units.values() if unit["area"] == "page"]
        assert page["scope"] == page_scope
        assert [(row["field"], row["removed"]) for _, _, row in run.store.records("scope_normalisation")] == [
            ("areas.scope", guide_scope), ("units.scope", ["docs/try-it.md"])]
    finally:
        run.close()


@pytest.mark.fast
def test_grow_align_receipt_and_lock_journey(tmp_path, monkeypatch):
    """A partial reply and a replayed mixed done/change cannot lock an unseen vision.

    Scalar approval tests miss the interruption between receiving several choices and
    folding them, and cannot catch a draft reaching ARCHITECT or B as the accepted vision.
    """
    from seldon import decisions, features, frontdoor, schema

    enabled = {"growAlign", "sizeFirst", "answerRepair"}
    monkeypatch.setattr(features, "enabled", enabled.__contains__)
    # Verbatim excerpts from Quiet Bench d56dc7b call-f5f75800. At r10 the recorded
    # simulator said "Build this". The grouped planner and owner replies are counterfactual.
    launch = "python server.py --port PORT --data DIR"
    dream = (f"Every session is saved on a small local storage server (`{launch}`) with its "
             "settings and results, and a web page on that server lets me start a run, browse "
             "saved sessions, reload one and compare two.")
    stopped = ("Report stopped and limited runs truthfully, including how many samples were processed; "
               "do not present a partial run as completed.")
    first = questions.consequential_round_example()
    first["vision"]["size"] = "large"  # The recorded three-wave bench was rated large.
    first["vision"]["stories"][1].update(outcome=stopped)
    for field in ("stories", "quality", "scopeOut"):
        for entry in first["vision"][field]:
            entry["sources"] = ["kickoff"]
    first["questions"].append({"affects": "scopeIn:inspect", "question": "How can a colleague inspect it?",
        "options": [{"label": "Reopen a saved run", "means": "Open it on this PC"},
                    {"label": "Export a copy", "means": "Open it on another bench"}]})
    mixed = "This is it, except I need a printable result"
    changed = "Compare only the shared measured interval and label the printable result partial."
    revised = deepcopy(first)
    revised.update(changed=changed, questions=[], recommendation="done")
    display = {}
    architect = []
    seen = []

    def provider(q):
        strict_schema(q["schema"])
        kind, facts = q["kind"], q["facts"]
        seen.append(kind)
        if kind == "ALIGN":
            if seen.count("ALIGN") == 1:
                assert facts["Dream"] == dream
                answer = first
            else:
                assert facts["Previous proposal"]["vision"] == first["vision"]
                assert facts["Owner size override"] == "medium"
                assert mixed in facts["Sources"].values()
                replies = {s["label"]: s["source"] for s in facts["Selected choices"]}
                interval = replies["Compare shared interval"]
                printing = replies["Other"]
                assert "Compare shared interval" in facts["Sources"][interval]
                assert facts["Sources"][printing] == mixed
                revised["vision"].update(size="medium", sizeWhy="The owner selected medium for planning.")
                revised["vision"]["stories"][0].update(outcome=changed, sources=[interval, printing])
                answer = deepcopy(revised)
                answer["vision"].update(size="large", sizeWhy="The draft still assumes large effort.")
        elif kind == "DECIDE":
            assert facts["User's verbatim words"] == mixed
            picks = {g["id"]: {"optionId": "unclear", "text": ""} for g in facts["Displayed groups"]}
            picks[display["handoff"]] = {"optionId": "other", "text": "printable result"}
            picks["round"] = {"optionId": "done", "text": ""}
            answer = {"selections": picks, "why": "Explicit done and a changed handoff"}
        elif kind == "ARCHITECT":
            architect.append(facts["Approved vision"])
            assert facts["Approved vision"] == revised["vision"]
            goals = list(facts["Commitment handles"])
            answer = {"areas": [{"handle": "bench", "responsibility": "Compare and print",
                                "scope": ["bench/**"], "commitments": goals}],
                      "contracts": [], "decisions": [],
                      "waves": [{"goal": "A printable partial comparison", "commitments": goals}]}
        elif kind == "DETAIL":
            goal = facts["Accepted commitments"]["scopeIn:compare"]
            assert goal["outcome"] == changed
            answer = {"verdict": "planned", "edits": [
                {"kind": "feature", "targetHandle": "f", "typedBody": {
                    "story": goal["story"], "acceptance": goal["outcome"],
                    "commitments": ["scopeIn:compare"]}},
                {"kind": "requirement", "targetHandle": "r", "typedBody": {
                    "acceptance": goal["outcome"], "features": ["f"]}}],
                "units": [{"scope": ["bench/**"], "requirements": ["r"], "dependencies": [],
                           "instructions": "Show and print the shared measured interval"}]}
        else:
            assert kind == "REVIEW"
            answer = {"verdict": "adequate", "gaps": []}
        response = {"answer": {**answer, "why": answer.get("why", "Trace the accepted bench outcome")}}
        schema.validate(response, q["schema"])
        return json.dumps(response)

    product = tmp_path / "product"
    setup = {"userOrigin": "counterfactual owner"}
    run = Run(product, tmp_path / "workers", dream, setup, provider, lambda _: None)
    try:
        assert run.step() == "A"
        initial = frontdoor.state(product)["waitingFor"]
        assert initial and initial["subject"]["vision"] == first["vision"]
        assert run.store.cursor().vision is None and not architect
        comparison, handoff = initial["ask"]["groups"][:2]
        display["handoff"] = handoff["id"]
        run.user = lambda _: {"request": initial["request"], "replyId": "invalid",
                              "selections": [{"groupId": comparison["id"], "optionId": "missing"}]}
        with pytest.raises(decisions.Waiting):
            run.step()
        invalid = frontdoor.state(product)
        assert invalid["waitingFor"] == initial
        assert all(part in invalid["reason"] for part in
                   ("Expected one displayed option", "'missing'", f"Use answer request {initial['request']}"))
        run.user = lambda _: {"request": initial["request"], "replyId": "interval",
            "selections": [{"groupId": comparison["id"], "optionId": comparison["options"][1]["id"]},
                           {"groupId": "size", "optionId": "medium"}]}
        with pytest.raises(decisions.Waiting):
            run.step()
        partial = frontdoor.state(product)["waitingFor"]
        assert comparison["id"] not in [g["id"] for g in partial["ask"]["groups"]]
        assert partial["ask"]["retained"][0]["label"] == "Compare shared interval"

        reply = {"request": partial["request"], "replyId": "print", "words": mixed}
        run.user = lambda _: reply
        record = run.store.record

        def interrupt_after_receipt(kind, body):
            ref = record(kind, body)
            if kind == "user_words" and body.get("replyId") == "print":
                raise KeyboardInterrupt
            return ref

        run.store.record = interrupt_after_receipt
        with pytest.raises(KeyboardInterrupt):
            run.step()
        run.close()
        enabled.remove("growAlign")  # An open round keeps its stored shape.
        run = Run.reopen(product, provider, lambda _: reply)
        assert frontdoor.state(product)["waitingFor"] == partial
        assert run.step() == "ALIGN"
        assert run.step() == "A"
        shown = frontdoor.state(product)["waitingFor"]
        assert shown["subject"]["vision"] == revised["vision"]
        assert any("size selected by the owner" in note for note in shown["subject"]["normalisations"])
        with pytest.raises(decisions.Waiting):
            run.step()  # Transport redelivery of the superseded request.
        assert shown["request"] in frontdoor.state(product)["reason"]
        assert run.store.cursor().A is None and run.store.cursor().vision is None and not architect
        receipts = [r for _, _, r in run.store.records("user_words") if r.get("replyId") == "print"]
        assert len(receipts) == 1 and receipts[0]["words"] == mixed and receipts[0]["origin"] == setup["userOrigin"]

        run.user = lambda _: {"request": shown["request"], "replyId": "lock",
                              "selections": [{"groupId": "round", "optionId": "done"}]}
        for phase in ("ARCHITECT", "DETAIL", "REVIEW", "B"):
            assert run.step() == phase
        run.user = lambda _: (_ for _ in ()).throw(decisions.Waiting())
        with pytest.raises(decisions.Waiting):
            run.step()
        b = frontdoor.state(product)["waitingFor"]["subject"]["plan"]
        locked = b["nodes"]["vision"]["body"]
        assert architect == [locked] == [shown["subject"]["vision"]]
        feature = next(f for f in plan.select(b, "feature").values() if f["acceptance"] == changed)
        story = questions.commitments(locked)[feature["commitments"][0]]
        assert run.store.read(story["sources"][1]) == receipts[0]
        approval = run.store.read(run.store.cursor().A)
        assert approval["request"] == shown["request"] and approval["subject"]["vision"] == locked
        assert seen.count("ALIGN") == 2
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize(("loose_answer", "review_reply"),
                         [(False, "button"), (False, "other"), (True, None)])
def test_fresh_lead_reviewed_spec_journey(tmp_path, monkeypatch, loose_answer, review_reply):
    """A stale reply cannot approve a new vision or undo a review repair before B.

    The graph-only journey bypasses kickoff, the provider, displayed options and bind-on-reopen.
    It cannot catch early approval, lost option context, or a trace gap stalling B.
    A review button applies its own edit before B, while Other spends one planner call.
    """
    import asyncio

    from seldon import decisions, frontdoor
    from seldon.lead2 import SpecStore, render_person
    from seldon.providers import render_question

    dream = "Let volunteers share the coat check. Keep receipts usable when the tablet restarts."
    other = "A volunteer can also hand off to the next shift while a coat is still out."
    calls = []
    draft = {"build_order": [{"outcome": "1. Check in and recover a coat: restore the claim", "recommend_demo": True},
                             {"outcome": "2. Add a printed claim card", "recommend_demo": False},
                             {"outcome": "3. Share the shift handover", "recommend_demo": True}]}
    access = [{"need": "Desk tablet", "supplier": "Volunteer lead"},
              {"need": "Spare receipt printer", "supplier": ""}]
    qualification = "Approve, only if the CSV includes each correction reason."
    review_question = "Where can people claim their coat?"
    counter = "Offer a printed claim lookup beside the existing desk."

    def add(name, kind, **body):
        return {"op": "add", "name": name, "node": {"kind": kind, **body}}

    def link(source, target):
        return {"op": "link", "source": source, "target": target}

    def flat(edits):
        fields = {"person": [], "story": [], "promise": [], "design": [], "interface": [],
                  "check": [], "walk": [], "retire": [], "link": [], "unlink": []}
        links = {}
        for edit in edits:
            if edit["op"] in ("link", "unlink"):
                links.setdefault((edit["op"], edit["source"]), []).append(edit["target"])
            elif edit["op"] == "retire":
                fields["retire"].append(edit["id"])
            else:
                node = edit["node"].copy()
                fields[node.pop("kind")].append({"ref": edit.get("name") or edit["id"], **node})
        for (op, source), targets in links.items():
            fields[op].append(dict(source=source, targets=targets))
        return fields

    def material_question(question, label):
        return dict(question=question, recommended=label, recommended_means="Show the same record",
                    alternative="Use a separate list", alternative_means="Keep a local log")

    def fill(story):
        return [
            add("receipt", "promise", action="Claim a coat after restart",
                result="The receipt still finds it", not_met_if="A restart loses the receipt"),
            add("storage", "design",
                decision="Claim storage module: save the claim before showing the receipt",
                reason="Durability", words=other),
            add("claims", "interface", contract="Claim identity persists across shifts and restarts."),
            add("restart", "check", action="Claim restart test: restart after allocating a claim",
                expected="Claim is retrievable"),
            add("handoff", "walk", scenario="Two volunteers see an outstanding claim",
                interruption="The first shift signs out before return"),
            link(story, "$receipt"), link("$receipt", "$storage"), link("$storage", "$claims"),
            link("$claims", "$restart"),
        ]

    def provider(q):
        kind, facts = q["kind"], q["facts"]
        calls.append(kind)
        pending = [q["schema"]]
        while pending:
            field = pending.pop()
            assert not {"anyOf", "oneOf", "allOf", "$ref"} & field.keys()
            pending.extend(field.get("properties", {}).values())
            if "items" in field:
                pending.append(field["items"])
        proposal = facts["Current proposal"]
        if "B condition" not in facts:
            assert facts["Owner's technical preference"] == (
                "Yes, show me the technical choices" if loose_answer else "No, keep it in business terms")
        prompt, _ = render_question(q)
        assert facts["Owner's words"] == dream and prompt.startswith(dream)
        words = SpecStore(run.store).snapshot().graph.nodes
        assert all(ref not in prompt for ref in words)
        story = next((line.split("]")[0][1:] for line in proposal.splitlines()
                      if '"kind":"story"' in line and "Two shifts share" in line), None)
        if kind == "LEAD2-ALIGN":
            count = calls.count(kind)
            edits, role_refs, sources = [], ["desk"], ["brief"]
            if count == 1:
                edits = [add("desk", "person", role="Volunteer"),
                         add("shift", "person", role="Incoming shift")]
                edits.append(add("handoff", "story", story="Two shifts share and recover outstanding claims"))
                story = "handoff"
                if loose_answer:
                    role_refs.append(story)  # A redundant self-link is discarded before the batch is applied.
            else:
                owner_reply = "Share a live list" if count == 2 else other
                assert facts["Owner's chosen size"] == "small"
                assert owner_reply in facts["Earlier replies"]
                assert any(row["alternative"] == "Use a separate list"
                           for row in facts["Previous questions and options"])
                sources += [block.splitlines()[0] for block in facts["Earlier replies"].split("\n\n")]
                role_refs = []
            questions = [
                material_question("Which list do volunteers use?", "Share a live list"),
                material_question("What if the first shift leaves early?", "Keep the claim open"),
                material_question("How should an interrupted claim look?", "Show the outstanding claim"),
            ]
            edits.extend(link(ref, story) for ref in role_refs + sources)
            rows = flat(edits)
            answer = dict(reason="A volunteer checks in a coat, hands off the claim, then retrieves it.",
                          **{kind: rows[kind] for kind in ("person", "story", "retire", "link", "unlink")},
                          questions=[questions[count - 1]],
                          aligned=count != 2, size="micro", size_reason="One coat desk")
            if count == 1 and loose_answer:
                return json.dumps({**answer, "build_order": draft["build_order"]})
        elif kind == "LEAD2-STACK":
            revising = calls.count(kind) == 2
            if revising:
                assert "Use PostgreSQL" in facts["Earlier replies"]
            choice = "Use PostgreSQL for claims" if revising else "Keep claims in a local database"
            answer = dict(reason="Keep claims available offline",
                          stack=[dict(ref="database", decision=choice,
                                      reason="Reopen claims without internet", words=dream)],
                          questions=[dict(question="Which database should we use?", recommended="SQLite",
                                          recommended_means="Store on the desk tablet", alternative="Use PostgreSQL",
                                          alternative_means="Use the existing shop server")]
                          if loose_answer and not revising else [])
        elif kind == "LEAD2-SPEC":
            assert "accepted default" in proposal and "Show the outstanding claim" in proposal
            assert "Use a separate list" not in proposal
            if condition := facts.get("B condition"):
                assert condition == qualification
                assert "build_order" not in q["schema"]["properties"]["answer"]["properties"]
                export = next(line.split("]")[0][1:] for line in proposal.splitlines()
                              if "An export lists each outstanding claim" in line)
                patch = dict(ref=export,
                             result="An export lists each outstanding claim and correction reason",
                             not_met_if="The CSV omits any correction reason.")
                return json.dumps({"answer": dict(reason="Preserve the correction reason",
                                                   amendments=[patch], retire=[story], build_order=[])})
            if resolving := facts.get("Resolve only this review reply"):
                assert resolving == counter and counter in facts["Earlier replies"]
                edits = [add("counter", "design", decision=counter,
                             reason="Owner chose paper", words=counter)]
            else:
                assert ("Use PostgreSQL for claims" if loose_answer else "Keep claims in a local database") in proposal
                edits = fill(story) + [link("$receipt", "$handoff")]
                role = next(line.split("]")[0][1:] for line in proposal.splitlines() if "Volunteer" in line)
                if loose_answer:
                    edits.extend([add("setup", "story", story="A replacement tablet can recover an open claim"),
                              link("brief", "setup"), link(role, "setup"), link("setup", "$receipt"),
                              add("queue", "story", story="An incoming volunteer sees the pickup queue"),
                              link("brief", "queue"), link(role, "queue"),
                              add("handover", "story",
                                  story="An incoming volunteer can find the handover note"),
                              link("brief", "handover"), link(role, "handover"),
                              add("lost", "story", story="A volunteer can look for a lost claim"),
                              link("brief", "lost"), link(role, "lost")])
            answer = dict(reason="Specify the complete handoff", **flat(edits), **draft,
                          mockups=[dict(id="queue", label="Queue first", means="Show handoffs at the top"),
                                   dict(id="lookup", label="Lookup first", means="Lead with receipt lookup")],
                          access=access if calls.count(kind) == 1 else [])
        else:
            assert kind == "REVIEW" and facts["Proposed build order"] == draft["build_order"]
            assert facts["Proposed access needs"] == access
            assert other in facts["Earlier replies"] and "A restart loses the receipt" in proposal
            if calls.count(kind) > 1:
                gaps = facts["Missing proof links"]
                if calls.count(kind) == 3:
                    assert facts["Focus on this unresolved issue"] == "Confirm the queue link after handover."
                    return json.dumps({"answer": dict(reason="Verify queue coverage", findings="Queue is linked.",
                                                       unresolved_issue="")})
                assert facts["Focus on this unresolved issue"] == "Link the pickup queue to the export."
                assert any("pickup queue" in gap for gap in gaps)
                assert "An export lists each outstanding claim" in proposal
                queue = next(line.split("]")[0][1:] for line in proposal.splitlines()
                             if "pickup queue" in line)
                export = next(line.split("]")[0][1:] for line in proposal.splitlines()
                              if "An export lists each outstanding claim" in line)
                return json.dumps({"answer": dict(reason="Link the queue", findings="One story gap remains.",
                                                   unresolved_issue="Confirm the queue link after handover.",
                                                   link=[dict(source=queue, targets=[export])])})
            walk = next(line.split("]")[0][1:] for line in proposal.splitlines() if '"kind":"walk"' in line)
            edits = [{"op": "amend", "id": walk, "node": {
                "kind": "walk", "scenario": "Two volunteers recover a claim and return the coat",
                "interruption": "Restart the tablet before the second shift scans the receipt",
            }}]
            promise = next(line for line in proposal.splitlines() if '"kind":"promise"' in line)
            ref, body = promise.split("] ", 1)
            node = json.loads(body)
            design = next(line.split("]")[0][1:] for line in proposal.splitlines()
                          if "Claim storage module" in line)
            if loose_answer:
                node["result"] = "The next shift finds the same outstanding coat after restart"
                edits.append({"op": "amend", "id": ref[1:], "node": node})
                edits.extend([add("export", "promise", action="Export outstanding claims",
                                  result="An export lists each outstanding claim",
                                  not_met_if="A claim is absent"),
                              link(story, "export"), link(design, walk)])
            else:
                node["result"] = "The receipt still finds it after a restart"
                edits.append({"op": "amend", "id": ref[1:], "node": node})
                for format_, label in (("print", "export"), ("screen", "screen"), ("CSV", "csv")):
                    edits.extend([add(label, "promise", action=f"Open the claims {format_} output",
                                      result=f"Claims {format_} — pickup time and claim ID, {format_} rows",
                                      not_met_if="An outstanding claim is omitted"), link(story, label)])
                edits.append({**link(ref[1:], design), "op": "unlink"})
            edits.append(link(ref[1:], walk))
            role = next(line.split("]")[0][1:] for line in proposal.splitlines() if "Incoming shift" in line)
            edits.append(link(role, story))
            if loose_answer:
                default = next(line.split("]")[0][1:] for line in proposal.splitlines()
                               if "Show the outstanding claim" in line and '"kind":"design"' in line)
                edits.append({"op": "retire", "id": default})
            answer = dict(reason="Exercise the original restart constraint", build_order=[], questions=[],
                          findings="The walk misses a tablet restart while the claim is still out.",
                          unresolved_issue="Link the pickup queue to the export." if loose_answer else "",
                          replaces=[{"source": ref[1:], "targets": ["export"] if loose_answer
                                     else ["export", "screen", "csv"]}],
                          **flat(edits))
            if review_reply:
                answer["questions"] = [dict(id="claim-desk", question=review_question,
                                            recommended_id="original", alternative_id="either", recommended="Original desk",
                                            recommended_means="Keep claims at their original desk.",
                                            alternative="Either desk",
                                            alternative_means="Let both desks claim coats.")]
                options = (("original", "at the original desk"), ("either", "at either desk"))
                answer["option_promise"] = [
                    dict(option_id=option, ref=ref[1:], title="", action=node["action"],
                         result=f"The receipt still finds it {place} after a restart", not_met_if=node["not_met_if"])
                    for option, place in options
                ]
        return json.dumps({"answer": answer})

    product = tmp_path / "product"
    monkeypatch.setenv("SELDON_MODE", "lead2")
    run = Run(product, tmp_path / "workers", dream, {"scriptedUser": True}, provider, None)
    run.user = lambda _: (_ for _ in ()).throw(decisions.Waiting())
    initial_commit = run.store.cursor().candidate
    try:
        def reply(name, *selected, words=None):
            displayed = frontdoor.state(product)["waitingFor"]
            envelope = {"request": displayed["request"], "replyId": name,
                        "selections": [dict(groupId=key, optionId=option, **extra)
                                       for key, option, extra in selected]}
            if words is not None:
                # Use the chat tool to prove that grouped ingress preserves both selection and exact words.
                side = frontdoor.side(product)
                side.mkdir(exist_ok=True)
                result = asyncio.run(frontdoor.server().call_tool(
                    "answer", {"run": str(product), **envelope, "words": words},
                ))
                assert not result.is_error, result
                if name == "list":
                    returned = json.loads(result.content[0].text)
                    assert returned["waitingFor"] is None and "round 2" in returned["reason"]
                run.user = lambda _: frontdoor.read_answer(side / "answer.json")
            else:
                run.user = lambda _: envelope
            return displayed

        assert run.step() == "ALIGN" and not calls
        kickoff = reply("kickoff", ("technical", "technical" if loose_answer else "plain", {}))
        assert kickoff["ask"]["groups"][0]["question"] == "Are you technical?"
        assert kickoff["ask"]["groups"][0]["options"][-1]["id"] == "other"
        assert run.step() == "ALIGN" and not calls
        assert run.step() == "A"
        spec = SpecStore(run.store)
        assert not spec.pins()  # Planner recommended alignment in the first round.
        first = reply("list", ("q1", "o1", {}), ("size", "small", {}), words="Share a live list")
        assert first["ask"]["groups"][0]["options"][-1]["label"] == "Other"
        assert [o["label"] for o in first["ask"]["groups"][0]["options"]] == [
            "Share a live list", "Use a separate list", "Other"]
        assert first["ask"]["delta"].startswith("A volunteer checks in a coat")
        assert first["ask"]["groups"][-1]["question"] == "Anything else we missed that you'd like to include?"
        assert sum(g["id"] == "size" for g in first["ask"]["groups"]) == 1
        assert "build_order" not in run.store.read(run.store.cursor().vision)["answer"]
        if loose_answer:
            assert run.store.records("lead2_normalisation")[-1][2]["ignoredEdits"] == 1
        assert run.step() == "ALIGN"
        assert run.step() == "A"
        second = reply("handoff", ("anything-else", "other", {"text": other}), ("round", "approve", {}))
        assert second["ask"]["groups"][-1]["id"] == "anything-else"
        assert all(g["id"] != "size" for g in second["ask"]["groups"])
        stale_user = run.user
        assert run.step() == "ALIGN"  # The simultaneous request for a change must be folded first.
        assert other in render_person(spec)
        assert run.step() == "A"
        run.close()

        monkeypatch.setenv("SELDON_MODE", "lead")
        run = Run.reopen(product, provider, stale_user)
        spec = SpecStore(run.store)
        with pytest.raises(decisions.Waiting):
            run.step()
        assert not spec.pins()
        a = reply("vision lock", ("round", "approve", {}))
        assert a["ask"]["groups"][-1]["id"] == "anything-else"
        assert a["subject"]["size"] == "small"  # The scripted planner kept recommending micro.
        assert run.step() == "L2-STACK"
        run.close()
        run = Run.reopen(product, provider, stale_user)
        spec = SpecStore(run.store)
        assert run.step() == "L2-STACK"
        with pytest.raises(decisions.Waiting):
            run.step()
        if loose_answer:
            stack = reply("change stack", ("q1", "o2", {}), ("round", "approve", {}))
            assert stack["ask"]["groups"][0]["options"][0]["label"] == "SQLite"
            assert run.step() == "L2-STACK" and run.step() == "L2-STACK"
        stack = reply("stack", ("round", "approve", {}))
        assert stack["gate"] == "Stack" and "Reopen claims without internet" in stack["ask"]["proposal"]
        assert run.step() == "L2-SPEC"
        assert run.step() == "L2-SPEC"
        mockup = reply("visual", ("mockup", "queue", {}))
        assert mockup["gate"] == "Mockups"
        assert run.step() == "L2-REVIEW"
        assert "Queue first" in render_person(spec, detailed=True)
        if loose_answer:
            current = frontdoor.state(product, full_spec=True)
            assert current["waitingFor"] is None
            assert "A replacement tablet can recover an open claim" in current["spec"]
        pinned = spec.snapshot(spec.pins()["A"])
        assert pinned.parent == a["subject"]["lead2_revision"]
        assert any(n.get("reason") == "accepted default" and "Show the outstanding claim" in n["decision"]
                   for n in pinned.graph.nodes.values())
        assert run.step() == "L2-REVIEW"
        if not loose_answer:
            if review_reply == "button":
                choice = reply("where", ("claim-desk", "either", {}))
                assert choice["ask"]["groups"][0]["options"][1]["label"] == "Either desk"
                assert "at either desk" in choice["ask"]["groups"][0]["options"][1]["means"]
                assert run.step() == "B"
                assert "The receipt still finds it at either desk" in render_person(spec)
                assert "at the original desk" not in render_person(spec)
            else:
                reply("where", ("claim-desk", "other", {"text": counter}))
                assert run.step() == "L2-REVIEW"
                assert run.step() == "B"
                assert counter in render_person(spec, detailed=True)
        else:
            assert run.step() == "L2-REVIEW"
            assert run.step() == "B"
        assert run.step() == "B"
        offered = frontdoor.state(product)["waitingFor"]["ask"]["proposal"]
        assert "1. Check in and recover a coat" in offered and "1. 1." not in offered
        assert "### Demonstrations\n- Delivery 1\n- Delivery 3" in offered
        assert "Desk tablet — Volunteer lead" in offered
        assert "Spare receipt printer — Can't test here" in offered
        if not loose_answer:
            assert ("- **Claims** · pickup time and claim ID,\n"
                    "  - CSV · CSV rows\n  - print · print rows\n  - screen · screen rows") in offered
        assert "### Trace gaps" in offered
        assert ("Story has no promise linked yet" if loose_answer else "Promise has no design yet") in offered
        if loose_answer:
            original_graph = spec.snapshot().graph
            original = reply("condition", ("round", "approve", {}), words=qualification)
            assert run.step() == "L2-SPEC" and "B" not in spec.pins()
            assert run.step() == "B" and run.step() == "B"
            revised = frontdoor.state(product)["waitingFor"]
            assert revised["request"] != original["request"]
            summary = revised["ask"]["proposal"]
            assert "### Changes since the last offer" in summary and qualification in summary
            assert "The CSV omits any correction reason" in summary
            assert "Desk tablet — Volunteer lead" in summary
            assert "Spare receipt printer — Can't test here" in summary
            assert "Story has no promise linked yet: A volunteer can look for a lost claim" in summary
            updated = spec.snapshot().graph
            changed = [ref for ref, node in original_graph.nodes.items() if node != updated.nodes[ref]]
            assert len(changed) == 1
            assert "An export lists each outstanding claim" in updated.nodes[changed[0]]["result"]
            assert original_graph.links == updated.links
        b = reply("demo", ("delivery-1", "show", {}))
        groups = {g["id"]: g for g in b["ask"]["groups"]}
        assert groups["delivery-1"]["default"] == "show" and groups["delivery-2"]["default"] == "skip"
        assert "1. Check in" not in groups["delivery-1"]["question"]
        assert "Restart the tablet before the second shift scans the receipt" not in b["ask"]["proposal"]
        full = frontdoor.state(product, full_spec=True)
        assert "Restart the tablet before the second shift scans the receipt" in full["spec"]
        assert "Claim storage module" in full["spec"] and "Claim restart test" in full["spec"]
        assert full["buildOrder"] == draft["build_order"]
        expected_result = "omits any correction reason" if loose_answer else "The receipt still finds it"
        assert expected_result in b["ask"]["proposal"]
        assert "Check in and recover a coat" in b["ask"]["proposal"]
        assert run.step() == "B"
        assert "B" not in spec.pins()
        waiting = reply("spec lock", ("round", "approve", {}))
        assert expected_result in waiting["ask"]["proposal"]
        assert waiting["ask"]["retained"][0]["label"] == "Show me this working"
        assert "### Demonstrations\n- Delivery 1" in waiting["ask"]["proposal"]
        assert run.step() == "L2-READY"
        cursor = run.store.cursor()
        run.close()
        assert cursor.state == "L2-READY"
        receipt = run.store.read(cursor.B)
        assert receipt["demonstrations"] == ["delivery-1", "delivery-3"]
        assert receipt["scripted"] is True and receipt["request"] == waiting["request"]
        assert spec.pins()["B"] == b["subject"]["lead2_revision"] > spec.pins()["A"]
        assert any(n.get("reason") == "accepted default" and "Show the outstanding claim" in n["decision"]
                   for n in spec.snapshot(spec.pins()["B"]).graph.nodes.values())
        assert run.store.read(receipt["userWords"])["words"] == "Approve the spec at B"
        status = frontdoor.state(product)
        assert status["status"] == "PAUSED"
        assert status["build"]["stage"] == "building"
        assert status["controller"] == "idle"
        assert status["approvedSpec"]["revision"] == spec.pins()["B"]
        assert status["approvedSpec"]["buildOrder"] == draft["build_order"]
        assert status["approvedSpec"]["accessNeeds"] == access
        from seldon.lead2.status_read import status as read_pane

        pane = read_pane(run.store)
        latest = pane["frames"][-1]
        assert pane["frames"][-2]["activity"] == "Plan approved at B"
        assert latest["activity"] == "Ready to build" and latest["chunk"] == "chunk 1 of 3"
        # The revised export still appears in the approved trace after the conditional B loop.
        assert any(expected_result in row["feature"]["text"] for row in pane["trace"][latest["revision"]])
        assert gitwork.git(product, "rev-parse", "HEAD") == initial_commit
        expected_calls = ["REVIEW", "REVIEW", "REVIEW", "LEAD2-SPEC"] if loose_answer else ["REVIEW"]
        if review_reply == "other":
            expected_calls.append("LEAD2-SPEC")
        assert calls == ["LEAD2-ALIGN"] * 3 + ["LEAD2-STACK"] * (2 if loose_answer else 1) + [
            "LEAD2-SPEC", *expected_calls]
        if review_reply == "button":
            _build_coat_journey(product, dream)
    finally:
        run.close()


def _build_coat_journey(product, dream):
    """Continue over an in-flight review and carry an approved edit even after a done return."""
    import threading

    from seldon import frontdoor
    from seldon.lead2 import SpecStore
    from seldon.lead2.status_read import status as read_pane
    from seldon.providers import render_question

    change = "A blue tag is shown on the coat claim."
    archive_result = "The manager can read yesterday's claims."
    clarified = "Front desk opens the claim when asked."
    review_started, builder_continued = threading.Event(), threading.Event()
    turns = []
    reviews = []
    walks = []
    defect_choices = []

    class Builder:
        def invoke(self, question, _):
            if question["kind"] == "LEAD2-WALK":
                from base64 import b64decode

                facts = question["facts"]
                checkout = Path(question["execution"]["walk"])
                assert checkout != Path(spec.build_status()["workspace"])
                assert gitwork.git(checkout, "rev-parse", "HEAD") == facts["Candidate"]
                assert not question["execution"]["session"]
                assert (checkout / "TRY.md").is_file()
                demo = bool(facts["Selected demonstration"])
                product_text = (checkout / "coat.txt").read_text(encoding="utf-8")
                clean = demo or "blue tag fixed" in product_text
                archive_broken = "archive hidden" in product_text
                archive = next((ref for ref, node in facts["Promises to judge"].items()
                                if node["result"] == archive_result), "")
                archive_story = next((ref for ref, row in facts["Stories to walk"].items()
                                      if archive in row["promises"]), "")
                if not demo:
                    walks.append(facts)
                stories = facts["Stories to walk"]
                picture = Path(facts["Evidence directory"]) / "claim.png"
                picture.write_bytes(b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
                                              "/x8AAwMCAO+aX1sAAAAASUVORK5CYII="))
                results = [{"promise": ref, "outcome": "works" if clean or ref != promise_ref else "broken",
                            "reason": "Claim survives restart" if clean else "Restart lost the saved claim"}
                           for ref in facts["Promises to judge"]]
                if archive_broken:
                    result = next(row for row in results if row["promise"] == archive)
                    result.update(outcome="broken", reason="Claim history is hidden")
                steps = [{"story": stories[ref]["walks"][0] if ref == archive_story else ref,
                          "role": "Front desk", "action": "Recover a coat claim",
                          "expected": "Claim persists and shows the tag",
                          "actual": "The saved claim and tag are visible" if clean or ref != story
                          else "The claim was lost and the blue tag is missing",
                          "outcome": "works" if clean or ref != story else "broken",
                          "screenshot": picture.name} for ref in stories]
                findings = []
                if archive_broken:
                    findings = [dict(story=archive_story, promise=archive, defect="history-hidden",
                                     owner="builder", finding="Restore access to claim history")]
                elif not clean:
                    findings = [
                        {"story": story, "promise": promise_ref, "defect": "claim-lost",
                         "owner": "builder", "finding": "Restore the claim after restart"},
                        {"story": story, "promise": promise_ref, "defect": "tag-missing",
                         "owner": "builder", "finding": "Show the blue tag on the restored claim"}]
                elif not demo:
                    findings = [
                        {"story": "", "owner": "access", "finding": "No label printer was available."}]
                return json.dumps(dict(steps=steps, promises=results, findings=findings,
                                       code_map="works", code_map_reason="Actual paths exist",
                                       guide="works", guide_path="TRY.md", guide_reason="Used the sample job",
                                       quality="can't-test-here" if clean and not demo else "works",
                                       quality_reason="No label printer was available." if clean and not demo else "",
                                       sample_job="Use claim 42.")), f"walker-{len(walks)}-{demo}"
            if question["kind"] == "LEAD2-WALK-STEER":
                facts = question["facts"]
                missing = [row for row in facts["Findings"] if row["owner"] == "planner"]
                recovery = next(ref for ref, walk in facts["Walk scenarios"].items()
                                if "recover a claim" in walk["scenario"])
                link = [{"source": row["finding"].rstrip(".").split()[-1],
                         "targets": [recovery]} for row in missing]
                direction = "Fix both observed coat regressions together."
                return json.dumps({"direction": direction, "link": link}), None
            if question["kind"] == "LEAD2-STEER":
                assert question["facts"]["Builder report"] == "Which collection desk is available for a check?"
                return json.dumps({"direction": "The spec does not name a desk we can use.", "questions": [{
                    "question": "Which desk is available for the receipt check?",
                    "recommended": "Use the front desk", "recommended_means": "Exercise lookup at the front.",
                    "alternative": "Use the side desk", "alternative_means": "Exercise lookup at the side.",
                }]}), None
            if question["kind"] == "LEAD2-CHUNK":
                reviews.append(question["facts"]["Feature"])
                if not question["facts"]["Checks"][0]["passed"]:
                    review_started.set()
                    assert builder_continued.wait(10)
                    return '{"verdict":"broken","reason":"The draft has no working coat claim."}', None
                if question["facts"]["Feature"] == feature:
                    assert question["facts"]["Prior_findings"]
                    assert question["facts"]["Unverified_for_C"] == ["The physical label printer is unavailable here."]
                    assert len(question["facts"]["Checks"]) == 1
                assert question["facts"]["Checks"][0]["passed"]
                return '{"verdict":"works","reason":"The earlier gap is closed."}', None
            assert question["kind"] == "WORK"
            assert question["execution"]["session"] == ("coat-session" if turns else None)
            turns.append(question)
            prompt, _ = render_question(question)
            workspace = Path(question["execution"]["cwd"])
            coat = workspace / "coat.txt"
            test = workspace / "check.py"
            chunk = {"delivery": "delivery-1: Verify the first coat flow", "command": [sys.executable, test.name]}
            mapping = [dict(promise=ref, modules=["./coat.txt", "./missing.txt"], tests=[test.name])
                       for ref, node in spec.snapshot().graph.nodes.items() if node["kind"] == "promise"]
            for row in mapping:
                if spec.snapshot().graph.nodes[row["promise"]]["result"] == archive_result:
                    row.update(modules=["archive.txt", "coat.txt"], tests=["archive_check.py"])
            chunks = [chunk]
            if len(turns) == 1:
                assert prompt.startswith(dream)
                assert "Not met if — A restart loses the receipt" in prompt
                assert "Planned code and tests" in prompt
                assert change not in prompt
                coat.write_text("draft")
                (workspace / "TRY.md").write_text("Open coat.txt and try claim 42.", encoding="utf-8")
                (workspace / "archive.txt").write_text("Yesterday: claim 41")
                (workspace / "archive_check.py").write_text(
                    "from pathlib import Path\nassert 'claim 41' in Path('archive.txt').read_text()\n")
                test.write_text("import sys\nfrom pathlib import Path\n"
                                "assert 'ready' in Path('coat.txt').read_text() and '--ready' in sys.argv\n")
                return json.dumps({"outcome": "partial", "note": "Finish the coat lookup", "chunks": chunks,
                                   "code_map": mapping, "demos": ["delivery-1"]}), "coat-session"
            if len(turns) == 5:
                assert clarified in prompt and coat.read_text(encoding="utf-8") == "ready — blue tag"
                return json.dumps(dict(outcome="done", acknowledged=True, code_map=mapping)), "coat-session"
            if len(turns) == 6:
                assert "Restore the claim after restart" in prompt and "Show the blue tag" in prompt
                coat.write_text("ready — blue tag fixed / archive hidden", encoding="utf-8")
                return json.dumps(dict(outcome="done", acknowledged=True, code_map=mapping)), "coat-session"
            if len(turns) in (7, 8):
                assert "Restore access to claim history" in prompt
                coat.write_text(f"ready — blue tag fixed / archive hidden (revision {len(turns)})", encoding="utf-8")
                return json.dumps(dict(outcome="done", acknowledged=True, code_map=mapping)), "coat-session"
            if len(turns) == 9:
                assert defect_choices
                coat.write_text("ready — blue tag fixed", encoding="utf-8")
                (workspace / "archive.txt").rename(workspace / "claims-history.txt")
                check = workspace / "archive_check.py"
                check.write_text(check.read_text().replace("archive.txt", "claims-history.txt"))
                for row in mapping:
                    if "archive.txt" in row["modules"]:
                        row["modules"].remove("archive.txt")
                        row["modules"].insert(0, "claims-history.txt")
                return json.dumps(dict(outcome="done", acknowledged=True, code_map=mapping)), "coat-session"
            assert coat.read_text() == "draft"
            if len(turns) == 2:
                assert change in prompt
                assert "The physical label printer is unavailable here." in prompt
                assert prompt.startswith("Continue: Finish the coat lookup")
                assert "Stories and promises" not in prompt
                assert review_started.wait(10)
                builder_continued.set()
                (workspace / "unrelated.txt").write_text("The claim code and its tests are unchanged.")

                return json.dumps({"outcome": "done", "acknowledged": False}), "coat-session"
            if len(turns) == 3:
                assert change in prompt
                assert "The draft has no working coat claim." in prompt and "Check failed" in prompt
                return json.dumps({"outcome": "partial", "acknowledged": True,
                                   "note": "Which collection desk is available for a check?"}), "coat-session"
            assert len(turns) == 4 and "Use the front desk" in prompt
            assert change not in prompt
            coat.write_text("ready — blue tag", encoding="utf-8")
            chunk["command"] = [sys.executable, test.name, "--ready"]
            spec.apply(dict(revision=spec.snapshot().revision, mode="substantive", reason=clarified, edits=[
                {"op": "add", "name": None, "node": {"kind": "design", "decision": clarified,
                    "reason": "Record which desk is available.", "words": "Use the front desk"}},
            ]), author="owner")
            chunks = [chunk, {**chunk, "delivery": "delivery-2 and delivery-3: Exercise the new flows"}]
            return json.dumps({"outcome": " Done ", "note": "Coat lookup and blue tag work",
                               "acknowledged": True, "chunks": chunks, "code_map": mapping}), "coat-session"

    def user(request):
        if request["gate"] == "C":
            from seldon.decisions import Waiting

            raise Waiting()
        if request["gate"] == "Walk decision" and "defects" in request["subject"]:
            defect_choices.extend(request["subject"]["defects"])
            return {"request": request["request"], "replyId": "new-archive-approach",
                    "selections": [{"groupId": "defect", "optionId": "approach"}]}
        if request["gate"] == "Walk decision":
            assert request["subject"]["limits"][0]["promise"] == "quality"
            assert "No label printer was available." in request["ask"]["proposal"]
            return {"request": request["request"], "replyId": "scripted-limit",
                    "selections": [{"groupId": "limits", "optionId": "allow"}]}
        if request["gate"] == "scope":
            assert change in request["ask"]["proposal"]
            group, option = "round", "approve"
        else:
            shown = frontdoor.state(product)["waitingFor"]
            assert shown["gate"] == "Build question"
            assert [o["label"] for o in shown["ask"]["groups"][0]["options"]] == [
                "Use the front desk", "Use the side desk", "Other"]
            group, option = "q1", "o1"
        return {"request": request["request"], "replyId": "mara-approved",
                "selections": [{"groupId": group, "optionId": option}]}

    provider = Builder()
    run = Run.reopen(product, provider, user)
    try:
        spec = SpecStore(run.store)
        approved = spec.snapshot(spec.pins()["B"])
        promise_ref, promise = next((ref, node["result"]) for ref, node in approved.graph.nodes.items()
                                    if node.get("result", "").startswith("The receipt still finds it"))
        story = next(ref for ref, node in approved.graph.nodes.items()
                     if node["kind"] == "story" and promise_ref in approved.graph.follow(ref))
        feature = "Check in and recover a coat: restore the claim"
        assert run.step() == "L2-READY"
        partial = spec.build_status()
        assert partial["features"][feature] == "under review"
        assert partial["demos"] == ["Check in and recover a coat: restore the claim"]
        observed, witness = next((ref, child) for ref, n in approved.graph.nodes.items() if n["kind"] == "promise"
                                 for child in approved.graph.follow(ref)
                                 if approved.graph.nodes[child]["kind"] in {"check", "walk"})
        spec.observe(approved.revision, promises=[observed], witness=witness,
                     commit=partial["candidate"], session=partial["session"], outcome="not-verifiable-here",
                     result="The physical label printer is unavailable here.")
        dream_ref = next(ref for ref, node in approved.graph.nodes.items() if node["kind"] == "dream")
        staged = spec.apply(dict(revision=approved.revision, mode="substantive", reason=change, edits=[
            {"op": "add", "name": "tag", "node": {"kind": "promise", "action": "Open the claim",
                "result": change, "not_met_if": "The tag is missing"}},
            {"op": "link", "source": story, "target": "$tag"},
            {"op": "add", "name": "archive_story",
             "node": {"kind": "story", "story": "Read yesterday's claims"}},
            {"op": "add", "name": "archive", "node": {"kind": "promise", "action": "Open archive",
                "result": archive_result, "not_met_if": "Yesterday's claims are missing"}},
            {"op": "add", "name": "archive_walk", "node": {"kind": "walk", "scenario": "Find claim 41",
                "interruption": "Start a new shift before viewing yesterday's claims"}},
            {"op": "link", "source": dream_ref, "target": "$archive_story"},
            {"op": "link", "source": "$archive_story", "target": "$archive"},
            {"op": "link", "source": "$archive", "target": "$archive_walk"},
        ]), author="owner")
        assert not staged.accepted and spec.snapshot().revision == approved.revision
        run.close()
        run = Run.reopen(product, provider, user)
        spec = SpecStore(run.store)
        assert run.run().state == "C"
        waiting = frontdoor.state(product)["waitingFor"]
        assert waiting["gate"] == "C"
        assert "Open coat.txt and try claim 42." in waiting["ask"]["proposal"]
        assert len(walks) == 5
        assert all(set(facts["Stories to walk"]) == set(walks[0]["Stories to walk"]) > {story}
                   for facts in walks[1:])
        archive_promise = next(ref for ref, node in spec.snapshot().graph.nodes.items()
                               if node.get("result") == archive_result)
        assert defect_choices == [f"{archive_promise}:history-hidden"]
        status = frontdoor.state(product)["build"]
        assert status["session"] == "coat-session"
        assert status["unverified"][0]["outcome"] == "not-verifiable-here"
        assert status["acceptedRevision"] == status["acknowledgedRevision"] > staged.revision
        assert status["candidate"] != partial["candidate"]
        assert status["features"][feature] == "passed"
        pane = read_pane(run.store)
        states = list(dict.fromkeys(frame["features"][0][1] for frame in pane["frames"]
                                    if "features" in frame))
        assert states == ["planned", "building", "under review", "failed", "fixing", "passed"]
        assert pane["frames"][-1]["activity"] == "Waiting on you"
        assert any(frame.get("judgment", {}).get(promise_ref) == "fixing" for frame in pane["frames"])
        assert pane["frames"][-1]["judgment"][promise_ref] == "proven-current"
        history = [frame.get("judgment", {}).get(archive_promise) for frame in pane["frames"]]
        assert history.index("stale") < history.index("failed") < len(history) - 1
        assert history[-1] == "proven-current"
        assert pane["unverified"][0]["text"] == "The physical label printer is unavailable here."
        assert reviews[:2] == [feature, feature]
        assert set(reviews[2:]) == {"Add a printed claim card", "Share the shift handover"}
        assert {row["promise"] for row in status["codeMap"]} == {
            ref for ref, n in spec.snapshot().graph.nodes.items() if n["kind"] == "promise"}
        assert {tuple(row["modules"] + row["tests"]) for row in status["codeMap"]} == {
            ("coat.txt", "check.py"), ("claims-history.txt", "coat.txt", "archive_check.py")}
        assert gitwork.git(status["workspace"], "show", f"{status['candidate']}:coat.txt") == (
            "ready — blue tag fixed")
        run = Run.reopen(product, provider, lambda item: {
            "request": item["request"], "replyId": "scripted-C",
            "selections": [{"groupId": "round", "optionId": "approve"}],
        })
        assert run.run().state == "SCRIPTED_C"
        assert frontdoor.state(product)["outcome"]["acceptedByPerson"] is False
    finally:
        run.close()


@pytest.mark.fast
def test_fresh_lead_attempt_and_pause_journey(tmp_path, monkeypatch):
    """Transport recovery can retry. An unusable answer waits for an explicit resume.

    The approval journey cannot catch hidden retries, raw provider warnings leaking into chat,
    or the idle controller reporting a paused run as active.
    """
    from types import SimpleNamespace

    from seldon import decisions, frontdoor
    from seldon.lead2 import chat

    warning = '{"type":"warning","message":"private diagnostic"}'
    clock = time.time
    monkeypatch.setattr(chat, "time", SimpleNamespace(time=lambda: clock() + 180))
    product = tmp_path / "product"

    class Planner:
        calls = 0

        def invoke(self, question, started):
            self.calls += 1
            progress = frontdoor.state(product)
            assert progress["status"] == "RUNNING" and "resume" not in progress["next"].lower()
            assert progress["attempt"]["number"] == (2 if self.calls == 2 else 1)
            assert "3 min" in progress["reason"] and warning not in progress["reason"]
            if self.calls == 1:
                raise ProviderFailure(f"Provider timed out; {warning}", True, True)
            if self.calls == 2:
                return json.dumps({"answer": {"reason": 12,
                                   "questions": [], "aligned": False, "size": "micro",
                                   "size_reason": "One screen"}}), None
            if self.calls == 4:
                raise ProviderFailure(f"Provider timed out; {warning}", False, True)
            assert "Exact corrections" not in question["facts"]
            answer = {"reason": "Keep the shelf visible", "aligned": False, "size": "micro",
                      "size_reason": "One screen", "questions": [], "retire": [],
                      "person": [{"ref": "keeper", "role": "Librarian"}],
                      "story": [{"ref": "shelf", "story": "View the shelf"}],
                      "link": [{"source": "brief", "targets": ["shelf"]},
                               {"source": "keeper", "targets": ["shelf"]}]}
            return json.dumps({"answer": answer}), None

    def waiting(_):
        raise decisions.Waiting()

    planner = Planner()
    run = Run(product, tmp_path / "workers", "Show a shared shelf.", {}, planner, waiting, mode="lead2")
    try:
        assert run.step() == "ALIGN"
        run.user = lambda item: {"request": item["request"], "replyId": "kickoff",
                                "selections": [{"groupId": "technical", "optionId": "other",
                                                "text": "Business terms, please."}]}
        assert run.step() == "ALIGN" and planner.calls == 0
        run.user = waiting
        assert run.run().state == "ALIGN" and planner.calls == 2
        assert len(run.store.records("lead2_unusable_answer")) == 1
        assert "reason: expected string" in frontdoor.state(product)["reason"]
    finally:
        run.close()
    run = Run.reopen(product, planner, waiting)
    try:
        assert run.run().state == "A" and planner.calls == 3
        paused = frontdoor.pause(str(product))
        assert paused["status"] == "PAUSED" and frontdoor.state(product)["status"] == "PAUSED"
        request = paused["waitingFor"]["request"]
    finally:
        run.close()
    reply = {"request": request, "replyId": "revise",
             "selections": [{"groupId": "round", "optionId": "revise"}]}
    run = Run.reopen(product, planner, lambda _: reply)
    try:
        assert run.run().state == "ALIGN" and planner.calls == 4
        failed = frontdoor.state(product)
        assert failed["status"] == "PAUSED" and failed["reason"] == "The planner timed out."
        assert warning in run.store.records("provider_failure")[-1][2]["reason"]
    finally:
        run.close()


@pytest.mark.fast
def test_grow_align_tiny_first_done_journey(tmp_path, monkeypatch):
    """A complete tiny proposal locks immediately; revision-only journeys miss forced extra turns."""
    from seldon import decisions, frontdoor, schema

    monkeypatch.delenv("SELDON_OFF", raising=False)
    calls = []

    def provider(q):
        strict_schema(q["schema"])
        calls.append(q["kind"])
        response = {"answer": questions.tiny_round_example()}
        schema.validate(response, q["schema"])
        return json.dumps(response)

    def user(item):
        return {"request": item["request"], "replyId": "tiny-lock",
                "selections": [{"groupId": "round", "optionId": "done"}]}

    product = tmp_path / "greeting"
    run = Run(product, tmp_path / "workers", "Greet a visitor by name", {}, provider, user)
    try:
        assert run.step() == "A"
        shown = frontdoor.state(product)["waitingFor"]
        assert shown["subject"]["vision"]["size"] == "tiny"
        run.user = lambda item: (_ for _ in ()).throw(decisions.Waiting())
        with pytest.raises(decisions.Waiting):
            run.step()
        run.user = user
        assert run.step() == "ARCHITECT"
        assert calls == ["ALIGN"]
        assert run.store.read(run.store.cursor().A)["request"] == shown["request"]
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("repeat", [False, True])
def test_quietbench_skeleton_commitments_journey(tmp_path, scalar_align, repeat):
    """Replay the real skeleton with a goal but no work through approval and into EXECUTE.

    Unlike duplicate-handle cases, every handle in the saved answer is valid. The bug
    is that DETAIL would ask nobody. The repeated reply must reach planner and user.
    """
    from seldon import decisions

    recorded = json.loads((Path(__file__).parent / "fixtures/quietbench-a01-planning.json")
                          .read_text(encoding="utf-8"))
    scopes = {"simulation": ("simulation/**", "scopeIn:2", "Run a tone through a fixed filter"),
              "service": ("server.py", "scopeIn:10", "Save the session and reopen it after restart"),
              "experience": ("web/**", "scopeIn:5", "Show the saved tone run in the page")}
    slot = tmp_path / "quietbench"
    run, _ = prepare(slot)
    corrections, detailed, recovery = [], {}, []

    class QuietBench(Scripted):
        def __call__(self, q):
            strict_schema(q["schema"])
            kind, facts = q["kind"], q["facts"]
            if kind == "ALIGN":
                return json.dumps(recorded[kind])
            if kind == "ARCHITECT":
                body = deepcopy(recorded[kind])
                if "Exact corrections" in facts:
                    corrections.append(facts["Exact corrections"][0]["Errors"][0])
                    if not repeat:
                        body["answer"]["waves"][0]["commitments"] = [c for _, c, _ in scopes.values()]
                return json.dumps(body)
            if kind == "DETAIL":
                area = facts["Area"]["handle"]
                scope, _, behavior = scopes[area]
                detailed[area] = list(facts["Accepted commitments"])
                return json.dumps({"answer": {"verdict": "planned", "edits": [
                    {"kind": "feature", "targetHandle": "slice",
                     "typedBody": {"story": behavior, "acceptance": behavior}},
                    {"kind": "requirement", "targetHandle": "proof",
                     "typedBody": {"features": ["slice"], "acceptance": behavior}}],
                    "units": [{"scope": [scope], "requirements": ["proof"], "dependencies": [],
                               "instructions": behavior}], "why": "Deliver the fixed-tone skeleton."}})
            if kind == "COACH":
                recovery.append(str(facts["Unresolved work"]))
            return super().__call__(q)

    def user(item):
        if "step" in item["subject"]:
            recovery.append(item["ask"]["question"])
            assert item["ask"]["options"][0]["effect"] == "pause"
            raise decisions.Waiting()
        return approve(item)

    run.provider, run.user = QuietBench(slot), user
    try:
        if repeat:
            assert run.run().state == "ARCHITECT"
            assert len(corrections) == 2
            assert len(recovery) == 2 and all(corrections[0] in message for message in recovery)
            assert not detailed
        else:
            for _ in range(12):
                if run.step() == "EXECUTE":
                    break
            assert run.store.cursor().state == "EXECUTE"
            assert plan.approved(run.store) and not run.store.records("step_failure")
            assert detailed == {area: [commitment] for area, (_, commitment, _) in scopes.items()}
            assert len(corrections) == 1 and not recovery
        reason = corrections[0]
        assert all(word in reason for word in ("wave 1", "wave 2", "wave 3", "expected", "got []",
                                               "Send", "scopeIn:2", *scopes))
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("case", ["align", "redetail", "link", "unlinked"])
def test_recorded_quiet_bench_planning_journey(tmp_path, monkeypatch, case):
    """A wrapped literal and a changed DETAIL edit order must reach execution.

    The recorded wave-one replan overwrote live trace links by edit position. Fresh
    DETAIL journeys never merge those links. A genuinely unlinked feature instead
    needs a useful correction, followed by planner and user recovery if it repeats.
    """
    from dataclasses import replace

    from seldon import decisions, features, recovery, schema
    from seldon.store import UnitRow

    fixture = Path(__file__).with_name("fixtures") / "quietbench-c01-planning.json"
    replies = json.loads(fixture.read_text(encoding="utf-8"))
    monkeypatch.setattr(features, "enabled", {"growAlign", "answerRepair", "sizeFirst"}.__contains__)
    monkeypatch.setattr(features, "planning_setup", lambda: {"sizeFirst": True, "split": "auto"})
    parked, corrections, coached = [], [], []
    previous, commitments = None, []

    def provider(q):
        strict_schema(q["schema"])
        kind, facts = q["kind"], q["facts"]
        if kind == "ALIGN":
            answer = replies["r11" if case == "align" else "r26"]
        elif kind == "ARCHITECT":
            answer = deepcopy(replies["r133" if previous else "r38"])
            if case == "align":
                literal = facts["Approved vision"]["constraints"][0]["value"]
                assert literal in replies["kickoff"] and "Node 20\nfor the page checks" in literal
                commitments[:] = facts["Commitment handles"]
                answer["areas"][0]["commitments"] = answer["waves"][0]["commitments"] = commitments
        elif kind == "DETAIL":
            answer = deepcopy(replies["r139" if previous else "r44"])
            if case == "align":
                for index, edit in enumerate(e for e in answer["edits"] if e["kind"] == "feature"):
                    edit["typedBody"]["commitments"] = commitments[index::4]
            if case in ("unlinked", "link"):
                handle = "new:unlinked-simulation"
                feature = {**answer["edits"][0], "targetHandle": handle}
                answer["edits"].append(feature)
                if case == "link" and "Exact corrections" in facts:
                    requirement = next(e for e in answer["edits"] if e["kind"] == "requirement")
                    requirement["typedBody"]["features"].append(handle)
            if "Exact corrections" in facts:
                corrections.extend(facts["Exact corrections"][0]["Errors"])
        elif kind == "COACH":
            coached.append(facts["Unresolved work"])
            answer = {"guidance": "Link each feature to the requirement that delivers it.",
                      "why": "The plan must trace the accepted simulation behavior."}
        else:
            assert kind == "REVIEW"
            answer = {"verdict": "adequate", "gaps": [], "why": "The plan traces the bench."}
        response = {"answer": answer}
        schema.validate(response, q["schema"])
        return json.dumps(response)

    def user(item):
        if "groups" in item["ask"]:
            return {"request": item["request"], "replyId": "quiet-bench-lock",
                    "selections": [{"groupId": "round", "optionId": "done"}]}
        if "step" in item["subject"]:
            parked.append(item["ask"])
            return "Stop"
        return approve(item)

    run = Run(tmp_path / "product", tmp_path / "workers", replies["kickoff"], {}, provider, user)
    step = run.step

    def to_execute():
        nonlocal previous
        cursor = run.store.cursor()
        if cursor.state != "EXECUTE":
            return step()
        if case != "redetail" or previous:
            raise decisions.Waiting()
        # Resume at the recorded integration boundary. The first plan, its B and
        # the replan from ARCHITECT through REVIEW all use the production controller.
        previous = run.store.plan(cursor.revision)
        owner, = plan.select(previous, "unit")
        run.store.unit(owner, UnitRow(wave=1, state="DONE", integrated=cursor.candidate))
        run.store.cursor(replace(cursor, state="WAVE-VERIFY"))
        gap = run.store.record("architecture_gap", {"vision": cursor.vision, "gap": replies["replanGap"]})
        recovery.planning(run.store, gap, "integrated bench check", "plan-gap", finalCommit=None)

    run.step = to_execute
    try:
        result = run.run()
        faults = [b for _, _, b in run.store.records("step_failure")]
        failures = [b for _, _, b in run.store.records("consumption_failure")]
        if case in ("link", "unlinked"):
            reason = corrections[0]
            assert all(word in reason for word in ("new:unlinked-simulation", "no requirement", "Send"))
            assert {failure["field"] for failure in failures} == {"edits.typedBody.features"}
        if case == "unlinked":
            assert result.state == "DETAIL" and result.decision
            assert len(corrections) == 2 and corrections[0] == corrections[1]
            assert len(coached) == len(parked) == 1
            assert reason in str(coached[0]) and reason in parked[0]["question"]
            assert parked[0]["options"][0]["effect"] == "pause"
        else:
            assert result.state == "EXECUTE", faults
            assert not faults and not parked and not coached
            assert len(failures) == len(corrections) == (1 if case == "link" else 0)
            if previous:
                current = run.store.plan(result.revision)
                for kind in ("feature", "requirement"):
                    assert plan.select(previous, kind).items() <= plan.select(current, kind).items()
                assert {"scopeIn:safety-control", "scopeIn:interfaces"} <= {
                    handle for feature in plan.select(current, "feature").values()
                    for handle in feature["commitments"]}
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("repeat", [False, True])
def test_answer_consumption_repair_golden(tmp_path, scalar_align, repeat):
    """An unusable but schema-valid architecture is corrected before asking the user.

    Duplicate handles used to spend schema retries with no consumption record or useful
    expected/got context. The repeat twin protects against endless exact-fix retries and
    proves the real COACH/user route recommends a resumable pause. Schema-only twins
    cannot exercise this producer-to-plan boundary.
    """
    from seldon import decisions

    slot = tmp_path / "answer-repair"
    run, _ = prepare(slot)
    questions_seen, user_questions = [], []

    class Mismatch(Scripted):
        def __call__(self, q):
            raw = super().__call__(q)
            if q["kind"] == "ARCHITECT":
                questions_seen.append(q)
                if repeat or "Exact corrections" not in q["facts"]:
                    body = json.loads(raw)
                    conflict = {**body["answer"]["areas"][0], "responsibility": "A conflicting responsibility"}
                    body["answer"]["areas"].append(conflict)
                    return json.dumps(body)
                # An identical repeated entry has one sensible meaning; normalise it.
                body = json.loads(raw)
                body["answer"]["areas"].append(deepcopy(body["answer"]["areas"][0]))
                return json.dumps(body)
            if q["kind"] == "COACH":
                assert "ARCHITECT" in str(q["facts"]["Unresolved work"])
                assert "areas" in str(q["facts"]["Unresolved work"])
                return json.dumps({"answer": {"guidance": "Send distinct area handles",
                                               "why": "The area list repeats a handle"}})
            return raw

    run.provider = Mismatch(slot)
    def user(item):
        if "step" in item["subject"]:
            user_questions.append(item)
            raise decisions.Waiting()
        return approve(item)
    run.user = user
    try:
        run.run()
        failures = [b for _, _, b in run.store.records("consumption_failure")]
        assert failures and all(b["kind"] == "ARCHITECT" and b["field"] == "areas"
                                for b in failures)
        reason = failures[0]["reason"]
        assert "expected" in reason and "got" in reason and "Send" in reason
        assert reason in str(questions_seen[1]["facts"]["Exact corrections"])
        if repeat:
            assert len(questions_seen) == 4 and len(failures) == 4
            assert run.store.records("planner_answer") and len(user_questions) == 1
            item = user_questions[0]
            assert reason in str(item["subject"])
            assert reason in item["ask"]["question"]
            assert "Seldon's fault" in item["ask"]["question"]
            assert "pause" in item["ask"]["question"]
            assert item["ask"]["options"][0]["effect"] == "pause"
        else:
            assert len(questions_seen) == 2 and len(failures) == 1
            assert not user_questions and not run.store.records("step_failure")
            assert run.store.cursor().state == "SCRIPTED_C"
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("off", [False, True])
def test_consumption_crash_rollback_and_switch_journey(tmp_path, monkeypatch, off):
    """A crash after plan insertion rolls back and teaches the same question its context.

    A schema-valid answer can fail inside Seldon's projection after writes have begun.
    Existing input-error twins fail before writing and miss partial plan publication.
    The off twin proves the cohort switch routes directly to existing planner recovery.
    """
    monkeypatch.setenv("SELDON_OFF", "growAlign,sizeFirst,answerRepair" if off else "growAlign,sizeFirst")
    slot = tmp_path / "consumption-crash"
    run, _ = prepare(slot)
    original = run.store.revision
    failed, seen = [], []
    def interrupted(document, parent=None):
        revision = original(document, parent)
        if not failed:
            failed.append(revision)
            raise IndexError("list index out of range")
        return revision
    monkeypatch.setattr(run.store, "revision", interrupted)

    class Planner(Scripted):
        def __call__(self, q):
            if q["kind"] == "ARCHITECT":
                seen.append(q)
            if failed and q["kind"] in ("ARCHITECT", "COACH"):
                assert run.store.cursor().revision is None
                assert run.store.db.execute("SELECT count(*) FROM plan_revision").fetchone()[0] == 0
                assert not [b for _, _, b in run.store.records("answer") if b["kind"] == "ARCHITECT"]
            return super().__call__(q)
    run.provider = Planner(slot)
    try:
        run.run()
        assert run.store.cursor().state == "SCRIPTED_C"
        failures = [b for _, _, b in run.store.records("consumption_failure")]
        assert len(failures) == 1 and failures[0]["exceptionType"] == "IndexError"
        reason = failures[0]["reason"]
        assert "ARCHITECT" in reason and "waves" in reason and "Send" in reason
        assert "list index out of range" not in reason
        assert len(seen) == 2
        assert bool(run.store.records("planner_answer")) == off
        assert (reason in str(seen[1]["facts"].get("Exact corrections"))) == (not off)
    finally:
        run.close()


@pytest.mark.fast
def test_coach_consumption_failure_parks_golden(tmp_path, scalar_align, monkeypatch):
    """If Seldon cannot consume COACH either, its saved question recommends pause.

    Ordinary step-fault twins assume coaching works; a bug applying its guidance used
    to escape the recovery handler itself, leaving no helpful question to resume from.
    """
    from seldon import decisions
    slot = tmp_path / "coach-crash"
    run, _ = prepare(slot)
    record = run.store.record
    def broken(kind, body):
        ref = record(kind, body)
        if kind == "planner_answer":
            raise IndexError("list index out of range")
        return ref
    monkeypatch.setattr(run.store, "record", broken)
    class Planner(Scripted):
        def __call__(self, q):
            raw = super().__call__(q)
            if q["kind"] == "ARCHITECT":
                body = json.loads(raw)
                body["answer"]["waves"] = []
                return json.dumps(body)
            return raw
    run.provider = Planner(slot)
    def user(item):
        if "step" in item["subject"]:
            raise decisions.Waiting()
        return approve(item)
    run.user = user
    try:
        run.run()
        request = run.store.read(run.store.cursor().decision)
        assert "COACH.guidance/ask" in request["ask"]["question"]
        assert "IndexError" in request["ask"]["question"]
        assert "list index out of range" not in request["ask"]["question"]
        assert request["ask"]["options"][0]["effect"] == "pause"
        assert not run.store.records("planner_answer")
        assert len([b for _, _, b in run.store.records("consumption_failure") if b["kind"] == "COACH"]) == 2
        assert len(run.store.records("step_failure")) == 2
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("fault", [False, True])
def test_wave_loop_golden(tmp_path, scalar_align, fault):
    """An unanswered check-in allows planning but holds new work; its answer grows the product.

    The fault twin breaks an old reader with a new format and repairs its owner, then steers
    at C. One-wave and planning-only journeys miss stale bases, reused sessions and check-in
    consent lost as the cursor advances. The strict provider rejects STEER before execution
    if any nested object makes fields optional. Recorded remaining-only STEER answers must
    preserve completed waves; empty-delta stubs missed the real wave-2 IndexError.
    """
    from wave_loop_journey import journey

    journey(tmp_path / "waves", fault)


@pytest.mark.fast
def test_directed_checkin_architect_continuation_journey(tmp_path, monkeypatch):
    """A check-in extends the existing writer through final proof without echoing ownership.

    Quiet Bench lost direction behind the closed horizon and kept a draft requirement on two writers
    after review. Earlier staged-wave journeys miss the draft repair and exact value dropped by WORK.
    """
    from contextlib import closing

    from seldon import features
    from seldon.providers import render_question
    from seldon.states import TERMINAL

    monkeypatch.setattr(features, "enabled", {"growAlign", "answerRepair"}.__contains__)
    monkeypatch.setattr(features, "planning_setup", lambda: {})
    slot = tmp_path / "directed-checkin"
    slot.mkdir()
    (slot / "mode").write_text("normal")
    direction = "Also let me export a saved value as CSV\nKeep the exact heading."
    goal = "Export the saved value as CSV"
    heading = "Expiring soon"
    story, quality = "scopeIn:greeting", "quality:clear"
    follow_on = "    Path(name, 'check.py').write_text(source)"
    (slot / "native.py").write_text(NATIVE.replace(follow_on, r"""    if number == 1 and turn == 2:
        assert facts['User check-in'].startswith('Also let me export a saved value as CSV')
        heading, = (c['value'] for c in facts['Applicable exact constraints'])
        Path(name, 'values.csv').write_text(heading + '\n' + value + '\n', encoding='utf-8')
        source += f"assert Path('{name}/values.csv').read_text().splitlines() == {[heading, value]!r}\n"
""" + follow_on), encoding="utf-8")
    briefs = []
    draft_requirement = "2.area-1.requirement2"
    reviewed_draft = False

    class Planner(Scripted):
        def __call__(self, q):
            nonlocal reviewed_draft
            kind, facts = q["kind"], q["facts"]
            if kind == "ALIGN":
                answer = questions.tiny_round_example()
                accepted = answer["vision"]
                accepted["stories"][0].update(story="Read the saved value", outcome="Show the saved value",
                                              constraints=["heading"], sources=["kickoff"])
                accepted["constraints"] = [{"handle": "heading", "value": heading,
                    "meaning": "Exact CSV heading", "source": "kickoff"}]
                return json.dumps({"answer": answer})
            if kind == "DECIDE" and facts["Decision"] == "CHECK-IN":
                return json.dumps({"answer": {"choice": "Other", "why": "The user requests CSV export."}})
            if kind == "JUDGE" and any("CSV exports" in r["acceptance"] for r in facts["Requirements"].values()):
                assert [c["value"] for c in facts["Applicable exact constraints"]] == [heading]
            if kind == "ARCHITECT" and "Finished wave" in facts:
                strict_schema(q["schema"])
                briefs.append(q)
                # Replay the recorded shape unless the current direction is explicit.
                waves = ([{"goal": goal, "commitments": [story]}]
                         if facts.get("User check-in") == direction and facts.get("Next wave") == 2
                         else [])
                return json.dumps({"answer": {"areas": [], "contracts": [], "decisions": [],
                    "waves": waves, "why": "Extend the existing saved-value outcome with export."}})
            if kind == "REVIEW" and draft_requirement in facts["Plan"] and not reviewed_draft:
                reviewed_draft = True
                return json.dumps({"answer": {"verdict": "gap", "gaps": [{"severity": "must-fix",
                    "target": draft_requirement, "correction": "Give the independent reader its own check."}],
                    "why": "The reader can prove its value independently of the continuing export."}})
            if kind == "DETAIL":
                area = facts["Area"]
                later = facts["Current wave"]["goal"] == goal
                repairing = later and "Merge correction" in facts
                # The repair reuses edit positions and replaces the proposed independent reader.
                scopes = [area["scope"][-1], area["scope"][0]] if repairing else area["scope"][:2 if later else 1]
                q = {**q, "facts": {**facts, "Area": {**area, "scope": scopes}}}
            answer = json.loads(super().__call__(q))["answer"]
            if kind == "ARCHITECT":
                answer["areas"][0].update(scope=[f"unit-{i:03}/**" for i in (1, 2, 3)],
                                          commitments=[story, quality])
                answer["waves"][0]["commitments"] = [story, quality]
            if kind == "DETAIL":
                for edit in answer["edits"]:
                    if edit["kind"] == "feature":
                        edit["typedBody"]["commitments"] = [story]
                if later:
                    assert facts["User check-in"] == direction
                    continuing = facts["Continuing owner"]
                    assert facts["What exists here"][continuing]["scope"] == ["unit-001/**"]
                    guide = facts["Walkthrough ownership"]
                    assert guide["owner"] == "guide" and guide["paths"] == list(questions.GUIDE_SCOPE)
                    del answer["units"][-1 if repairing else 0]["scope"]
                    export = next(edit for edit in answer["edits"] if edit["targetHandle"] == "r1")
                    export["typedBody"]["acceptance"] = "CSV exports the original saved value."
            return json.dumps({"answer": answer})

    def user(item):
        if item["gate"] == "A":
            return {"request": item["request"], "replyId": "lock", "selections": [
                {"groupId": "round", "optionId": "done"}]}
        if item["gate"] == "CHECK-IN" and item["subject"]["wave"] == 1:
            return direction
        return approve(item)

    provider = Planner(slot)
    with closing(Run(slot / "product", slot / "workers", f"Read saved values. Use the exact heading {heading}.",
                     provider.setup, provider, user)) as run:
        while run.store.cursor().state != "CHECK-IN":
            assert run.step() not in TERMINAL
        store = run.store
        before = store.cursor()
        document = store.plan(before.revision)
        owners = store.units()
        evidence = store.records("observation")
        assert before.wave == 1 and len(document["architecture"]["waves"]) == 1
        assert owners and all(row.state == "DONE" for row in owners.values())
        assert evidence and any(body["commit"] == before.candidate and body["exitCode"] == 0
                                for _, _, body in evidence)
        assert run.step() == "PRODUCT-JUDGE"
        assert run.step() == "ARCHITECT"
        assert run.step() == "DETAIL"
        q, = briefs
        assert q["facts"]["Remaining waves"] == []
        for reviewed in (False, True):
            rendered, _ = render_question(q, prompts2=reviewed)
            assert direction in rendered and "Plan wave 2" in rendered
        assert not store.records("consumption_failure")
        after = store.cursor()
        continued = store.plan(after.revision)
        assert after.wave == 2 and after.waveStart == before.candidate == after.candidate
        assert continued["architecture"]["waves"] == [*document["architecture"]["waves"],
                                                       {"goal": goal, "commitments": [story]}]
        assert continued["architecture"]["areas"] == document["architecture"]["areas"]
        assert continued["nodes"] == document["nodes"]
        assert store.units() == owners and store.records("observation") == evidence
        owner = unit_ref(run, 1)
        assert run.step() == "REVIEW"
        draft = store.plan(store.cursor().revision)
        assert "CSV exports" in draft["nodes"][draft_requirement]["body"]["acceptance"]
        assert draft_requirement in plan.select(draft, "unit")[owner]["requirements"]
        assert any(unit["scope"] == ["unit-002/**"] for unit in plan.select(draft, "unit").values())
        assert run.step() == "REPAIR"
        assert run.step() == "DETAIL"
        final, _, rows = finish(run)
        execution = plan.execution(store)
        units = plan.select(execution, "unit")
        assert final.B == before.B and unit_ref(run, 1) == owner and rows[owner].wave == 2
        assert units[owner]["scope"] == plan.select(document, "unit")[owner]["scope"]
        new, = units.keys() - owners.keys()
        assert units[new]["scope"] == ["unit-003/**"] and rows[new].wave == 2
        assert [ref for ref, unit in units.items() if draft_requirement in unit["requirements"]] == [new]
        prior_requirements = plan.select(document, "unit")[owner]["requirements"]
        assert all(ref in units[owner]["requirements"] and execution["nodes"][ref] == document["nodes"][ref]
                   for ref in prior_requirements)
        assert (run.project / "unit-001/values.csv").read_text().splitlines() == [heading, "value-001"]
        for question_path in (slot / "calls").glob("call-*/question.json"):
            work = json.loads(question_path.read_bytes())
            if (work["facts"].get("User check-in") == direction
                    and work["facts"]["Write scope"] == units[owner]["scope"]):
                actual = question_path.with_name("prompt.txt").read_text("utf-8")
                for rendered in (actual, render_question(work, prompts2=True)[0]):
                    assert "User check-in\n" + direction in rendered
                    assert f'"value": "{heading}"' in rendered.split("Applicable exact constraints\n", 1)[1]
                break
        else:
            pytest.fail("No wave-2 worker brief carried the prior direction in the original scope")
        assert not store.records("consumption_failure") and not store.records("detail_correction")


@pytest.mark.fast
@pytest.mark.parametrize("missing_object", [False, True])
def test_recorded_shelflife_repair_coaching_journey(tmp_path, scalar_align, missing_object):
    """A reopened repair without a worktree reaches its UNIT question on the recorded base.

    ShelfLife r439 failed before COACH because cwd=None queried Seldon's repo. Existing
    repair journeys kept a worktree and missed this recorded ASKED state. A lost Git object
    twin must park with the actual execution_facts caller, rather than git()'s generic raise.
    """
    import base64
    import zlib
    from seldon import decisions
    from seldon.store import Store

    fixture = json.loads((Path(__file__).parent / "fixtures/shelflife_not_tree.json").read_text())
    project = tmp_path / "product"
    project.mkdir()
    gitwork.git(project, "init", "--quiet")
    for oid, kind, packed in fixture["objects"]:
        assert gitwork.git(project, "hash-object", "-t", kind, "-w", "--stdin",
                           data=zlib.decompress(base64.b64decode(packed))) == oid
    candidate = fixture["cursor"]["candidate"]
    gitwork.git(project, "update-ref", "HEAD", candidate)
    gitwork.checkout(project, candidate)
    store = Store(project)
    # Rehydrate the recorded snapshot; only local filesystem addresses and the paused
    # controller decision change. No units, patches or recovery outcomes are invented.
    def relocate(value):
        if isinstance(value, str):
            old = "C:/seldon/tests/m1/shelflife-main-15682aa-s01"
            for spelling in (old, old.replace("/", "\\"), old.replace("/", "\\\\")):
                value = value.replace(spelling, str(tmp_path))
            return value
        if isinstance(value, list):
            return [relocate(v) for v in value]
        if isinstance(value, dict):
            return {k: relocate(v) for k, v in value.items()}
        return value
    for ref, kind, body in fixture["records"]:
        store.db.execute("INSERT INTO record VALUES (?,?,?)", (ref, kind, json.dumps(relocate(body))))
    for table, rows in (("unit", fixture["units"]), ("recovery", fixture["recoveries"])):
        for ref, body in rows:
            store.db.execute(f"INSERT INTO {table} VALUES (?,?)", (ref, json.dumps(relocate(body))))
    cursor = relocate(fixture["cursor"])
    cursor["decision"] = None
    store.db.execute("INSERT INTO run VALUES (1,?)", (json.dumps(cursor),))
    store.db.execute("INSERT INTO plan_revision VALUES (?,NULL,?)",
                     (cursor["revision"], json.dumps(fixture["document"])))
    store.close()
    if missing_object:
        obj = project / ".git/objects" / candidate[:2] / candidate[2:]
        obj.chmod(0o666)
        obj.unlink()
    calls = []
    def provider(q):
        assert q["kind"] == "COACH"
        calls.append(q)
        return json.dumps({"answer": {"ask": {"question": "How should this repair continue?",
            "options": [{"label": "Repair the owner", "means": "Continue with the recorded correction"},
                        {"label": "Clarify", "means": "Explain the outstanding result"}]},
            "why": "The unresolved repair needs direction"}})
    def user(item):
        raise decisions.Waiting()
    run = Run.reopen(project, provider, user)
    try:
        run.run()
        request = run.store.read(run.store.cursor().decision)
        failures = run.store.records("step_failure")
        if missing_object:
            assert request["gate"] == "EXECUTE" and failures
            body = failures[-1][2]
            assert "not a tree object" in body["message"]
            assert body["location"].startswith("src/seldon/")
            assert body["location"].endswith(" execution_facts")
        else:
            assert request["gate"] == "UNIT" and not failures
            facts = calls[0]["facts"]["Execution facts"]
            assert facts["Base"] == candidate and facts["Worktree"] is None
            assert facts["Paths present"] == ["README.md", "checks/integration/fresh-folder.cjs"]
            assert request["subject"]["unit"] == "1.import-integration.1"
        assert len(calls) == (0 if missing_object else 1)
        assert run.store.cursor().candidate == candidate
        assert run.store.unit("1.backend.1").state == "DONE"
    finally:
        run.close()


@pytest.mark.fast
def test_architect_scope_returns_to_vision_journey(tmp_path, scalar_align):
    """An architect's scope discovery reaches ALIGN and A before any worker can start.

    Ordinary wave journeys keep the vision fixed and miss scope text lost at the ALIGN boundary.
    """
    from seldon.decisions import Waiting

    run, _ = prepare(tmp_path / "scope")

    class Scope(Scripted):
        def __call__(self, q):
            if q["kind"] == "ARCHITECT":
                return json.dumps({"answer": {"scope": "Read exported values", "why": "Needs acceptance"}})
            answer = json.loads(super().__call__(q))["answer"]
            if q["kind"] == "ALIGN" and "Requested change" in q["facts"]:
                answer["scopeIn"].append(q["facts"]["Requested change"]["answer"]["scope"])
            return json.dumps({"answer": answer})

    run.provider = Scope(tmp_path / "scope")
    def user(item):
        if item["gate"] == "A" and "Read exported values" in item["subject"]["vision"]["scopeIn"]:
            raise Waiting()
        return approve(item)

    run.user = user
    try:
        with pytest.raises(Waiting):
            while True:
                run.step()
        request = run.store.read(run.store.cursor().decision)
        assert run.store.cursor().state == request["gate"] == "A"
        assert "Read exported values" in request["subject"]["vision"]["scopeIn"]
        assert not run.store.units()
    finally:
        run.close()

VISION = {
    "size": "small",
    "understanding": "Read named values",
    "users": ["CLI user"],
    "scopeIn": ["Read each named persistent value"],
    "scopeOut": [],
    "improvements": [],
    "quality": [{"criterion": "Clear output", "verification": "Read the real command output"}],
    "alternatives": [{"label": "Show all values", "means": "List values without naming one"}],
}

NATIVE = r"""
import json, os, sys, time, uuid
from pathlib import Path
slot = Path(sys.argv[1])
args = sys.argv[2:]
schema = Path(args[args.index('--output-schema')+1])
q = json.loads(schema.with_name('question.json').read_text('utf-8'))
facts = q['facts']
name = facts['Write scope'][0].split('/')[0]
mode = (slot/'mode').read_text()
def owner(number):
    scope = 'unit-'+str(number).zfill(3)+'/**'
    return next(r for r, paths in facts['Correction owners'].items() if scope in paths)
context = slot/('context-'+name+'.json')
if q['execution'].get('session') and 'Exact failure' in facts:
    facts = {**json.loads(context.read_text()), **facts}
else:
    context.write_text(json.dumps(facts))
if mode == 'parallel-contract':
    assert ('Design guidance used by UI workers' in facts) == (name == 'unit-002')
counter = slot/('turn-'+name)
turn = int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(turn))
thread = args[args.index('resume')+1] if 'resume' in args else str(uuid.uuid4())
print(json.dumps({'type':'thread.started','thread_id':thread}), flush=True)
with (slot/('launch-'+name+'.jsonl')).open('a') as out:
    out.write(json.dumps({'thread':thread,'resumed':'resume' in args,'turn':turn})+'\n')
if mode == 'scale-five' and name == 'unit-013':
    (slot/'peer-running').write_text(str(os.getpid()))
    deadline = time.monotonic() + 60
    while not (slot/'peer-answered').exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert (slot/'peer-answered').exists(), 'no answer while peer process ran'
    (slot/'peer-finished').write_text(str(os.getpid()))
if mode == 'scale-five' and name == 'unit-012' and turn == 1:
    deadline = time.monotonic() + 60
    while not (slot/'peer-running').exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert (slot/'peer-running').exists(), 'peer process never launched'
if mode == 'scale-five' and name == 'unit-022' and not (slot/'resumed').exists():
    (slot/'kill-wave3').write_text(str(os.getpid()))
    time.sleep(240)
if mode == 'kill-work' and name == 'unit-002' and not (slot/'resume').exists():
    (slot/'kill-ready').write_text(str(os.getpid()))
    time.sleep(30)
answer = {'outcome':'done','summary':'Wrote and exercised the acceptance',
          'checks':[], 'checkPaths':[], 'discoveries':[], 'why':'Ready for independent observation'}
if mode == 'schema-fix' and name == 'unit-001' and turn == 1:
    answer = json.loads((slot/'recorded-work.json').read_text())['answer']
elif mode == 'scale-five' and name == 'unit-012' and turn == 1:
    answer.update(outcome='question', question='Should the reader preserve the exact label?')
elif mode == 'repeat-question' and name == 'unit-001':
    answer.update(outcome='question', question='Which documented fixture should the reader exercise?',
                  summary=f'Work to verify the saved-value fixture, turn {turn}.')
elif mode == 'followup-question' and name == 'unit-001':
    answer.update(outcome='question', question='Which saved label should the reader preserve?')
elif mode == 'question' and name == 'unit-001' and turn == 1:
    answer.update(outcome='question', question='How can I check persistence in this scope?')
elif mode == 'coach-facts' and name == 'unit-003' and turn == 1:
    answer.update(outcome='question', question="Coordinator: supply u001's existing task/thread ID and evidence location")
elif mode == 'coach-facts' and name == 'unit-001' and turn == 1:
    answer.update(outcome='question', question="Where are u002's files and execution evidence?")
elif name == 'docs':
    command = [sys.executable, '-B', 'docs/try-it/check.py']
    Path('docs/try-it').mkdir(parents=True, exist_ok=True)
    Path(facts['Guide path']).write_text('Read the saved value in unit-001/value.txt. Observe value-001.\n')
    Path('docs/try-it/check.py').write_text("from pathlib import Path\n"
        "assert Path('unit-001/value.txt').read_text() == 'value-001'\nprint('observed value-001')\n")
    answer['checkPaths'] = ['docs/try-it/check.py']
    answer['checks'] = [{'purpose':'Follow the reader path', 'command':command,
                         'expectedEvidence':'Observed exact saved value'}]
else:
    Path(name).mkdir(exist_ok=True)
    number = int(name.split('-')[1])
    execution = facts['Execution facts']
    for peer, state in execution.get('Units', {}).items():
        if peer != owner(int(name.split('-')[1])) and state['state'] == 'DONE':
            paths = [p for p in execution['Paths present'] if p.startswith('unit-') and p.endswith('value.txt')]
            assert paths and all(Path(p).read_text().startswith('value-') for p in paths)
    if mode == 'coach-facts' and name == 'unit-003':
        guidance = facts['Exact failure']
        assert 'WAVE-VERIFY' in guidance
        assert 'Worktree' in guidance and 'Runtime' in guidance
        assert execution['Proof owner'] == 'WAVE-VERIFY'
    if mode == 'coach-facts' and name == 'unit-001':
        guidance = facts['Exact failure']
        assert 'Worktree' in guidance and 'Runtime' in guidance
    value = 'value-'+str(number).zfill(3)
    source = ('from pathlib import Path\nimport os\n'
        + f"assert Path('{name}/value.txt').read_text() == '{value}'\n"
        + f"print('observed {value}')\n")
    if mode == 'contracts':
        if number == 1:
            Path(name, 'items.json').write_text(json.dumps({'items': ['value-001']}))
        elif any(c['provider'] == owner(1) for c in facts.get('Contracts consumed', {}).values()):
            agreements = {c['text'] for label in ('Contracts consumed', 'Area contracts')
                          for c in facts.get(label, {}).values()}
            assert 'JSON response: {"items": ["value-001"]}.' in agreements
            source += ("import json\n"
                "assert json.loads(Path('unit-001/items.json').read_text())['items'] == ['value-001']\n")
        else:
            assert not any(c['provider'] == owner(1) for label in
                           ('Contracts provided', 'Contracts consumed', 'Area contracts')
                           for c in facts.get(label, {}).values())
    if mode == 'contract-fanout' and number >= 3:
        assert 'UTF-8 text: value-001.' in str(facts['Contracts consumed'])
        source += "assert Path('unit-001/value.txt').read_text() == 'value-001'\n"
    if mode == 'coach-facts' and name == 'unit-003':
        source += "assert Path('unit-001/value.txt').read_text() == 'value-001'\n"
    source += "if os.environ.get('SELDON_EVIDENCE_DIR'):\n"
    source += "    evidence = Path(os.environ['SELDON_EVIDENCE_DIR'])\n"
    source += "    evidence.joinpath('value.json').write_text('observed persistence')\n"
    if mode == 'see-proof-gap' and name == 'unit-002' and turn == 1:
        source = source.replace('observed value-002', 'unlabelled value-002')
    if ((mode == 'proof-failure' and name == 'unit-001' and turn == 1)
            or (mode == 'scale-five' and name == 'unit-011' and turn <= 2)):
        source += 'raise AssertionError("actual failed product assertion")\n'
    if mode == 'wave-fault' and name == 'unit-001' and turn == 1:
        source += "assert not Path('unit-002/value.txt').exists(), 'integrated mismatch'\n"
    if mode == 'wave-gap' and name == 'unit-002' and turn == 1:
        source += "assert not Path('unit-001/value.txt').exists(), 'integrated mismatch'\n"
    if mode.startswith('defect-'):
        if name == 'unit-001':
            source += "assert Path('unit-002/peer.txt').read_text() == 'correct', 'peer defect'\n"
        else:
            Path(name, 'peer.txt').write_text('correct' if turn > 1 and mode != 'defect-fails' else 'broken')
    Path(name, 'check.py').write_text(source)
    Path(name, 'value.txt').write_text(value)
    if mode == 'contract-gap' and name == 'unit-001' and 'today.txt' in facts.get('Exact failure', ''):
        Path(name, 'today.txt').write_text('2026-11-18')
    if mode == 'scope' and turn == 1 and name == 'unit-001':
        Path('unowned.txt').write_text('outside scope')
    if mode == 'scope' and turn > 1:
        Path('unowned.txt').unlink(missing_ok=True)
    answer['discoveries'] = [{'owner': owner(int(name.split('-')[1])), 'correction': 'Keep the saved values visible'}]
    if mode.startswith('defect-') and name == 'unit-001':
        answer['discoveries'] = [{'owner': owner(2), 'correction': 'Write correct into unit-002/peer.txt'}]
        if mode == 'defect-question':
            answer.update(outcome='question', summary='May u002 fix the peer defect?')
    answer['checkPaths'] = [name+'/check.py']
    answer['checks'] = [{'purpose':'Read the actual persistent value',
        'command':[sys.executable,'-B',name+'/check.py'],
        'expectedEvidence':'Observed exact saved value'}]
if 'Existing checks' in facts and not (mode == 'schema-fix' and name == 'unit-001' and turn == 1):
    requirements = facts['Requirements']
    latest = max((int(r.split('.')[0]) for r in requirements), default=0)
    for check in answer['checks']:
        links = [r for r in requirements if r.startswith(str(latest) + '.')]
        check['requirements'] = links
        for handle, prior in facts['Existing checks'].items():
            if check['command'] == prior['command'] and set(links) - set(prior['requirements']):
                check.update(update=handle, requirements=list(dict.fromkeys(prior['requirements'] + links)))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'answer':answer})}}), flush=True)
print(json.dumps({'type':'turn.completed'}), flush=True)
"""


def strict_schema(schema, path="schema"):
    """Codex's pre-invocation rejection, shared by scripted planning and native WORK."""
    if schema.get("type") == "object":
        missing = schema["properties"].keys() - set(schema.get("required", []))
        reason = (f"'{path}.required' is required to include every key in properties. "
                  f"Missing '{next(iter(sorted(missing)))}'." if missing else
                  f"'{path}.additionalProperties' is required to be false."
                  if schema.get("additionalProperties") is not False else None)
        if reason:
            raise RuntimeError("Codex 400 Invalid schema for response_format 'codex_output_schema': " + reason)
        for name, child in schema["properties"].items():
            strict_schema(child, f"{path}.properties.{name}")
    if "items" in schema:
        strict_schema(schema["items"], path + ".items")
    for index, child in enumerate(schema.get("anyOf", [])):
        strict_schema(child, f"{path}.anyOf.{index}")


def judge_owners(q):
    """The owners a JUDGE gap may name (the judged unit first, then its contracts); [] before 11-18."""
    gap = q["schema"]["properties"]["answer"]["anyOf"][0]["properties"]["gaps"]["items"]
    return gap["properties"]["owners"]["items"]["enum"] if "owners" in gap["properties"] else []


class Scripted:
    def __init__(self, slot, mode="normal", count=1):
        self.slot, self.mode, self.count = Path(slot), mode, count
        self.setup = {
            "providers": deepcopy(ROLES),
            "scriptedUser": True,
            "userOrigin": "scripted journey",
            "codexCommand": [sys.executable, "-B", str(self.slot / "native.py"), str(self.slot)],
            "python": {"path": sys.executable},
        }
        self.native = Codex(self.slot / "calls", self.setup)
        self.calls = []
        self.gaps = set()
        self.parallel = Barrier(3) if mode == "parallel-contract" else None

    def timeout_for(self, kind, context):
        return 30

    def invoke(self, q, started):
        strict_schema(q["schema"])
        if q["kind"] == "WORK":
            if self.mode == "wave-fault" and "Exact failure" in q["facts"]:
                assert q["execution"]["session"]
                assert "Correction observations" not in q["facts"]
                assert "integrated mismatch" in q["facts"]["Exact failure"]
                assert "Requirements" not in q["facts"]
            if self.parallel:
                self.parallel.wait(timeout=60)  # Hang guard, not a timing assertion.
                if questions.GUIDE_PATH in q["facts"]["Write scope"]:
                    assert q["facts"]["This wave's commitments"] == {"scopeIn:1": VISION["scopeIn"][0]}
                    assert "value-001" in str(q["facts"]["Contracts consumed"])
            return self.native.invoke(q, started)
        return self(q), None

    def __call__(self, q):
        strict_schema(q["schema"])
        kind, facts = q["kind"], q["facts"]
        self.calls.append(kind)
        if kind == "ALIGN":
            answer = deepcopy(VISION)
            if self.mode == "parallel-contract":
                answer["quality"][0]["criterion"] = "Visual direction: a clear, deliberate reader page"
        elif kind == "ARCHITECT":
            groups = (
                [[n] for n in range(1, self.count + 1)] if self.mode == "contracts" else
                [[1], [2], [3]] if self.mode == "coach-facts" else
                [[1, 2], [3, 4]] if self.mode == "contract-fanout" else [[1], [2]]
                if self.mode in ("contracts", "parallel-contract", "wave-gap", "contract-gap", "see-proof", "see-proof-gap", "contract-fanout")
                else [list(range(i, min(i + 10, self.count + 1))) for i in range(1, self.count + 1, 10)]
            )
            self.refs = {n: f"1.area-{i}.{j}" for i, group in enumerate(groups, 1)
                         for j, n in enumerate(group, 1)}
            answer = {
                "areas": [
                    {
                        "handle": f"area-{i}",
                        "responsibility": "Deliver named values",
                        "scope": [f"unit-{n:03}/**" for n in group],
                        "commitments": (["scopeIn:1"] if i == 1 else ["quality:1"])
                        if self.mode == "parallel-contract" else ["scopeIn:1", "quality:1"],
                    }
                    for i, group in enumerate(groups, 1)
                ],
                "contracts": [],
                "decisions": [],
                "waves": [{"goal": "Deliver the complete values",
                           "commitments": ["scopeIn:1", "quality:1"]}],
            }
            if self.mode in ("contracts", "parallel-contract", "coach-facts", "wave-gap", "contract-gap", "see-proof", "see-proof-gap", "contract-fanout"):
                answer["contracts"] = [
                    {
                        "handle": "values",
                        "kind": "data rule",
                        "text": "UTF-8 text: value-001.".ljust(3000, " "),
                        "provider": "area-1",
                        "consumers": (["area-1", "area-2", "area-2"]
                                      if self.mode == "parallel-contract" else
                                      ["area-3"] if self.mode == "coach-facts" else ["area-2"]),
                    }
                ]
                if self.mode == "contracts":
                    answer["contracts"].append({
                        "handle": "items", "kind": "api",
                        "text": 'JSON response: {"items": ["value-001"]}.',
                        "provider": "area-1", "consumers": [],
                    })
        elif kind == "DETAIL":
            numbers = [int(scope.split("/")[0].split("-")[1]) for scope in facts["Area"]["scope"]]
            edits, units = [], []
            for n in numbers:
                f, r = f"f{n}", f"r{n}"
                edits += [
                    {
                        "kind": "feature",
                        "targetHandle": f,
                        "typedBody": {"story": f"Read value {n:03}", "acceptance": f"Observe value-{n:03}"},
                    },
                    {
                        "kind": "requirement",
                        "targetHandle": r,
                        "typedBody": {
                            "acceptance": f"Running the reader displays saved value-{n:03}.",
                            "features": [f],
                        },
                    },
                ]
                units.append(
                    {
                        "scope": [f"unit-{n:03}/**"],
                        "requirements": [r],
                        "dependencies": [],
                        "instructions": f"Deliver and check value-{n:03}",
                    }
                )
            if self.mode == "contract-fanout" and facts["Area"]["handle"] == "area-1":
                units[0]["provides"] = ["values"]
            if self.mode in ("kill-work", "wave-fault") and len(units) > 1:
                units[1]["dependencies"] = [1]
            if self.mode.startswith("defect-"):
                units[0]["dependencies"] = [2]
            answer = {"verdict": "planned", "edits": edits, "units": units}
        elif kind == "REVIEW":
            answer = {"verdict": "adequate", "gaps": []}
            if self.mode == "review-gap":
                answer = {
                    "verdict": "gap",
                    "gaps": [
                        {"severity": "must-fix",
                            "target": "plan",
                            "correction": "Keep the accepted persistent value in the requirement sentence.",
                        }
                    ],
                }
        elif kind == "COACH":
            answer = {"guidance": "Yes. Write a scoped Python check that reads the saved value "
                      "and asserts its contents."}
            if self.mode == "coach-facts":
                answer["guidance"] = questions.labelled({**facts["Execution facts"], "Runtime": facts["Runtime"]})
        elif kind == "DECIDE":
            words = facts["User's words"]
            answer = {"choice": "Waive" if words == "Go on and do not stop again" and "Waive" in q["targets"]
                      else "Other" if words.startswith(("Revise", "Try again", "again"))
                      else "Stop" if words.startswith("No") else q["targets"][0]}
        elif kind in ("JUDGE", "FLOW", "PRODUCT"):
            answer = {
                "verdict": "satisfied",
                "gaps": [],
            }
            if kind == "JUDGE":
                observations = facts["Observations"]
                assert any(o["purpose"] in ("P", "final") and o["exitCode"] == 0 for o in observations)
                for r in q["targets"]:
                    assert any(
                        r in o["requirements"] and o["purpose"] in ("T", "wave-start") and o["exitCode"] != 0
                        for o in observations
                    )
                if (self.mode in ("unit-gap", "unit-rework") and "value-001" in str(facts["Requirements"])
                        or self.mode == "wave-gap" and "value-002" in str(facts["Requirements"])):
                    name = "unit-002" if self.mode == "wave-gap" else "unit-001"
                    turn = int((self.slot / ("turn-" + name)).read_text())
                    if self.mode == "unit-gap" or turn == 1:
                        r = q["targets"][0]
                        answer.update(
                            verdict="gap",
                            gaps=[
                                {"severity": "must-fix",
                                    "target": r, "owners": judge_owners(q)[:1],
                                    "correction": facts["Requirements"][r]["acceptance"]
                                    + (" The observed RED does not demonstrate this requirement."
                                       if self.mode == "unit-rework" else " The check is incomplete."),
                                }
                            ],
                        )
                if self.mode in ("see-proof", "see-proof-gap") and "value-002" in str(facts["Requirements"]):
                    assert any(o["purpose"] == "final" and o["exitCode"] == 0 for o in observations)
                    if any("unlabelled value-002" in o["stdout"] for o in observations
                           if o["purpose"] == "final"):
                        answer.update(verdict="gap", gaps=[{"severity": "must-fix", "target": q["targets"][0],
                            "owners": judge_owners(q)[:1], "correction": "The reader output omits the saved value label"}])
            else:
                obs = facts["Final observations"]
                assert obs and all(o["exitCode"] == 0 for o in obs)
                if self.mode in ("see-proof", "see-proof-gap"):
                    for verdict in facts["Source judgment"]:
                        assert verdict["verdict"] == "satisfied" and verdict["why"]
                        assert verdict["gaps"] == []
                    if "value-002" in str(facts):
                        assert any(v["unit"] == self.refs[2] for v in facts["Source judgment"])
                if self.mode == "unit-gap":
                    for verdict in facts["Source judgment"]:
                        if verdict["unit"] == self.refs[1]:
                            assert verdict["verdict"] == "gap" and verdict["why"]
                            assert "The check is incomplete" in str(verdict["gaps"])
                if kind == "FLOW" and self.mode == "flow-fault" and "value-001" in str(facts["Requirements"]):
                    if kind not in self.gaps:
                        self.gaps.add(kind)
                        r = q["targets"][0]
                        answer.update(
                            verdict="gap",
                            gaps=[
                                {"severity": "must-fix",
                                    "target": r,
                                    "correction": "Exercise the promised reader",
                                    "owners": list(facts["Correction owners"]),
                                }
                            ],
                        )
                if (kind == "PRODUCT" and self.mode == "product-fault"
                        and any(c["criterion"] == VISION["quality"][0]["criterion"]
                                for c in facts["Criteria"].values())
                        and kind not in self.gaps):
                    self.gaps.add(kind)
                    answer.update(
                        verdict="gap",
                        gaps=[{"severity": "must-fix", "target": q["targets"][0], "correction": "Make the accepted output clear",
                               "owners": [self.refs[1], self.refs[2]]}],
                    )
        else:
            raise AssertionError(kind)
        answer["why"] = "The observed acceptance supports this answer"
        return json.dumps({"answer": answer})


def approve(request):
    from seldon.decisions import Waiting

    if request["gate"] not in ("A", "B", "CHECK-IN", "C"):
        raise Waiting()
    return {"request": request["request"], "subject": request["subject"], "choice": "approve"}


@pytest.mark.fast
@pytest.mark.parametrize("staging", ["single", "owner-review", "switch-off"])
def test_lead_sizes_by_builder_session(tmp_path, monkeypatch, staging):
    """The reviewed B plan covers the same product in one session or honors a requested checkpoint.

    This catches the old non-tiny cutoff, a blank reason opening MILESTONES, and an ALIGN spec
    swallowing a requested split. The existing tiny Lead journeys miss the size cutoff.
    For a single delivery, an unanswered export choice must keep its displayed default
    when the owner locks A. The staged and tiny Lead cases have no product choice.
    """
    from seldon.decisions import Waiting

    monkeypatch.setenv("SELDON_OFF", "leadSizeByBuilder" if staging == "switch-off" else "")
    dream = "Build a local reading log with an add-book command, a saved list and CSV export."
    review = "I want to try adding and listing books before you build CSV export."
    if staging == "owner-review":
        dream += " " + review
    draft = questions.tiny_round_example()
    draft.update(questions=[], recommendation="done")
    if staging == "single":
        draft["questions"] = [{"affects": "scopeIn:export", "question": "Which books should the CSV include?",
            "options": [{"label": "All saved books", "means": "Take the whole list to another app"},
                        {"label": "Only finished books", "means": "Share just what has been read"}]}]
    vision = draft["vision"]
    vision.update(size="small", sizeWhy="Three commands sharing a small local store.", understanding=dream,
                  constraints=[], scopeOut=[], stories=[
        {"handle": handle, "feature": feature, "story": story, "outcome": outcome,
         "constraints": [], "sources": ["kickoff"]}
        for handle, feature, story, outcome in (
            ("record", "Reading list", "Save a book and reopen the list.", "The saved title appears."),
            ("export", "CSV export", "Export the reading list.", "The CSV contains every saved title."))])
    vision["quality"] = [{"handle": "errors", "criterion": "Missing titles get clear feedback",
                          "verification": "Try to add a book without a title.",
                          "constraints": [], "sources": ["kickoff"]}]
    record = {"vision": "scopeIn:record", "action": "Add A Wizard of Earthsea and reopen the list",
              "result": "A Wizard of Earthsea is listed", "notMetIf": "The saved title disappears."}
    export = {"vision": "scopeIn:export", "action": "Export the saved reading list",
              "result": "The CSV has a row for A Wizard of Earthsea", "notMetIf": "The CSV is empty."}
    errors = {"vision": "quality:errors", "action": "Add a book without a title",
              "result": "The error asks for a title", "notMetIf": "An empty title is saved."}
    first = {"outcome": "Save and list books", "design": "Store titles locally and show both commands in the guide.",
             "promises": [record, errors]}
    second = {"outcome": "Export saved books", "design": "Read the same store and show how to export it.",
              "promises": [export]}
    whole = {"outcome": "Save, list and export books", "design": first["design"] + " " + second["design"],
             "promises": [record, export, errors]}
    calls = []
    displayed = {}

    def provider(question):
        kind = question["kind"]
        calls.append(kind)
        if kind == "ALIGN":
            answer = {**draft, "microSpec": whole}
            if staging != "switch-off":
                answer["extraMilestoneReasons"] = [review] if staging == "owner-review" else [" "]
        elif kind == "MILESTONES":
            corrected = question["facts"].get("Exact corrections")
            answer = {"milestones": [first, second] if staging == "owner-review" and corrected else [whole]}
        else:
            assert kind == "REVIEW", question
            answer = {"amendments": [], "unresolved": [], "why": "Every action has a material outcome."}
        return json.dumps({"answer": answer})

    def user(request):
        displayed[request["gate"]] = request["subject"]
        if request["gate"] == "A":
            if staging == "single":
                export_group = request["ask"]["groups"][0]
                assert export_group["question"] == "Which books should the CSV include?"
                assert next(o["label"] for o in export_group["options"]
                            if o["id"] == export_group["default"]) == "All saved books"
            return {"request": request["request"], "replyId": "lock",
                    "selections": [{"groupId": "round", "optionId": "done"}]}
        raise Waiting()

    run = Run(tmp_path / "product", tmp_path / "workers", dream, {}, provider, user, mode="lead")
    try:
        cursor = run.run()
        assert cursor.state == "B" and cursor.B is None, cursor.failure
        assert list(displayed) == ["A", "B"]
        assert displayed["A"]["vision"]["size"] == "small"
        if staging == "single":
            locked = run.store.read(cursor.A)["subject"]["vision"]
            assert questions.commitments(locked)["scopeIn:export"]["outcome"] == "The CSV contains every saved title."
        approved = displayed["B"]["milestones"]
        expected = [[record, errors], [export]] if staging == "owner-review" else [[record, export, errors]]
        assert [item["promises"] for item in approved] == expected
        planning_calls = {"single": [], "owner-review": ["MILESTONES", "MILESTONES"], "switch-off": ["MILESTONES"]}
        assert calls == ["ALIGN", *planning_calls[staging], "REVIEW"]
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("arm", ["hello-reopen", "micro-redraft", "cumulative-repair", "unchanged", "proof-owner", "plan-owner",
                                 "lh01", "lh02", "explicit-question", "lr01", "weak-check", "guide-replay",
                                 "bound-note", "unresolved-plan", "missing-check", "guard-repair", "failed-witness",
                                 "guard-feedback", "incomplete-witness", "unrelated-witness", "carry-witness",
                                 "carry-with-limit", "owner-interruption", "keep-building", "builder-stalled",
                                 "keep-building-off", "guard-once", "scope-note", "absolute-evidence",
                                 "extreme-advisory", "undeclared-credit", "environment-limit",
                                 "d01-evidence", "d01-evidence-off", "guide-coached",
                                 "killed-work", "killed-before-thread"])
def test_lead_milestones_golden(tmp_path, monkeypatch, arm):
    """The Lead charter's B owns writes and each later candidate must preserve earlier promises.

    A replacement check can hide a regression, and a receipt can stall proof or lose a genuine question.
    Wave journeys never exercise Lead's B-bound spec, durable witness or owner-specific repair.
    missing-check catches an omitted builder witness blocking the guard before its real walk.
    guard-repair catches a demonstrated product defect falling through to C. Cumulative repair
    does not check that unrelated guard actions carry forward.
    guide-replay catches a missing shell observation or a receipt typo unnecessarily reopening the builder.
    proof-owner catches an omitted proof subject disappearing at C or reopening the builder.
    The wave journeys do not exercise those Lead paths.
    unresolved-plan catches a missing accepted promise disappearing at B or triggering a whole-plan retry.
    bound-note catches review edits to the proof standard getting lost at B or in the builder's brief.
    The other Lead arms do not apply reviewer edits to proof lines.
    hello-reopen launches the native guard adapter to catch disabled tools, a missing candidate copy,
    a product file leaking onto the base through checkPaths, and evidence lost during cleanup.
    Its timeout catches a fresh guard losing completed actions and large streams losing output.
    failed-witness catches an observed nonzero builder check becoming a PROVE question when the guard
    finds no separate product defect. It requires the exact failed check to reach the same session.
    d01-evidence catches generated PNG, CSV and lifecycle paths in a receipt blocking tracked checks.
    absolute-evidence catches both rejecting absolute paths and silently losing a tracked witness.
    d01-evidence only submits relative paths. scope-note catches a guard restatement triggering
    another plan, approval and build. bound-note changes a proof line before B through REVIEW.
    guide-replay instead has a missing command file and still needs receipt correction.
    guide-coached catches an unsuccessful receipt correction reaching the person before the planner
    can supply a candidate-specific correction. guide-replay fixes the receipt on its first pass.
    guard-feedback catches an empty guard observation reaching C; existing journeys always supply feedback.
    incomplete-witness catches a green retained check hiding missing recorded steps of a multi-line B promise.
    failed-witness uses a nonzero exit, and guard-repair observes the defect directly in the walk.
    unrelated-witness catches an executed check for a different promise authorizing the same builder repair.
    It also catches a stale action number or a nonexistent B field causing another judge call.
    extreme-advisory catches an oversized name reopening the builder instead of reaching the owner as advice.
    scope-note covers plan restatements only. undeclared-credit catches a second judge or false proof when a
    valid greeting observation is credited to rejection as well. No other arm mixes valid and invalid credits.
    carry-witness catches C treating a missing lifecycle row as missing product proof after a real guard walk.
    carry-with-limit catches the wc04 repair loop when another aspect of the same promise remains uncertain.
    incomplete-witness has no independent observation; neither carry case replaces its required repair.
    owner-interruption catches a green standalone rejection check masking an interrupted paste from
    the owner's account. Only this arm walks a failing combination omitted from B and verifies repair.
    guard-repair also resumes with a Windows-encoded guide edit, which previously blocked the guard
    before the repaired rejection could reach C.
    guard-once catches serial defect discovery or a repair re-walk that replays a complete packet:
    two real bad inputs must reach one resumed builder turn, with only the affected path walked again.
    guard-repair only reproduces one defect.
    micro-redraft catches an extra call or stale spec after an A edit. The native builder must receive the
    owner's exact words first and the revised proof standard before its receipt. bound-note exercises the
    old brief switch. Looking only at stored WORK facts misses a renderer that sends the wrong brief.
    keep-building catches an early status receipt becoming a user question or a cold builder start.
    environment-limit catches a built milestone being resumed for a physical-device check, or
    losing the reason before C. The other limit arms originate in guard findings or replayed commands.
    builder-stalled catches rewritten status prose masking a repeated tree and bypassing planner options.
    Existing repair arms resume only after proof or an explicit owner decision. keep-building-off
    confirms a proof fault still follows the original routing in the control arm.
    Killed WORK catches a controller exit before result.json strands an approved build.
    These arms kill the controller with partial product writes and check both native resume
    and a fresh call without a thread. Existing resumes all have a completed recording.
    """
    from types import SimpleNamespace

    from seldon import frontdoor, lead
    from seldon.decisions import Waiting

    milestone_count = 2 if arm in ("cumulative-repair", "unchanged", "environment-limit") else 1
    killed_work = arm in ("killed-work", "killed-before-thread")
    if killed_work and sys.platform != "win32":
        pytest.skip("Controller death tears down the native Windows Job")
    micro = arm == "micro-redraft"
    guide_case = arm in ("guide-replay", "guide-coached")
    if not micro and milestone_count == 1:
        monkeypatch.setenv("SELDON_OFF", "leadPlanOne,leadWitnessCoach,leadThinBrief"
                           if arm == "bound-note" else "leadPlanOne")
    else:
        monkeypatch.delenv("SELDON_OFF", raising=False)
    if arm == "proof-owner":
        monkeypatch.setenv("SELDON_OFF", "leadPlanOne,leadGuardOwner")
    if arm == "keep-building-off":
        monkeypatch.setenv("SELDON_OFF", "leadPlanOne,leadKeepBuilding,leadSeldonOwns,leadThinBrief")
    if arm == "d01-evidence-off":
        monkeypatch.setenv("SELDON_OFF", "leadPlanOne,leadSeldonOwns,leadPlausibleGuard")
    edit = "Require the exact stderr text Usage: pass a name when rejecting --help."
    flow = []
    dream = "Run `python hello.py Ada` and say ‘Hello, Ada!’ exactly. Reject `python hello.py --help`. " + (
        "In a separate milestone add ‘Goodbye, Ada!’." if milestone_count == 2 else "")
    dream += " Fixture: " + arm
    interruption = "Someone can paste --help Ada together. Reject the whole paste without a greeting."
    if arm == "owner-interruption":
        dream += "\n" + interruption
    if arm == "guard-once":
        dream += "\nReject unknown flags such as --unknown the same way as --help."
    device_check = "Run the greeting from a physical accessibility terminal"
    device_reason = "No physical accessibility terminal is connected to this build environment."
    if arm == "environment-limit":
        dream += f"\n{device_check}."
    draft = questions.consequential_round_example()
    draft.update(questions=[], recommendation="done")
    vision = draft["vision"]
    vision.update(size="tiny", sizeWhy="A small command-line experience.", understanding=dream,
                  constraints=[{"handle": "greeting", "value": "Hello, Ada!",
                                "meaning": "The exact greeting for Ada", "source": "kickoff"}], scopeOut=[])
    if arm == "scope-note":
        vision["scopeOut"] = [{"outcome": "A network service is outside the approved CLI scope.",
                               "sources": ["kickoff"]}]
    vision["stories"] = [
        {"handle": name, "feature": name, "story": f"A visitor wants to run {name}.", "outcome": outcome,
         "constraints": ["greeting"] if name == "hello" else [], "sources": ["kickoff"]}
        for name, outcome in [("hello", "Hello, Ada!"), ("bye", "Goodbye, Ada!")][:milestone_count]
    ]
    vision["quality"] = [{"handle": "exact", "criterion": "Reject an unsupported argument with useful usage",
                          "verification": "Compare exit status and streams", "constraints": [], "sources": ["kickoff"]}]
    if arm == "environment-limit":
        vision["quality"].append({"handle": "device", "criterion": "Greet on the physical terminal",
                                  "verification": device_check, "constraints": [], "sources": ["kickoff"]})
    planned = []
    for story in vision["stories"]:
        name = story["handle"]
        expected = story["outcome"]
        promises = [{"vision": f"scopeIn:{name}", "action": f"Pass Ada to {name}",
                     "result": f"See {expected}", "notMetIf": "The greeting omits Ada."}]
        if name == "hello":
            promises.append({"vision": "quality:exact", "action": "Pass --help",
                             "result": "Exit 2 with usage on stderr and empty stdout",
                             "notMetIf": "The command succeeds or omits usage."})
            if arm == "guard-once":
                promises[-1]["action"] = "Pass --help or an unknown flag"
            if arm == "environment-limit":
                promises.append({"vision": "quality:device", "action": device_check,
                                 "result": "The connected terminal shows Hello, Ada!",
                                 "notMetIf": "The connected terminal cannot display the greeting."})
        planned.append({"outcome": expected, "promises": promises, "design": "Read a positional name in Python."})
    reviewed_rejection = {"vision": "quality:exact", "action": "Run `python hello.py --help`",
                          "result": "Exit 2 with Usage: pass a name on stderr and empty stdout",
                          "notMetIf": "The command exits with a status other than 2 or prints usage to stdout."}
    if micro:
        reviewed_rejection["notMetIf"] = "The command exits other than 2 or stderr omits Usage: pass a name."
    if arm == "bound-note":
        planned[0]["promises"][1]["notMetIf"] = "The command exits 2 or omits usage."
    witness_result = ("Exit 2 with usage on stderr and empty stdout.\n"
                      "Record status, stdout, and stderr as separate observed steps.\n"
                      "A summary of the overall rejection loses the stream details.")
    carry_case = arm in ("carry-witness", "carry-with-limit")
    witness_case = carry_case or arm in ("incomplete-witness", "unrelated-witness")
    if witness_case:
        planned[0]["promises"][1]["result"] = witness_result

    native = tmp_path / "lead_native.py"
    native.write_text(r"""
import json, sys, uuid
from pathlib import Path

args = sys.argv[1:]
schema = Path(args[args.index("--output-schema") + 1])
q = json.loads(schema.with_name("question.json").read_text(encoding="utf-8"))
prompt = schema.with_name("prompt.txt").read_text(encoding="utf-8")
facts = q["facts"]
if q["kind"] == "JUDGE":
    import base64, os, subprocess, tempfile, time

    assert 'sandbox_mode="danger-full-access"' in args and 'features.shell_tool=false' not in args
    assert "never open files or pages in desktop apps" in prompt and os.environ["GIT_EDITOR"] == "true"
    assert "--ephemeral" not in args and Path.cwd().resolve() == Path(facts["Candidate"]).resolve()
    assert not Path(".git").exists() and Path(facts["Guide path"]).is_file()
    assert Path(tempfile.gettempdir()).parent == Path.cwd().parent
    evidence = Path(os.environ["SELDON_EVIDENCE_DIR"])
    assert evidence.parent == Path.cwd().parent
    base = Path(facts["Base"])
    assert not (base / "hello.py").exists()
    saved = Path(tempfile.gettempdir()) / "guard-session.json"
    if "resume" not in args:
        assert not saved.exists()
        actions = []
        for step, arg, code in (("Greet", "Ada", 0), ("Restart", "Ada", 0), ("Reject bad input", "--help", 2)):
            result = subprocess.run([sys.executable, "-B", "hello.py", arg], capture_output=True, text=True)
            assert result.returncode == code
            red = subprocess.run([sys.executable, "-B", "hello.py", arg], cwd=base, capture_output=True, text=True)
            assert "can't open file" in red.stderr
            actions.append({"action": step, "actual": (result.stdout or result.stderr).strip(),
                            "baseline": red.stderr.strip(), "screenshots": ["view.png"] if code == 0 else [],
                            "promises": ["tryItGuide", "quality:exact" if code else "scopeIn:hello"]})
        saved.write_text(json.dumps(actions), encoding="utf-8")
        print(json.dumps({"type": "thread.started", "thread_id": "guard-session"}), flush=True)
        time.sleep(60)
        raise AssertionError("guard was not interrupted")
    assert args[args.index("resume") + 1] == "guard-session"
    actions = json.loads(saved.read_text(encoding="utf-8"))
    print(json.dumps({"type": "thread.started", "thread_id": "guard-session"}), flush=True)
    for _ in range(520):
        print(json.dumps({"type": "fixture.tool.output", "text": "x" * 8192}))
        print("y" * 8192, file=sys.stderr)
    print("guard stderr end", file=sys.stderr)
    actions.append({"action": "Inspect hello.py", "actual": Path("hello.py").read_text(),
                    "baseline": "", "screenshots": [], "promises": ["sourceAcceptance"]})
    (evidence / "command.txt").write_text(actions[2]["actual"], encoding="utf-8")
    (evidence / "view.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR1sAAAAASUVORK5CYII="))
    answer = {"why": "Walked the command and rejection from the guide.", "actions": actions, "findings": [],
              "feedback": [{"step": "Reject bad input", "note": "Usage is easy to scan."}],
              "reuse": [], "maintenance": "Update the argument guard in hello.py before greeting."}
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"answer": answer})}}))
    print(json.dumps({"type": "turn.completed"}))
    raise SystemExit
arm = facts["Owner words"]["Dream"].rsplit(" Fixture: ", 1)[1].splitlines()[0]
if arm in ("bound-note", "keep-building-off"):
    assert prompt.startswith(facts["Milestone brief"] + "\n\n")
else:
    assert prompt.startswith(facts["Owner words"]["Dream"] + "\n\nApproved spec\n")
    assert prompt.index("How we work\n") < prompt.index("Return schema\n")
    assert json.loads(prompt.split("\n\nExample receipt\n", 1)[1]) == q["examples"][0]
    assert str(Path.cwd()) not in prompt and "Previous checkpoint" not in prompt
assert "provider-native sub-agents and skills" in prompt and "Return schema" in prompt
assert "features.multi_agent=true" in args and "skills.bundled.enabled=true" in args
assert "--ephemeral" not in args and 'sandbox_mode="danger-full-access"' in args
thread = args[args.index("resume") + 1] if "resume" in args else (
    "milestone-builder" if arm in ("keep-building", "builder-stalled", "keep-building-off") else str(uuid.uuid4()))
if arm in ("killed-work", "killed-before-thread"):
    marker = Path.cwd().parents[1] / "kill-ready"
    partial = Path("hello.py")
    if not marker.exists():
        import time

        partial.write_text("import sys\n", encoding="utf-8")
        if arm == "killed-work":
            thread = "interrupted-builder"
            print(json.dumps({"type": "thread.started", "thread_id": thread}), flush=True)
        marker.touch()
        time.sleep(60)
        raise AssertionError("The controller should have been killed during WORK")
    assert ("resume" in args) == (arm == "killed-work")
    if arm == "killed-work":
        assert thread == "interrupted-builder"
    assert "You were interrupted. Continue" in prompt
    assert partial.read_text() == "import sys\n"
print(json.dumps({"type": "thread.started", "thread_id": thread}), flush=True)
assert Path.cwd().resolve() == Path(facts["Write scope"]).resolve()
if arm == "micro-redraft":
    assert "Exit 2 with Usage: pass a name on stderr and empty stdout" in prompt.split("\nHow we work\n")[0]
    assert "Design the data model around the real-world identities in the owner's story" in prompt
if arm == "bound-note":
    assert "\nCoaching\n" not in prompt and "Use one runnable path" in prompt
assert "`python hello.py --help`" in facts["Owner words"]["Dream"]
if arm in ("keep-building", "builder-stalled", "keep-building-off"):
    assert "Use work remains only for unfinished implementation" in prompt
    partial = Path("hello.py")
    if "resume" in args:
        assert args[args.index("resume") + 1] == "milestone-builder"
        assert "Hello, Ada!" in prompt
    if direction := facts.get("Owner direction"):
        assert arm == "builder-stalled" and direction == "Use the existing Python: Build the CLI with Python"
    elif "resume" not in args or not partial.exists():
        if "resume" in args:
            assert "The next step is to implement the greeting." in prompt
            if arm == "keep-building":
                partial.write_text("print('Hello, Ada!')\n", encoding="utf-8")
        next_step = ("add the usage rejection and checks" if partial.exists()
                     else "build the CLI" if "resume" in args else "implement the greeting")
        summary = f"The milestone is incomplete. The next step is to {next_step}."
        checks = [{"command": [sys.executable, "-B", "hello.py", "Ada"], "promises": ["scopeIn:hello"]}]
        receipt = {"outcome": "proof fault" if arm == "keep-building-off" else "work remains",
                   "summary": summary, "question": "", "checkPaths": [], "unverified": [],
                   "checks": checks if partial.exists() else []}
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message",
                          "text": json.dumps({"answer": receipt})}}))
        print(json.dumps({"type": "turn.completed", "usage": {}}))
        raise SystemExit
    else:
        assert "The next step is to add the usage rejection and checks." in prompt
if arm == "plan-owner" and "positional" in facts["Approved spec"]["how"]:
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(
        {"answer": {"outcome": "plan correction", "summary": "The approved design has an ambiguous parser.",
                    "checks": [], "checkPaths": [], "unverified": [], "question": ""}})}}),
          flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {}}), flush=True)
    raise SystemExit
expected = facts["Milestone brief"].splitlines()[1]
name = "hello" if expected.startswith("Hello") else "bye"
path = Path(f"checks/{name}.py")
path.parent.mkdir(exist_ok=True)
probe = (
    "import subprocess, sys\n"
    f"output = subprocess.check_output([sys.executable, '{name}.py', 'Ada'], text=True).strip()\n"
    f"assert output == {expected!r}, f'Expected {expected}, got {{output}}'\n"
    "print(output)\n"
)
path.write_text(probe, encoding="utf-8")
checks = [{"vision": f"scopeIn:{name}", "purpose": f"Run {name} with Ada",
           "command": [sys.executable, "-B", path.as_posix()], "expectedEvidence": expected}]
def implement(name, greeting):
    target = Path(name + ".py")
    prefix = target.read_text() if arm in ("killed-work", "killed-before-thread") else "import sys\n"
    target.write_text(
        prefix + "if sys.argv[1:] == ['--help']:\n"
        "    print('Usage: pass a name', file=sys.stderr)\n    sys.exit(2)\n"
        + f"print(f'{greeting}, {{sys.argv[1]}}!')\n", encoding="utf-8")
implement(name, expected.split(",")[0])
if arm == "owner-interruption" and "resume" in args:
    assert "Observed: status 0, stdout 'Hello, --help!\\n'" in facts["Exact failure"]
    product = Path("hello.py")
    product.write_text(product.read_text().replace("sys.argv[1:] == ['--help']", "'--help' in sys.argv[1:]"))
if arm == "guard-repair":
    if "resume" in args:
        assert "B: Exit 2 with usage on stderr and empty stdout" in facts["Exact failure"]
        assert "python hello.py --help exited 0" in facts["Exact failure"]
    else:
        Path("hello.py").write_text("import sys\nprint(f'Hello, {sys.argv[1]}!')\n", encoding="utf-8")
if arm == "guard-once":
    product = Path("hello.py")
    if "resume" in args:
        assert "status 0, stdout 'Hello, --help!\\n'" in facts["Exact failure"]
        assert "status 0, stdout 'Hello, --unknown!\\n'" in facts["Exact failure"]
        assert "run your regression checks" in prompt
        product.write_text(product.read_text().replace(
            "sys.argv[1:] == ['--help']", "sys.argv[1].startswith('--')"), encoding="utf-8")
    else:
        product.write_text("import sys\nprint(f'Hello, {sys.argv[1]}!')\n", encoding="utf-8")
check_paths = [path.as_posix()]
if arm == "hello-reopen":
    check_paths.append("hello.py")
if name == "hello":
    err = Path("checks/usage.py")
    setup = ""
    if arm in ("lr01", "weak-check"):
        setup = (
            "subprocess.check_output(['git', 'ls-files', '--error-unmatch', '--', 'checks/usage.py'])\n"
            "assert not subprocess.check_output(['git', 'status', '--porcelain'])\n"
            "print('tracked witness in a clean checkout')\n"
        )
    usage = ("import subprocess, sys\n" + setup +
             "r = subprocess.run([sys.executable, 'hello.py', '--help'], capture_output=True, text=True)\n")
    err.write_text(usage +
        "assert (r.returncode, r.stdout, r.stderr) == (2, '', 'Usage: pass a name\\n')\n"
        "print('usage error asserted')\n", encoding="utf-8")
    if arm == "failed-witness":
        if "resume" in args:
            assert "'exitCode': 7" in facts["Exact failure"] and "checks/usage.py" in facts["Exact failure"]
        else:
            with err.open("a", encoding="utf-8") as output:
                output.write("raise SystemExit(7)\n")
    if arm == "weak-check":
        if "resume" in args:
            assert facts["Exact failure"].endswith(
                "In checks/usage.py, assert the CLI's usage line, exit 2 and empty stdout.")
        else:
            err.write_text(usage + "assert r.returncode == 2 and not r.stdout and r.stderr.strip()\n"
                           "print('usage error asserted')\n", encoding="utf-8")
    checks.append({"vision": "quality:exact", "purpose": "Exercise documented rejection",
                   "command": [sys.executable, "-B", err.as_posix()], "expectedEvidence": "usage on stderr"})
    check_paths.append(err.as_posix())
    import base64
    Path("preview.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6ioAAAAASUVORK5CYII="))
elif "resume" in args:
    assert "Observed: Broken, Ada!" in facts["Exact failure"]
    if arm != "unchanged":
        implement("hello", "Hello")
elif arm != "environment-limit":
    implement("hello", "Broken")
    Path("checks/hello.py").write_text("print('Hello, Ada!')\n", encoding="utf-8")
if name == "bye" and arm != "environment-limit":
    check_paths.append("checks/hello.py")
    checks.append({"vision": "scopeIn:hello", "purpose": "Proposed replacement",
                   "command": [sys.executable, "-B", "checks/hello.py"], "expectedEvidence": "Hello, Ada!"})
    if "resume" in args and arm != "unchanged":
        checks.pop()
        check_paths.pop()
        Path("checks/hello.py").write_text(
            "import subprocess, sys\n"
            "output = subprocess.check_output([sys.executable, 'hello.py', 'Ada'], text=True).strip()\n"
            "assert output == 'Hello, Ada!', f'Expected Hello, Ada!, got {output}'\n"
            "print(output)\n", encoding="utf-8")
guide = Path("docs/try-it.md")
guide.parent.mkdir(exist_ok=True)
if arm == "guard-repair" and "resume" in args:
    with guide.open("ab") as output:
        output.write("\nAda’s error should appear on stderr.\n".encode("cp1252"))
if arm == "bound-note":
    from tempfile import TemporaryDirectory
    with err.open("a", encoding="utf-8") as output:
        output.write("import os\nfrom pathlib import Path\n"
                     "Path(os.environ['SELDON_EVIDENCE_DIR'], 'preview.png').write_bytes(Path('preview.png').read_bytes())\n")
    with TemporaryDirectory() as scratch:
        assert not Path(scratch).is_relative_to(Path.cwd())
    for legacy in ("docs/try-it/work/fresh/pkg", "node_modules/pkg", ".venv/pkg", "dist/pkg"):
        generated = Path(legacy, "generated.txt")
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("legacy generated file")
if "resume" not in args or arm in ("keep-building", "builder-stalled", "killed-work"):
    with guide.open("a", encoding="utf-8") as output:
        output.write(f"\nRun `python {name}.py Ada`. Expect {expected}\n")
        if name == "hello":
            output.write("Run `python hello.py --help`. Expect usage on stderr and exit 2.\n")
if arm in ("guide-replay", "guide-coached"):
    import shutil
    shell = shutil.which("powershell") or shutil.which("pwsh")
    assert shell
    with guide.open("a", encoding="utf-8") as output:
        if "resume" not in args:
            output.write("Run in PowerShell: `python hello.py --help; $LASTEXITCODE`. Expect 2.\n")
    script = Path("docs/try-it/replay.py")
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("print('guide check passed without shell status')\n", encoding="utf-8")
    check_paths.append(script.as_posix())
checks = [{"command": check["command"], "promises": [check["vision"]]} for check in checks]
if arm in ("guide-replay", "guide-coached"):
    checks.append({"command": [sys.executable, "-B", script.as_posix()], "promises": ["tryItGuide"]})
else:
    checks[-1 if name == "hello" else 0]["promises"].append("tryItGuide")
if arm in ("incomplete-witness", "unrelated-witness", "carry-witness", "carry-with-limit"):
    if "resume" in args:
        failure = facts["Exact failure"]
        assert "B: Exit 2 with usage on stderr and empty stdout.\nRecord status, stdout, and stderr" in failure
        assert "A summary of the overall rejection loses the stream details." in failure
        assert "checks/usage.py" in failure and "Rejection summary" in failure and "exitCode': 0" in failure
        assert "omits stdout and stderr" in failure
        steps = "[('Status', r.returncode), ('Stdout', r.stdout), ('Stderr', r.stderr)]"
    else:
        steps = "[('Rejection summary', r.returncode == 2)]"
    evidence_rows = f"[{{'step': name, 'actual': value}} for name, value in {steps}]"
    if arm in ("carry-witness", "carry-with-limit"):
        evidence_rows += (" + [{'step': 'Reject bad input', 'observed': False, 'reason': 'No stream record', "
                          "'requirements': ['quality:exact']}]")
    if arm == "carry-with-limit":
        evidence_rows += (" + [{'step': 'Follow the guide manually', 'observed': False, "
                          "'requirements': ['tryItGuide']}]")
    with err.open("a", encoding="utf-8") as output:
        output.write("import json, os\nfrom pathlib import Path\n"
                     "Path(os.environ['SELDON_EVIDENCE_DIR'], 'operation-lifecycle.json').write_text(\n"
                     f"    json.dumps({evidence_rows}))\n")
if arm in ("missing-check", "guard-repair", "guard-once"):
    checks = [checks[0]]
    if arm == "guard-once" and "resume" in args:
        import subprocess
        subprocess.run([sys.executable, "-B", err.as_posix()], check=True)
        for flag in ("--help", "--unknown"):
            result = subprocess.run([sys.executable, "-B", "hello.py", flag], capture_output=True, text=True)
            assert (result.returncode, result.stdout, result.stderr) == (2, "", "Usage: pass a name\n")
    elif arm != "guard-once":
        checks[0]["promises"].append("tryItGuide")
receipt = {"outcome": "done", "summary": f"A visitor can run {name}. Resumed: {'resume' in args}.",
           "checks": checks, "checkPaths": check_paths, "question": "", "unverified": []}
if arm == "environment-limit" and name == "hello":
    receipt["unverified"] = [{"promise": "quality:device", "action": facts["Promises"]["quality:device"]["action"],
                              "reason": "No physical accessibility terminal is connected to this build environment."}]
# Retained e2dc3f5 lh01 r57 and lh02 r40, with the fixture's runnable witnesses attached.
if arm == "lh01":
    receipt.update(outcome="question", summary=(
        "Planner-owned blocker: the active response schema does not require or explicitly forbid the requested "
        "check details, but the preceding receipt incorrectly claimed a mismatch. The implementation and "
        "Windows Python 3.12.7 checks were reported passing; no checks were rerun."))
if arm == "lh02":
    receipt.update(outcome="proof fault", summary=(
        "The CLI, behavioral check, and try-it guide were written in the assigned workspace. On Windows "
        "Python 3, the greeting, usage-error, combined contract, and walkthrough checks each printed “passed.” "
        "However, the reported test command ran them in sequence without independently capturing each check’s "
        "process exit status, so the unattended check outcomes were not fully proven. Browser testing is not "
        "applicable because this product has no browser path. The next step is to rerun each check while "
        "asserting its exit status is 0."))
if arm in ("guide-replay", "guide-coached"):
    receipt["checkPaths"].append("mistyped-guide-name.py")
    checks[-1]["command"][-1] = "mistyped-guide-name.py"
if arm.startswith("d01-evidence"):
    receipt["checkPaths"].extend(["browser-evidence/full-390.png", "browser-evidence/fridays.csv",
                                  "browser-evidence/operation-lifecycle.json"])
    with err.open("a", encoding="utf-8") as output:
        output.write("import os\nfrom pathlib import Path\n"
                     "evidence = Path(os.environ['SELDON_EVIDENCE_DIR'])\n"
                     "evidence.joinpath('full-390.png').write_bytes(Path('preview.png').read_bytes())\n"
                     "evidence.joinpath('fridays.csv').write_text('day,count\\nFriday,14\\n')\n"
                     "evidence.joinpath('operation-lifecycle.json').write_text('[]')\n")
if arm == "absolute-evidence":
    receipt["checkPaths"] = [str(Path.cwd() / path) for path in check_paths]
    receipt["checkPaths"].extend([str(Path.cwd().parent / "outside-evidence.png"), "../generated.csv"])
if arm == "bound-note":
    err.write_text(err.read_text() +
        "import json, os\nfrom pathlib import Path\n"
        "Path(os.environ['SELDON_EVIDENCE_DIR'], 'operation-lifecycle.json').write_text("
        "json.dumps([{'step': 'Read shell exit status', 'observed': False, 'reason': 'Unavailable'}]))\n")
if arm == "explicit-question":
    receipt["question"] = "Which of the two Windows Python runtimes should the guide target?"
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message",
                  "text": json.dumps({"answer": receipt})}}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {}}), flush=True)
""", encoding="utf-8")
    setup = {"providers": deepcopy(ROLES), "scriptedUser": True, "userOrigin": "scripted journey",
             "codexCommand": [sys.executable, "-B", str(native)], "python": {"path": sys.executable}}
    provider = Codex(tmp_path / "calls", setup)
    invoke = provider.invoke
    if arm == "hello-reopen":
        from seldon import providers

        execute = providers.execute

        def guard_deadline(command, cwd, timeout, *args, **kwargs):
            if Path(cwd).parent.name.startswith("guard-"):
                assert timeout == setup["providers"]["build"]["timeout"]
                if "resume" not in command:
                    timeout = 20
            return execute(command, cwd, timeout, *args, **kwargs)

        monkeypatch.setattr(providers, "execute", guard_deadline)
    reviewed = []
    approved_milestone = None

    def scripted(question, started):
        strict_schema(question["schema"])
        kind, facts = question["kind"], question["facts"]
        if micro and kind in ("ALIGN", "MILESTONES", "REVIEW", "WORK"):
            flow.append(kind)
        if kind == "WORK":
            if arm == "bound-note":
                assert facts["Milestone brief"] == approved_milestone["brief"]
                assert facts["Promises"]["quality:exact"] == reviewed_rejection
                assert facts["Promises"]["scopeIn:hello"]["notMetIf"] == "The greeting omits Ada."
            else:
                assert facts["Milestone brief"] in reviewed[-1]
            if facts.get("Previous checkpoint"):
                assert facts["How the previous milestone felt"][0]["note"] == "Usage is easy to scan."
            return invoke(question, started)
        if kind == "ALIGN":
            answer = deepcopy(draft)
            if "microSpec" in question["schema"]["properties"]["answer"]["properties"]:
                answer["microSpec"] = deepcopy(planned[0]) if micro else None
                answer["extraMilestoneReasons"] = ["The owner requested a separate milestone for Goodbye."] if milestone_count == 2 else []
            if micro and "Previous proposal" in facts:
                assert edit in facts["Sources"].values()
                assert "microSpec" not in facts["Previous proposal"]
                answer["vision"]["quality"][0]["criterion"] = edit
                answer["microSpec"]["promises"][1] = reviewed_rejection
        elif kind == "MILESTONES":
            assert not micro
            assert facts["Dream"] == dream
            answer = {"milestones": deepcopy(planned)}
            if facts.get("Exact failure"):
                assert arm == "plan-owner"
                assert facts["Exact failure"] == "The approved design has an ambiguous parser."
                answer["milestones"][0]["design"] = "Parse one name and reject extra flags."
            promises = answer["milestones"][0]["promises"]
            if arm == "bound-note":
                if not facts.get("Exact corrections"):
                    del promises[0]["notMetIf"]
                    promises[1]["vision"] = "unapproved"
                else:
                    errors = " ".join(facts["Exact corrections"][0]["Errors"])
                    assert "notMetIf" in errors and ".vision: expected one of" in errors
                    promises.append(promises[0].copy())
            if arm == "unresolved-plan":
                promises.pop()
        elif kind == "REVIEW":
            document = facts["Builder spec"]
            assert dream in document and questions.GUIDE_WALKTHROUGH in document
            assert "Accepted constraint greeting\nHello, Ada!" in document
            reviewed.append(document)
            if micro:
                assert reviewed_rejection["result"] in document
                assert planned[0]["promises"][1]["result"] not in document
            answer = {"amendments": [], "unresolved": [], "why": "The spec exercises the named action."}
            if arm == "bound-note":
                assert document.count("scopeIn:hello action\n") == 1
                answer["amendments"] = [
                    {"target": f"quality:exact {field}", "replacement": reviewed_rejection[field]}
                    for field in ("action", "result", "notMetIf")]
            if arm == "unresolved-plan":
                assert "quality:exact" in facts["Unassigned promises"]
                answer["unresolved"] = ["The guide must show the rejection path."]
        elif kind == "DECIDE":
            assert facts["User's words"] == "yes, please continue"
            answer = {"choice": "Unclear", "why": "No change to the named input."}
        elif kind == "RECEIPT":
            answer = deepcopy(facts["Return"])
            if guide_case:
                assert "mistyped-guide-name.py" in facts["Error"]
                if arm == "guide-replay" or "Recovery direction" in facts:
                    answer["checkPaths"].remove("mistyped-guide-name.py")
                    answer["checks"][-1]["command"][-1] = "docs/try-it/replay.py"
            else:
                assert arm == "d01-evidence-off" and "browser-evidence/fridays.csv" in facts["Error"]
        elif kind == "COACH":
            if arm == "guide-coached":
                assert "mistyped-guide-name.py" in facts["Unresolved work"]["failure"]
                answer = {"guidance": "Use the committed docs/try-it/replay.py command in the receipt.",
                          "why": "The existing guide describes the PowerShell walkthrough."}
            elif arm == "builder-stalled":
                assert facts["Unresolved work"]["question"].endswith("The next step is to build the CLI.")
                answer = {"ask": {"question": "Which Python will the visitor use on their own machine?",
                                  "options": [{"label": "Use the existing Python", "means": "Build the CLI with Python"},
                                              {"label": "Provide the deployment runtime", "means": "Supply its path"}]},
                          "why": "Only you know which Python is available to the visitor."}
            else:
                raise Waiting()
        else:
            import shutil
            import subprocess

            assert kind == "JUDGE"
            work = Path(question["execution"]["walk"])
            if arm == "environment-limit" and "scopeIn:bye" not in facts["Subjects"]:
                limit, = facts["Unverified checks"]
                assert limit == {"promise": "quality:device", "action": device_check, "reason": device_reason,
                                 "observed": False, "commit": facts["Candidate commit"], "source": limit["source"]}
            if arm == "environment-limit" and "scopeIn:bye" in facts["Subjects"]:
                limit, = facts["Earlier unverified checks"]
                assert limit["commit"] != facts["Candidate commit"] and limit["reason"] == device_reason
            if arm == "hello-reopen":
                return invoke(question, started)
            assert (work / questions.GUIDE_PATH).is_file()
            repair = facts.get("Repair")
            rewalk = repair or facts.get("Previous walk")
            actions = []
            for name, args, expected_status in (("Greet", ["Ada"], 0), ("Restart", ["Ada"], 0),
                                                 ("Reject bad input", ["--help"], 2)):
                if arm in ("guard-repair", "guard-once") and rewalk and expected_status == 0:
                    continue
                if witness_case and not carry_case and not rewalk and expected_status:
                    continue
                if arm in ("owner-interruption", "guard-once") and expected_status:
                    continue
                result = subprocess.run([sys.executable, "-B", "hello.py", *args], cwd=work,
                                        capture_output=True, text=True, check=False)
                if arm != "guard-repair" or rewalk or expected_status == 0:
                    assert result.returncode == expected_status
                promise = "quality:exact" if expected_status else "scopeIn:hello"
                baseline = ""
                if promise in facts["New promises"]:
                    baseline = subprocess.run([sys.executable, "-B", "hello.py", *args], cwd=facts["Base"],
                                              capture_output=True, text=True).stderr.strip()
                actions.append({"action": name, "actual": (result.stdout or result.stderr).strip(),
                                "promises": [promise, "tryItGuide"], "screenshots": [], "baseline": baseline})
                if carry_case and expected_status:
                    assert (result.returncode, result.stdout, result.stderr) == (2, "", "Usage: pass a name\n")
                    actions[-1]["actual"] = repr((result.returncode, result.stdout, result.stderr))
                    rejection = len(actions)
            if arm != "proof-owner":
                actions.append({"action": "Inspect implementation", "actual": (work / "hello.py").read_text(),
                                "promises": ["sourceAcceptance"], "baseline": "", "screenshots": []})
            if "scopeIn:bye" in facts["Subjects"]:
                command = [sys.executable, "-B", "bye.py", "Ada"]
                bye = subprocess.run(command, cwd=work, capture_output=True, text=True, check=True)
                red = subprocess.run(command, cwd=facts["Base"], capture_output=True, text=True)
                actions.append({"action": "Say goodbye", "actual": bye.stdout, "baseline": red.stderr,
                                "promises": ["scopeIn:bye"], "screenshots": []})
            green = [row for row in facts["Observations"].values() if row["purpose"] == "milestone"]
            assert all(row["cleanCheckout"] and row["executionStarted"] and row["cleanupVerified"]
                       and row["commit"] == facts["Candidate commit"] for row in green)
            if not repair and arm not in ("missing-check", "guard-repair", "guard-once"):
                assert any("usage error asserted" in row["stdout"] for row in green)
            answer = {"why": "Walked the command and rejection from the guide.", "actions": actions,
                      "findings": [], "feedback": [{"step": "Reject bad input", "note": "Usage is easy to scan."}]}
            if arm == "scope-note":
                answer["findings"] = [{"promise": "scopeIn:hello", "kind": "amend",
                                       "reason": "This walk does not expand scope to a network service.",
                                       "evidence": "Only the approved CLI path was walked."},
                                      {"promise": "scopeIn:hello", "kind": "amend", "field": "result",
                                       "replacement": facts["Subjects"]["scopeIn:hello"]["result"],
                                       "reason": "Keep the approved greeting.",
                                       "evidence": "The greeting matches the result at B."}]
            if arm == "proof-owner":
                assert "Owner decisions" not in facts
            else:
                guard_fields = question["schema"]["properties"]["answer"]["properties"]
                answer["maintenance"] = "Update the argument guard in hello.py before greeting."
                if "reuse" in guard_fields:
                    answer["reuse"] = []
            if arm == "guard-feedback":
                if facts.get("Exact corrections"):
                    assert "feedback" in str(facts["Exact corrections"])
                else:
                    answer["feedback"] = []
            if arm == "extreme-advisory":
                long_name = "A" * 10000
                result = subprocess.run([sys.executable, "-B", "hello.py", long_name], cwd=work,
                                        capture_output=True, text=True, check=True)
                assert result.stdout.strip() == f"Hello, {long_name}!"
                answer["findings"].append({"promise": "scopeIn:hello", "kind": "extreme",
                    "evidence": "A 10,000-character name was accepted and echoed in full.",
                    "reason": "No limit on name length. Consider 200 characters."})
            if arm == "undeclared-credit":
                greeting, = [ref for ref, row in facts["Observations"].items()
                             if row["requirements"] == ["scopeIn:hello"]]
                answer["reuse"] = [{"observation": greeting, "promises": ["scopeIn:hello", "quality:exact"],
                                    "baseline": actions[0]["baseline"]}]
                actions[:] = [a for a in actions if a["action"] in ("Greet", "Inspect implementation")]
                actions[0]["promises"].remove("scopeIn:hello")

            def defect(promise, reason, **link):
                answer["findings"].append({"promise": promise, "kind": "defect", "field": "result",
                                           "evidenceLink": link, "reason": reason})

            def uncertain(evidence, reason):
                answer["findings"].append({"promise": "quality:exact", "kind": "uncertain",
                                           "evidence": evidence, "reason": reason})

            if actions[0]["actual"] == "Broken, Ada!":
                defect("scopeIn:hello", "Restore the original greeting.", action=1)
            if arm == "owner-interruption":
                check, = [ref for ref, row in facts["Observations"].items() if "quality:exact" in row["requirements"]]
                assert facts["Observations"][check]["exitCode"] == 0
                if not repair:
                    assert interruption in facts["Dream"]
                    assert "Ada together" not in str(facts["Subjects"])
                    assert "This is it" in facts["Owner replies"]
                    assert [row["gate"] for row in facts["Owner decisions"]] == ["A", "B"]
                    baseline = subprocess.run([sys.executable, "-B", "hello.py", "--help"], cwd=facts["Base"],
                                              capture_output=True, text=True)
                    reference = guard_fields["reuse"]["items"]["properties"]["observation"]
                    # Native structured output uses the available IDs. A free text field elicited prose in S1.
                    observation = check if "enum" in reference else f"{check} proved standalone rejection."
                    answer["reuse"] = [{"observation": observation, "promises": ["quality:exact"],
                                        "baseline": baseline.stderr}]
                result = subprocess.run([sys.executable, "-B", "hello.py", "--help", "Ada"], cwd=work,
                                        capture_output=True, text=True)
                actions.append({"action": "Paste --help Ada together", "promises": ["quality:exact"],
                                "actual": f"status {result.returncode}, stdout {result.stdout!r}",
                                "baseline": "", "screenshots": []})
                if result.returncode != 2 or result.stdout:
                    defect("quality:exact", "The pasted extra word bypasses validation.", action=len(actions))
            if arm == "guard-repair":
                if repair:
                    assert "+Ada’s error should appear on stderr." in repair["Diff"]
                    assert actions[0]["actual"] == "Usage: pass a name"
                elif actions[2]["actual"] == "Hello, --help!":
                    defect("quality:exact", "python hello.py --help exited 0 and printed Hello, --help!", action=3)
            if arm == "guard-once":
                if repair:
                    assert "Dream" not in facts and "Previous walk" not in facts
                    assert not facts["Observations"], "The unchanged greeting check belongs to the first walk"
                    assert len(repair["Defects"]) == 2 and "startswith('--')" in repair["Diff"]
                else:
                    assert "Reject unknown flags" in facts["Dream"]
                for flag in ("--help", "--unknown"):
                    command = [sys.executable, "-B", "hello.py", flag]
                    result = subprocess.run(command, cwd=work, capture_output=True, text=True)
                    actual = f"status {result.returncode}, stdout {result.stdout!r}, stderr {result.stderr!r}"
                    red = "" if repair else subprocess.run(command, cwd=facts["Base"], capture_output=True,
                                                            text=True).stderr.strip()
                    actions.append({"action": flag, "actual": actual, "promises": ["quality:exact", "tryItGuide"],
                                    "baseline": red, "screenshots": []})
                    if repair:
                        assert (result.returncode, result.stdout, result.stderr) == (2, "", "Usage: pass a name\n")
                    else:
                        assert result.returncode == 0
                        defect("quality:exact", f"{flag} bypasses input validation.", action=len(actions))
            if arm == "bound-note":
                uncertain("Usage", "A remaining accessibility result is unverified.")
            if witness_case:
                (witness_id, witness), = [(ref, row) for ref, row in facts["Observations"].items()
                                          if "quality:exact" in row["requirements"]]
                assert witness["exitCode"] == 0
                if rewalk:
                    assert witness["operationLifecycle"] == [
                        {"step": "Status", "actual": 2}, {"step": "Stdout", "actual": ""},
                        {"step": "Stderr", "actual": "Usage: pass a name\n"}]
                else:
                    lifecycle = witness["operationLifecycle"]
                    assert lifecycle[0] == {"step": "Rejection summary", "actual": True}
                    if carry_case:
                        assert lifecycle[1]["observed"] is False
                    else:
                        answer["feedback"] = [{"step": "Greet", "note": "The greeting is easy to scan."}]
                    if arm == "incomplete-witness" or carry_case:
                        defect("quality:exact", "The retained Rejection summary omits stdout and stderr.",
                               observation=witness_id)
                        if carry_case:
                            answer["findings"][-1].update(witnessPath="checks/usage.py", provedAction=rejection)
                            if arm == "carry-with-limit":
                                uncertain("Reject bad input", "A separate shell rendering remains unobserved.")
                    else:
                        unrelated, = [ref for ref, row in facts["Observations"].items()
                                      if "scopeIn:hello" in row["requirements"]]
                        defect("quality:exact", "I could not observe the rejection.", observation=unrelated)
                        defect("tryItGuide", "The guide step was not recorded.", action=len(actions) + 1)
                        defect("sourceAcceptance", "The file layout could be clearer.", action=len(actions))
                        answer["findings"][-1]["field"] = "notMetIf"
            if arm == "weak-check":
                actions[2]["baseline"] = ""
            if guide_case:
                shell = shutil.which("powershell") or shutil.which("pwsh")
                status = subprocess.run([shell, "-NoProfile", "-Command", "python hello.py --help; $LASTEXITCODE"],
                                        cwd=work, capture_output=True, text=True, check=True)
                assert status.stdout.strip() == "2" and "Usage:" in status.stderr
                actions.append({"action": "Read PowerShell $LASTEXITCODE", "actual": status.stdout.strip(),
                                "baseline": "", "promises": ["tryItGuide"], "screenshots": []})
        return json.dumps({"answer": answer}), None

    monkeypatch.setattr(provider, "invoke", scripted)
    displayed = []
    build_options = []

    def user(request):
        nonlocal approved_milestone
        gate = request["gate"]
        displayed.append(gate)
        if gate == "BUILD" and arm in ("builder-stalled", "keep-building-off"):
            ask = frontdoor.state(str(project))["waitingFor"]["ask"]
            build_options.extend(o["label"] for o in ask["options"])
            if arm == "builder-stalled":
                assert "Only you know which Python is available to the visitor." in ask["question"]
                return "Use the existing Python"
            raise Waiting()
        if micro and gate == "B":
            flow.append(gate)
            milestone, = request["subject"]["milestones"]
            assert reviewed_rejection in milestone["promises"]
        if gate == "B" and arm == "bound-note":
            subject = request["subject"]
            approved_milestone, = subject["milestones"]
            assert subject["review"]["revision"] == subject["plan"]
            assert reviewed_rejection in approved_milestone["promises"]
            assert all(text in approved_milestone["brief"] for text in reviewed_rejection.values())
        if gate == "B" and witness_case:
            approved, = request["subject"]["milestones"]
            quality, = [p for p in approved["promises"] if p["vision"] == "quality:exact"]
            assert quality["result"] == witness_result
        if gate == "CHECK-IN":
            step = "Greet" if arm == "unrelated-witness" else "Reject bad input"
            assert request["subject"]["guard"]["feedback"][0]["step"] == step
            if arm.startswith("d01-evidence"):
                note, = request["subject"]["guard"]["normalisations"]
                assert all(path in note for path in ("full-390.png", "fridays.csv", "operation-lifecycle.json"))
        if gate == "PROVE":
            assert arm == "d01-evidence-off" and request["subject"]["inputOwner"] == "Seldon"
            raise Waiting()
        if gate == "C" and arm == "hello-reopen":
            guard = request["subject"]["guard"]
            assert Path(guard["evidenceFiles"]["command.txt"]).read_text("utf-8") == "Usage: pass a name"
            assert Path(guard["actions"][0]["screenshots"][0]).read_bytes().startswith(b"\x89PNG")
            assert not list((tmp_path / "workers").glob("guard-*"))
            assert not request["subject"]["unverified"]
        if gate == "C" and arm == "proof-owner":
            assert request["subject"]["unverified"] == {
                "guard:sourceAcceptance": "not judged: No independent action recorded."}
        if gate == "C" and arm == "environment-limit":
            subject = request["subject"]
            assert subject["unverified"] == {"guard:quality:device": f"not judged: {device_check}: {device_reason}"}
            limit, = subject["guard"]["limitations"]
            assert limit["observed"] is False and limit["reason"] == device_reason
            assert any(row["exitCode"] == 0 and "scopeIn:hello" in row["requirements"]
                       for row in subject["observations"])
        if gate == "C" and arm == "guard-feedback":
            assert request["subject"]["guard"]["feedback"] == [
                {"step": "Reject bad input", "note": "Usage is easy to scan."}]
        if gate == "C" and arm in ("guard-repair", "guard-once"):
            assert not request["subject"]["unverified"]
            guard = {a["action"]: a for a in request["subject"]["guard"]["actions"]}
            for action in (("--help", "--unknown") if arm == "guard-once" else ("Reject bad input",)):
                assert guard[action]["commit"] == request["subject"]["commit"]
            assert guard["Greet"]["commit"] != request["subject"]["commit"]
        if gate == "C" and arm == "incomplete-witness":
            assert not request["subject"]["unverified"], "The green but incomplete witness never reached its builder"
        if gate == "C" and carry_case:
            subject = request["subject"]
            gap, = subject["guard"]["carriedWitnesses"]
            assert gap["witnessPath"] == "checks/usage.py" and gap["quote"] == witness_result
            action = subject["guard"]["actions"][gap["provedAction"] - 1]
            assert action["commit"] == subject["commit"] and action["actual"] == repr((2, "", "Usage: pass a name\n"))
            assert subject["unverified"] == ({
                "guard:quality:exact": "not judged: A separate shell rendering remains unobserved.",
                "tryItGuide:walkthrough": "not judged: Follow the guide manually",
            } if arm == "carry-with-limit" else {})
        if gate == "C" and arm == "unrelated-witness":
            assert "does not cover quality:exact" in request["subject"]["unverified"]["guard:quality:exact"]
            assert "does not cover tryItGuide" in request["subject"]["unverified"]["guard:tryItGuide"]
            assert any(f["kind"] == "note" and "No approved B line" in f["reason"]
                       for f in request["subject"]["guard"]["findings"])
        if gate == "C" and arm in ("bound-note", "weak-check"):
            assert "guard:quality:exact" in request["subject"]["unverified"]
            if arm == "bound-note":
                assert "tryItGuide:walkthrough" in request["subject"]["unverified"]
        if gate == "A":
            if micro and displayed.count("A") == 1:
                return {"request": request["request"], "replyId": "edit",
                        "selections": [{"groupId": "round", "optionId": "other", "text": edit}]}
            return {"request": request["request"], "replyId": "lock",
                    "selections": [{"groupId": "round", "optionId": "done"}]}
        return approve(request)

    def pause_at_b(request):
        if request["gate"] == "B":
            raise Waiting()
        return user(request)

    mode = ["lead"]
    monkeypatch.setattr(lead, "os", SimpleNamespace(getenv=lambda name, default: mode[0]))
    project = tmp_path / "product"
    run = Run(project, tmp_path / "workers", dream, setup, provider, pause_at_b,
              mode=None if milestone_count == 1 else "lead")
    try:
        assert run.run().state == "B"
        before = run.store.cursor()
        assert before.B is None and gitwork.git(project, "rev-list", "--count", "HEAD") == "1"
        assert not list(run.store.records("lead_assignment"))
        assert not list((tmp_path / "calls").iterdir())
        assert frontdoor.state(str(project))["mode"] == "lead"
        if arm == "unresolved-plan":
            notes = run.store.read(before.decision)["subject"]["review"]["notJudged"]
            assert notes["quality:exact"]["reason"] == "No milestone assigns this accepted promise."
            assert notes["plan"]["reason"] == "The guide must show the rejection path."
            assert len(reviewed) == 1 and len(run.store.records("milestone_plan")) == 1
            return
        if killed_work:
            run.user = user
            assert run.step() == "BUILD"
    finally:
        run.close()
    mode[0] = "orchestrator"
    if killed_work:
        import os
        import subprocess

        controller = """
import json, sys
from pathlib import Path
from seldon.providers import Provider
from seldon.run import Run
slot = Path(sys.argv[1])
provider = Provider(slot / 'calls', json.loads(sys.argv[2]))
def unexpected(request):
    raise AssertionError(request)
Run.reopen(slot / 'product', provider, unexpected).run()
"""
        source = Path(gitwork.__file__).resolve().parents[1]
        child = subprocess.Popen([sys.executable, "-B", "-c", controller, str(tmp_path), json.dumps(setup)],
                                 env={**os.environ, "PYTHONPATH": str(source)},
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 20
            while not (tmp_path / "kill-ready").exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert (tmp_path / "kill-ready").is_file() and child.poll() is None
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
        (interrupted,) = (tmp_path / "calls").iterdir()
        assert not (interrupted / "result.json").exists()
    run = Run.reopen(project, provider, user)
    try:
        cursor = run.run()
        if micro:
            assert flow == ["ALIGN", "ALIGN", "REVIEW", "B", "WORK"]
        if arm == "keep-building-off":
            assert displayed == ["A", "B", "BUILD"] and cursor.state == "BUILD"
            assert len(run.store.records("call_session")) == 1
            assert build_options == ["Pause", "Provide a fix", "Other"]
            return
        if arm == "explicit-question":
            assert cursor.state == "BUILD" and cursor.decision
            ask = run.store.read(cursor.decision)["subject"]
            assert ask["inputOwner"] == "Planner"
            assert ask["question"] == "Which of the two Windows Python runtimes should the guide target?"
            assert not run.store.records("observation")
            return
        if arm == "d01-evidence-off":
            assert cursor.state == "PROVE" and displayed == ["A", "B", "PROVE"]
            run.close()
            monkeypatch.setenv("SELDON_OFF", "leadPlanOne")
            assert frontdoor.state(str(project))["waitingFor"] is None
            displayed.pop()
            run = Run.reopen(project, provider, user)
            cursor = run.run()
        if arm == "failed-witness":
            (_, _, failure), = run.store.records("lead_failure")
            assert failure["owner"] == "Builder"
            evidence = [run.store.read(ref) for ref in failure["evidence"]]
            assert any(row.get("exitCode") == 7 and row["commit"] == failure["candidate"] for row in evidence)
            assert any(row.get("actions") and row["commit"] == failure["candidate"] for row in evidence)
        if arm == "unchanged":
            request = cursor.decision
            assert request and cursor.state == ("BUILD" if arm == "unchanged" else "PROVE")
            question = run.store.read(request)
            assert question["subject"]["inputOwner"] == ("Planner" if arm == "unchanged" else "Seldon")
            assert len(run.store.records("call_session")) == (3 if arm == "unchanged" else 1)
            work_count = len(run.store.records("call_session"))
            run.close()
            run = Run.reopen(project, provider, lambda _: "yes, please continue")
            assert run.run().decision == request
            assert len(run.store.records("call_session")) == work_count
            coached = [q for _, _, q in run.store.records("question") if q["kind"] == "COACH"]
            assert bool(coached) == (arm == "unchanged")
            if arm == "unchanged":
                first, repeated = [row for _, _, row in run.store.records("lead_failure")]
                assert first["candidate"] == repeated["candidate"]
                assert len(run.store.records("lead_verdict")) == 2  # First milestone and the regressed candidate.
                assert "Observed: Broken, Ada!" in question["subject"]["failure"]
                failed = [run.store.read(ref) for ref in first["evidence"]]
                assert any(row.get("check") == "milestone-1:scopeIn:hello" and row["exitCode"] != 0
                           for row in failed)
            return
        assert cursor.state == "SCRIPTED_C", frontdoor.state(str(project))
        if arm == "environment-limit":
            assert [row["kind"] for _, _, row in run.store.records("question")] == [
                "ALIGN", "MILESTONES", "REVIEW", "WORK", "JUDGE", "WORK", "JUDGE"]
            assert not run.store.records("lead_failure")
            assert run.store.records("outcome")[-1][2]["verification_complete"] is False
        if killed_work:
            assert run.store.records("outcome")[-1][2]["verification_complete"] is True
        if arm in ("scope-note", "absolute-evidence"):
            assert [row["kind"] for _, _, row in run.store.records("question")] == [
                "ALIGN", "MILESTONES", "REVIEW", "WORK", "JUDGE"]
            assert run.store.records("outcome")[-1][2]["verification_complete"] is True
            (decision,) = [row for _, _, row in run.store.records("decision_request") if row["gate"] == "C"]
            guard = decision["subject"]["guard"]
            if arm == "scope-note":
                assert [(f["kind"], f["reason"]) for f in guard["findings"]] == [
                    ("note", "This walk does not expand scope to a network service."),
                    ("note", "Keep the approved greeting.")]
            else:
                assert all(path in " ".join(guard["normalisations"])
                           for path in ("outside-evidence.png", "../generated.csv"))
                assert all("checks/usage.py" in run.store.read(ref)["witness"]["paths"]
                           for ref in cursor.finalObservations)
        if arm.startswith("d01-evidence"):
            assert run.store.records("outcome")[-1][2]["verification_complete"] is True
            assert len(run.store.records("call_session")) == 1
            usage, = [run.store.read(ref) for ref in cursor.finalObservations
                      if "quality:exact" in run.store.read(ref)["requirements"]]
            assert usage["exitCode"] == 0 and "usage error asserted" in usage["stdout"]
            assert "checks/usage.py" in usage["witness"]["paths"]
            assert all(not path.startswith("browser-evidence/") for path in usage["witness"]["paths"])
        if arm == "guide-coached":
            assert run.store.records("outcome")[-1][2]["verification_complete"] is True
            assert any("tryItGuide" in row["requirements"] and row["exitCode"] == 0
                       and row["command"][-1] == "docs/try-it/replay.py"
                       for row in map(run.store.read, cursor.finalObservations))
        if arm in ("lh01", "lh02", "lr01", "bound-note", "proof-owner"):
            assert not run.store.records("lead_failure")
        if arm == "bound-note":
            (original, _, _), (_, _, revised) = run.store.records("milestone_plan")
            assert revised["parent"] == original
            assert len(reviewed) == 1
        if arm in ("weak-check", "guide-replay", "bound-note"):
            assert not run.store.records("lead_failure")
            assert len([q for _, _, q in run.store.records("question") if q["kind"] == "RECEIPT"]) == (
                arm == "guide-replay")
            if arm != "guide-replay":
                assert run.store.records("outcome")[-1][2]["verification_complete"] is False
        if arm == "missing-check":
            assert not run.store.records("lead_failure")
            assert not [q for _, _, q in run.store.records("question") if q["kind"] == "RECEIPT"]
            assert run.store.records("outcome")[-1][2]["verification_complete"] is True
        assert displayed == [*(["A"] * (2 if micro else 1)), "B", *(["B"] if arm == "plan-owner" else []),
                             *(["BUILD"] if arm == "builder-stalled" else []),
                             *(["CHECK-IN"] * milestone_count), "C"]
        assert run.store.read(cursor.kickoff)["mode"] == "lead"
        assignments = run.store.records("lead_assignment")
        assert len(assignments) == milestone_count + (arm == "plan-owner")
        assert all(int(ref[1:]) > int(row["B"][1:]) for ref, _, row in assignments)
        assert assignments[-1][2]["B"] == cursor.B
        if arm == "plan-owner":
            assert assignments[0][2]["B"] != cursor.B
        proofs = [row for _, _, row in run.store.records("milestone_proof")]
        if arm == "guide-replay":
            (receipt,) = [row for _, _, row in run.store.records("answer") if row["kind"] == "RECEIPT"]
            assert receipt["binding"] == proofs[0]["commit"]
        kept = [row for row in questions.observed(run.store, cursor.finalObservations)
                if "scopeIn:hello" in row["requirements"]]
        if arm in ("extreme-advisory", "undeclared-credit", "unrelated-witness"):
            assert [row["kind"] for _, _, row in run.store.records("question")].count("JUDGE") == 1
            assert not run.store.records("lead_failure")
            (decision,) = [row for _, _, row in run.store.records("decision_request") if row["gate"] == "C"]
            subject = decision["subject"]
            if arm == "extreme-advisory":
                note, = subject["guard"]["findings"]
                assert (note["kind"], note["reason"]) == ("note", "No limit on name length. Consider 200 characters.")
                assert not subject["unverified"]
            elif arm == "undeclared-credit":
                note, = subject["guard"]["findings"]
                assert note["kind"] == "note" and "Dropped undeclared credit for quality:exact" in note["reason"]
                assert set(subject["unverified"]) == {"guard:quality:exact"}
        if milestone_count == 1 and not guide_case and arm not in ("missing-check", "guard-repair", "guard-once"):
            green = [run.store.read(ref) for ref in cursor.finalObservations]
            usage = [r for r in green if "quality:exact" in r["requirements"]]
            assert len(usage) == 1 and "tryItGuide" in usage[0]["requirements"]
        assert len(kept) == 1 and kept[0]["commit"] == cursor.candidate
        assert kept[0]["stdout"].strip() == "Hello, Ada!"
        assert gitwork.git(project, "rev-list", "--count", "HEAD") == str(1 + milestone_count)
        sessions = [row["session"] for _, _, row in run.store.records("call_session")]
        if arm == "hello-reopen":
            assert len(sessions) == 2 and sessions[-1] == "guard-session"
            (_, _, failure), = run.store.records("provider_failure")
            assert failure["session"] == "guard-session" and failure["cleanupVerified"]
            receipt = json.loads(Path(failure["receipt"]).read_text("utf-8"))
            assert receipt["timedOut"] and receipt["executionStarted"]
            guard_attempts = [(ref, row) for ref, _, row in run.store.records("attempt")
                              if row["operation"] == "JUDGE"]
            assert len(guard_attempts) == 2 and guard_attempts[1][1]["retryOf"] == guard_attempts[0][0]
            (folder,) = [Path(row["folder"]) for _, _, row in run.store.records("call_started")
                         if row["attempt"] == guard_attempts[1][0]]
            assert (folder / "stdout.bin").stat().st_size > 4 * 1024**2
            stderr = (folder / "stderr.bin").read_bytes()
            assert len(stderr) > 4 * 1024**2 and stderr.rstrip().endswith(b"guard stderr end")
            assert run.store.records("outcome")[-1][2]["verification_complete"] is True
        elif arm in ("keep-building", "builder-stalled"):
            assert sessions == ["milestone-builder"] * 3
            assert run.store.records("outcome")[-1][2]["verification_complete"] is True
            if arm == "builder-stalled":
                assert build_options == ["Use the existing Python", "Provide the deployment runtime", "Other"]
        elif arm in ("guard-repair", "incomplete-witness", "owner-interruption", "guard-once", "failed-witness"):
            assert len(sessions) == 2 and sessions[0] == sessions[1]
            assert run.store.records("outcome")[-1][2]["verification_complete"] is True
            if arm == "guard-once":
                verdicts = run.store.records("lead_verdict")
                assert len(verdicts) == 2 and len(verdicts[0][2]["fixes"]) == 2 and not verdicts[1][2]["fixes"]
                assert [q["kind"] for _, _, q in run.store.records("question")
                        if q["kind"] in ("WORK", "JUDGE")] == ["WORK", "JUDGE", "WORK", "JUDGE"]
                assert cursor.repairs == 1
            if arm == "incomplete-witness":
                (_, _, failure), = run.store.records("lead_failure")
                assert failure["owner"] == "Builder" and failure["assignment"] == assignments[0][0]
                assert failure["candidate"] != cursor.candidate
                verdict = run.store.read(failure["evidence"][0])
                finding, = verdict["findings"]
                assert finding["quote"] == witness_result
                ref = finding["evidenceLink"]["observation"]
                observed = run.store.read(ref)
                assert ref in failure["evidence"] and observed["commit"] == failure["candidate"]
                assert observed["exitCode"] == 0 and observed["operationLifecycle"][0]["step"] == "Rejection summary"
        elif carry_case:
            assert len(sessions) == 1 and not run.store.records("lead_failure")
            assert run.store.records("outcome")[-1][2]["verification_complete"] is (arm == "carry-witness")
        elif arm == "unrelated-witness":
            assert len(sessions) == 1 and not run.store.records("lead_failure")
            assert run.store.records("outcome")[-1][2]["verification_complete"] is False
        elif arm == "environment-limit":
            assert len(sessions) == 2 and sessions[0] != sessions[1]
        elif milestone_count == 1:
            assert len(sessions) == (2 if arm == "plan-owner" else 1)
        else:
            assert len(sessions) == 3 and sessions[0] != sessions[1] == sessions[2]
            assert proofs[0]["commit"] != proofs[1]["commit"]
            _, _, failure = run.store.records("lead_failure")[0]
            assert failure["assignment"] == assignments[-1][0]
            assert "Observed: Broken, Ada!" in failure["failure"]
    finally:
        run.close()


def unit_ref(run, number):
    scope = f"unit-{number:03}/**"
    return next(r for r, u in plan.select(run.store.plan(run.store.cursor().revision), "unit").items()
                if scope in u["scope"])


def prepare(slot, mode="normal", count=1, capacity=6):
    slot.mkdir(parents=True)
    (slot / "mode").write_text(mode)
    (slot / "native.py").write_text(NATIVE, encoding="utf-8")
    if mode == "schema-fix":
        import shutil

        shutil.copyfile(Path(__file__).with_name("recorded_work.json"), slot / "recorded-work.json")
    provider = Scripted(slot, mode, count)
    return Run(
        slot / "product",
        slot / "workers",
        "Read named values",
        provider.setup,
        provider,
        approve,
        capacity=capacity,
    ), provider


def finish(run):
    while run.store.cursor().state not in ("SCRIPTED_C", "ACCEPTED", "STOPPED"):
        run.step()
    cursor = run.store.cursor()
    assert cursor.state == "SCRIPTED_C"
    assert all(u.state in ("DONE", "DROPPED") for u in run.store.units().values())
    document = plan.execution(run.store)
    obs = [run.store.read(r) for r in cursor.finalObservations]
    assert all(o["commit"] == cursor.candidate and o["exitCode"] == 0 for o in obs)
    requirements = set(plan.select(document, "requirement"))
    assert requirements == {run.store.read(r)["subject"] for r in cursor.requirementVerdicts}
    gitwork.unchanged(run.project, cursor.candidate)
    assert not run.store.records("outcome")[-1][2]["accepted"]
    return cursor, run.store.records(), run.store.units()


@pytest.mark.fast
@pytest.mark.parametrize("arm", ["planner", "off", "retained", "continuation"])
def test_planner_brief_golden(tmp_path, monkeypatch, arm):
    """Catch lost or rewritten DETAIL prose at the native worker boundary.

    The existing prompt journeys cannot detect a planner answer dropped during compilation,
    a separate guide handed a product brief, or an old plan refused on reopen.
    """
    from seldon import features, providers

    active = arm in ("planner", "continuation")
    enabled = features.enabled
    monkeypatch.setattr(features, "enabled", lambda name: active if name == "plannerBrief"
                        else False if name in ("prompts2", "growAlign") else enabled(name))
    monkeypatch.setattr(features, "planning_setup", lambda: {})
    briefs = {f"unit-{i:03}/**": (
        f"  For the reader of value {i}, \"Read named values\" means seeing the saved result.\n\n"
        "Keep one readable path from the reader to the persisted value.  ") for i in (1, 2)}
    guide = ("Help a new reader try the saved values from a clean checkout.\n"
             "Run the documented steps and verify the value they see.")
    scripted_answer = Scripted.__call__

    def answer(self, q):
        result = scripted_answer(self, q)
        if q["kind"] == "DETAIL" and active:
            response = json.loads(result)
            for unit in response["answer"]["units"]:
                unit["brief"] = briefs[unit["scope"][0]]
            if "Separate walkthrough" in q["facts"]:
                response["answer"]["guideBrief"] = guide
            return json.dumps(response)
        return result

    monkeypatch.setattr(Scripted, "__call__", answer)
    slot = tmp_path / arm
    mode = "wave-fault" if arm == "continuation" else "parallel-contract"
    run, scripted = prepare(slot, mode, 2)
    try:
        if arm == "retained":
            while run.store.cursor().state != "EXECUTE":
                run.step()
            run.close()
            active = True
            run = Run.reopen(slot / "product", scripted, approve)
        finish(run)
    finally:
        run.close()

    assigned = set()
    continued = False
    for folder in (slot / "calls").glob("call-*"):
        q = json.loads((folder / "question.json").read_bytes())
        if q["kind"] != "WORK":
            continue
        prompt = (folder / "prompt.txt").read_text("utf-8")
        if q["execution"]["session"]:
            assert "Exact failure" in q["facts"]
            assert prompt == providers.render_question(q, planner_brief=False)[0]
            continued = True
        elif arm in ("planner", "continuation"):
            scope = q["facts"]["Write scope"][0]
            brief = guide if scope == questions.GUIDE_PATH else briefs[scope]
            assert prompt.startswith(brief + "\n\nWrite scope\n")
            assert prompt.index("Person's exact words\nRead named values\n") < prompt.index("Answer schema\n")
            assert "Write code a senior engineer" not in prompt
            assert "Deliver your part through its real entry point" not in prompt
            if mode == "parallel-contract":
                side = "Contracts provided" if scope == "unit-001/**" else "Contracts consumed"
                assert prompt.index(side) < prompt.index("Person's exact words")
            assigned.add(scope)
        else:
            assert prompt == providers.render_question(q, planner_brief=False)[0]
    if arm in ("planner", "continuation"):
        assert assigned == {*briefs, questions.GUIDE_PATH}
    assert continued == (arm == "continuation")


def journey(slot, mode="normal", count=1):
    run, provider = prepare(slot, mode, count)
    try:
        if count == 50:
            while run.store.cursor().state != "WAVE-VERIFY":
                run.step()
            candidate = run.store.cursor().candidate
            run.close()
            run = Run.reopen(slot / "product", provider, approve)
        if mode == "see-proof-gap":
            from seldon import fixups

            while run.store.cursor().state != "WAVE-VERIFY":
                run.step()
            unaffected = run.store.unit(unit_ref(run, 1))

            class Interrupted(BaseException):
                pass

            def interrupt(store, gaps):
                assert "saved value label" in str(gaps)
                raise Interrupted()
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(fixups, "apply", interrupt)
                with pytest.raises(Interrupted):
                    run.step()
            run.close()
            run = Run.reopen(slot / "product", provider, approve)
        if mode == "wave-gap":
            while run.store.cursor().state != "WAVE-VERIFY":
                run.step()
            assert all(u.state in ("DONE", "DROPPED") for u in run.store.units().values())
            assert run.store.unit(unit_ref(run, 2)).judgment is None
            assert not any(b.get("unit") == unit_ref(run, 2) and b["next"] == "REWORK"
                           for _, _, b in run.store.records("transition"))
            run.step()
            assert run.store.records("unit_fixup")
        finish(run)
        if mode == "normal":
            assert any(b["discoveries"] for _, _, b in run.store.records("worker_checks"))
            from run_report import report
            output, row = report(slot)
            assert row[3] == "SCRIPTED_C" and "accepted=False" in output
            shown = json.loads(next(line.split(": ",1)[1] for line in output.splitlines()
                                    if line.startswith("User questions:")))
            assert all(q["answered"] for q in shown)
            assert any(q["gate"] == "A" and q["subject"]["vision"]["understanding"] == VISION["understanding"]
                       for q in shown)
            usage = json.loads(next(line.split(": ",1)[1] for line in output.splitlines()
                                    if line.startswith("Calls and fresh tokens per wave:")))
            assert usage["1"]["freshTokens"] is None, "missing native usage became zero tokens"

        if mode == "parallel-contract":
            assert provider.calls.count("ARCHITECT") == 1
            assert not run.store.records("detail_correction")
            assert not run.store.records("architecture_gap")
            assert not any("Dependencies" in b["reason"] for _, _, b in run.store.records("unit_wait"))
            transitions = [b for _, _, b in run.store.records("transition")
                           if b.get("unit") in (unit_ref(run, 1), unit_ref(run, 2), "guide")]
            working = {b["unit"]: i for i, b in enumerate(transitions) if b["next"] == "WORKING"}
            assert set(working) == {unit_ref(run, 1), unit_ref(run, 2), "guide"}
            assert all(b["next"] == "WORKING" for b in transitions[min(working.values()):max(working.values())+1])
            walkthrough = [b for _, _, b in run.store.records("observation") if b["unit"] == "guide"]
            assert any(b["purpose"] == "T" and b["exitCode"] != 0
                       and "unit-001/value.txt" in b["stderr"] for b in walkthrough)
            assert any(b["purpose"] == "final" and b["exitCode"] == 0
                       and b["commit"] == run.store.cursor().candidate for b in walkthrough)
            assert not any(b["purpose"] == "P" for b in walkthrough)
            agreement, = plan.select(plan.execution(run.store), "contract").values()
            assert len(agreement["text"]) == 3000 and agreement["kind"] == "data rule"
            assert agreement["consumers"] == [unit_ref(run, 2), "guide"]
        if count == 50:
            assert run.store.cursor().candidate == candidate
            assert len(run.store.units()) == 51
            assert all((slot / f"turn-unit-{i:03}").read_text() == "1" for i in range(1, 51))
        assert (run.project / "unit-001/value.txt").read_text() == "value-001"
        records = run.store.records()
        gates = [b["gate"] for _, _, b in run.store.records("decision_request")]
        assert set(gates) == {"A", "B", "CHECK-IN", "C"}
        if mode == "question":
            from run_report import report
            output, _ = report(slot)
            coached = json.loads(next(line.split(": ", 1)[1] for line in output.splitlines()
                                      if line.startswith("Planner-answered questions:")))
            assert any(c["question"]["Unresolved work"]["failure"] == "How can I check persistence in this scope?"
                       and "scoped Python check" in c["answer"]["guidance"] for c in coached)
            assert provider.calls.count("COACH") == 1
            assert (slot / "turn-unit-001").read_text() == "2"
            assert not run.store.records("unit_failure")
        if mode == "coach-facts":
            assert any(b["unit"] == unit_ref(run, 3) and b["next"] == "PROVING"
                       for _, _, b in run.store.records("transition"))
            observations = [b for _, _, b in run.store.records("observation") if b["unit"] == unit_ref(run, 3)]
            assert {b["purpose"] for b in observations} == {"T", "final"}
            assert any(b["purpose"] == "final" and b["exitCode"] == 0 for b in observations)
        if mode in ("unit-gap", "unit-rework"):
            assert (slot / "turn-unit-001").read_text() == "2"
            assert len(run.store.records("unit_gap")) == (2 if mode == "unit-gap" else 1)
            assert not any(b["errors"] for _, _, b in run.store.records("provider_reply"))
            checkin = next(b for _, _, b in run.store.records("decision_request") if b["gate"] == "CHECK-IN")
            assert bool(checkin["subject"]["open gaps"]) == (mode == "unit-gap")
        if mode == "see-proof-gap":
            assignment = next(b for _, _, b in run.store.records("unit_fixup") if b["owner"] == unit_ref(run, 2))
            assert "saved value label" in str(assignment["gaps"])
            assert run.store.unit(unit_ref(run, 1)) == unaffected
            assert any("observed value-002" in run.store.read(r)["stdout"]
                       for r in run.store.cursor().finalObservations)
        if mode in ("wave-fault", "flow-fault", "product-fault"):
            assert run.store.records("unit_fixup")
            assert (slot / "turn-unit-002").read_text() == ("2" if mode == "product-fault" else "1")
        if mode == "schema-fix":
            assert (slot / "turn-unit-001").read_text() == "2"
            assert any(b["errors"] for _, _, b in run.store.records("provider_reply"))
        if mode == "proof-failure":
            assert (slot / "turn-unit-001").read_text() == "2"
            assert any("actual failed product assertion" in b["stderr"]
                       for _, _, b in run.store.records("observation") if b["purpose"] == "P")
        if mode == "contract-fanout":
            document = plan.execution(run.store)
            agreement, = plan.select(document, "contract").values()
            assert agreement["provider"] == unit_ref(run, 1)
            assert agreement["consumers"] == [unit_ref(run, 3), unit_ref(run, 4), "guide"]
            assert all(run.store.unit(ref).recoveries == 0 for ref in (unit_ref(run, 3), unit_ref(run, 4)))
        if mode == "contracts":
            assert not run.store.records("unit_fixup")
            assert not any(b.get("next") == "REWORK" for _, _, b in run.store.records("transition"))
            import re
            from seldon import providers
            from seldon.store import encode

            for _, _, question in run.store.records("question"):
                folder, _ = provider.native.prepare(question)
                prompt = (folder / "prompt.txt").read_text("utf-8")
                instructions = (folder / "instructions.txt").read_text("utf-8")
                facts = question["facts"]
                if question["kind"] == "WORK":
                    from seldon.desktop import WRITE_RULE
                    prompt = prompt.removesuffix("\n\n" + WRITE_RULE)
                headings = ["Answer schema", "Example", *facts]
                sections = re.split(r"\n\n(?=(?:" + "|".join(map(re.escape, headings)) + r")\n)", prompt)
                stable = [instructions + "\n" + question["prompt"],
                          "Answer schema\n" + encode(question["schema"]),
                          "Example\n" + encode(question["examples"][0])]
                assert sections[:3] == stable
                assert set(sections) == {*stable, *(questions.labelled({k: v}) for k, v in facts.items())}
                if question["kind"] == "JUDGE" and "Contracts consumed" in facts:
                    assert prompt.index("\n\nContracts consumed\n") < prompt.index("\n\nObservations\n")
                if question["kind"] == "FLOW":
                    assert prompt.index("\n\nCrossed contracts\n") < prompt.index("\n\nFinal observations\n")
                assert json.loads((folder / "question.json").read_text("utf-8")) == question
                context = question.get("renderContext", {})
                if (question["kind"] == "WORK" and context.get("soleOwner") is False
                        and context.get("guideRole") != "separate"):
                    # Split builders need the craft bar even though someone else owns integration.
                    trial, _ = providers.render_question(question, prompts2=True)
                    assert "Prove the failure paths and edge cases the requirements imply" in trial
        if mode == "review-gap":
            assert provider.calls.count("REVIEW") == 2
            b = next(b for _, _, b in run.store.records("decision_request") if b["gate"] == "B")
            assert b["subject"]["review"]["answer"]["gaps"]
        return records
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize(
    "mode,count",
    [
        ("normal", 1),
        ("question", 1),
        ("coach-facts", 3),
        ("unit-gap", 1),
        ("unit-rework", 1),
        ("review-gap", 1),
        ("schema-fix", 1),
        ("contracts", 3),
        ("contract-fanout", 4),
        ("parallel-contract", 2),
        ("wave-fault", 2),
        ("wave-gap", 2),
        ("see-proof", 2),
        ("see-proof-gap", 2),
        ("flow-fault", 2),
        ("product-fault", 2),
        ("proof-failure", 2),
        ("scope", 2),
        ("scale50", 50),
    ],
)
def test_thin_plan_golden(tmp_path, scalar_align, mode, count):
    """Real worker checks reach C through thin intent, local recovery and strict integrated reviews.

    The parallel-contract twin catches a guide waiting on product work or proving its
    walkthrough before integration; value-only sequential journeys miss that barrier.

    Success alone misses worker questions, persistent unit/plan gaps and final-only failures.
    The two-area contract journey catches serialized contract peers and rejected long agreements;
    ordinary journeys do not exercise provider-in-consumers normalization or simultaneous WORKING.
    Multi-unit areas catch consumer briefs lost when DETAIL omits redundant endpoints,
    and preserve one explicit provider when its area has multiple units. Single-unit cases miss this.
    Recorded contract questions protect stable-prefix ordering and lossless section rendering;
    scripted answers alone never inspect the prompt supplied to the native provider.
    A consumer's real JSON check needs its provider's sibling API agreement to reach C without
    repair; one-contract journeys miss that blind spot and unrelated units must not receive it.
    Backend/page briefs catch craft guidance leaking to peers without the visual commitment.
    Factual questions require COACH with execution facts; wave-owned gaps must integrate before repair.
    A real sibling read from an early unit catches running integrated acceptance before integration.
    Integrated JUDGE evidence and its verdict reaching FLOW/PRODUCT catch blind source judgment;
    a semantic gap on GREEN proof must rework its owner, even after interruption before fixup.
    Check-failure twins miss semantic gaps and replay of an already-recorded semantic reply.
    The persistent local-gap twin also requires final judges to see the actual negative finding.
    The report follows canonical requests and COACH guidance; provider-only assertions miss
    an unanswered question being reported as planner-answered or unknown usage becoming zero.
    Fifty units and reopen catch a hidden size ceiling or replay of already integrated work.
    """
    began = time.monotonic()
    journey(tmp_path / mode, mode, count)
    print(f"{mode}: {time.monotonic() - began:.2f}s")


@pytest.mark.fast
@pytest.mark.parametrize("route", ["brief", "coach", "gap"])
def test_quietbench_peer_agreements_journey(tmp_path, route, monkeypatch):
    """Replay Quiet Bench through B and its first backend WORK and COACH.

    The approved outline had the agreements but DETAIL had no peer units yet. Existing
    contract journeys already bind their providers, so they miss an outline-only agreement.
    """
    from seldon import decisions

    monkeypatch.delenv("SELDON_OFF", raising=False)
    fixture = json.loads((Path(__file__).parent / "fixtures/quietbench-c01-agreements.json").read_text(
        encoding="utf-8"))
    slot = tmp_path / "quietbench-agreements"
    seen, user_questions = [], []
    expected = {c["handle"]: c["text"] for c in fixture["r23"]["contracts"]
                if c["handle"] in ("simulation-run", "page-assets")}

    class ReceivedAgreement(BaseException):
        pass

    def agreements(facts):
        return {ref: contract["text"] for name, contracts in facts.items()
                if name in ("Contracts consumed", "Planned contracts")
                for ref, contract in contracts.items() if ref in expected}

    class QuietBench(Scripted):
        def invoke(self, q, started):
            strict_schema(q["schema"])
            kind, facts = q["kind"], q["facts"]
            if kind == "ARCHITECT" and "Architecture correction" in facts:
                message = facts["Architecture correction"]
                assert all(part in message for part in ("Expected", "got", "simulation-run",
                                                        "Define the fixed-filter settings", "Wave 1"))
                seen.append("architecture")
            if kind in ("ALIGN", "ARCHITECT", "DETAIL"):
                answer = fixture[{"ALIGN": "r12", "ARCHITECT": "r23", "DETAIL": "r60"}[kind]]
            elif kind == "WORK":
                if questions.GUIDE_PATH in facts["Write scope"]:
                    return super().invoke(q, started)
                seen.append("work")
                if route == "brief" and agreements(facts) == expected:
                    raise ReceivedAgreement
                if route == "coach" and "Direction: " + guidance in facts.get("Exact failure", ""):
                    raise ReceivedAgreement
                answer = {**fixture["r103"],
                          "question": "What are the agreed simulation-run interface and page-assets paths?"}
            elif kind == "COACH":
                seen.append("coach")
                answer = fixture["r111"]
                if agreements(facts) == expected:
                    answer = {"contract": {"resolution": "gap" if route == "gap" else "answer",
                              "text": "Define the fixed-filter settings in simulation-run." if route == "gap"
                                      else guidance}, "why": "Resolve the peer agreement in the plan."}
            else:
                return super().invoke(q, started)
            return json.dumps({"answer": answer}), None

    guidance = "Use " + expected["simulation-run"] + " " + expected["page-assets"]

    def user(item):
        if item["gate"] == "A":
            return {"request": item["request"], "replyId": "approved-skeleton", "selections": [
                {"groupId": "size", "optionId": "large"}, {"groupId": "round", "optionId": "done"}]}
        if item["gate"] == "UNIT":
            user_questions.append(item["ask"]["question"])
            raise decisions.Waiting()
        return approve(item)

    slot.mkdir()
    (slot / "mode").write_text("normal")
    (slot / "native.py").write_text(NATIVE, encoding="utf-8")
    provider = QuietBench(slot)
    run = Run(slot / "product", slot / "workers", fixture["dream"],
              provider.setup, provider, user, capacity=1)
    try:
        try:
            run.run()
        except ReceivedAgreement:
            pass
        assert not user_questions, user_questions
        assert not [b for _, _, b in run.store.records("decision_request") if b["gate"] == "UNIT"]
        assert seen == {"brief": ["work"], "coach": ["work", "coach", "work"],
                        "gap": ["work", "coach", "architecture", "work", "coach"]}[route]
        if route == "gap":
            assert run.store.cursor().state == "ARCHITECT"
            assert "The planner must settle it" in run.store.cursor().failure
    finally:
        run.close()


@pytest.mark.fast
def test_repeating_worker_question_journey(tmp_path, scalar_align, monkeypatch):
    """A repeat with changed progress stays parked until the owner supplies changed input.

    The existing question golden answers once without a restart and cannot catch a lost
    receipt or a second WORK dispatch on an acknowledgement after the same question returns.
    """
    from seldon import units
    from seldon.decisions import Waiting

    slot = tmp_path / "repeat-question"
    run, provider = prepare(slot, "repeat-question", 3)
    original = provider.invoke
    first_answer = []
    fresh_question = "Which saved label should the reader preserve?"

    def invoke(q, started):
        facts = q["facts"]
        if q["kind"] == "COACH":
            work = facts["Unresolved work"]
            assert work["workerQuestion"] == work["receipt"]["question"]
            assert work["receipt"]["why"] == "Ready for independent observation"
            if work["workerQuestion"] == fresh_question:
                (slot / "mode").write_text("normal")
                return json.dumps({"answer": {"guidance": "Preserve the exact saved label.",
                                              "why": "The accepted settings already name it."}}), None
        if q["kind"] == "DECIDE" and "Required input" in facts:
            # The raw replay guard owns an exact repeated answer, even if DECIDE calls it Other.
            choice = "Unclear" if facts["User's words"] == "Understood, continue" else "Other"
            return json.dumps({"answer": {"choice": choice, "why": "Interpret the owner's reply."}}), None
        return original(q, started)

    def answer_once(item):
        if item["gate"] != "UNIT":
            return approve(item)
        if item["subject"].get("inputOwner"):
            raise Waiting()
        first_answer.append(item["ask"]["options"][0]["label"])
        return first_answer[-1]

    provider.invoke, run.user = invoke, answer_once
    try:
        class ControllerInterrupted(BaseException):
            pass

        def interrupt(*_):
            raise ControllerInterrupted()

        with monkeypatch.context() as interrupted:
            interrupted.setattr(units, "ask_unit", interrupt)
            with pytest.raises(ControllerInterrupted):
                while True:
                    run.step()
        assert run.store.unit(unit_ref(run, 1)).state == "ASKED"
        assert not [b for _, _, b in run.store.records("answer") if b["kind"] == "COACH"]
        run.close()
        run = Run.reopen(slot / "product", provider, answer_once)
        with pytest.raises(Waiting):
            while True:
                run.step()
        previous, request = [b for _, _, b in run.store.records("decision_request") if b["gate"] == "UNIT"]
        assert previous["subject"]["receipt"]["summary"] != request["subject"]["receipt"]["summary"]
        assert "still needs an answer" in request["ask"]["question"]
        assert run.store.unit(unit_ref(run, 1)).state == "ASKED"
        assert all(run.store.unit(unit_ref(run, peer)).state == "DONE" for peer in (2, 3))
        decision = run.store.cursor().decision
        candidate = run.store.cursor().candidate
        attempts = (slot / "turn-unit-001").read_text()
        coached = len([b for _, _, b in run.store.records("answer") if b["kind"] == "COACH"])
        run.close()
        run = Run.reopen(slot / "product", provider, approve)
        with pytest.raises(Waiting):
            run.step()
        for words in ("Understood, continue", first_answer[0], "Provide a fix"):
            run.user = lambda _, words=words: words
            with pytest.raises(Waiting):
                run.step()
            assert run.store.cursor().decision == decision
        assert run.store.cursor().candidate == candidate
        assert (slot / "turn-unit-001").read_text() == attempts
        assert len([b for _, _, b in run.store.records("answer") if b["kind"] == "COACH"]) == coached
        (slot / "mode").write_text("followup-question")
        run.user = lambda item: ("Revise the fixture to use the saved-value check"
                                if item["gate"] == "UNIT" else approve(item))
        finish(run)
        assert all((slot / f"turn-unit-{n:03}").read_text() == "1" for n in (2, 3))
        assert any(b["subject"].get("workerQuestion") == fresh_question
                   for _, _, b in run.store.records("planner_answer"))
    finally:
        run.close()


@pytest.mark.fast
def test_hermetic_golden(tmp_path, scalar_align):
    """Runtime writes cannot corrupt final source; read-only checks never exercise this isolation."""
    from test_proof import hermetic_journey

    hermetic_journey(tmp_path / "hermetic")


@pytest.mark.fast
def test_guide_walkthrough_journey(tmp_path, scalar_align):
    """A reader's never-exiting server fence cannot block C; value-only guides miss prose execution.

    The real kit walk also checks ordering, delivered sources and observations in PRODUCT.
    """
    from kit_journey import kit_journey

    kit_journey(tmp_path / "walkthrough", "server")


@pytest.mark.fast
@pytest.mark.parametrize("unprojectable", [False, True])
def test_product_judge_converges_after_guide_repair(tmp_path, monkeypatch, unprojectable):
    """Quiet Bench r497/r535: green walkthroughs can still overload final judgment.

    Small guide journeys miss a repair adding full simulation responses to the observation.
    A rejection must keep its cause across new native threads and reach a resumable question.
    """
    from collections import Counter
    from seldon import features
    from seldon.providers import render_question

    enabled = features.enabled
    arms = {"prompts2": True, "judgeEvidence": True, "growAlign": False, "sizeFirst": False}
    monkeypatch.setattr(features, "enabled", lambda name: arms[name] if name in arms else enabled(name))
    slot = tmp_path / "product-loop"
    run, provider = prepare(slot, "contracts", 2)
    native = slot / "native.py"
    code = native.read_text(encoding="utf-8")
    rejection = """
if q['kind'] == 'PRODUCT':
    print(json.dumps({'type': 'thread.started', 'thread_id': str(uuid.uuid4())}), flush=True)
    print('Error: turn/start: turn/start failed: Input exceeds the maximum length of 1048576 characters.'
          ' (code -32602), data: {"input_error_code":"input_too_large","max_chars":1048576}',
          file=sys.stderr)
    sys.exit(1)
"""
    code = code.replace("facts = q['facts']", rejection + "\nfacts = q['facts']", 1)
    start = code.index("    Path('docs/try-it/check.py').write_text")
    end = code.index("    answer['checkPaths']", start)
    native.write_text(code[:start] + r"""
    output = (
        "import json, math, os\n"
        "from pathlib import Path\n"
        "assert Path('unit-001/value.txt').read_text() == 'value-001'\n"
        "print('observed value-001')\n"
    )
    if turn > 1:
        output += (
            "series = [math.sin(i) for i in range(16000)]\n"
            "rows = [{'operation': name, 'observed': True, 'documentation': 'docs/try-it.md#page',"
            " 'after': {'status': 'completed', 'processed_samples': len(series),"
            " 'desired': series, 'residual': series, 'output': series}}"
            " for name in ('band-noise simulation', 'wav simulation')]\n"
            "Path(os.environ['SELDON_EVIDENCE_DIR'], 'operation-lifecycle.json')"
            ".write_text(json.dumps(rows))\n"
        )
    Path('docs/try-it/check.py').write_text(output)
""" + code[end:], encoding="utf-8")
    calls = Counter()
    largest = []
    seen_operations = set()
    original = provider.invoke

    def invoke(question, started):
        if question["kind"] != "PRODUCT":
            return original(question, started)
        facts = question["facts"]
        calls.update((facts["Final observations"][0]["commit"], target) for target in question["targets"])
        lifecycle = [row for obs in facts["Final observations"]
                     for row in obs.get("operationLifecycle", ()) if row.get("observed")]
        if lifecycle:
            prompt, _ = render_question(question)
            largest.append(len(prompt))
            if unprojectable or len(prompt) > 1_048_576:
                return provider.native.invoke(question, started)
            seen_operations.update(
                row["operation"] for row in lifecycle
                if row["after"]["status"] == "completed"
                and row["after"]["processed_samples"] == row["after"]["output"]["count"] == 16000
                and -1 <= row["after"]["output"]["min"] < row["after"]["output"]["max"] <= 1
            )
        if not lifecycle and question["targets"] == ["tryItGuide"]:
            return json.dumps({"answer": {"verdict": "gap", "why": "Examples were not executed",
                "gaps": [{"target": "tryItGuide", "owners": ["guide"], "severity": "must-fix",
                          "correction": "Run the band-noise and eligible WAV examples."}]}}), None
        return original(question, started)

    provider.invoke = invoke
    try:
        cursor = run.run()
        assert largest
        repeats = {subject: n for subject, n in calls.items() if n > 1}
        if unprojectable:
            request = run.store.read(cursor.decision)
            assert request["gate"] == "PRODUCT-JUDGE"
            assert "Seldon" in request["ask"]["question"]
            assert sum("COACH" == call for call in provider.calls) == 1
            assert repeats and max(repeats.values()) == 2
        else:
            assert not repeats, repeats
            assert cursor.state == "SCRIPTED_C"
            assert seen_operations == {"band-noise simulation", "wav simulation"}
            assert [b["owner"] for _, _, b in run.store.records("unit_fixup")] == ["guide"]
            assert sum(
                target.startswith("quality:") and target.endswith(":area-2") for _, target in calls
            ) == 1
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.skipif(sys.platform != "win32", reason="Recorded product faults use Windows file sharing")
@pytest.mark.parametrize("product", ["quiet", "shelf"])
def test_recorded_runtime_evidence_reaches_integrated_judge(tmp_path, monkeypatch, product):
    """Passing checks can hide a stalled Stop, browser errors and cleanup locks.

    Screenshots and exit checks miss these defects. The two recorded final packets also catch lost
    source-quality coaching, a must-fix that never reaches its owner, or a style nit that blocks C.
    """
    from evidence_journey import evidence_journey

    evidence_journey(tmp_path, monkeypatch, product)


@pytest.mark.fast
@pytest.mark.parametrize("packed", [True, False], ids=["packed", "readable"])
def test_recorded_code_shape_reaches_integrated_judge(tmp_path, monkeypatch, packed):
    """Passing pantry checks leave maintainability to the judge using measured evidence.

    Prior quality journeys supply source and rubric alone, so they miss missing measurements,
    check source leaking into those facts, or a cited shape finding losing its owner.
    """
    from evidence_journey import code_shape_journey

    code_shape_journey(tmp_path, monkeypatch, packed)


@pytest.mark.fast
@pytest.mark.parametrize("surface", ["browser", "broken-guide"])
def test_check_kit_golden(tmp_path, scalar_align, surface, monkeypatch):
    """Worker-authored real browser/server checks retain baseline helpers and clean descendants.

    Value-file journeys cannot expose absent browser skills, screenshots or server leaks.
    Unlinked checks and module commands catch declaration refusals hidden by explicit file commands.
    Guide observations catch local proof of a walkthrough that belongs to the integrated product.
    """
    from kit_journey import kit_journey

    if surface == "broken-guide":
        monkeypatch.setenv("SELDON_TEST_GUIDE_FAULT", str(tmp_path / "guide-fault"))
    kit_journey(tmp_path / "kit", surface)


@pytest.mark.fast
@pytest.mark.parametrize("outcome", ["done", "question", "fails"])
def test_defect_reaches_owner_journey(tmp_path, scalar_align, outcome):
    """A real failing peer check delivers its writer's correction to the named file owner.

    Existing wave faults repair their writer and miss lost discoveries, premature local
    proof and forced writer routing. The failed-fix twin protects the owner's recovery
    and durable user wait: an ineffective fix must never publish or loop forever.
    """
    from seldon.decisions import Waiting
    from seldon.frontdoor import state

    slot = tmp_path / "defect"
    run, provider = prepare(slot, "defect-" + outcome, 2)

    class ControllerInterrupted(BaseException):
        pass

    original_invoke = provider.invoke

    def old_provider(q, started):
        if q["kind"] != "WORK" or q["facts"]["Write scope"] != ["unit-001/**"]:
            return original_invoke(q, started)
        old = deepcopy(q)
        variants = old["schema"]["properties"]["answer"]["anyOf"]
        variants[0]["properties"]["outcome"]["enum"].append("question")
        variants[:] = variants[:1]
        original_invoke(old, started)
        raise ControllerInterrupted()

    try:
        if outcome == "question":
            provider.invoke = old_provider
            with pytest.raises(ControllerInterrupted):
                while True:
                    run.step()
            run.close()
            provider.invoke = original_invoke
            run = Run.reopen(slot / "product", provider, approve)
        while run.store.cursor().state != "WAVE-VERIFY":
            run.step()
        assert run.store.unit(unit_ref(run, 1)).state == "DONE"
        if outcome == "question":
            assert (slot / "turn-unit-001").read_text() == "1"
        original = gitwork.blobs(run.project, run.store.cursor().candidate, ["unit-001/**"])
        assert (run.project / "unit-002/peer.txt").read_text() == "broken"
        run.step()
        fixup, = [b for _, _, b in run.store.records("unit_fixup")]
        assert fixup["owner"] == unit_ref(run, 2)
        gap, = fixup["gaps"]
        assert gap["subject"] == unit_ref(run, 1) + ":check:1" and gap["units"] == [unit_ref(run, 2)]
        assert "Write correct into unit-002/peer.txt" in gap["correction"]
        assert "peer defect" in run.store.read(gap["source"])["stderr"]
        if outcome == "fails":
            with pytest.raises(Waiting):
                while True:
                    run.step()
            assert state(str(run.project))["waitingFor"]["gate"] == "UNIT"
            request, = [b for _, _, b in run.store.records("decision_request") if b["gate"] == "UNIT"]
            assert request["subject"]["unit"] == unit_ref(run, 2)
            assert run.store.unit(unit_ref(run, 1)).state == "DONE"
            candidate = run.store.cursor().candidate
            run.close()
            run = Run.reopen(slot / "product", provider, approve)
            with pytest.raises(Waiting):
                run.step()
            assert run.store.cursor().candidate == candidate
            assert run.store.read(run.store.cursor().decision) == request
            (slot / "mode").write_text("defect-done")
            run.user = lambda item: ("Try again: correct peer.txt" if item["gate"] == "UNIT" else approve(item))
        finish(run)
        assert (run.project / "unit-002/peer.txt").read_text() == "correct"
        assert gitwork.blobs(run.project, run.store.cursor().candidate, ["unit-001/**"]) == original
        if outcome != "fails":
            assert "COACH" not in provider.calls
            assert not any(b["gate"] == "UNIT" for _, _, b in run.store.records("decision_request"))
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("retire", [False, True])
def test_shortened_work_report_keeps_checks_at_c(tmp_path, scalar_align, retire):
    """ShelfLife's import-only repair must still run the previously passing interface check.

    Other repair journeys resend their whole check list and miss lost proofs, shifted check
    identities and incorrect FLOW links. The retirement twin confirms that omission and
    naming the check for retirement have different effects on real integrated execution.
    """
    slot = tmp_path / "check-identity"
    run, _ = prepare(slot, "wave-gap", 2)

    class ShelfLife(Scripted):
        def __call__(self, q):
            answer = json.loads(super().__call__(q))["answer"]
            if q["kind"] == "DETAIL" and q["facts"]["Area"]["handle"] == "area-2":
                answer["edits"] += [
                    {"kind": "feature", "targetHandle": "interface", "typedBody": {
                        "story": "Read the interface", "acceptance": "See the saved label"}},
                    {"kind": "requirement", "targetHandle": "label", "typedBody": {
                        "acceptance": "Show the saved label.", "features": ["interface"]}},
                ]
                answer["units"][0]["requirements"].append("label")
            return json.dumps({"answer": answer})

    run.provider = ShelfLife(slot, "wave-gap", 2)
    native = slot / "native.py"
    source = native.read_text()
    script = r"""
if name == 'unit-002':
    path = name + '/interface.py'
    if 'Existing checks' in facts:
        imported = answer['checks'][0]
        imported.pop('update', None)
        imported['requirements'] = [r for r, b in facts['Requirements'].items()
                                    if b['acceptance'] != 'Show the saved label.']
    if turn == 1:
        Path(name, 'label.txt').write_text('Saved items')
        Path(path).write_text("from pathlib import Path\n"
            "assert Path('unit-002/label.txt').read_text() == 'Saved items'\n"
            "print('observed interface label')\n")
        interface = {'purpose': 'Read the interface label', 'command': [sys.executable, '-B', path],
                     'expectedEvidence': 'The saved label is visible'}
        if 'Existing checks' in facts:
            interface['requirements'] = [r for r, b in facts['Requirements'].items()
                                         if b['acceptance'] == 'Show the saved label.']
        answer['checkPaths'].append(path)
        answer['checks'].insert(0, interface)
    elif RETIRE:
        handle = next(r for r, c in facts['Existing checks'].items()
                      if c['purpose'] == 'Read the interface label')
        imported['update'] = next(r for r, c in facts['Existing checks'].items()
                                  if c['command'] == imported['command'])
        imported['requirements'] = list(facts['Requirements'])
        check_path = Path(name, 'check.py')
        check_path.write_text(check_path.read_text()
                              + "assert Path('unit-002/label.txt').read_text() == 'Saved items'\n")
        if 'Exact corrections' not in facts:
            handle = 'interface.py'
        else:
            correction = str(facts['Exact corrections'])
            assert "Check 'interface.py' is not owned" in correction
            assert 'Use an Existing checks handle' in correction
        answer['checks'].append({'retire': handle})
    elif 'Existing checks' in facts:
        if 'Exact corrections' not in facts:
            imported['requirements'] = ['label.txt']
        else:
            correction = str(facts['Exact corrections'])
            assert "unassigned requirements ['label.txt']" in correction
            assert 'Use the handles under Requirements' in correction
"""
    native.write_text(source.replace("print(json.dumps({'type':'item.completed'",
        script.replace("RETIRE", repr(retire)) + "\nprint(json.dumps({'type':'item.completed'"), encoding="utf-8")
    try:
        while run.store.cursor().state != "WAVE-VERIFY":
            run.step()
        run.step()
        gap, = [b["gaps"][0] for _, _, b in run.store.records("unit_fixup")]
        first_commit = run.store.read(gap["source"])["commit"]
        first_interface, = [b for _, _, b in run.store.records("observation")
                            if b["commit"] == first_commit and b["purpose"] == "final"
                            and "observed interface label" in b["stdout"]]
        finish(run)
        final = [run.store.read(r) for r in run.store.cursor().finalObservations]
        imported, = [b for b in final if "unit-002/check.py" in b["command"]]
        interface = [b for b in final if "observed interface label" in b["stdout"]]
        assert bool(interface) is not retire
        assert imported["check"] == gap["subject"]
        if interface:
            assert interface[0]["check"] == first_interface["check"]
            label, = interface[0]["requirements"]
            saved, = imported["requirements"]
            assert label != saved
            declared = plan.select(run.store.plan(run.store.cursor().revision), "requirement")
            assert declared[label]["acceptance"] == "Show the saved label."
            assert declared[saved]["acceptance"] == "Running the reader displays saved value-002."
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("fault", ["criterion-gap", "area-uncertain", "outside-unit",
                                   "outside-contract", "resume-unavailable"])
def test_judge_once_golden(tmp_path, scalar_align, fault):
    """Protect area-to-criterion truth and real correction recovery through C.

    Single-criterion golden misses a lost second verdict or uncertainty promoted to success;
    traced-owner gaps miss routing to DONE peers outside the feature. Resume-only repairs
    miss a new session receiving a delta without the original contracts and requirements.
    """
    from seldon.providers import ResumeUnavailable
    from seldon import judgment

    slot = tmp_path / fault
    run, provider = prepare(slot, "contracts", 2)
    planted, resumed, fresh = [], [], []
    schemas = {}

    class Fault:
        def invoke(self, q, started):
            kind, facts = q["kind"], q["facts"]
            if kind == "WORK":
                if "Exact failure" in facts:
                    if q["execution"].get("session"):
                        assert {"Exact failure", "Write scope", "Correction owners",
                                "Correction observations", "Existing checks"} <= facts.keys()
                        assert ("values" in facts.get("Contracts provided", {})
                                or "values" in facts.get("Contracts consumed", {}))
                        assert facts["Correction observations"]
                        resumed.append(q)
                        if fault == "resume-unavailable":
                            raise ResumeUnavailable("Recorded session no longer available", False, True)
                    else:
                        assert facts["Requirements"] and facts["Execution facts"]
                        assert facts["Contract authority"]
                        fresh.append(q)
                return provider.invoke(q, started)
            answer = json.loads(provider(q))["answer"]
            if kind == "ALIGN":
                answer["quality"].append({"criterion": "Readable saved label",
                                          "verification": "Observe the saved value label"})
            if kind == "ARCHITECT":
                for area in answer["areas"]:
                    area["commitments"].append("quality:2")
                if fault == "outside-contract":
                    answer["areas"].append({"handle": "area-3", "responsibility": "Read third value",
                        "scope": ["unit-003/**"], "commitments": ["scopeIn:1", "quality:1", "quality:2"]})
                    answer["contracts"][0]["consumers"] = ["area-3"]
            if kind in ("FLOW", "PRODUCT"):
                if kind in schemas:
                    assert schemas[kind] == q["schema"]
                schemas[kind] = q["schema"]
                document = plan.execution(run.store)
                if not planted:
                    if kind == "PRODUCT" and fault in ("criterion-gap", "area-uncertain", "resume-unavailable") and all(t in plan.select(document, "criterion") for t in q["targets"]) and unit_ref(run, 1) in judgment.owners(document, kind, q["targets"][0]):
                        area = plan.select(document, "criterion")[q["targets"][0]]["area"]
                        criteria = {r for r,b in plan.select(document, "criterion").items() if b["area"] == area}
                        assert set(facts["Criteria"]) == set(q["targets"]) == criteria
                        assert len(criteria) == 2
                        target = q["targets"][-1]
                        owner = unit_ref(run, 1)
                    elif kind == "FLOW" and fault in ("outside-unit", "outside-contract") and all("value-002" in b["acceptance"] for b in facts["Requirements"].values()):
                        target = q["targets"][0]
                        assert unit_ref(run, 1) not in facts["Correction owners"]
                        owner = (next(iter(plan.select(document, "contract")))
                                 if fault == "outside-contract" else unit_ref(run, 1))
                        if fault == "outside-contract":
                            assert owner not in facts["Crossed contracts"]
                    else:
                        return json.dumps({"answer": answer}), None
                    planted.append((kind, target, owner))
                    if fault == "area-uncertain":
                        answer = {"verdict": "uncertain", "neededFacts": ["Visual observation unavailable"], "why": "Need the missing observation"}
                    else:
                        answer.update(verdict="gap", gaps=[{"severity": "must-fix", "target": target, "owners": [owner],
                                      "correction": "Exercise the saved value label"}])
            return json.dumps({"answer": answer}), None

    run.provider = Fault()
    try:
        finish(run)
        assert planted
        document = plan.execution(run.store)
        rows = {run.store.read(r)["subject"]: run.store.read(r) for r in run.store.cursor().qualityVerdicts}
        assert set(rows) == set(judgment.subjects(document, "PRODUCT"))
        if fault == "area-uncertain":
            target = planted[0][1]
            area = plan.select(document, "criterion")[target]["area"]
            for r,b in plan.select(document, "criterion").items():
                if b["area"] == area:
                    assert rows[r]["answer"]["verdict"] == "not judged"
                    assert "Visual observation unavailable" in judgment.not_judged(run.store)[r]
            assert not run.store.records("outcome")[-1][2]["verification_complete"]
        elif fault == "outside-contract":
            # A contract owner is the planner's agreement to correct, never its provider's fixup.
            gaps = [b["gap"] for _, _, b in run.store.records("architecture_gap")]
            assert len(gaps) == 1 and f"contract {planted[0][2]} disagree" in gaps[0]
            assert not run.store.records("unit_fixup")
            assert all(r["answer"]["verdict"] == "satisfied" for r in rows.values())
        else:
            assignments = [b for _,_,b in run.store.records("unit_fixup")]
            assert {b["owner"] for b in assignments} == {planted[0][2]}
            assert resumed
            assert all(r["answer"]["verdict"] == "satisfied" for r in rows.values())
            if fault == "resume-unavailable":
                assert fresh and run.store.records("resume_unavailable")
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("steering", ["partial", "resent"])
def test_progressive_wave_plan_journey(tmp_path, scalar_align, steering):
    """Preserve built ownership while detailing new commitments and changed consumers.

    One-wave journeys miss renumbered worker rows, lost requirements, full-horizon DETAIL,
    stale contract handles and B expiring when the approved vision has not changed.
    This planning journey supplies the lead-owned next-wave transition at its boundary.
    """
    from dataclasses import replace
    from seldon import architecture, detail, inference

    slot = tmp_path / steering
    run, provider = prepare(slot, "contracts", 3)
    original = provider.__call__
    detailed = []
    interruptions = []

    class Interrupted(BaseException):
        pass

    def invoke(q, started):
        if q["kind"] == "WORK":
            return provider.invoke(q, started)
        answer = json.loads(original(q))["answer"]
        if q["kind"] == "ALIGN":
            answer["scopeIn"].append("Export saved values")
        elif q["kind"] == "ARCHITECT":
            answer["areas"][0]["commitments"].append("scopeIn:2")
            answer["areas"][1]["scope"].append("unit-004/**")
            answer["contracts"][0]["text"] = "Saved values transport; export shape comes in wave 2."
            answer["contracts"].append({"handle": "quiet", "kind": "data rule", "provider": "area-3",
                                        "consumers": [], "text": "Third value remains UTF-8."})
            answer["waves"].append({"goal": "Export saved values", "commitments": []})
            # Code assigns the omitted commitment to the last wave.
        elif q["kind"] == "DETAIL":
            detailed.append(q)
            if q["facts"]["Current wave"]["goal"] == "Export saved values":
                for edit in answer["edits"]:
                    if edit["kind"] == "requirement":
                        edit["typedBody"]["acceptance"] += " Export preserves the saved value."
                if q["facts"]["Area"]["handle"] == "area-2":
                    assert "export: UTF-8 CSV" in q["facts"]["Contracts"]["values"]["text"]
                    canonical = run.store.plan(run.store.cursor().revision)
                    assert "export: UTF-8 CSV" in plan.select(canonical, "contract")["values"]["text"]
                    assert "items" not in q["facts"]["Contracts"]
                    assert "quiet" not in q["facts"]["Contracts"]
                    exists, = q["facts"]["What exists here"].values()
                    assert exists["scope"] == ["unit-002/**"]
                    assert "saved value-002" in str(exists["acceptance"])
                    assert exists["last worker summary"] == "Wrote and exercised the acceptance"
                    if steering == "partial" and not interruptions:
                        interruptions.append(True)
                        raise Interrupted()
            else:
                answer["edits"] = [e for e in answer["edits"] if e["targetHandle"] not in ("f4", "r4")]
                answer["units"] = [u for u in answer["units"] if u["scope"] != ["unit-004/**"]]
        return json.dumps({"answer": answer}), None

    from types import SimpleNamespace
    run.provider = SimpleNamespace(invoke=invoke)
    try:
        while run.store.cursor().state != "CHECK-IN":
            run.step()
        run.step()  # Post the check-in and allow planning to proceed.
        run.step()  # Consume the real check-in words before the planning-only boundary below.
        cursor = run.store.cursor()
        before = run.store.plan(cursor.revision)
        names = {r: deepcopy(u) for r, u in plan.select(before, "unit").items()}
        assert before["architecture"]["waves"][1]["commitments"] == ["scopeIn:2"]
        built = plan.built(run.store)
        assert set(built["Finished wave"]) == set(names)
        assert built["User check-in"] == run.store.read(run.store.read(cursor.checkin)["words"])["words"]
        assert all("summary" in row and "discoveries" in row and "open gaps" in row
                   for row in built["Finished wave"].values())
        previous = before["architecture"]
        changed = {**previous["contracts"][0], "text": "Saved values and export: UTF-8 CSV, name,value header."}
        partial = {"areas": [], "contracts": [changed], "decisions": [], "waves": [],
                   "why": "Export the built values"}
        resent = {**deepcopy(previous), "contracts": [changed, *deepcopy(previous["contracts"][1:])],
                  "why": partial["why"]}
        merged = architecture.merge(previous, partial)
        assert merged == architecture.merge(previous, resent)
        architecture.outline(merged, before["nodes"]["vision"]["body"])
        scope = {"scope": "Include live hardware", "why": "Needs user agreement"}
        assert architecture.merge(previous, scope) is scope
        brief = unit_questions.architect_brief(before["nodes"]["vision"]["body"],
                                           questions.user_words(run.store), built=built)
        _, answer = inference.call(run.store, lambda q: json.dumps({"answer":
            partial if steering == "partial" else resent}), brief,
            check=lambda a: architecture.outline(architecture.merge(previous, a),
                                                 before["nodes"]["vision"]["body"]))
        document = {**deepcopy(before), "architecture": architecture.merge(previous, answer)}
        revision = run.store.revision(document, cursor.revision)
        run.store.cursor(replace(cursor, state="DETAIL", wave=2, revision=revision))
        detailed.clear()
        try:
            detail.step(run.store, run.provider, run.capacity)
        except Interrupted:
            planner = run.provider
            run.close()
            run = Run.reopen(slot / "product", planner, approve)
            detail.step(run.store, run.provider, run.capacity)
        after = run.store.plan(run.store.cursor().revision)
        assert {q["facts"]["Area"]["handle"] for q in detailed} == {"area-1", "area-2"}
        assert plan.approved(run.store)
        units = plan.select(after, "unit")
        assert set(names) < set(units)
        for ref, old in names.items():
            assert units[ref]["scope"] == old["scope"]
            assert set(old["requirements"]) <= set(units[ref]["requirements"])
            for req in old["requirements"]:
                assert after["nodes"][req] == before["nodes"][req]
        assert units[unit_ref(run, 3)] == names[unit_ref(run, 3)]
        assert len(units[unit_ref(run, 1)]["requirements"]) > len(names[unit_ref(run, 1)]["requirements"])
        assert unit_ref(run, 4).startswith("2.area-2.")
        assert "quiet" not in architecture.contracts_for(after, "guide")
        assert plan.select(after, "contract")["values"]["text"] == changed["text"]
        vision = deepcopy(before["nodes"]["vision"]["body"])
        vision["scopeIn"].append("Include live hardware")
        vref = run.store.record("answer", {"answer": vision})
        run.store.cursor(replace(run.store.cursor(), vision=vref))
        assert not plan.approved(run.store)
    finally:
        run.close()



@pytest.mark.fast
@pytest.mark.parametrize("fail_through", [2, 3, "live"])
def test_planner_first_recovery_journey(tmp_path, scalar_align, fail_through):
    """Real failing checks get planner direction before spending the user's time.

    A persistent failure keeps its request across restart; an independent reader finishes
    while a dependent waits. Picking the proposal must resume without DECIDE. The existing
    worker-question twin misses failed checks, planner-first repair and label selection.
    """
    from seldon.decisions import Waiting

    slot = tmp_path / "planner-first"
    run, original = prepare(slot, count=3)
    native = slot / "native.py"
    failures = 3 if fail_through == "live" else fail_through
    native.write_text(native.read_text().replace("    Path(name, 'check.py').write_text(source)",
        f"    if number == 1 and turn <= {failures}:\n"
        "        source += \"raise AssertionError('reader unavailable')\\n\"\n"
        "    Path(name, 'check.py').write_text(source)"))

    if fail_through == "live":
        native.write_text(native.read_text().replace("    value = 'value-'",
            "    if number == 2:\n"
            "        while not (slot/'question-shown').exists(): time.sleep(0.05)\n"
            "    value = 'value-'"))

    class Planner(Scripted):
        def __call__(self, q):
            if q["kind"] == "DECIDE":
                raise AssertionError("A displayed label needs no interpreter")
            if q["kind"] == "FLOW" and not q["facts"]["Final observations"]:
                assert q["facts"]["Left out by the user"]
                assert all(v["why"] == "left out by the user" for v in q["facts"]["Source judgment"])
                return json.dumps({"answer": {"verdict": "satisfied", "gaps": [], "why": "left out by the user"}})
            raw = super().__call__(q)
            if q["kind"] == "DETAIL":
                body = json.loads(raw)
                body["answer"]["units"][2]["dependencies"] = [1]
                return json.dumps(body)
            if q["kind"] == "COACH":
                assert run.store.integration_lock.acquire(blocking=False), "COACH holds integration lock"
                run.store.integration_lock.release()
                facts = q["facts"]
                assert "reader unavailable" in str(facts["Unresolved work"])
                assert facts["Requirements"] and facts["Vision"]
                return json.dumps({"answer": {"guidance": "Read the saved value and repair the reader",
                                              "why": "Preserve the accepted persistence"}})
            return raw

    provider = Planner(slot, count=3)
    run.provider = provider
    if fail_through == "live":
        def answer_when_ready(item):
            if item["gate"] != "UNIT":
                return approve(item)
            assert run.store.unit(unit_ref(run, 3)).state == "WAITING"
            (slot / "question-shown").touch()
            if run.store.unit(unit_ref(run, 2)).state != "DONE":
                raise Waiting()
            return item["ask"]["options"][0]["label"]
        run.user = answer_when_ready
    try:
        if fail_through == 3:
            with pytest.raises(Waiting):
                while True:
                    run.step()
            request = run.store.cursor().decision
            item = run.store.read(request)
            assert item["ask"]["options"][0]["means"] == "Read the saved value and repair the reader"
            assert run.store.unit(unit_ref(run, 2)).state == "DONE"
            assert run.store.unit(unit_ref(run, 3)).state == "WAITING"
            run.close()
            run = Run.reopen(slot / "product", provider, approve)
            with pytest.raises(Waiting):
                run.step()
            assert run.store.cursor().decision == request and run.store.read(request) == item
            run.user = lambda item: ("  TRY THE PLANNER'S NEW APPROACH  "
                                    if item["gate"] == "UNIT" else approve(item))
        finish(run)
        assert run.store.records("planner_answer")
        if fail_through == 2:
            assert not any(b["gate"] == "UNIT" for _, _, b in run.store.records("decision_request"))
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("steer_gate", ["CHECK-IN", "C"])
def test_options_and_unclear_golden(tmp_path, scalar_align, steer_gate):
    """Unclear words cannot approve A or C; a visible option can, without a model.

    Scripted approvals never exercise interpretation, alternatives or acceptance ambiguity.
    The scripted dict path is retained while option labels and own words use real requests.
    """
    from seldon import decisions

    slot = tmp_path / "options"
    from wave_loop_journey import prepared, Waves
    run, original = prepared(slot, False)

    class Interpreter(Waves):
        def __call__(self, q):
            if q["kind"] == "DECIDE":
                if q["facts"]["User's words"] == "Keep the value visible":
                    return json.dumps({"answer": {"choice": "Other", "why": "A requested improvement"}})
                return json.dumps({"answer": {"choice": "Unclear", "why": "Please choose an option or give a direction"}})
            return super().__call__(q)

    provider = Interpreter(slot, "normal", 3)
    run.provider = provider
    try:
        run.step()
        run.user = lambda item: "perhaps, what?"
        with pytest.raises(decisions.Waiting):
            run.step()
        ref = run.store.cursor().decision
        assert not run.store.records("approval")
        assert run.store.cursor().failure and run.store.cursor().state == "A"
        item = run.store.read(ref)
        assert item["ask"]["options"][1]["label"] == VISION["alternatives"][0]["label"]
        run.close()
        run = Run.reopen(slot / "product", provider, lambda item: "Show all values")
        assert run.store.cursor().decision == ref and run.store.read(ref) == item
        run.step()
        assert run.store.cursor().state == "ALIGN"
        assert VISION["alternatives"][0]["means"] in run.store.read(run.store.cursor().requestedChange)["words"]
        run.user = lambda item: item["ask"]["options"][0]["label"]
        if steer_gate == "CHECK-IN":
            while run.store.cursor().state != "CHECK-IN":
                run.step()
            run.user = lambda item: "Keep the value visible"
            run.step()  # Post the nonblocking check-in.
            run.step()  # Interpret its answer while planning can proceed.
            assert run.store.cursor().state == "ARCHITECT"
            assert run.store.read(run.store.cursor().requestedChange)["words"] == "Keep the value visible"
            run.user = lambda item: item["ask"]["options"][0]["label"]
        while run.store.cursor().state != "C":
            run.step()
        assert run.store.cursor().state == "C"
        run.user = lambda item: "perhaps, what?"
        prior = list(run.store.records("approval"))
        with pytest.raises(decisions.Waiting):
            run.step()
        ref = run.store.cursor().decision
        assert run.store.records("approval") == prior and not run.store.records("outcome")
        with pytest.raises(decisions.Waiting):
            run.step()
        assert run.store.cursor().decision == ref
        if steer_gate == "C":
            run.user = lambda item: "Keep the value visible"
            run.step()
            assert run.store.cursor().state == "ARCHITECT" and not run.store.records("outcome")
            assert run.store.read(run.store.cursor().requestedChange)["words"] == "Keep the value visible"
        run.user = lambda item: item["ask"]["options"][0]["label"]
        finish(run)
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("kept_gap", [False, True])
def test_planner_asks_and_drop_journey(tmp_path, scalar_align, kept_gap):
    """A planner's open choice can omit a blocked branch while independent work finishes.

    Repair twins never drop a unit, so they miss transitive dependents being stranded or
    starting after omission. A real missing output label still repairs its kept owner;
    omission must not erase accepted work or bypass C judgments.
    """
    from seldon.decisions import Waiting

    slot = tmp_path / "drop"
    run, _ = prepare(slot, "repeat-question", 3)

    class Planner(Scripted):
        def __call__(self, q):
            if q["kind"] == "DECIDE":
                raise AssertionError("Displayed choices need no interpretation")
            if q["kind"] == "COACH":
                return json.dumps({"answer": {"ask": {"question": "Which reader should we deliver?",
                    "options": [{"label": "Keep the named reader", "means": "Repair named reads"},
                                {"label": "Use a list", "means": "Deliver a list instead"}]},
                    "why": "The user must choose the scope"}})
            if q["kind"] == "PRODUCT" and any("unlabelled value-002" in o["stdout"]
                    for o in q["facts"]["Final observations"]):
                return json.dumps({"answer": {"verdict": "gap", "gaps": [{"severity": "must-fix",
                    "target": q["targets"][0], "owners": [unit_ref(run, 2)],
                    "correction": "Restore the saved value label"}], "why": "Kept output loses its label"}})
            if q["kind"] == "FLOW" and not q["facts"]["Final observations"]:
                assert q["facts"]["Left out by the user"]
                assert all(v["why"] == "left out by the user" for v in q["facts"]["Source judgment"])
                return json.dumps({"answer": {"verdict": "satisfied", "gaps": [], "why": "left out by the user"}})
            raw = super().__call__(q)
            if q["kind"] == "DETAIL":
                body = json.loads(raw)
                body["answer"]["units"][2]["dependencies"] = [1]
                return json.dumps(body)
            return raw

    provider = Planner(slot, "repeat-question", 3)
    run.provider = provider
    native = (slot / "native.py").read_text()
    start, end = native.index("elif name == 'docs':"), native.index("else:\n    Path(name).mkdir", native.index("elif name == 'docs':"))
    native = native[:start] + native[start:end].replace("unit-001", "unit-002").replace("value-001", "value-002") + native[end:]
    if kept_gap:
        native = native.replace("    Path(name, 'check.py').write_text(source)",
            "    if name == 'unit-002' and turn == 1: source = source.replace('observed value-002', 'unlabelled value-002')\n"
            "    Path(name, 'check.py').write_text(source)")
    (slot / "native.py").write_text(native)
    try:
        with pytest.raises(Waiting):
            while True:
                run.step()
        request = run.store.cursor().decision
        item = run.store.read(request)
        assert item["ask"]["question"] == "Which reader should we deliver?"
        assert item["ask"]["options"][0]["label"] == "Keep the named reader"
        assert run.store.unit(unit_ref(run, 2)).state == "DONE"
        run.user = lambda item: "Leave this out"
        run.step()
        assert run.store.cursor().state == "WAVE-VERIFY"
        assert all(run.store.unit(r).state == "DROPPED" for r in (unit_ref(run, 1), unit_ref(run, 3)))
        assert run.store.unit(unit_ref(run, 2)).state == "DONE"
        assert not (slot / "turn-unit-003").exists()
        assert any(b["request"] == request and b["effect"] == "drop"
                   for _, _, b in run.store.records("approval"))
        run.user = approve
        finish(run)
        c = [b for _, _, b in run.store.records("decision_request") if b["gate"] == "C"][-1]
        assert set(c["subject"]["leftOut"]) == {unit_ref(run, 1), unit_ref(run, 3)}
        assert all(b["reason"] == "left out by the user" for b in c["subject"]["leftOut"].values())
        assert (slot / "turn-unit-002").read_text() == ("2" if kept_gap else "1")
    finally:
        run.close()


@pytest.mark.fast
def test_step_fault_and_waiver_journey(tmp_path, scalar_align):
    """An identical invocation fault after planner guidance recommends a resumable Stop.

    Worker-failure twins miss controller faults that encourage endless user retries,
    and message-only records hide the owning source location from store triage.
    Reopen and a later successful call must retain the decision and allow waived check-ins.
    """
    from seldon import decisions

    slot = tmp_path / "fault"
    run, _ = prepare(slot)

    class Fault(Scripted):
        fixed = False
        def __call__(self, q):
            if q["kind"] == "ARCHITECT" and not self.fixed:
                receipt = slot / f"failure-{time.time_ns()}.json"
                receipt.write_text('{"cleanupVerified": true}')
                raise ProviderFailure("Codex invocation failed: 400 Invalid schema for response_format "
                    "'codex_output_schema': 'schema.properties.answer.anyOf.0.required' is required "
                    "to include every key in properties. Missing 'areas'.", True, True, str(receipt))
            assert q["kind"] != "DECIDE"
            return super().__call__(q)

    provider = Fault(slot)
    run.provider = provider
    def user(item):
        if "step" in item["subject"]:
            raise decisions.Waiting()
        return approve(item)
    run.user = user
    try:
        run.run()
        item = run.store.read(run.store.cursor().decision)
        assert item["subject"]["step"] == "ARCHITECT"
        failures = [body for _, _, body in run.store.records("step_failure")]
        assert failures and all(body["exceptionType"] == "ProviderFailure" for body in failures)
        assert all(body["failure"] == "ProviderFailure: " + body["message"] for body in failures)
        assert all(body["location"].startswith("src/seldon/inference.py:") and
                   int(body["location"].rsplit(":", 1)[1].split()[0]) > 0 for body in failures)
        assert run.store.records("planner_answer")
        assert "Seldon's fault" in item["ask"]["question"]
        assert "recommend Stop" in item["ask"]["question"]
        assert "400 Invalid schema" in str(item["subject"]["tried"])
        assert [o["label"] for o in item["ask"]["options"]] == ["Stop", "Try again"]
        assert decisions.holds(run.store)
        request = run.store.cursor().decision
        run.close()
        run = Run.reopen(slot / "product", provider, lambda item: "Stop")
        run.run()
        assert run.store.cursor().state == "ARCHITECT" and run.store.cursor().decision == request
        run.close()
        provider.fixed = True
        run = Run.reopen(slot / "product", provider,
            lambda item: "Try again" if "step" in item["subject"] else approve(item))
        while run.store.cursor().state != "B":
            run.step()
        run.user = lambda item: "Build it and do not stop at check-ins"
        run.step()
        while run.store.cursor().state != "CHECK-IN":
            run.step()
        decisions.request(run.store, "CHECK-IN", {"commit": run.store.cursor().candidate})
        assert run.store.cursor().waived and run.store.cursor().decision is None
        approval = run.store.read(run.store.cursor().checkin)
        assert run.store.read(approval["words"])["words"] == "waived by the user"
        assert run.store.read(approval["request"])["ask"]["options"]
        assert not decisions.holds(run.store)
    finally:
        run.close()


@pytest.mark.fast
def test_planning_gap_coaching_journey(tmp_path, scalar_align):
    """A real repeated DETAIL gap consumes planner guidance, then resumes on a user choice.

    Worker coaching cannot catch lost planning guidance or reuse of the rejected area answer.
    The success path goes through recovery.planning, the recorded approval and DETAIL's consumer.
    """
    from seldon import decisions

    slot = tmp_path / "planning-gap"
    run, _ = prepare(slot)

    class Planner(Scripted):
        def __call__(self, q):
            if q["kind"] == "DECIDE":
                raise AssertionError("A displayed choice needs no DECIDE")
            if q["kind"] == "COACH":
                return json.dumps({"answer": {"guidance": "Use the saved text contract",
                                              "why": "Keep the agreed reader"}})
            if q["kind"] == "DETAIL":
                words = str(q["facts"]["User's words"])
                if "Try the planner's new approach" not in words:
                    return json.dumps({"answer": {"verdict": "contract_gap",
                        "need": "Which representation should the reader consume?", "why": "Need a direction"}})
                assert "Use the saved text contract" in words
            return super().__call__(q)

    provider = Planner(slot)
    run.provider = provider
    try:
        while True:
            try:
                run.step()
            except decisions.Waiting:
                if run.store.cursor().decision and "ask" in run.store.read(run.store.cursor().decision):
                    break
        assert run.store.records("planner_answer")
        run.user = lambda item: (item["ask"]["options"][0]["label"]
                                if "step" in item["subject"] else approve(item))
        finish(run)
    finally:
        run.close()



@pytest.mark.fast
@pytest.mark.parametrize("missing", [False, True])
def test_cross_area_commitment_golden(tmp_path, scalar_align, missing):
    """Catch a promise lost between passing features, absent from area-only journeys.

    The real guide reads the carried path; only its consumer owner repairs a missing path.
    The clean twin reaches C without rework and C retains each accepted commitment once.
    """
    run, provider = prepare(tmp_path / "commitment", "contracts", 2)
    native = NATIVE.replace(
        "Path(name, 'value.txt').write_text(value)",
        "Path(name, 'value.txt').write_text(value)\n"
        "    if name == 'unit-002':\n"
        + ("        if turn > 1: Path(name, 'carried.txt').write_text('carried value-001')\n"
           if missing else "        Path(name, 'carried.txt').write_text('carried value-001')\n"))
    native = native.replace(
        "print('observed value-001')\\n",
        "print('observed value-001')\\nprint(Path('unit-002/carried.txt').read_text() "
        "if Path('unit-002/carried.txt').exists() else 'path missing')\\n")
    (provider.slot / "native.py").write_text(native, encoding="utf-8")
    checked, repairs = [], []

    class Commitment:
        def invoke(self, q, started):
            kind, facts = q["kind"], q["facts"]
            if kind == "WORK":
                if "Exact failure" in facts:
                    repairs.append(facts["Write scope"])
                return provider.invoke(q, started)
            answer = json.loads(provider(q))["answer"]
            if kind == "ALIGN":
                answer["scopeIn"] = ["Carry the saved value from producer through consumer"]
                answer["improvements"] = ["Keep the carried value visible in the walkthrough"]
            elif kind == "ARCHITECT":
                for area in answer["areas"]:
                    area["commitments"].append("improvements:1")
                answer["waves"][0]["commitments"].append("improvements:1")
            elif kind == "PRODUCT" and "Feature verdicts" in facts:
                assert {row["acceptance"] for row in facts["Feature verdicts"]} == {
                    "Observe value-001", "Observe value-002"}
                assert all(row["answer"]["verdict"] == "satisfied" for row in facts["Feature verdicts"])
                assert "Product files" not in facts and "Check sources" not in facts
                guide = [o for o in facts["Final observations"] if o["check"].startswith("guide:")]
                assert guide
                carried = any("carried value-001" in o["stdout"] for o in guide)
                checked.append((q["targets"][0], carried))
                if not carried:
                    answer.update(verdict="gap", gaps=[{"severity": "must-fix", "target": q["targets"][0],
                        "owners": [unit_ref(run, 2)], "correction": "Carry value-001 through unit-002/carried.txt"}])
            return json.dumps({"answer": answer}), None

    run.provider = Commitment()
    try:
        finish(run)
        assert {s for s, ok in checked if ok} == {"scopeIn:1", "improvements:1"}
        assert any(not ok for _, ok in checked) == missing
        assert bool(repairs) == missing
        assert all(scope == ["unit-002/**"] for scope in repairs)
        rows = [run.store.read(r) for r in run.store.cursor().qualityVerdicts]
        assert [r["subject"] for r in rows].count("scopeIn:1") == 1
        assert [r["subject"] for r in rows].count("improvements:1") == 1
        assert all(r["answer"]["verdict"] == "satisfied" for r in rows)
    finally:
        run.close()


@pytest.mark.fast
def test_saved_area_order_does_not_reassign_review_or_validation_journey(tmp_path):
    """A reopened ShelfLife plan must brief the named owner after outline reordering.

    Ordered scripted plans and promises spanning every area can hide an owner swap.
    Replay u01's saved plan and worker declarations through both question producers.
    """
    from seldon import judgment, plan_review
    from seldon.store import RunRow, Store, UnitRow

    fixture = json.loads((Path(__file__).parent / "fixtures/shelflife-u01-area-join.json").read_text())
    areas = fixture["architecture"]["areas"]
    expected_subjects = {
        "backend": ("scopeIn:1 scopeIn:2 scopeIn:3 scopeIn:4 scopeIn:5 scopeIn:6 scopeIn:11", "quality:3"),
        "interface": ("scopeIn:7 scopeIn:8 improvements:1", "quality:1 quality:2 quality:5"),
        "import": ("scopeIn:9 scopeIn:10 improvements:2", "quality:4"),
    }
    declarations = {row["unit"]: row for row in fixture["workerChecks"]}
    expected_review, expected_validation = {}, {}
    guide_checks = {"guide:check:1"}
    for area, (commitments, quality) in expected_subjects.items():
        unit = f"1.{area}.1"
        local_checks = {f"{unit}:check:{i}" for i, _ in enumerate(declarations[unit]["checks"], 1)}
        expected_review[area] = ({unit}, set((commitments + " " + quality).split()))
        for commitment in commitments.split():
            checks = local_checks | guide_checks
            expected_validation[commitment] = ({unit}, checks, checks)
        for criterion in quality.split():
            check_ids = local_checks | (guide_checks if area == "backend" else set())
            expected_validation[f"quality:{int(criterion.split(':')[1]):03}:{area}"] = (
                {unit}, check_ids, check_ids)
    expected_validation["tryItGuide"] = (set(), guide_checks, guide_checks)

    actual = {}
    for name, order in (("recorded", areas), ("permuted", areas[1:] + areas[:1])):
        project = tmp_path / name
        project.mkdir()
        gitwork.git(project, "init", "--quiet")
        guide = gitwork.git(project, "hash-object", "-w", "--stdin", data=b"Open the ShelfLife walkthrough.\n")
        candidate, = gitwork.import_commits(project, None, [[(questions.GUIDE_PATH, "100644", guide)]])
        document = {"architecture": {"areas": order}, "nodes": fixture["nodes"]}
        store = Store(project)
        try:
            revision = store.revision(document)
            kickoff = store.record("kickoff", {"setup": {}, "capacity": 3, "words": "Build ShelfLife"})
            cursor = RunRow(state="REVIEW", revision=revision, kickoff=kickoff, candidate=candidate)
            store.cursor(cursor)
            store.close()
            store = Store(project)
            provider = Scripted(project)
            plan_review.step(store, provider, 3)
            assert store.cursor().state == "B"

            # u01 stopped before final validation. Supply candidate-bound observations for
            # its saved check declarations so this replay tests routing, not product proof.
            for ref, declaration in declarations.items():
                store.record("worker_checks", declaration)
                evidence = {"scopeTree": candidate, "commit": candidate}
                if ref != "guide":
                    evidence["answer"] = {"verdict": "satisfied", "gaps": []}
                verdict = store.record("unit_verdict", evidence)
                store.unit(ref, UnitRow(state="DONE", judgment=verdict))
            observations = [store.record("observation", {
                "commit": candidate, "scopeTree": candidate, "unit": check["owner"], "check": ref,
                "requirements": check["requirements"], "purpose": "final", "command": check["command"],
                "exitCode": 0, "stdout": ref, "stderr": "", "cwd": str(project), "evidenceFiles": [],
                "executionStarted": True, "cleanupVerified": True, "timedOut": False,
            }) for ref, check in plan.select(plan.execution(store), "check").items()]
            from dataclasses import replace
            store.cursor(replace(cursor, state="PRODUCT-JUDGE", finalCommit=candidate, finalObservations=observations))
            judgment.step(store, provider)
            assert store.cursor().state == "C"

            review, validation = {}, {}
            for _, _, question in store.records("question"):
                facts = question["facts"]
                if question["kind"] == "REVIEW":
                    review[facts["Review subject"]] = (set(facts["Units"]), set(facts["Accepted commitments"]))
                elif question["kind"] == "PRODUCT":
                    for subject in facts["Criteria"]:
                        validation[subject] = ({row["unit"] for row in facts["Source judgment"]},
                                               set(facts["Checks"]),
                                               {row["check"] for row in facts["Final observations"]})
            actual[name] = {"REVIEW": review, "PRODUCT": validation}
        finally:
            store.close()
    expected = {"REVIEW": expected_review, "PRODUCT": expected_validation}
    assert actual == {"recorded": expected, "permuted": expected}


@pytest.mark.fast
def test_no_check_final_judgment_journey(tmp_path, scalar_align):
    """Retain a source-judged unit without final observations through the C presentation.

    Existing workers always declare checks, hiding scope-tree lookup and lost FLOW evidence.
    Full C eligibility remains the protected run.py owner's integration responsibility.
    """
    from seldon import judgment
    run, provider = prepare(tmp_path / "no-check", count=2)
    native = NATIVE.replace(
        "print(json.dumps({'type':'item.completed'",
        "if name == 'unit-002': answer.update(checks=[], checkPaths=[])\n"
        "print(json.dumps({'type':'item.completed'")
    (provider.slot / "native.py").write_text(native, encoding="utf-8")
    source, seen = [], []

    class NoCheck:
        def invoke(self, q, started):
            if q["kind"] == "WORK":
                return provider.invoke(q, started)
            if q["kind"] == "JUDGE" and not q["facts"]["Observations"]:
                assert "value-002" in str(q["facts"]["Product files"])
                source.append(q)
                return json.dumps({"answer": {"verdict": "satisfied", "gaps": [],
                    "why": "The unit writes the accepted saved value; no executable check declared"}}), None
            if q["kind"] == "FLOW" and not q["facts"]["Final observations"]:
                assert any(v["unit"] == unit_ref(run, 2) and v["verdict"] == "satisfied"
                           for v in q["facts"]["Source judgment"])
                seen.append(q)
                return json.dumps({"answer": {"verdict": "satisfied", "gaps": [],
                    "why": "The retained local source judgment covers the saved value"}}), None
            return provider.invoke(q, started)

    run.provider = NoCheck()
    try:
        while run.store.cursor().state != "C":
            run.step()
        assert source and seen
        local = run.store.read(run.store.unit(unit_ref(run, 2)).judgment)
        assert local["answer"]["verdict"] == "satisfied"
        rows = [run.store.read(r) for r in run.store.cursor().requirementVerdicts]
        assert any(r["scopeTrees"].get(unit_ref(run, 2)) == local["scopeTree"] for r in rows)
        assert all(judgment.ready(r["answer"]) for r in rows)
        finish(run)
    finally:
        run.close()



@pytest.mark.fast
def test_scale_five_wave_golden(tmp_path, scalar_align):
    """Fifty values survive progressive steering, local questions and a wave-3 process kill.

    Catches one-wave early C, failure escalation before the planner, answers that kill
    unrelated workers and replay of DONE work. The existing single-wave scale case
    never crosses CHECK-IN into a later wave or answers while another worker runs.
    """
    import os
    import sqlite3
    import subprocess

    slot = tmp_path / "scale-five"
    command = [sys.executable, "-B", str(Path(__file__).with_name("scale_journey.py")), str(slot)]
    hang_guard = 290  # Each controller launch has its own hang guard.
    log = tmp_path / "scale.log"

    def start(extra=()):
        return subprocess.Popen([*command, *extra], stdout=output, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def stop(child):
        if child.poll() is None:
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
            child.wait(timeout=10)

    with log.open("wb") as output:
        child = start()
        deadline = time.monotonic() + hang_guard
        try:
            while not (slot / "kill-wave3").exists():
                if child.poll() is not None:
                    pytest.fail(log.read_text("utf-8"))
                if time.monotonic() >= deadline:
                    pytest.fail("STOP: scale journey exceeded five-minute hang guard")
                time.sleep(.05)
            db = sqlite3.connect((slot / "product/.seldon/state.sqlite3").as_uri()+"?mode=ro",uri=True)
            try:
                before = {r:json.loads(b) for r,b in db.execute("SELECT id,body FROM unit")
                          if json.loads(b)["state"] == "DONE" and r != "guide"}
                cursor = json.loads(db.execute("SELECT cursor FROM run").fetchone()[0])
                assert cursor["wave"] == 3
            finally:
                db.close()
            stop(child)
            child = start(["--resume"])
            child.wait(timeout=hang_guard)
            assert child.returncode == 0, log.read_text("utf-8")
            db = sqlite3.connect((slot / "product/.seldon/state.sqlite3").as_uri()+"?mode=ro",uri=True)
            try:
                after = {r:json.loads(b) for r,b in db.execute("SELECT id,body FROM unit")}
                assert before and all(after[r]["integrated"] == b["integrated"] for r,b in before.items())
            finally:
                db.close()
        finally:
            stop(child)


@pytest.mark.fast
@pytest.mark.parametrize("severity", ["nit", "pre-existing"])
def test_judge_notes_reach_c_without_rework(tmp_path, scalar_align, severity):
    """Nonblocking findings survive REVIEW, source, flow and product to C with no repairs.

    Catches polish or a peer's old defect reopening a healthy owner; existing must-fix
    recovery journeys prove blocking gaps but never carry a note through final closure.
    """
    run, original = prepare(tmp_path / "notes", count=2)

    class Notes(Scripted):
        def __call__(self, q):
            answer = json.loads(super().__call__(q))["answer"]
            if q["kind"] in ("REVIEW", "JUDGE", "FLOW", "PRODUCT"):
                gap = {"target": next(iter(q["targets"]), "plan"), "severity": severity,
                       "correction": q["kind"] + ": " + (
                           "A small requirement remains unproven in this delivered app"
                           if severity == "nit" else
                           "The peer's old unit-002 file has a pre-existing formatting issue")}
                if q["kind"] in ("FLOW", "PRODUCT"):
                    gap["owners"] = [self.refs[2]]
                elif q["kind"] == "JUDGE":
                    gap["owners"] = judge_owners(q)[:1]
                answer.update(verdict="gap", gaps=[gap])
            return json.dumps({"answer": answer})

    run.provider = Notes(original.slot, count=2)
    shown = []
    def user(item):
        if item["gate"] == "C":
            shown.extend(item["subject"]["notes"])
        return approve(item)
    run.user = user
    try:
        finish(run)
        assert {n["correction"].split(":")[0] for n in shown} == {"REVIEW", "JUDGE", "FLOW", "PRODUCT"}
        assert all(n["severity"] == severity for n in shown)
        assert not run.store.records("unit_fixup") and not run.store.records("unit_gap")
        assert all(int((original.slot / ("turn-unit-" + str(n).zfill(3))).read_text()) == 1
                   for n in (1, 2))
    finally:
        run.close()


LATER_GUIDE = r"""
    walk_import = mode == 'later-forced' or 'Import values' in json.dumps(facts)
    Path('docs/try-it/check.py').write_text("from pathlib import Path\n"
        "assert Path('unit-001/value.txt').read_text() == 'value-001'\nprint('observed value-001')\n"
        + ("assert Path('unit-002/value.txt').exists(), 'Expected unit-002/value.txt from the importer'\n"
           "print('observed import')\n" if walk_import else ""))
    if 'importer' in facts.get('Exact failure', ''):
        later = next((r for r, v in facts['Correction owners'].items()
                      if isinstance(v, dict) and 'unit-002/**' in v['scope']), None)
        # Terra named the closest listed owner when the real owner was not listed (t01).
        answer['discoveries'] = [{'owner': later or owner(1), 'correction': 'Build unit-002/value.txt, the importer'}]
"""


class LaterWave(Scripted):
    """ShelfLife t01 shape: wave 1 builds the reader, the importer area waits for wave 2."""

    def __call__(self, q):
        kind, facts = q["kind"], q["facts"]
        if kind == "ARCHITECT":
            first = {"goal": "Read the saved values", "commitments": ["scopeIn:1", "quality:1"]}
            later = {"goal": "Import values", "commitments": ["scopeIn:2"]}
            previous = facts.get("Previous architecture")
            if "Finished wave" in facts:
                answer = {**{k: previous[k] for k in ("areas", "contracts", "decisions")},
                          "waves": facts["Remaining waves"]}
            elif "Architecture correction" in facts:
                assert "no unit of this wave holds" in facts["Architecture correction"]
                assert "area-2" in facts["Architecture correction"]
                answer = {**{k: previous[k] for k in ("areas", "contracts", "decisions")},
                          "waves": [{**first, "commitments": first["commitments"] + later["commitments"]}]}
            else:
                answer = {"areas": [
                    {"handle": "area-1", "responsibility": "Read saved values", "scope": ["unit-001/**"],
                     "commitments": ["scopeIn:1", "quality:1"]},
                    {"handle": "area-2", "responsibility": "Import values", "scope": ["unit-002/**"],
                     "commitments": ["scopeIn:2"]}],
                    "contracts": [{"handle": "values-file", "kind": "file", "provider": "area-1",
                                   "consumers": ["area-2"],
                                   "text": "Wave 2 agreement: Import values reuse the saved value format."}],
                    "decisions": [], "waves": [first, later]}
            answer["why"] = "The importer follows the reader"
            return json.dumps({"answer": answer})
        answer = json.loads(super().__call__(q))["answer"]
        if kind == "ALIGN":
            answer["scopeIn"].append("Import values from a file")
            answer["understanding"] = "Read named values and Import values from a file"
        return json.dumps({"answer": answer})


@pytest.mark.fast
@pytest.mark.parametrize("forced", [False, True])
def test_later_wave_piece_journey(tmp_path, scalar_align, forced):
    """A wave's walkthrough proves that wave; work no unit of the wave owns goes to the planner.

    ShelfLife t01: the wave-1 guide walked the wave-2 importer, its check was routed to the
    backend, whose scope cannot hold the importer, and 10 COACH calls looped. The clean twin
    keeps the importer out of the wave-1 walkthrough and adds it in wave 2. The forced twin
    walks it anyway: the planner brings the importer into wave 1 and no wrong owner is assigned.
    """
    slot = tmp_path / "later"
    run, _ = prepare(slot, "later-forced" if forced else "later", 2)
    native = slot / "native.py"
    source = native.read_text()
    start = source.index("    Path('docs/try-it/check.py').write_text(")
    end = source.index("    answer['checkPaths'] = ['docs/try-it/check.py']")
    native.write_text(source[:start] + LATER_GUIDE + source[end:], encoding="utf-8")
    run.provider = LaterWave(slot, "later", 2)
    briefs = []
    original = run.provider.invoke

    def invoke(q, started):
        if q["kind"] == "WORK" and questions.GUIDE_PATH in q["facts"]["Write scope"] and "Readers" in q["facts"]:
            briefs.append(q["facts"])
        return original(q, started)

    run.provider.invoke = invoke
    try:
        finish(run)
        store = run.store
        reader = unit_ref(run, 1)
        assert not any(b["owner"] == reader for _, _, b in store.records("unit_fixup"))
        first = briefs[0]
        assert "scopeIn:2" not in first["This wave's commitments"]
        assert "User's words" not in first and "Import values" not in str(first.get("Contracts consumed"))
        importer = unit_ref(run, 2)
        guide = [store.read(r) for r in store.cursor().finalObservations
                 if store.read(r)["check"].startswith("guide:")]
        assert guide and all("observed import" in o["stdout"] for o in guide)
        gaps = store.records("architecture_gap")
        if forced:
            # One plan gap, then the planner gave the importer a wave-1 unit.
            assert len(gaps) == 1 and "Expected: a unit of this wave owns" in gaps[0][2]["gap"]
            assert importer.startswith("1.") and store.unit(importer).wave == 1
            assert store.cursor().wave == 1
        else:
            assert not gaps and not store.records("unit_fixup")
            assert importer.startswith("2.") and store.cursor().wave == 2
            assert "scopeIn:2" in briefs[-1]["This wave's commitments"]
    finally:
        run.close()


@pytest.mark.fast
def test_repeated_unit_failure_pauses_journey(tmp_path, scalar_align):
    """A repeated product assertion reaches the planner for a plain user question.

    ShelfLife t01 offered "Try the planner's new approach" five times for one unchanged failure;
    the step-fault twin covers planning steps only.
    """
    from seldon.decisions import Waiting

    slot = tmp_path / "repeat"
    run, provider = prepare(slot)
    native = slot / "native.py"
    native.write_text(native.read_text().replace("    Path(name, 'check.py').write_text(source)",
        "    source += \"raise AssertionError('reader unavailable')\\n\"\n"
        "    Path(name, 'check.py').write_text(source)"))
    shown = []

    def user(item):
        if item["gate"] != "UNIT":
            return approve(item)
        shown.append(item)
        if len(shown) == 1:
            return item["ask"]["options"][0]["label"]
        raise Waiting()

    run.user = user
    try:
        with pytest.raises(Waiting):
            while True:
                run.step()
        first, again = shown[0], shown[-1]
        assert first["ask"]["options"][0]["label"] == "Try the planner's new approach"
        ask = again["ask"]
        assert "could not make" in ask["question"] and ":check:1" in ask["question"]
        assert "Seldon's fault" not in ask["question"]
        assert "reader unavailable" in again["subject"]["failure"]
        assert ask["options"][0]["label"] == "Change the agreement or approach"
        assert [o["effect"] for o in ask["options"]] == ["direct", "drop", "pause"]
        assert provider.calls.count("COACH") == 3
        assert run.store.cursor().decision == again["request"]
    finally:
        run.close()


class ContractGap(Scripted):
    """Recorded ShelfLife u01 shape: the consumer relies on what the agreed contract does not define.

    The page required an X-Pantry-Today header that the pantry API contract never named; the
    interface worker could not fix that alone and the run paused on "This is Seldon's fault".
    """
    def __init__(self, slot, case):
        super().__init__(slot, "contract-gap", 1)
        self.case = case

    def __call__(self, q):
        if q["kind"] == "ARCHITECT" and "Architecture correction" in q["facts"]:
            assert "values" in q["facts"]["Architecture correction"]
            answer = json.loads(super().__call__(q))["answer"]
            answer["contracts"][0]["text"] += " The provider also writes the date to unit-001/today.txt."
            return json.dumps({"answer": answer})
        if q["kind"] != "JUDGE":
            return super().__call__(q)
        answer = json.loads(super().__call__(q))["answer"]
        facts, owners = q["facts"], judge_owners(q)
        agreed = str(facts.get("Contracts consumed", {})) + str(facts.get("Contracts provided", {}))
        consumer = "value-002" in str(facts["Requirements"])
        gap = None
        if consumer and (self.case == "unfixable" or "today.txt" not in agreed):
            gap = {"correction": "Make the reader work with the agreed values contract without "
                   "requiring a date the contract does not define", "owners": (
                       ["values"] if self.case == "contract" and "values" in owners else owners[:1])}
        elif not consumer and "today.txt" in agreed and "unit-001/today.txt" not in facts["Product files"]:
            gap = {"correction": "Write unit-001/today.txt as the corrected values contract says",
                   "owners": owners[:1]}
        if gap:
            gap = {"severity": "must-fix", "target": q["targets"][0], **gap}
            if not owners:
                del gap["owners"]  # The base schema had no JUDGE owners.
            answer.update(verdict="gap", gaps=[gap])
        return json.dumps({"answer": answer})


@pytest.mark.fast
def test_check_contract_journey(tmp_path, scalar_align):
    """Quiet Bench r1025/r1158: a check writer sends a failure to service, which needs a
    guarantee from engine. A worker-only fix repeats; the judge-gap twin never exercises
    a failing executable check or requires both endpoint patches to pass it.
    """
    from seldon.decisions import Waiting

    slot = tmp_path / "check-contract"
    run, _ = prepare(slot, "contracts", 3)
    native = slot / "native.py"
    native.write_text(native.read_text(encoding="utf-8").replace(
        "    if mode == 'contracts':", "    if mode == 'check-contract':\n"
        "        if number in (1, 2):\n"
        "            side = 'Contracts provided' if number == 1 else 'Contracts consumed'\n"
        "            corrected = 'Publish ready during processing' in str(facts.get(side, {}))\n"
        "            Path(name, 'callback.txt').write_text(('ready' if corrected else 'terminal')\n"
        "                if number == 1 else ('ready' if corrected else 'progress'))\n"
        "        if number == 3:\n"
        "            source += \"assert Path('unit-001/callback.txt').read_text() == \"\n"
        "            source += \"Path('unit-002/callback.txt').read_text() == 'ready', \"\n"
        "            source += \"'POST /api/runs could not return an active session'\\n\"\n"
        "    if mode == 'contracts':").replace(
        "    answer['checkPaths'] = [name+'/check.py']",
        "    if mode == 'check-contract' and number == 3:\n"
        "        answer['discoveries'] = [{'owner': owner(2), 'correction': 'Return an active session'}]\n"
        "    if mode == 'check-contract' and number == 2 and not corrected:\n"
        "        answer['discoveries'] = [{'owner': owner(1), 'correction': 'Guarantee when callbacks fire'}]\n"
        "    answer['checkPaths'] = [name+'/check.py']"), encoding="utf-8")
    (slot / "mode").write_text("check-contract")

    class CheckContract(Scripted):
        def __call__(self, q):
            answer = json.loads(super().__call__(q))["answer"]
            if q["kind"] == "ARCHITECT" and "Architecture correction" in q["facts"]:
                correction = q["facts"]["Architecture correction"]
                assert "POST /api/runs could not return an active session" in correction
                assert "Guarantee when callbacks fire" in correction and "Return an active session" in correction
                answer["contracts"][0]["text"] += " Publish ready during processing."
            return json.dumps({"answer": answer})

    run.provider = CheckContract(slot, "contracts", 3)
    shown = []

    def user(item):
        if item["gate"] != "UNIT":
            return approve(item)
        shown.append(item)
        if len(shown) == 1:
            return item["ask"]["options"][0]["label"]
        raise Waiting()

    run.user = user
    store = run.store
    try:
        try:
            finish(run)
        except Waiting:
            owners = [b["owner"] for _, _, b in store.records("unit_fixup")]
            pytest.fail(f"Contract disagreement still asks the user after fixups to {owners}: "
                        f"{[item['ask']['question'] for item in shown]}")
        assert not shown
        assert len(store.records("architecture_gap")) == 1
        assert "Publish ready during processing" in plan.select(plan.execution(store), "contract")["values"]["text"]
        assert (slot / "product" / "unit-001" / "callback.txt").read_text() == "ready"
        assert (slot / "product" / "unit-002" / "callback.txt").read_text() == "ready"
        assert any(store.read(r)["check"] == f"{unit_ref(run, 3)}:check:1"
                   for r in store.cursor().finalObservations)
    finally:
        run.close()


@pytest.mark.fast
@pytest.mark.parametrize("case", ["contract", "unfixable"])
def test_contract_gap_journey(tmp_path, scalar_align, case):
    """A judged disagreement with a contract becomes one planner correction, not a worker loop.

    Recorded u01 (2026-11-17) looped one worker on a cross-unit gap and then blamed Seldon; the
    repeated-check twin covers executable failures. contract: the planner extends the contract,
    the provider is reworked and the wave verifies with no user question. unfixable: a judged gap
    no worker fixes reaches the user in plain words with planner-framed options.
    """
    from seldon.decisions import Waiting

    slot = tmp_path / case
    run, _ = prepare(slot, "contract-gap")
    run.provider = provider = ContractGap(slot, case)
    shown = []

    def user(item):
        if item["gate"] != "UNIT":
            return approve(item)
        shown.append(item)
        if len(shown) == 1 and case == "unfixable":
            return item["ask"]["options"][0]["label"]
        raise Waiting()

    run.user = user
    store = run.store
    try:
        if case == "contract":
            finish(run)
            assert not shown and not [b for _, _, b in store.records("decision_request") if b["gate"] == "UNIT"]
            gaps = [b["gap"] for _, _, b in store.records("architecture_gap")]
            assert len(gaps) == 1 and "contract values disagree" in gaps[0]
            assert "requiring a date the contract does not define" in gaps[0]
            assert "today.txt" in plan.select(plan.execution(store), "contract")["values"]["text"]
            fixed = {b["owner"] for _, _, b in store.records("unit_fixup")}
            assert fixed == {unit_ref(run, 1)}  # the provider was reworked; the consumer was never looped
            assert (slot / "product" / "unit-001" / "today.txt").read_text() == "2026-11-18"
            assert provider.calls.count("COACH") == 0
        else:
            with pytest.raises(Waiting):
                while True:
                    run.step()
            first, last = shown[0], shown[-1]
            assert first is not last and first["ask"]["options"][0]["label"] == "Try the planner's new approach"
            ask = last["ask"]
            assert "Seldon's fault" not in ask["question"]
            assert "could not fix what the judge found missing" in ask["question"]
            assert "requiring a date the contract does not define" in ask["question"]
            labels = [o["label"] for o in ask["options"]]
            assert labels == ["Try the planner's new approach", "Leave this out", "Pause"]
            assert provider.calls.count("COACH") == 3  # coached once, asked once, then framed the options
            assert not store.records("architecture_gap")
    finally:
        run.close()

@pytest.mark.fast
@pytest.mark.parametrize("dream, index",
                         [("ShelfLife", 0), ("ShelfLife", 1), ("Quiet Bench", 0), ("Quiet Bench", 1)])
def test_recorded_align_normalisation_journey(tmp_path, monkeypatch, dream, index):
    """Richer recorded ALIGN replies must reach A with provenance still visible.

    Exact-value happy paths miss bundled paraphrases, mislabeled citations and structured
    settings. Quiet Bench's recorded JSON text also exercises the equivalent native object form.
    """
    from contextlib import closing

    from seldon import features, frontdoor

    monkeypatch.setattr(features, "enabled", {"growAlign", "sizeFirst", "answerRepair", "prompts2",
                                            "judgeEvidence"}.__contains__)
    fixture = json.loads((Path(__file__).with_name("fixtures") / "align-normalisation.json")
                         .read_text(encoding="utf-8"))[dream]
    reply = deepcopy(fixture["align"][index if dream == "ShelfLife" else 0])
    if dream == "Quiet Bench" and index:
        settings = next(c for c in reply["vision"]["constraints"] if c["handle"] == "defaults")
        settings["value"] = json.loads(settings["value"])
        reply["vision"]["constraints"].append(
            {"handle": "open-backup", "value": ["external drive"], "meaning": "Proposed backup destination",
             "source": "Runtime"})
        reply["questions"].append(
            {"affects": "open-backup", "question": "Where should the exported backup go?",
             "options": [{"label": "Project folder", "means": "Keep it beside the project"},
                         {"label": "Separate folder", "means": "Choose another folder in Other"}]})

    def provider(q):
        assert q["kind"] == "ALIGN"
        strict_schema(q["schema"])
        return json.dumps({"answer": reply})

    root = tmp_path / "product"
    with closing(Run(root, tmp_path / "workers", fixture["kickoff"], {}, provider, lambda _: None)) as run:
        assert run.step() == "A"
        assert not run.store.records("consumption_failure")
        request = frontdoor.state(root)["waitingFor"]
        vision = request["subject"]["vision"]
        events = [b for _, _, b in run.store.records("answer_normalisation")]
        assert request["subject"]["normalisations"] == list(dict.fromkeys(b["summary"] for b in events))
        assert all(source == "kickoff" for item in vision["stories"] + vision["quality"]
                   for source in item["sources"])
        if dream == "ShelfLife":
            assert any(b["action"] == ("paraphrased" if index == 0 else "citation") for b in events)
            if index == 0:
                launch = next(c for c in vision["constraints"] if c["handle"] == "launch")
                assert launch["value"] == "python server.py --port PORT --db PATH"
                assert any(b.get("sourceSpan") and b["sourceSpan"] in fixture["kickoff"] for b in events)
        elif index:
            stored = {c["handle"]: c for c in vision["constraints"]}
            assert json.loads(stored["defaults"]["value"]) == settings["value"]
            assert "open-backup" not in stored
            assert {"rendered", "source_handle", "dropped_constraint"} <= {b["action"] for b in events}
            question = next(g for g in request["ask"]["groups"] if "exported backup" in g["question"])
            assert [o["label"] for o in question["options"]] == ["Project folder", "Separate folder", "Other"]
        else:
            assert any(b["action"] == "source_handle" and b["subject"] == "defaults" for b in events)
            assert next(c["source"] for c in vision["constraints"] if c["handle"] == "defaults") == "kickoff"


@pytest.mark.fast
@pytest.mark.parametrize("collision", [False, True])
def test_recorded_shelflife_detail_owner_journey(tmp_path, monkeypatch, collision):
    """Continue the integrated ShelfLife checkpoint with the recorded expanded owner.

    Earlier exact-scope replans miss the overlap against the existing owner when DETAIL
    adds the importer. A partial overlap still needs a concrete ownership correction.
    """
    from contextlib import closing
    from dataclasses import replace

    from seldon import decisions, features, frontdoor
    from seldon.store import UnitRow

    monkeypatch.setattr(features, "enabled", {"growAlign", "sizeFirst", "answerRepair", "prompts2",
                                            "judgeEvidence"}.__contains__)
    fixture = json.loads((Path(__file__).with_name("fixtures") / "align-normalisation.json")
                         .read_text(encoding="utf-8"))["ShelfLife"]
    later = False

    def provider(q):
        strict_schema(q["schema"])
        kind = q["kind"]
        if kind == "REVIEW":
            answer = {"verdict": "adequate", "gaps": [], "why": "The plan covers the two checkpoints."}
        elif kind == "DETAIL":
            answer = deepcopy(fixture["nextDetail" if later else "firstDetail"])
            if later and collision:
                answer["units"][0]["scope"] = ["server.py", "import_items.py"]
        else:
            answer = deepcopy(fixture[{"ALIGN": "locked", "ARCHITECT": "outline"}[kind]])
        return json.dumps({"answer": answer})

    def user(item):
        if item["gate"] == "A":
            return {"request": item["request"], "replyId": "shelf-lock",
                    "selections": [{"groupId": "round", "optionId": "done"}]}
        return None if later else approve(item)

    product = tmp_path / "product"
    with closing(Run(product, tmp_path / "workers", fixture["kickoff"], {}, provider, user)) as run:
        for state in ("A", "ARCHITECT", "DETAIL", "REVIEW", "B", "EXECUTE"):
            assert run.step() == state
        store = run.store
        cursor = store.cursor()
        prior = plan.select(store.plan(cursor.revision), "unit")
        owner, = prior.keys() - {"guide"}
        for ref in prior:
            store.unit(ref, UnitRow(wave=1, state="DONE", integrated=cursor.candidate))
        store.cursor(replace(cursor, wave=2, state="DETAIL"))
        later = True
        if collision:
            with pytest.raises(decisions.Waiting):
                run.step()
            failures = store.records("detail_correction")
            correction = next(iter(failures[-1][2]["corrections"].values()))[0]
            assert "own the shared files" in correction["error"]
            assert len(correction["otherSide"]) == 2
            waiting = frontdoor.state(product)["waitingFor"]
            assert "Which assignment should own the files shared" in waiting["subject"]["question"]
            assert not store.records("step_failure")
            return
        assert run.step() == "REVIEW"
        assert run.step() == "EXECUTE"
        current = plan.select(store.plan(store.cursor().revision), "unit")
        assert current.keys() == prior.keys()
        assert set(current[owner]["scope"]) - set(prior[owner]["scope"]) == {"import_items.py"}
        assert set(prior[owner]["requirements"]) < set(current[owner]["requirements"])
        # Later waves reuse B. Their next existing user surface is the check-in.
        store.cursor(replace(store.cursor(), state="CHECK-IN"))
        assert run.step() == "PRODUCT-JUDGE"
        request = frontdoor.state(product)["waitingFor"]
        assert any("import_items.py" in note for note in request["subject"]["normalisations"])
        assert any(b.get("action") == "extended_owner" for _, _, b in store.records("scope_normalisation"))
        assert not store.records("detail_correction") and not store.records("step_failure")

@pytest.mark.fast
@pytest.mark.parametrize("case", ["work", "runtime", "references", "ownership", "subdivision", "missing-scope"])
def test_answer_bookkeeping_journey(tmp_path, monkeypatch, case):
    """The recorded guide check reaches integrated proof and the external contract reaches WORK.

    Legacy journeys cite canonical requirement IDs or internal providers. The reference
    twin adds redundant shapes through A/B. A subdivision must carry its normalised
    links through both DETAIL passes, and missing scope still asks the planner.
    """
    from seldon import features, schema

    monkeypatch.setattr(features, "enabled", {"growAlign", "answerRepair"}.__contains__)
    monkeypatch.setattr(features, "planning_setup", lambda: {})
    fixture = json.loads((Path(__file__).parent / "fixtures/quietbench-b01-bookkeeping.json")
                         .read_text(encoding="utf-8"))
    slot = tmp_path / "bookkeeping"
    run, _ = prepare(slot)
    shown, reasks = [], []
    commitment, quality = "scopeIn:simulate", "quality:clear"

    class Planner(Scripted):
        def __call__(self, q):
            kind, facts = q["kind"], q["facts"]
            if "Exact corrections" in facts:
                reasks.extend(facts["Exact corrections"][0]["Errors"])
            if kind == "ALIGN":
                answer = questions.tiny_round_example()
                story = answer["vision"]["stories"][0]
                story.update(handle="simulate", feature="Reference run", outcome="Read the saved result")
                if case == "references":
                    constraint = {"handle": "constraint:result", "source": "kickoff",
                                  "value": "Read named values", "meaning": "Keep named output"}
                    answer["vision"]["constraints"] = [constraint, deepcopy(constraint)]
                    story["constraints"] = ["result", "result"]
                    answer["questions"] = [{"affects": "simulate", "question": "Keep the saved result?",
                        "options": [{"label": "Keep", "means": "Keep the named output"},
                                    {"label": "Revise", "means": "Change the output"}]}]
            elif kind == "ARCHITECT":
                area = {"handle": "area:quiet-bench" if case == "references" else "quiet-bench",
                        "responsibility": "Deliver the reference run", "scope": ["unit-001/**"],
                        "commitments": [commitment, quality]}
                answer = {"areas": [area], "contracts": [], "decisions": [],
                          "waves": [{"goal": "Observe the reference run", "commitments": [commitment, quality]}]}
                if case == "runtime":
                    answer["contracts"].append(deepcopy(fixture["r378.runtime-verification"]))
                if case == "subdivision":
                    area["scope"].append("unit-002/**")
                if case == "references":
                    area["scope"].append(area["scope"][0])
                    area["commitments"] = ["simulate", "simulate"]
                    answer["areas"].append(deepcopy(area))
                    answer["waves"][0]["commitments"] = ["simulate"]
                    answer["decisions"] = [{"choice": "Keep one writer", "reason": "One scope",
                                            "origin": "architecture", "areas": ["quiet-bench", "quiet-bench"]}]
            elif kind == "DETAIL" and case == "subdivision" and facts["Area"]["handle"] == "quiet-bench":
                result_area = {"handle": "result", "responsibility": "Write the result",
                               "scope": ["unit-001/**"], "commitments": ["simulate"]}
                display_area = {"handle": "display", "responsibility": "Present the result clearly",
                                "scope": ["unit-002/**"], "commitments": ["clear"]}
                answer = {"verdict": "subdivide", "subareas": [result_area, display_area, deepcopy(result_area)],
                          "contracts": []}
            elif kind == "DETAIL":
                answer = json.loads(super().__call__(q))["answer"]
                feature, requirement = answer["edits"]
                feature["targetHandle"] = "feature:reference"
                feature["typedBody"]["commitments"] = list(facts["Accepted commitments"]) if (
                    case == "subdivision") else ["simulate" if case == "references" else commitment]
                requirement["typedBody"]["features"] = [commitment if case == "references" else "feature:reference"]
                if case == "references":
                    answer["edits"].append(deepcopy(feature))
                    answer["units"][0].update(requirements=["feature:reference"], dependencies=[1, 1])
                if case == "ownership":
                    answer["units"][0]["requirements"] = []
                if case == "missing-scope" and "Exact corrections" not in facts:
                    answer["units"][0]["scope"] = ["docs/try-it.md"]
                if case == "runtime":
                    assert fixture["r378.runtime-verification"]["text"] in str(facts["Decisions"])
            elif kind == "DECIDE" and "Displayed groups" in facts:
                answer = {"selections": {group["id"]: {
                    "optionId": "done" if group["id"] == "round" else group["default"],
                    "text": "The selected option and explanation"} for group in facts["Displayed groups"]}}
            else:
                return super().__call__(q)
            result = {"answer": {"why": "Deliver the accepted result", **answer}}
            schema.validate(result, q["schema"])
            return json.dumps(result)

        def invoke(self, q, started):
            if case == "runtime" and q["kind"] == "WORK":
                assert q["facts"]["Runtime assumptions"] == [fixture["r378.runtime-verification"]["text"]]
            raw, thread = super().invoke(q, started)
            if case == "work" and q["kind"] == "WORK" and "Readers" in q["facts"]:
                directory = Path(q["execution"]["cwd"]) / "docs/try-it"
                (directory / "check.py").rename(directory / "walkthrough.py")
                reply = deepcopy(fixture["r114"])
                reply["checks"][0]["command"][0] = sys.executable
                return json.dumps({"answer": reply}), thread
            return raw, thread

    def user(item):
        shown.append(item)
        if "groups" in item["ask"]:
            if case == "references":
                return {"request": item["request"], "replyId": "bookkeeping-words",
                        "words": "Keep the saved result, and alignment is done."}
            return {"request": item["request"], "replyId": "bookkeeping-lock",
                    "selections": [{"groupId": "round", "optionId": "done"}]}
        return approve(item)

    run.provider, run.user = Planner(slot), user
    try:
        while run.store.cursor().state != "EXECUTE":
            run.step()
        if case == "missing-scope":
            assert len(reasks) == 1 and "Send at least one project-relative product path" in reasks[0]
            assert plan.approved(run.store)
            return
        assert not reasks
        request_b = next(item["subject"] for item in shown if item["gate"] == "B")
        if case == "references":
            request_a = next(item["subject"] for item in shown if item["gate"] == "A")
            assert any("identical entries" in note for note in request_a["normalisations"])
            assert any("questions.affects" in note for note in request_a["normalisations"])
            document = request_b["plan"]
            owner = next(u for r, u in plan.select(document, "unit").items() if r != "guide")
            assert owner["dependencies"] == [] and len(owner["requirements"]) == 1
            assert set(document["architecture"]["waves"][0]["commitments"]) == {commitment, quality}
            assert any("redundant dependencies" in note for note in request_b["normalisations"])
            assert any("distinct product paths" in note for note in request_b["normalisations"])
            assert any("redundant text" in b["summary"] for _, _, b in run.store.records("answer_normalisation"))
            return
        if case == "subdivision":
            areas = {a["handle"]: a for a in request_b["plan"]["architecture"]["areas"]}
            assert {ref: a["commitments"] for ref, a in areas.items()} == {
                "result": [commitment], "display": [quality]}
            assert any("identical entries in subareas" in note for note in request_b["normalisations"])
            return
        if case == "ownership":
            unit = next(u for r, u in plan.select(request_b["plan"], "unit").items() if r != "guide")
            assert unit["requirements"] == list(plan.select(request_b["plan"], "requirement"))
            assert any("omitted requirements" in note for note in request_b["normalisations"])
            return
        if case == "runtime":
            assert not plan.select(request_b["plan"], "contract")
            assert any("runtime assumption" in note for note in request_b["normalisations"])
        run.step()
        assert not run.store.records("consumption_failure"), [
            b["reason"] for _, _, b in run.store.records("consumption_failure")]
        finish(run)
        if case == "work":
            document = plan.execution(run.store)
            check, = plan.guide_checks(document).values()
            assert check["requirements"] == list(plan.select(document, "requirement"))
            assert any("walkthrough.py" in run.store.read(ref)["command"][-1]
                       for ref in run.store.cursor().finalObservations)
            request = next(item for item in shown if item["gate"] == "CHECK-IN")
            assert any(commitment in note for note in request["subject"]["normalisations"])
        assert not run.store.records("consumption_failure")
    finally:
        run.close()
