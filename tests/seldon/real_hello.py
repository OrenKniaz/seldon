"""C1/C20/C26: lead-operated real-provider development run, never owner acceptance."""

import argparse
import json
import os
import sys
import hashlib
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from seldon.run import Run
from seldon import features, questions, schema
from seldon.decisions import Waiting
from seldon.frontdoor import state
from seldon.providers import Provider, ProviderFailure, setup_facts

DREAM = ('Build a tiny web page with one Play button. When I click Play, the page shows '
         'the text "Hello, world!". A single static HTML file is enough. '
         'No server, no accounts, no dependencies.')


def scripted_answer(request, store, script):
    # Count canonical answers so restarting the rig does not replay check-in 1.
    requests = {r: b for r, _, b in store.records("decision_request")}
    previous = {b["request"]: b for _, _, b in store.records("user_words")}
    if "words" in (saved := previous.get(request["request"], {})):
        return saved["words"]
    prior = sum(requests[ref]["gate"] == request["gate"] for ref in previous)
    entries = script.get(request["gate"], [])
    words = entries[prior] if prior < len(entries) else "first"
    if words == "first":
        options = request["ask"]["options"]
        option = options[0]
        words = option["label"] if isinstance(option, dict) else option
    return words


def transport_answer(request):
    return [{"groupId": group["id"], "optionId": "done" if group["id"] == "round" else group["default"]}
            for group in request["ask"]["groups"]]


def persona_answer(display, persona, earlier, provider):
    groups = display["ask"].get("groups")
    prompt = ("Play this simulated owner. Judge only the proposal actually displayed. Prefer its "
              "recommendations when they fit and choose a better option when they do not. If the "
              "proposal misses something consequential, request it in your own words as Other. "
              "Carry forward earlier requests without volunteering them a second time. Finish explicitly "
              "only when the displayed product covers your goal and preference and respects your exclusion.")
    if groups:
        def selection(group):
            ids = [o["id"] for o in group["options"] if o["id"] != "other"]
            group_id = schema.choice(group["id"])
            return {"anyOf": [schema.obj(groupId=group_id, optionId=schema.choice(*ids)),
                              schema.obj(groupId=group_id, optionId=schema.choice("other"), text=schema.TEXT)]}

        decision = next(g for g in groups if g["id"] == "round")
        product = [selection(g) for g in groups if g["id"] != "round"]
        fields = {"decision": selection(decision)}
        example = {"decision": {"groupId": "round", "optionId": "done"}}
        if product:
            fields["choices"] = schema.array({"anyOf": product})
            example["choices"] = []
        prompt += (" Choose the round decision explicitly. If changing a default or adding Other text, "
                   "request revision and wait to see it before selecting done. Use Other in the round "
                   "decision when your request does not fit any product group. Send each group at most once.")
    else:
        fields = {"words": schema.TEXT}
        example = {"words": "Please let me reopen an unfinished repair at the next shift."}
        prompt += (" Reply with the exact menu label when choosing an option, including the approval label "
                   "when finished. For Other, send only your own words.")
    question = {"kind": "PERSONA", "schema": schema.obj(answer=schema.obj(**fields)), "prompt": prompt,
                "examples": [{"answer": example}], "facts": {"Private persona": persona,
                "Displayed request": display, "Earlier replies": earlier}}
    answer = questions.ask(provider, question, lambda _: None)
    if groups:
        return [*answer.get("choices", []), answer["decision"]]
    return answer["words"]


def save_json(path, value):
    staging = path.with_suffix(".tmp")
    staging.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    staging.replace(path)


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slot", type=Path, help="new slot folder under C:/seldon/tests/m1")
    parser.add_argument("--dry-run", action="store_true", help="prepare ALIGN; never launch a provider")
    parser.add_argument("--dream-file", type=Path, help="dream text instead of hello-world")
    parser.add_argument("--brief", type=Path, help="ladder JSON brief, including its scripted user")
    parser.add_argument("--reopen", action="store_true", help="resume this slot's canonical run")
    parser.add_argument("--runs", type=Path, help="override RUNS.md for an offline diagnostic")
    args = parser.parse_args()
    slot = args.slot.resolve()
    if not slot.is_relative_to(Path("C:/seldon/tests/m1").resolve()):
        parser.error("slot must be under C:/seldon/tests/m1")
    if not args.reopen:
        slot.mkdir(parents=True)
    try:
        brief = json.loads(args.brief.read_text("utf-8")) if args.brief else {}
        dream = brief.get("message") or (args.dream_file.read_text("utf-8").strip() if args.dream_file else DREAM)
        if brief.get("profile"):
            dream += "\n\nAudience and preferences:\n" + json.dumps(brief["profile"], ensure_ascii=False)
        if brief.get("oracleInterface"):
            dream += "\n\nShared black-box interface:\n" + json.dumps(brief["oracleInterface"], ensure_ascii=False)
        return run(slot, args.dry_run, dream, args.reopen, brief.get("scriptedUser", {}))
    finally:
        # A diagnostic must never replace the delivery result or its exception.
        try:
            from run_report import main as report_run
            report_run([str(slot), *(["--runs", str(args.runs)] if args.runs else [])])
        except Exception as error:
            print(f"Dev-run report failed: {error}", file=sys.stderr)


def run(slot, dry_run, dream=DREAM, reopen=False, script=None):
    temp = slot / "temp"
    temp.mkdir(exist_ok=reopen)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(temp)
    evidence = slot / "browser-evidence"
    evidence.mkdir(exist_ok=reopen)
    os.environ["SELDON_EVIDENCE_DIR"] = str(evidence)
    setup = json.loads((slot / "setup.json").read_text("utf-8")) if reopen else setup_facts()
    source = Path(__file__).resolve().parents[2]
    if not reopen:
        setup["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source,
                                                  text=True).strip()
        setup["runtimeSource"] = hashlib.sha256(b"\0".join(
            p.name.encode() + b"\0" + p.read_bytes() for p in sorted((source / "src/seldon").glob("*.py"))
        )).hexdigest()
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "src/seldon"], cwd=source).returncode:
            setup["commit"] += "+worktree:" + setup["runtimeSource"][:12]
        setup["questionContract"] = hashlib.sha256(b"".join((source / "src/seldon" / name).read_bytes()
            for name in ("questions.py", "schema.py"))).hexdigest()
        setup["evidenceDirectory"] = str(evidence)
        if os.environ.get("SELDON_FIRST_EFFORT"):  # cohort A/B arm for first product WORK effort
            setup["providers"]["worker_first_effort"] = os.environ["SELDON_FIRST_EFFORT"]
        # Cohort model arm (Oren 11-15: no top model in cohorts; small models show where Seldon helps).
        for role, config in setup["providers"].items():
            if isinstance(config, dict) and "model" in config:
                config["model"] = os.environ.get("SELDON_ROLE_MODEL", config["model"])
                if role != "decider":
                    config["effort"] = os.environ.get("SELDON_ROLE_EFFORT", config["effort"])
        (slot / "setup.json").write_text(json.dumps(setup, indent=2), encoding="utf-8")
    provider = Provider(slot / "calls", setup)

    script = script or json.loads(os.environ.get("SELDON_USER_SCRIPT", "{}"))
    private = slot / "simulated-user.json"
    if reopen and private.is_file():
        script = json.loads(private.read_text("utf-8"))
    elif not reopen and script:
        save_json(private, script)
    persona = script.get("persona") if features.enabled("growPersona") else None

    def yes(request):
        print(json.dumps({"scriptedDecision": request}, ensure_ascii=False), flush=True)
        if request["gate"] == "B" and os.environ.get("SELDON_STOP_AT_B") == "1":
            raise Waiting()
        reply = slot / f"simulated-{request['request']}.json"
        grouped = "groups" in request["ask"]
        if request["gate"] == "A" and (persona or grouped or reply.is_file()):
            mode = "scripted persona" if persona else "scripted transport control"
            earlier = [body for _, _, body in loop.store.records("user_words")]
            saved = json.loads(reply.read_text("utf-8")) if reply.is_file() else None
            delivered = {body["replyId"] for body in earlier
                         if body["request"] == request["request"] and body.get("replyId")}
            if saved and (not grouped or saved["words"]["replyId"] not in delivered):
                words, mode = saved["words"], saved["mode"]
            else:
                if persona:
                    display = state(str(loop.project))["waitingFor"]
                    history = [body.get("selections", body.get("words")) for body in earlier]
                    try:
                        judge = Provider(slot / "persona-calls", setup)
                        words = persona_answer(display, persona, history, judge)
                    except (ProviderFailure, questions.AnswerRejected) as error:
                        print(f"Scripted persona could not answer {request['request']}: {error}",
                              file=sys.stderr)
                        raise Waiting() from error
                else:
                    words = transport_answer(request)
                if grouped:
                    reply_id = f"simulated-{request['request']}"
                    if delivered:
                        reply_id += f"-{len(delivered) + 1}"
                    words = {"request": request["request"], "replyId": reply_id, "selections": words}
                save_json(reply, {"words": words, "mode": mode})
        else:
            words = scripted_answer(request, loop.store, script)
            mode = "scripted menu"
        print(json.dumps({"simUserAnswer": words, "gate": request["gate"], "mode": mode},
                         ensure_ascii=False), flush=True)
        return words

    loop = (Run.reopen(slot / "product", provider, yes) if reopen else
            Run(slot / "product", slot / "workers", dream, setup, provider, yes))
    try:
        if dry_run:
            question = questions.build("ALIGN", {"Dream": dream, "Runtime": setup})
            folder, invocation = provider.prepare(question)
            print(invocation["commandLine"] + ' < "' + str(folder / "prompt.txt") + '"')
            print(json.dumps(question["schema"], indent=2))
            return 0
        cursor = loop.run()
        # A question persisted mid-step ends run() WAITING before the user callback; resume so the
        # (simulated) owner sees it, as the front door would. Stop when the owner gives no new words.
        while cursor.decision:
            before = cursor.decision
            cursor = loop.run()
            if cursor.decision == before:
                break
        # A run never fails (R15): it ends with an outcome or waits on one question; print whichever it is.
        outcomes = loop.store.records("outcome")
        if cursor.state == "B" and os.environ.get("SELDON_STOP_AT_B") == "1":
            from seldon import plan
            document = loop.store.plan(cursor.revision)
            attempts = [b["startedAt"] for _, _, b in loop.store.records("attempt")]
            ends = [b["finishedAt"] for _, _, b in loop.store.records("attempt_end")]
            print(json.dumps({"planSummary": {"units": plan.select(document, "unit"),
                "checkCount": len(plan.select(document, "check")), "reviewRounds": sum(
                    b["answer"]["verdict"] == "gap" for _, _, b in loop.store.records("review_verdict")),
                "planningMinutes": (max(ends) - min(attempts)) / 60,
                "ownerQuestions": [b for _, _, b in loop.store.records("decision_request")
                                   if b["gate"] not in ("A", "B", "CHECK-IN", "C")]}},
                ensure_ascii=False), flush=True)
            print(json.dumps({"state": "B", "scriptedUser": True, "ownerAccepted": False,
                              "outcome": None}), flush=True)
            return 0
        status = state(str(loop.project))
        print(json.dumps({"state": "WAITING" if cursor.decision else cursor.state, "scriptedUser": True,
                          "ownerAccepted": False, "outcome": outcomes[-1][2] if outcomes else None,
                          "waitingFor": status.get("waitingFor"), "status": status.get("status"),
                          "reason": status.get("reason"), "next": status.get("next")}, indent=2), flush=True)
        return 0
    finally:
        loop.close()


if __name__ == "__main__":
    raise SystemExit(main())
