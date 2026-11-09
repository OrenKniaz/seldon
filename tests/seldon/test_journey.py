"""C2/C8/C14–C16/C26: offline journeys; only provider semantics are scripted."""

import hashlib
import json
import subprocess
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from seldon.loop import Loop
from seldon.providers import Codex, ROLES

PRODUCT_TEST = r'''import json, os, subprocess, sys
from pathlib import Path
root = Path(__file__).resolve().parent
folder = Path(os.environ["SELDON_EVIDENCE_DIR"])
assert not folder.is_relative_to(root)
report = {"toolReady": False, "checks": []}
try:
    tool = root / "missing-tool.exe" if "--broken-tool" in sys.argv else sys.executable
    subprocess.run([str(tool), "-B", "-c", "pass"], check=True, capture_output=True)
    report["toolReady"] = True
    if "--skipped" in sys.argv:
        report["checks"].append({"name": "personal greeting", "status": "skipped"})
    else:
        for name in (["Ada", "Grace"] if "--more" in sys.argv else ["Ada"]):
            print("Checking personal greeting", name, flush=True)
            result = subprocess.run([sys.executable, "-B", str(root / "greet.py"), name], capture_output=True, text=True)
            ok = result.returncode == 0 and result.stdout == f"Hello, {name}!\n"
            report["checks"].append({"name": "greet " + name, "status": "passed" if ok else "failed"})
            if not ok:
                print(repr(result.stdout) + result.stderr, file=sys.stderr)
        if "--usage" in sys.argv:
            result = subprocess.run([sys.executable, "-B", str(root / "greet.py")], capture_output=True, text=True)
            ok = result.returncode == 2 and "Usage:" in result.stdout
            report["checks"].append({"name": "missing name usage", "status": "passed" if ok else "failed"})
            if not ok:
                print(result.stderr, file=sys.stderr)
except OSError as error:
    print(error, file=sys.stderr)
finally:
    (folder / "execution.json").write_text(json.dumps(report))
raise SystemExit(0 if report["toolReady"] and all(c["status"] != "failed" for c in report["checks"]) else 1)
'''
PRODUCT = 'import sys\nprint(f"Hello, {sys.argv[1]}!")\n'
GUIDE = "Try it yourself\nRun python -B greet.py Ada; expect Hello, Ada!\nTry a different name too.\n"
GUIDE_TEST = r'''import json, os
from pathlib import Path
ok = "python -B greet.py" in Path("TRY.txt").read_text()
Path(os.environ["SELDON_EVIDENCE_DIR"], "execution.json").write_text(json.dumps({"toolReady": True,
    "checks": [{"name": "guide instructions", "status": "passed" if ok else "failed"}]}))
raise SystemExit(0 if ok else 1)
'''


def scripted_plan():
    def put(kind, slot, **body):
        return {"kind": kind, "targetHandle": "new:" + str(slot), "typedBody": body}
    return {"edits": [
        put("feature", 1, story="Receive a personal greeting", acceptance="A supplied name is greeted", vision=["vision"]),
        put("requirement", 2, obligation="Greet the supplied name on stdout", design="One name argument",
            contract="name -> Hello, <name>! newline", features=["new:1"]),
        put("test", 3, purpose="Exercise a supplied name", stage="verification",
            command=[sys.executable, "-B", "check_greeting.py"], requiredEnvironment=["Python"],
            expectedEvidence="Actual CLI output", requirements=["new:2"])],
        "productFiles": ["greet.py"], "testFiles": ["check_greeting.py"]}


CASES = ["none", "native-transport", "owner", "free-form", "stale-decision", "revise-B", "shell-command", "missing-plan-tool",
         "product-executable", "broken-tool", "all-skipped", "malformed-fixed", "malformed-exhausted",
         "review-gap", "plan-new-test", "repair-bound", "atomic-invalid", "evidence-gap", "uncertain-empty", "uncertain-twice",
         "local-fault", "implementation-fault", "guide-gap", "RED-gap", "scope-change", "discovery"]
PROMPT_SIZES = {}


def clean_core_golden(root):
    """The repository golden entry runs this same driver, with no alternate proof."""
    for fault in CASES:
        folder = root / fault
        folder.mkdir(parents=True)
        test_journey_delivers_tested_demonstrated_candidate(folder, fault)


@pytest.mark.fast
@pytest.mark.parametrize("fault", CASES)
def test_journey_delivers_tested_demonstrated_candidate(tmp_path, fault):
    """Exercise real storage, processes, ownership and integration through C.

    Guide-only repair catches lost unchanged-input credit; initial success has one candidate.
    Broken-tool/skipped twins catch false RED; missing-product execution cannot.
    Evidence gaps catch forced edits; local/integrated semantic faults catch misrouted
    failures that successful workers cannot expose. Extra-name execution discovers a
    hardcoded implementation despite green planned checks. Bound owner/scripted replies
    catch needless interpretation and false owner acceptance; free words need semantics.
    Product-executable and malformed argv catch tool/output confusion at real planning.
    Review/scope/invalid twins protect atomic authorized edits; success cannot expose these.
    Added-test repair catches stranded ownership; editing an existing check cannot.
    Native transport catches lost stdin/output/provenance; sidecar-only recording cannot.
    Discovery catches lost upward information; a worker without discoveries cannot.
    """
    calls, tasks, purposes = Counter(), Counter(), Counter()
    requests, timeline, worker_roots, malformed = [], [], [], []
    local_failures, writer_failures, demo_reuse = [], [], []
    gap_sent = False
    provider_configs = {role: dict(config) for role, config in ROLES.items()}
    setup = {"providers": provider_configs, "interpreter": sys.executable,
             "userOrigin": "owner conversation" if fault == "owner" else "scripted journey",
             "scriptedUser": fault != "owner"}
    if fault in ("evidence-gap", "uncertain-twice"):
        note = tmp_path / "verification.txt"
        note.write_text("Python subprocess verification is available", encoding="utf-8")
        setup["factFiles"] = {"setup:verification": str(note)}

    def provider(question):
        nonlocal gap_sent
        kind, facts, context = question["kind"], question["facts"], question["context"]
        bodies = [f["boundedContent"] for f in facts.values() if isinstance(f["boundedContent"], dict)]
        PROMPT_SIZES[kind] = max(PROMPT_SIZES.get(kind, 0), len(json.dumps(question).encode()))
        if "exactFix" not in context:
            calls[kind] += 1
        answer = {"why": "The supplied facts support this semantic decision", "citations": list(facts)}
        if kind == "ALIGN":
            assert next(b for b in bodies if "words" in b)["capabilities"]["interpreter"] == sys.executable
            answer.update(size="tiny", understanding="A personal greeting command", users=["Terminal users"],
                scopeIn=["Greet a supplied name"], scopeOut=["UI"], improvements=[],
                quality=[{"criterion": "Clear output and usable guide", "verification": "Run the CLI and follow its guide"}], unknowns=[])
            if fault.startswith("malformed"):
                malformed.append(question)
                if fault == "malformed-exhausted" or len(malformed) <= 2:
                    answer.update(size="invalid", users=False, unexpected=True)
        elif kind == "DECIDE":
            assert fault == "free-form" and context["gate"] == "C"
            words = next(b for b in bodies if "words" in b)
            assert words["words"] == "Looks good, approve this version."
            assert facts[words["request"]]["boundedContent"]["subject"] == requests[-1]["subject"]
            answer.update(choice="approve")
        elif kind == "PLAN":
            answer.update(scripted_plan())
            if fault in ("shell-command", "missing-plan-tool") and "exactFix" not in context:
                answer["edits"][2]["typedBody"]["command"] = ([f"& '{sys.executable}' '-B' 'check_greeting.py'"]
                    if fault == "shell-command" else [str(tmp_path / "absent.exe")])
            if fault in ("broken-tool", "all-skipped"):
                answer["edits"][2]["typedBody"]["command"].append("--broken-tool" if fault == "broken-tool" else "--skipped")
            if fault == "product-executable":
                answer["productFiles"].append("product-python.exe")
                answer["testFiles"].append("check_guide.py")
                answer["edits"].append({"kind": "test", "targetHandle": "new:4", "typedBody": {
                    "purpose": "Read the guide using the delivered executable", "stage": "walkthrough",
                    "command": ["./product-python.exe", "-B", "check_guide.py"], "requiredEnvironment": [],
                    "expectedEvidence": "Guide instructions are readable", "requirements": ["new:2"]}})
        elif kind == "REVIEW":
            answer.update(verdict="adequate", gaps=[])
            if fault in ("review-gap", "plan-new-test", "repair-bound", "atomic-invalid") and (calls[kind] == 1 or fault == "repair-bound"):
                answer.update(verdict="gap", gaps=[{"target": "n2", "correction": "Clarify the promised input behavior"}])
            if fault in ("uncertain-empty", "uncertain-twice"):
                answer = {"verdict": "uncertain", "why": "Need installed verification capability", "citations": list(facts),
                    "neededFacts": [{"target": "setup:verification" if fault == "uncertain-twice" else next(iter(facts)),
                                     "question": "Which verification tools are available?"}]}
        elif kind == "REPAIR":
            nodes = next(b["document"]["nodes"] for b in bodies if "document" in b)
            target = "n3" if fault == "RED-gap" else "n2"
            body = deepcopy(nodes[target]["body"])
            if target == "n3":
                body["command"].append("--more")
            else:
                body["obligation"] += "; support every supplied name" + str(calls[kind])
            answer["edits"] = [{"kind": nodes[target]["kind"], "targetHandle": target, "typedBody": body}]
            if fault == "plan-new-test":
                test = deepcopy(nodes["n3"]["body"])
                test["purpose"] = "Challenge hardcoded names with a second input"
                test["command"].append("--more")
                answer["edits"] = [{"kind": "test", "targetHandle": "new:1", "typedBody": test}]
            if fault == "scope-change":
                body["obligation"] += "; missing name prints usage and exits 2"
                test = deepcopy(nodes["n3"]["body"])
                test["command"].append("--usage")
                answer["edits"].append({"kind": "test", "targetHandle": "n3", "typedBody": test})
            if fault == "atomic-invalid":
                answer["edits"].append({"op": "Retire", "targetHandle": "n1", "replacementHandles": ["n2"]})
        elif kind == "IMPACT":
            answer.update(verdict="scope_change" if fault == "scope-change" else "within_scope")
        elif kind == "WORK":
            task = context["task"]
            timeline.append(task)
            tasks[task] += 1
            assignment = next(b for b in bodies if "worktree" in b)
            root = Path(assignment["worktree"])
            worker_roots.append(root)
            observed_failures = [b for b in bodies if "exitCode" in b and b["exitCode"] != 0]
            if task == "build" and fault == "local-fault" and tasks[task] == 2:
                assert any("Wrong greeting" in b["stderr"] for b in observed_failures)
                local_failures.extend(observed_failures)
                assert root == worker_roots[-2]  # Refine the same assignment before returning it.
            if task == "build" and fault == "implementation-fault" and tasks[task] == 2:
                failure = next(b for b in bodies if b.get("purpose") == "demo")
                detail = failure["answer"]
                assert "Hello, Ada!" in detail["gaps"][0]["correction"]
                writer_failures.append(detail)
            if task == "tests":
                content = {"check_greeting.py": PRODUCT_TEST}
                if fault == "product-executable":
                    content["check_guide.py"] = GUIDE_TEST
            else:
                content = {"greet.py": PRODUCT, "TRY.txt": GUIDE}
                if fault == "local-fault" and tasks[task] == 1:
                    content["greet.py"] = 'print("Wrong greeting")\n'
                if fault == "implementation-fault" and tasks[task] == 1:
                    content["greet.py"] = 'print("Hello, Ada!")\n'
                if fault == "guide-gap" and tasks[task] == 1:
                    content.pop("TRY.txt")
                if fault == "product-executable":
                    content["product-python.exe"] = Path(sys._base_executable).read_bytes()
                if fault == "scope-change" and calls["REPAIR"]:
                    content["greet.py"] = 'import sys\nif len(sys.argv) < 2:\n    print("Usage: greet.py NAME")\n    raise SystemExit(2)\nprint(f"Hello, {sys.argv[1]}!")\n'
            changed = []
            for name, data in content.items():
                data = data if isinstance(data, bytes) else data.encode()
                path = root / name
                if not path.exists() or path.read_bytes() != data:
                    path.write_bytes(data)
                baseline = loop.assignment["baseline"].get(name)
                if baseline != data:
                    changed.append(name)
            discoveries = [{"cause": "plan", "target": "n2", "correction": "Consider localized greetings in a future scope"}] if fault == "discovery" and task == "build" else []
            answer.update(outcome="done", summary="Assigned delivery ready for local checking", artifactPaths=changed, discoveries=discoveries)
        elif kind == "JUDGE":
            purpose = context["purpose"]
            purposes[purpose] += 1
            obs = [b for b in bodies if "outputRefs" in b]
            assert all(b["applies"] and b["executionUsable"] for b in obs)
            if purpose == "RED":
                assert any(b["failed"] for b in obs)
                if purposes[purpose] == 1:
                    assert any(b["products"].get("greet.py") is None for b in obs)
            answer.update(verdict="satisfied", gaps=[], assessments=[
                {"target": t, "satisfied": True, "why": "Actual interaction challenges/establishes this obligation"}
                for t in context["assessments"]])
            gap = None
            if fault == "RED-gap" and purpose == "RED" and not gap_sent:
                gap = ("plan", "n3", "Exercise more than one supplied name")
            if fault == "scope-change" and purpose == "GREEN" and not gap_sent:
                gap = ("plan", "n2", "Clarify support for every supplied name")
            if fault == "evidence-gap" and purpose == "demo":
                note = facts["setup:verification"]["boundedContent"]
                if isinstance(note, dict):
                    gap = ("evidence", "setup:verification", "Read the available verification capability")
                else:
                    assert note == "Python subprocess verification is available"
            if fault == "implementation-fault" and purpose == "demo":
                result = subprocess.run([sys.executable, "-B", "greet.py", "Grace"], cwd=loop.project, capture_output=True, text=True)
                if result.stdout != "Hello, Grace!\n":
                    gap = ("implementation", "greet.py", "Grace incorrectly produced " + repr(result.stdout))
            if purpose == "demo" and not (loop.project / "TRY.txt").is_file():
                gap = ("implementation", "TRY.txt", "Deliver the missing try-it guide")
            if gap:
                gap_sent = True
                answer.update(verdict="gap", gaps=[dict(zip(("cause", "target", "correction"), gap))])
            if purpose == "demo":
                packet = next(b for b in bodies if "traceComplete" in b)
                assert packet["traceComplete"] and packet["reusedEvidence"]
                green = next(b for b in bodies if b.get("purpose") == "GREEN" and "satisfied" in b)
                assert green["satisfied"]
                demo_reuse.extend(b for b in obs if b["purpose"] == "GREEN" and b["inputsByteIdentical"])
        return json.dumps({"answer": answer})

    def user(request):
        requests.append(request)
        timeline.append(request["gate"])
        if fault == "free-form" and request["gate"] == "C":
            return "Looks good, approve this version."
        return {"request": request["request"], "subject": "stale" if fault == "stale-decision" and request["gate"] == "C" else request["subject"]["digest"],
                "choice": "revise" if fault == "revise-B" and request["gate"] == "B" and not calls["REPAIR"] else "approve"}

    command = ["offline-question-recorder"]
    if fault == "native-transport":
        native = tmp_path / "offline_native.py"
        native.write_text('import json, sys\nfrom pathlib import Path\n'
            'q = json.loads(sys.stdin.read().splitlines()[-1])\n'
            'assert q["facts"] and q["kind"]\n'
            'reply = Path(__file__).with_suffix(".reply").read_text(encoding="utf-8")\n'
            'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":reply}}))\n'
            'print(json.dumps({"type":"turn.completed"}))\n', encoding="utf-8")
        command = [sys.executable, "-B", str(native)]
    recorder = Codex(tmp_path / "calls", {**setup, "codexCommand": command})

    def recorded_provider(question):
        if fault == "native-transport":
            native.with_suffix(".reply").write_text(provider(question), encoding="utf-8")
            return recorder(question)  # Execute the offline CLI twin, never Codex or a real provider.
        folder, invocation = recorder.prepare(question)  # Real sidecar producer; never launch a provider.
        config = invocation["provider"]
        argv = invocation["command"]
        assert argv[argv.index("--model") + 1] == config["model"]
        assert 'model_reasoning_effort=' + json.dumps(config["effort"]) in argv
        expected = "high" if invocation["role"] in ("build", "demo") else "low" if question["kind"] == "DECIDE" else "medium"
        assert config["effort"] == expected
        reply = provider(question)
        (folder / "answer.txt").write_text(reply, encoding="utf-8")
        return reply

    loop = Loop(tmp_path / "product", tmp_path / "workers", "Make a command that greets a supplied name", setup, recorded_provider, user)
    cursor = loop.run()
    rows = loop.store.records()
    revisions = loop.store.db.execute("SELECT count(*) FROM plan_revision").fetchone()[0]
    persisted = loop.store.cursor()
    records = {ref: body for ref, _, body in rows}
    observations = [(ref, b) for ref, k, b in rows if k == "observation" and not b["purpose"].startswith("local:")]
    outcome = next(b for _, k, b in reversed(rows) if k == "outcome")
    loop.store.db.close()
    incomplete = fault in ("stale-decision", "broken-tool", "all-skipped", "malformed-exhausted", "repair-bound", "atomic-invalid", "uncertain-empty", "uncertain-twice")
    assert cursor["state"] == persisted["state"] == ("INCOMPLETE" if incomplete else "ACCEPTED" if fault == "owner" else "SCRIPTED_C"), outcome
    assert not tasks["guide"]
    assert calls["DECIDE"] == (1 if fault == "free-form" else 0)
    if fault == "native-transport":
        results = [json.loads(p.read_text()) for p in (tmp_path / "calls").glob("*/result.json")]
        assert len(results) == sum(calls.values()) and all(r["executionStarted"] and r["cleanupVerified"] and r["exitCode"] == 0 for r in results)
    if malformed:
        assert len(malformed) == 3
        assert all(all(q["facts"][r] == f for r, f in malformed[0]["facts"].items()) for q in malformed[1:])
        assert all(all(field in json.dumps(q["facts"]) for field in ("unexpected", "users", "size")) for q in malformed[1:])
    if incomplete:
        assert not outcome["accepted"] and not outcome["verification_complete"] and outcome["nextAction"]
        if fault in ("broken-tool", "all-skipped"):
            assert not purposes["RED"] and not tasks["build"]
            local = [b for _, k, b in rows if k == "observation"]
            assert local and all(not b["toolReady"] if fault == "broken-tool" else all(c["status"] == "skipped" for c in b["checks"]) for b in local)
        if fault == "atomic-invalid":
            assert revisions == 1 and "wrong trace target kind" in outcome["reason"]
        if fault == "repair-bound":
            assert calls["REPAIR"] == 3 and "Three repairs exhausted" in outcome["reason"]
        return
    assert outcome["accepted"] == (fault == "owner") and outcome["verification_complete"]
    assert all(root.is_dir() and (root / ".git").is_file() for root in worker_roots)
    red = observations[0][1]
    assert red["purpose"] == "RED" and red["executionStarted"] and red["toolReady"] and red["exitCode"] != 0
    assert red["products"]["greet.py"] is None and red["checks"][0]["status"] == "failed"
    assert demo_reuse
    subject = requests[-1]["subject"]
    assert subject["candidate"] == cursor["candidate"]
    assert records[subject["candidate"]]["files"] == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in loop.project.iterdir() if p.is_file()}
    assert all(records[r]["satisfied"] for r in subject["judgments"])
    assert "Try it yourself" in (loop.project / "TRY.txt").read_text()
    result = subprocess.run([sys.executable, "-B", "greet.py", "Grace"], cwd=loop.project, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout == "Hello, Grace!\n"
    if fault == "none":
        assert timeline == ["A", "B", "tests", "build", "check-in", "C"] and not calls["REPAIR"]
        assert len(observations) == 2  # No cloned functional walkthrough.
        from tests.seldon.run_report import report
        text, summary = report(tmp_path)
        assert "RED FLAGS: 0 events" in text and summary[3] == "SCRIPTED_C"
    if fault in ("evidence-gap", "implementation-fault", "local-fault", "guide-gap"):
        assert not calls["REPAIR"] and revisions == 1
    if fault == "evidence-gap":
        assert tasks == {"tests": 1, "build": 1} and any(k == "fact_gather" for _, k, _ in rows)
    if fault == "local-fault":
        assert local_failures and tasks["build"] == 2
    if fault == "implementation-fault":
        assert writer_failures and tasks["build"] == 2 and tasks["tests"] == 1
    if fault == "guide-gap":
        assert purposes["GREEN"] == 1 and purposes["demo"] == 2 and tasks["build"] == 2
        assert demo_reuse[-1]["candidate"] != cursor["candidate"]
        assert [b["changedPaths"] for _, k, b in rows if k == "integration"][-1] == ["TRY.txt"]
    if fault == "product-executable":
        assert len(observations) == 3 and calls["PLAN"] == 1
    if fault == "plan-new-test":
        assert revisions == 2 and len(observations) == 4 and purposes["RED"] == 1
        assert any(c["name"] == "greet Grace" for _, b in observations for c in b["checks"])
    if fault == "discovery":
        discovery = next(r for r, k, b in rows if k == "discovery")
        assert discovery in next(r for r in requests if r["gate"] == "check-in")["subject"]["proposedDeferrals"]
        assert any(k == "disposition" and b["discovery"] == discovery and b["disposition"] == "deferred" and b["authority"] for _, k, b in rows)
    if fault == "scope-change":
        assert any(r["gate"] == "scope" for r in requests) and any(k == "authorization" for _, k, _ in rows)
