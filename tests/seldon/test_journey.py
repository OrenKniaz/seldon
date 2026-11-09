"""C1/C8/C14/C15: one complete offline journey; inference alone is scripted."""

import hashlib
import json
import sqlite3
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from seldon.loop import Loop
from seldon.providers import Codex

PRODUCT_TEST = '''import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
assert not Path(os.environ["SELDON_EVIDENCE_DIR"]).is_relative_to(root), "Evidence belongs outside product files"
print("Checking personal greeting", flush=True)
result = subprocess.run([sys.executable, "-B", str(root / "greet.py"), "Ada"],
                        capture_output=True, text=True, timeout=5)
if result.returncode:
    print(result.stderr, file=sys.stderr)
    raise SystemExit(result.returncode)
assert result.stdout == "Hello, Ada!\\n", result.stdout
if "--walkthrough" in sys.argv:
    assert "Try it yourself" in (root / "TRY.txt").read_text()
print("PASS: the CLI greets the supplied person")
'''
PRODUCT = '''import sys

print(f"Hello, {sys.argv[1]}!")
'''
GUIDE = '''Try it yourself
==============
From this folder, run the configured Python interpreter with:
  python -B greet.py Ada
Expected output: Hello, Ada!
Pass a different name to receive a greeting for that person.
'''


def scripted_plan():
    """Planner semantics; durable identities and execution bindings belong to CODE."""
    def put(kind, handle, **body):
        return {"kind": kind, "targetHandle": handle, "typedBody": body}
    edits = [
        put("feature", "greeting", story="As a terminal user I receive a personal greeting",
            acceptance="Given Ada, print Hello, Ada! and exit successfully", vision=["vision"]),
        put("requirement", "name", obligation="The CLI greets the supplied name on stdout",
            design="A command taking one name argument", contract="name -> Hello, <name>! newline",
            features=["greeting"]),
        put("test", "personal-greeting", purpose="Observe the promised CLI interaction",
            stage="verification", command=[sys.executable, "-B", "check_greeting.py"],
            requiredEnvironment=[f"Python interpreter: {sys.executable}"],
            expectedEvidence="Actual CLI stdout and successful semantic assertion",
            requirements=["name"]),
        put("test", "walkthrough", purpose="Follow the delivered TRY.txt walkthrough",
            stage="walkthrough", command=[sys.executable, "-B", "check_greeting.py", "--walkthrough"],
            requiredEnvironment=[], expectedEvidence="The delivered guide explains the passing interaction",
            requirements=["name"]),
    ]
    handles = {edit["targetHandle"]: f"new:{i}" for i, edit in enumerate(edits, 1)}
    for edit in edits:
        edit["targetHandle"] = handles[edit["targetHandle"]]
        for relation in ("vision", "features", "requirements", "tests", "dependencies", "beads"):
            if relation in edit["typedBody"]:
                edit["typedBody"][relation] = [handles.get(ref, ref) for ref in edit["typedBody"][relation]]
    return {"edits": edits, "productFiles": ["greet.py"], "testFiles": ["check_greeting.py"]}


PROMPT_SIZES = {}

CASES = ["none", "shell-command", "missing-plan-executable", "missing-observe-executable", "malformed-fixed", "malformed-exhausted",
    "review-gap", "RED-gap", "GREEN-gap", "demo-gap", "uncertain-fact", "uncertain-empty",
    "uncertain-judge", "uncertain-twice", "repair-bound", "GREEN-bound", "scope-change", "revise-A", "revise-B",
    "revise-C", "atomic-invalid"]


def clean_core_golden(root):
    """Same journey driver for the repository's golden entry; no separate proof path."""
    for fault in CASES:
        folder = root / fault
        folder.mkdir(parents=True)
        test_journey_delivers_tested_demonstrated_candidate(folder, fault)


@pytest.mark.fast
@pytest.mark.parametrize("fault", CASES)
def test_journey_delivers_tested_demonstrated_candidate(tmp_path, fault):
    """C14: exercise actual delivery, persisted approval and user-visible repair exits.

    Gaps catch false C/abandoned repair; initial success never changes a plan.
    Uncertainty catches identical re-asks; successful judges never need missing facts.
    Command twins catch shell text accepted as argv and a disappeared executable
    counted as RED; ordinary successful command execution cannot expose either.
    Failed GREEN carries real stderr to judge and repair; review gaps cannot.
    Walkthrough reads TRY.txt only at demo, catching premature GREEN guide judgment.
    DECIDE consumes bound verbatim words; the old fake supplied approval regardless.
    Scripted C vs owner C catches manufactured acceptance; file assertions cannot.
    The running check consumes the evidence path; old checks never exposed an
    evidence directory inside the product. The success timeline catches guide
    work preceding check-in, which a delivered-files assertion alone misses.
    Bound/invalid twins catch endless repair or partial application; valid batches cannot.
    The recorded bead-repair twin catches planner authority over CODE's workflow;
    the successful product-edit twin cannot expose that authority leak.
    Revise/scope twins catch authority transfer without consent; initial yes cannot.
    Malformed twins catch lost retry facts/unbounded retries; valid answers cannot.
    """
    from copy import deepcopy
    calls, purposes, tasks = Counter(), Counter(), Counter()
    requests, worker_roots, malformed_questions, timeline = [], [], [], []
    uncertainty_facts = []
    gaps = []
    disposable = tmp_path / "vanishing-python.exe"
    if fault == "missing-observe-executable":
        shutil.copyfile(sys.executable, disposable)

    def provider(question):
        kind, facts, context = question["kind"], question["facts"], question["context"]
        label = kind + (":" + context["purpose"] if kind == "JUDGE" else "")
        PROMPT_SIZES[label] = max(PROMPT_SIZES.get(label, 0), len(json.dumps(question, separators=(",", ":"), ensure_ascii=False).encode()))
        if kind in ("JUDGE", "REPAIR"):
            observations = [f["boundedContent"] for f in facts.values()
                            if isinstance(f["boundedContent"], dict) and "outputRefs" in f["boundedContent"]]
            for observation in observations:
                assert observation["command"] and observation["cwd"]
                assert "exitCode" in observation and "stdout" in observation and "stderr" in observation
            if fault == "GREEN-gap" and (kind == "REPAIR" or context.get("purpose") == "GREEN") and tasks["build"] == 1:
                assert any(o["exitCode"] != 0 and "AssertionError" in o["stderr"] and "Wrong greeting" in o["stderr"]
                           and "Checking personal greeting" in o["stdout"] for o in observations)

        if "exactFix" not in context:
            calls[kind] += 1
        why = "The supplied facts support this semantic decision"
        answer = {"why": why, "citations": list(facts)}
        if kind == "ALIGN":
            answer.update(size="tiny", understanding="A personal greeting command",
                users=["Terminal users"], scopeIn=["Greet a supplied name"], scopeOut=["Interactive UI"],
                improvements=["Personalize rather than a fixed greeting"],
                quality=[{"criterion": "Clear stdout and a usable guide",
                          "verification": "Execute the CLI and follow the guide"}], unknowns=[])
            if fault.startswith("malformed"):
                malformed_questions.append(question)
                if fault == "malformed-exhausted" or len(malformed_questions) <= 2:
                    answer.update(size="invalid", users=False, unexpected=True)
        elif kind == "DECIDE":
            request = next(f["boundedContent"] for f in facts.values()
                           if isinstance(f["boundedContent"], dict) and "gate" in f["boundedContent"])
            revise = fault == "revise-" + request["gate"] and not calls["REPAIR"]
            words = next(f["boundedContent"] for f in facts.values()
                         if isinstance(f["boundedContent"], dict) and "words" in f["boundedContent"])
            assert words["words"] == ("Please revise the guidance" if revise else "yes")
            assert words["request"] in facts and facts[words["request"]]["boundedContent"] == request
            assert context["gate"] == request["gate"] and context["scriptedUser"] == (fault != "revise-C")
            answer.update(choice="revise" if revise else "approve",
                          why="Make the guide more useful" if revise else "The supplied yes approves this subject")
        elif kind == "PLAN":
            answer.update(scripted_plan())
            if fault in ("shell-command", "missing-plan-executable"):
                if "exactFix" not in context:
                    answer["edits"][2]["typedBody"]["command"] = ([f"& '{sys.executable}' '-B' 'check_greeting.py'"]
                        if fault == "shell-command" else [str(tmp_path / "absent.exe"), "check_greeting.py"])
                else:
                    rejected = [f["boundedContent"] for f in facts.values() if "errors" in f["boundedContent"]]
                    assert any("command[0]" in " ".join(r["errors"]) and "each argument" in " ".join(r["errors"]) for r in rejected)
                    if fault == "shell-command":
                        from seldon.store import encode
                        assert encode([sys.executable, "-B", "check_greeting.py"]) in " ".join(rejected[0]["errors"])
            if fault == "missing-observe-executable":
                answer["edits"][2]["typedBody"]["command"][0] = str(disposable)
        elif kind == "REVIEW":
            answer.update(verdict="adequate", gaps=[])
            if fault in ("review-gap", "repair-bound", "atomic-invalid") and (calls[kind] == 1 or fault == "repair-bound"):
                answer.update(verdict="gap", gaps=[{"target": "n2", "correction": "Clarify the promised input behavior"}])
                gaps.append("Clarify the promised input behavior")
            if fault in ("uncertain-fact", "uncertain-empty", "uncertain-twice") and (calls[kind] == 1 or fault == "uncertain-twice"):
                uncertainty_facts.append(deepcopy(facts))
                answer = {**answer, "verdict": "uncertain", "neededFacts": [{
                    "target": "setup:verification" if fault != "uncertain-empty" else next(iter(facts)),
                    "question": "Which verification tools are installed?"}]}
                answer.pop("gaps")
            elif fault == "uncertain-fact":
                uncertainty_facts.append(deepcopy(facts))
                assert facts["setup:verification"]["boundedContent"] == "Python subprocess verification"
        elif kind == "REPAIR":
            handles = next((f["boundedContent"]["document"]["nodes"] for f in facts.values()
                            if "document" in f["boundedContent"]), None)
            if handles is None:
                handles = {"vision": {"kind": "vision", "body": next(f["boundedContent"]["answer"] for f in facts.values() if "answer" in f["boundedContent"])}}
                handles["vision"]["body"] = {k: v for k, v in handles["vision"]["body"].items() if k not in ("why", "citations")}
            if fault == "review-gap" and "exactFix" not in context:
                # The real run tried moving guide work into build during REPAIR.
                answer["edits"] = [{"kind": "bead", "targetHandle": "n6", "typedBody": {
                    "task": "build", "instructions": "Write TRY.txt before GREEN", "ownedPaths": ["greet.py", "TRY.txt"],
                    "dependencies": [], "requirements": ["n2"], "tests": ["n3", "n4"]}}]
                return json.dumps({"answer": answer})
            if fault in ("GREEN-gap", "GREEN-bound", "demo-gap", "revise-C"):
                answer["edits"] = []  # Product is correct; CODE reschedules the failed implementation/guide.
            else:
                target = "vision" if fault in ("revise-A", "scope-change") else "n3" if fault == "RED-gap" else "n2"
                body = deepcopy(handles[target]["body"])
                field = {"vision": "understanding", "requirement": "obligation", "test": "purpose"}[handles[target]["kind"]]
                body[field] += f"; clarification {calls[kind]}"
                answer["edits"] = [{"kind": handles[target]["kind"], "targetHandle": target, "typedBody": body}]
                if fault == "atomic-invalid":
                    answer["edits"].append({"op": "Retire", "targetHandle": "n1", "replacementHandles": ["n2"]})
        elif kind == "IMPACT":
            answer.update(verdict="scope_change" if fault == "scope-change" else "within_scope")
        elif kind == "WORK":
            task = context["task"]
            timeline.append(task)
            tasks[task] += 1
            assignment = next(f["boundedContent"] for f in facts.values()
                              if isinstance(f["boundedContent"], dict) and "worktree" in f["boundedContent"])
            root = Path(assignment["worktree"])
            worker_roots.append(root)
            content = {"tests": {"check_greeting.py": PRODUCT_TEST},
                       "build": {"greet.py": PRODUCT}, "guide": {"TRY.txt": GUIDE}}[task]
            if calls["REPAIR"]:
                if task == "tests":
                    content = {"check_greeting.py": PRODUCT_TEST + '\nextra = subprocess.run([sys.executable, "-B", str(root / "greet.py"), "Lin"], capture_output=True, text=True, timeout=5)\nassert extra.returncode == 0 and extra.stdout == "Hello, Lin!\\n"\nusage = subprocess.run([sys.executable, "-B", str(root / "greet.py")], capture_output=True, text=True, timeout=5)\nassert usage.returncode == 2 and "Usage:" in usage.stdout\n'}
                elif task == "build":
                    content = {"greet.py": 'import sys\nif len(sys.argv) < 2:\n    print("Usage: greet.py NAME")\n    raise SystemExit(2)\nprint(f"Hello, {sys.argv[1]}!")\n' + f"# repair {calls['REPAIR']}\n"}
                elif task == "guide":
                    content = {"TRY.txt": GUIDE + "A name is required; omit it to see usage.\n"}
            if fault == "GREEN-gap" and task == "build" and not calls["REPAIR"]:
                content = {"greet.py": 'print("Wrong greeting")\n'}
            if fault == "missing-observe-executable" and task == "tests":
                disposable.unlink()
            for name, data in content.items():
                (root / name).write_text(data, encoding="utf-8")
            answer.update(outcome="done", summary="Assigned files written", artifactPaths=list(content), discoveries=[])
        elif kind == "JUDGE":
            purpose = context["purpose"]
            purposes[purpose] += 1
            answer.update(verdict="satisfied", gaps=[], assessments=[
                {"target": target, "satisfied": True, "why": "Actual interaction establishes the obligation"}
                for target in context["assessments"]])
            nodes = next(f["boundedContent"]["document"]["nodes"] for f in facts.values()
                         if "document" in f["boundedContent"])
            if purpose == "GREEN" and any(n["kind"] == "test" and n["body"]["stage"] == "walkthrough" for n in nodes.values()):
                answer.update(verdict="gap", gaps=[{"target": next(iter(nodes)), "correction": "TRY.txt does not exist yet"}])
            if purpose == "demo" and not any(isinstance(f["boundedContent"], dict) and
                    f["boundedContent"].get("purpose") == "GREEN" and f["boundedContent"].get("satisfied") for f in facts.values()):
                answer.update(verdict="gap", gaps=[{"target": "traceComplete", "correction": "Supply the retained verification judgment"}])
            if fault == "uncertain-judge" and purpose == "RED":
                if purposes[purpose] == 1:
                    answer = {"verdict": "uncertain", "why": why, "citations": list(facts),
                        "neededFacts": [{"target": "setup:verification", "question": "Which verification tools are installed?"}]}
                else:
                    assert facts["setup:verification"]["boundedContent"] == "Python subprocess verification"
            if (fault in (purpose + "-gap", "scope-change") and purposes[purpose] == 1 and (
                    fault != "scope-change" or purpose == "GREEN")) or (fault == "GREEN-bound" and purpose == "GREEN"):
                correction = "Supply meaningful extra input coverage" if purpose == "RED" else "Improve usage guidance"
                answer.update(verdict="gap", gaps=[{"target": context["assessments"][0], "correction": correction}])
                gaps.append(correction)
        return json.dumps({"answer": answer})

    def user(request):
        requests.append(request)
        timeline.append(request["gate"])
        return "Please revise the guidance" if fault == "revise-" + request["gate"] and not calls["REPAIR"] else "yes"

    setup = {"providers": {role: f"scripted:{role}" for role in ("planner", "reviewer", "worker", "judge")},
             "interpreter": sys.executable, "userOrigin": "owner conversation" if fault == "revise-C" else "scripted journey conversation",
             "scriptedUser": fault != "revise-C"}
    recorder = Codex(tmp_path / "calls", {**setup, "codexCommand": ["offline-question-recorder"]})

    def recorded_provider(question):
        folder, _ = recorder.prepare(question)  # The real question/sidecar producer, without a native launch.
        answer = provider(question)
        (folder / "answer.txt").write_text(answer, encoding="utf-8")
        return answer

    if fault in ("uncertain-fact", "uncertain-judge", "uncertain-twice"):
        notes = tmp_path / "verification-facts.txt"
        notes.write_text("Python subprocess verification", encoding="utf-8")
        setup["factFiles"] = {"setup:verification": str(notes)}
    loop = Loop(tmp_path / "product", tmp_path / "workers", "Make a command that greets a supplied name",
                setup, recorded_provider, user)
    cursor = loop.run()
    loop.store.db.close()
    with sqlite3.connect(loop.project / ".seldon" / "state.sqlite3") as db:
        rows = [(f"r{i}", kind, json.loads(body)) for i, kind, body in
                db.execute("SELECT id,kind,body FROM record ORDER BY id")]
        persisted = json.loads(db.execute("SELECT cursor FROM run").fetchone()[0])
        revisions = [json.loads(row[0]) for row in db.execute("SELECT document FROM plan_revision ORDER BY id")]
    records = {ref: body for ref, _, body in rows}
    observations = [(ref, body) for ref, kind, body in rows if kind == "observation"]
    if fault == "missing-observe-executable":
        assert cursor["state"] == "INCOMPLETE" and not calls["JUDGE"] and not calls["REPAIR"]
        outcome = next(body for _, kind, body in rows if kind == "outcome")
        assert "SELDON_COMMAND_LAUNCH_FAILED" in outcome["reason"] and disposable.name in outcome["reason"]
        assert not outcome["accepted"] and not outcome["lastProvenResult"]
        return
    if malformed_questions:
        assert len(malformed_questions) == 3
        original = malformed_questions[0]["facts"]
        for index, question in enumerate(malformed_questions[1:], 1):
            assert all(question["facts"][key] == value for key, value in original.items())
            rejected = [f["boundedContent"] for ref, f in question["facts"].items() if ref not in original]
            assert len(rejected) == index
            assert all(all(field in " ".join(previous["errors"]) for field in ("size", "users", "unexpected"))
                       for previous in rejected)
    incomplete = fault in ("malformed-exhausted", "uncertain-empty", "uncertain-twice", "repair-bound", "GREEN-bound", "atomic-invalid")
    outcome = next(body for _, kind, body in reversed(rows) if kind == "outcome")
    assert cursor["state"] == persisted["state"] == ("INCOMPLETE" if incomplete else "ACCEPTED" if fault == "revise-C" else "SCRIPTED_C"), outcome
    if incomplete:
        expected = {"malformed-exhausted": "two exact-fix retries", "uncertain-empty": "Which verification tools", "uncertain-twice": "Which verification tools",
                    "repair-bound": "Three repairs exhausted", "GREEN-bound": "Three repairs exhausted", "atomic-invalid": "wrong trace target kind"}[fault]
        assert expected.lower() in outcome["reason"].lower() and outcome["nextAction"]
        assert not outcome["accepted"] and not outcome["verification_complete"]
        if fault == "GREEN-bound":
            assert len(worker_roots) == len(observations) == 5 and calls["REPAIR"] == 3
            assert "Improve usage guidance" in outcome["reason"] and [r["gate"] for r in requests] == ["A", "B"]
        else:
            assert not worker_roots
        if fault == "repair-bound":
            assert calls["REPAIR"] == 3 and "Clarify the promised input behavior" in outcome["reason"]
        if fault == "atomic-invalid":
            assert len(revisions) == 1 and "n6" in revisions[0]["nodes"]
            assert "wrong trace target kind" in outcome["reason"]
        if fault in ("uncertain-empty", "uncertain-twice"):
            assert calls["REVIEW"] == (1 if fault == "uncertain-empty" else 2)
        return
    assert outcome["accepted"] == (fault == "revise-C") and outcome["verification_complete"]
    if fault != "revise-C":
        assert outcome["reason"] == "scripted C, not owner acceptance"
    if fault == "review-gap":
        rejected = [b for _, k, b in rows if k == "provider_reply" and b["errors"]]
        assert len(rejected) == 1 and '"bead"' in rejected[0]["rawAnswer"]
        assert len(revisions) == 2  # The rejected workflow edit published no revision.
    gates = [r["gate"] for r in requests]
    assert calls["DECIDE"] == len(gates)  # One interpretation per actual decision, including fresh revisions.
    if fault == "none":
        assert timeline == ["A", "B", "tests", "build", "check-in", "guide", "C"]
        assert not calls["REPAIR"] and not any(b["errors"] for _, k, b in rows if k == "provider_reply")
        from tests.seldon.run_report import report
        text, summary = report(tmp_path)
        assert "RED FLAGS: 0 events" in text and summary[3] == "SCRIPTED_C"
        assert "scripted C, not owner acceptance" in text
        assert "Test/judge disagreements: none" in text  # Observed RED is expected to fail.
        assert all(f"DECIDE({gate}): 1 calls" in text for gate in ("A", "B", "check-in", "C"))
    assert gates[0] == "A" and gates[-1] == "C"
    assert gates.count("B") == (2 if fault == "revise-B" else 1)
    assert gates.count("scope") == (1 if fault == "scope-change" else 0)
    assert gates.count("C") == (2 if fault == "revise-C" else 1)
    assert "check-in" in gates
    if fault == "uncertain-fact":
        assert len(uncertainty_facts) == 2 and uncertainty_facts[0] != uncertainty_facts[1]
    if calls["REPAIR"]:
        assert len(revisions) == 2
        proposal = next(body for _, kind, body in rows if kind == "repair_proposal")
        assert records[proposal["cause"]] and proposal["preimage"] == (None if fault == "revise-A" else 1)
        assert calls["REVIEW"] == (1 if fault in ("revise-A", "GREEN-gap", "demo-gap", "revise-C") else 2)
        if fault in ("GREEN-gap", "demo-gap", "revise-C"):
            assert not calls["IMPACT"]  # Identity preserves scope and the existing plan review.
    if fault in ("GREEN-gap", "scope-change", "demo-gap", "revise-C", "RED-gap"):
        assert any(kind == "authorization" for _, kind, _ in rows)
    if fault in ("GREEN-gap", "GREEN-bound", "scope-change"):
        assert purposes["RED"] == (2 if fault == "scope-change" else 1) and purposes["GREEN"] == 2
        assert "n6" in revisions[-1]["nodes"]  # CODE retains the sole build writer
        usage = subprocess.run([sys.executable, "-B", "greet.py"], cwd=loop.project, capture_output=True, text=True, timeout=5)
        assert usage.returncode == 2 and "Usage:" in usage.stdout
    if fault == "RED-gap":
        assert purposes["RED"] == 2
    red = observations[0][1]
    assert red["executionStarted"] and red["exitCode"] != 0 and not red["timedOut"]
    assert "greet.py" not in records[red["candidate"]]["files"]
    assert all(root.is_dir() and (root / ".git").is_file() for root in worker_roots)
    assert "Try it yourself" in (loop.project / "TRY.txt").read_text()
    c_subject = requests[-1]["subject"]
    assert c_subject["candidate"] == persisted["candidate"]
    assert records[c_subject["candidate"]]["files"] == {
        name: hashlib.sha256((loop.project / name).read_bytes()).hexdigest()
        for name in ("check_greeting.py", "greet.py", "TRY.txt")}
    assert all(records[ref]["satisfied"] for ref in c_subject["judgments"])
    if fault in ("GREEN-gap", "GREEN-bound", "scope-change"):
        assert [records[ref]["purpose"] for ref in c_subject["evidence"]] == ["RED", "GREEN", "demo"]
        assert len(observations) == (5 if fault == "scope-change" else 4)  # Original observations remain immutable.
    result = subprocess.run([sys.executable, "-B", "greet.py", "Grace"], cwd=loop.project,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and result.stdout == "Hello, Grace!\n"
