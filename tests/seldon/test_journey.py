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
         "local-fault", "implementation-fault", "guide-gap", "RED-gap", "scope-change", "discovery", "new-contract"]
SCOPED_CASES = ["parallel", "conflict", "change", "killed", "publication", "uncertain", "check-repair", "reply", "ignored"]
PROMPT_SIZES = {}


def clean_core_golden(root):
    """The repository golden entry runs this same driver, with no alternate proof."""
    for fault in CASES:
        folder = root / fault
        folder.mkdir(parents=True)
        test_journey_delivers_tested_demonstrated_candidate(folder, fault)
    for mode in SCOPED_CASES:
        folder = root / mode
        folder.mkdir()
        test_scoped_units_deliver_and_recover(folder, mode)


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
    A new accepted requirement/test/file catches an unamendable writer contract; unchanged scopes cannot.
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
                unit = deepcopy(nodes["n4"]["body"])
                unit["tests"].append("new:1")
                answer["edits"] = [{"kind": "test", "targetHandle": "new:1", "typedBody": test},
                    {"kind": "bead", "targetHandle": "n4", "typedBody": unit}]
            if fault == "scope-change":
                body["obligation"] += "; missing name prints usage and exits 2"
                test = deepcopy(nodes["n3"]["body"])
                test["command"].append("--usage")
                answer["edits"].append({"kind": "test", "targetHandle": "n3", "typedBody": test})
            if fault == "new-contract":
                unit = deepcopy(nodes["n4"]["body"])
                unit["requirements"].append("new:1")
                unit["tests"].append("new:2")
                unit["ownedPaths"] += ["check_usage.py", "usage.py"]
                unit["testPaths"].append("check_usage.py")
                answer["edits"] = [
                    {"kind": "requirement", "targetHandle": "new:1", "typedBody": {"obligation": "A missing name prints usage and exits 2",
                        "design": "A separate usage helper", "contract": "No name -> usage and exit 2", "features": ["n1"]}},
                    {"kind": "test", "targetHandle": "new:2", "typedBody": {"purpose": "Exercise the missing-name path", "stage": "verification",
                        "command": [sys.executable, "-B", "check_usage.py"], "requiredEnvironment": ["Python"],
                        "expectedEvidence": "Actual usage output and exit status", "requirements": ["new:1"]}},
                    {"kind": "bead", "targetHandle": "n4", "typedBody": unit}]
            if fault == "atomic-invalid":
                answer["edits"].append({"op": "Retire", "targetHandle": "n1", "replacementHandles": ["n2"]})
        elif kind == "IMPACT":
            answer.update(verdict="scope_change" if fault in ("scope-change", "new-contract") else "within_scope")
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
                if "check_usage.py" in assignment["ownedPaths"]:
                    content["check_usage.py"] = PRODUCT_TEST.replace('(["Ada", "Grace"] if "--more" in sys.argv else ["Ada"])', '["Ada"]').replace('if "--usage" in sys.argv:', 'if True:')
                if fault == "product-executable":
                    content["check_guide.py"] = GUIDE_TEST
            else:
                content = {"greet.py": PRODUCT, "TRY.txt": GUIDE}
                if "usage.py" in assignment["ownedPaths"]:
                    content["usage.py"] = 'def missing_name():\n    print("Usage: greet.py NAME")\n    raise SystemExit(2)\n'
                    content["greet.py"] = 'import sys\nif len(sys.argv) < 2:\n    from usage import missing_name\n    missing_name()\nprint(f"Hello, {sys.argv[1]}!")\n'
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
                baseline = assignment["baseline"]["files"].get(name)
                if baseline != hashlib.sha256(data).hexdigest():
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
            if fault == "new-contract" and purpose == "demo" and not gap_sent:
                result = subprocess.run([sys.executable, "-B", "greet.py"], cwd=loop.project, capture_output=True, text=True)
                assert result.returncode != 2 or "Usage:" not in result.stdout
                gap = ("plan", "n2", "Accept a missing-name usage requirement, semantic test and helper file")
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
        if fault == "new-contract" and request["gate"] == "scope":
            assert not (loop.project / "usage.py").exists()
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

    if fault == "new-contract":
        assert any(r["gate"] == "scope" for r in requests)
        result = subprocess.run([sys.executable, "-B", "greet.py"], cwd=loop.project, capture_output=True, text=True)
        assert result.returncode == 2 and "Usage:" in result.stdout
        assert (loop.project / "usage.py").is_file()
        assert any(b["purpose"] == "RED" and b["exitCode"] != 0 and "check_usage.py" in b["command"] for _, b in observations)


# Real command checks for independent scopes and their integrated consumer.
SCOPED_CHECK = r'''import json, os, subprocess, sys, time
from pathlib import Path
name, expected, control, mode = sys.argv[1:]
root = Path.cwd()
control = Path(control)
if mode == "killed" and name == "b.py" and (root / name).exists() and root.name.startswith("worker-") and not (control / "resume").exists():
    (control / "kill-ready").write_text(str(os.getpid()))
    time.sleep(30)
result = subprocess.run([sys.executable, "-B", name], capture_output=True, text=True)
ok = result.returncode == 0 and result.stdout.strip() == expected
Path(os.environ["SELDON_EVIDENCE_DIR"], "execution.json").write_text(json.dumps({"toolReady": True,
    "checks": [{"name": name + " output", "status": "passed" if ok else "failed"}]}))
print(result.stdout + result.stderr)
raise SystemExit(0 if ok else 1)
'''


def scoped_run(root, mode, reopen=False):
    """Script semantic replies; run actual worker processes, commands, Git and SQLite."""
    from seldon import work
    root = Path(root)
    launch_log = root / "launches.jsonl"
    changed_once = False
    loop = None

    def provider(question):
        nonlocal changed_once
        kind, context, facts = question["kind"], question["context"], question["facts"]
        answer = {"why": "The assigned obligations are covered by actual command interactions", "citations": list(facts)}
        bodies = [f["boundedContent"] for f in facts.values() if isinstance(f["boundedContent"], dict)]
        if kind == "ALIGN":
            answer.update(size="small", understanding="Three independent commands composed into one application", users=["CLI users"],
                scopeIn=["A, B and C output and their composition"], scopeOut=[], improvements=[],
                quality=[{"criterion": "Clear composed output", "verification": "Run the integrated application"}], unknowns=[])
        elif kind == "PLAN":
            edits, scopes = [], []
            for i, (name, expected) in enumerate((("a", "Alpha"), ("b", "Beta"), ("c", "Gamma"), ("app", "Alpha|Beta|Gamma"))):
                f, r, t = (f"new:{3*i+j}" for j in (1, 2, 3))
                edits.extend([
                    {"kind": "feature", "targetHandle": f, "typedBody": {"story": "Run " + name, "acceptance": "Observe " + expected, "vision": ["vision"]}},
                    {"kind": "requirement", "targetHandle": r, "typedBody": {"obligation": "Output " + expected, "design": "Independent command" if i < 3 else "Compose the three commands", "contract": "stdout " + expected, "features": [f]}},
                    {"kind": "test", "targetHandle": t, "typedBody": {"purpose": "Exercise " + name, "stage": "verification", "command": [sys.executable, "-B", f"check_{name}.py", name + ".py", expected, str(root), mode], "requiredEnvironment": ["Python"], "expectedEvidence": "Actual process output", "requirements": [r]}}])
                if mode == "check-repair" and name == "a":
                    edits[-2]["typedBody"]["contract"] += "; --variant outputs Alpha2"
                scopes.append({"requirements": [r], "tests": [t], "testFiles": [f"check_{name}.py"],
                    "productFiles": [name + ".py", *(["shared.txt"] if mode == "conflict" and i < 2 else []), *([".gitignore", "dist/index.html"] if mode == "ignored" and i == 0 else [])],
                    "dependencies": ["1", "2", "3"] if i == 3 else []})
            answer.update(edits=edits, units=scopes)
        elif kind == "REVIEW":
            answer.update(verdict="adequate", gaps=[])
        elif kind == "IMPACT":
            answer.update(verdict="within_scope")
        elif kind == "REPAIR":
            nodes = next(b["document"]["nodes"] for b in bodies if "document" in b)
            requirement = deepcopy(nodes["n2"]["body"])
            requirement["obligation"] = "Output Alpha2"
            requirement["contract"] = "stdout Alpha2"
            check = deepcopy(nodes["n3"]["body"])
            check["command"][4] = "Alpha2"
            integrated = deepcopy(nodes["n12"]["body"])
            integrated["command"][4] = "Alpha2|Beta|Gamma"
            answer["edits"] = [{"kind": "requirement", "targetHandle": "n2", "typedBody": requirement},
                {"kind": "test", "targetHandle": "n3", "typedBody": check},
                {"kind": "test", "targetHandle": "n12", "typedBody": integrated}]
        elif kind == "WORK":
            assignment = next(b for b in bodies if "worktree" in b)
            worker = Path(assignment["worktree"])
            with launch_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"unit": context["unit"], "phase": context["task"], "worktree": str(worker)}) + "\n")
            document = loop.store.plan(loop.store.cursor()["revision"])
            launched = [json.loads(line) for line in launch_log.read_text().splitlines()]
            turn = sum(row["unit"] == context["unit"] and row["phase"] == context["task"] for row in launched)
            content = {}
            for path in assignment["ownedPaths"]:
                if path.startswith("check_"):
                    content[path] = SCOPED_CHECK
                    if mode == "check-repair" and path == "check_a.py" and turn > 1:
                        content[path] = content[path].replace('Path(os.environ["SELDON_EVIDENCE_DIR"]',
                            'variant = subprocess.run([sys.executable, "-B", "a.py", "--variant"], capture_output=True, text=True)\nok = ok and variant.returncode == 0 and variant.stdout.strip() == "Alpha2"\nPath(os.environ["SELDON_EVIDENCE_DIR"]')
                elif path in ("a.py", "b.py", "c.py"):
                    req = document["nodes"][loop.store.units()[context["unit"]]["contract"]["requirements"][0]]["body"]["obligation"]
                    content[path] = "print(" + repr(req.removeprefix("Output ")) + ")\n"
                    if mode == "check-repair" and path == "a.py" and turn > 1:
                        content[path] = 'import sys\nprint("Alpha2" if "--variant" in sys.argv else "Alpha")\n'
                elif path == "app.py":
                    content[path] = 'import subprocess, sys\nprint("|".join(subprocess.check_output([sys.executable, "-B", name + ".py"], text=True).strip() for name in ("a", "b", "c")))\n'
                elif path == ".gitignore":
                    content[path] = "dist/\n"
                elif path == "TRY.txt":
                    content[path] = "Try it yourself: python -B app.py; observe all three outputs.\n"
                else:
                    content[path] = "Shared ownership is serialized.\n"
            # Real processes author files and spend enough time to distinguish overlap from serial scheduling.
            writer = 'import json, os, sys, time\nfrom pathlib import Path\ndata=json.loads(sys.argv[1])\n'
            if mode == "uncertain" and "b.py" in content:
                writer += f'Path({str(root / "kill-ready")!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n'
            writer += 'time.sleep(0.65)\nfor name, source in data.items():\n    Path(name).parent.mkdir(parents=True, exist_ok=True)\n    Path(name).write_text(source, encoding="utf-8")\n'
            result = work.execute([sys.executable, "-B", "-c", writer, json.dumps(content)], worker)
            assert result["exitCode"] == 0 and result["cleanupVerified"], result
            observed = work.files(worker)
            paths = [p for p, data in observed.items() if assignment["baseline"]["files"].get(p) != hashlib.sha256(data).hexdigest()]
            answer.update(outcome="done", summary="Scoped delivery checked by its declared command", artifactPaths=paths, discoveries=[])
        elif kind == "JUDGE":
            observed = [b for b in bodies if "outputRefs" in b]
            assert observed and all(b["applies"] and b["executionUsable"] for b in observed)
            if context["purpose"] == "RED":
                assert any(b["failed"] for b in observed)
            else:
                assert all(b["passed"] for b in observed)
            answer.update(verdict="satisfied", gaps=[], assessments=[{"target": r, "satisfied": True, "why": "The executed process establishes its observable contract"} for r in context["assessments"]])
            if mode == "check-repair" and context["purpose"] == "demo" and not changed_once:
                variant = subprocess.run([sys.executable, "-B", "a.py", "--variant"], cwd=loop.project, capture_output=True, text=True)
                assert variant.stdout.strip() != "Alpha2"
                changed_once = True
                answer.update(verdict="gap", gaps=[{"cause": "implementation", "target": "check_a.py", "correction": "Exercise the accepted variant output too"}])
            if mode == "change" and context["purpose"] == "demo" and not changed_once:
                changed_once = True
                answer.update(verdict="gap", gaps=[{"cause": "plan", "target": "n2", "correction": "Change the A output to Alpha2 within the same composition"}])
        return json.dumps({"answer": answer})

    def user(request):
        return {"request": request["request"], "subject": request["subject"]["digest"], "choice": "approve"}

    if reopen:
        loop = Loop.reopen(root / "product", provider, user)
    else:
        setup = {"providers": deepcopy(ROLES), "interpreter": sys.executable, "userOrigin": "scripted journey", "scriptedUser": True}
        loop = Loop(root / "product", root / "workers", "Compose three independent commands", setup, provider, user)
    cursor = loop.run()
    return loop, cursor


def scoped_crash_child(root, mode):
    """Fault injection exits the actual controller after its first published file."""
    if mode == "publication":
        import os
        from seldon import work
        replace = os.replace

        def crash_after_file(source, target):
            replace(source, target)
            if Path(target) == Path(root) / "product" / "TRY.txt":
                os._exit(77)
        work.os.replace = crash_after_file
    if mode == "reply":
        import os
        from seldon import questions
        ask = questions.ask

        def crash_after_reply(provider, question, record, check):
            answer = ask(provider, question, record, check)
            if question["kind"] == "WORK" and question["context"]["task"] == "build" and any(
                    "app.py" in f["boundedContent"].get("ownedPaths", []) for f in question["facts"].values() if isinstance(f["boundedContent"], dict)):
                os._exit(78)
            return answer
        questions.ask = crash_after_reply
    scoped_run(Path(root), mode)


@pytest.mark.fast
@pytest.mark.parametrize("mode", SCOPED_CASES)
def test_scoped_units_deliver_and_recover(tmp_path, mode):
    """C12/C18/C23: independent scopes compose through C using real processes.

    Recorded overlap catches a serial scheduler; shared-file output catches concurrent
    ownership. Changing A catches blanket invalidation that the tiny journey misses.
    Killing during verification/publication catches lost finished work and half candidates;
    killing an unreported writer catches unsafe relaunch, absent in clean exits.
    A repaired check must establish new RED while other scopes retain credit; a plan
    edit cannot expose that path. Reply/report death catches lost durable terminal work;
    post-report death misses it. Ignored outputs catch HEAD/candidate divergence that
    ordinary files cannot expose.
    """
    import os
    import time
    from seldon.store import Store
    from seldon import evidence, work

    before = None
    if mode in ("killed", "publication", "uncertain", "reply"):
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        code = "from tests.seldon.test_journey import scoped_crash_child; import sys; scoped_crash_child(sys.argv[1],sys.argv[2])"
        log = tmp_path / "child.log"
        with log.open("wb") as output:
            child = subprocess.Popen([sys.executable, "-B", "-c", code, str(tmp_path), mode], env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if mode in ("publication", "reply") and child.poll() is not None:
                        assert child.returncode == (77 if mode == "publication" else 78), log.read_text()
                        break
                    if (tmp_path / "kill-ready").exists():
                        store = Store(tmp_path / "product")
                        finished = [r for r, u in store.units().items() if "build" in u["results"]]
                        store.db.close()
                        if finished:
                            with pytest.raises(ValueError, match="Another controller"):
                                Loop.reopen(tmp_path / "product", lambda q: pytest.fail("Live run must not launch"), lambda q: None)
                            child.kill()
                            child.wait(10)
                            break
                    assert child.poll() is None, log.read_text()
                    time.sleep(0.02)
                else:
                    pytest.fail("Controller never reached the requested real fault boundary: " + log.read_text())
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(10)
        if mode in ("killed", "uncertain") and os.name == "nt":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x100000, False, int((tmp_path / "kill-ready").read_text()))
            if not handle:
                assert ctypes.get_last_error() == 87, "Command liveness could not be verified"
            else:
                try:
                    assert kernel.WaitForSingleObject(handle, 5000) == 0, "Killed controller left its command alive"
                finally:
                    kernel.CloseHandle(handle)
        store = Store(tmp_path / "product")
        before = store.units()
        publication = store.cursor().get("publication")
        if mode == "reply":
            pending = next(u["active"] for u in before.values() if u.get("active"))
            assert not pending.get("report") and not pending.get("work")
            assert any(b["attempt"] == pending["invocation"] and not b["errors"] for _, _, b in store.records("provider_reply"))
        if mode == "publication":
            assert publication
            old = store.read(store.cursor()["candidate"])
            assert old != work.candidate(work.files(tmp_path / "product"))  # A partial publication is never credited.
        store.db.close()
        launches = (tmp_path / "launches.jsonl").read_text().splitlines()
        (tmp_path / "resume").write_text("Resume checks; never rerun a completed writer")
        loop, cursor = scoped_run(tmp_path, mode, reopen=True)
        if mode == "uncertain":
            assert cursor["state"] == "INCOMPLETE"
            assert any(u.get("active", {}).get("status") == "uncertain" for u in loop.store.units().values())
            assert launches == (tmp_path / "launches.jsonl").read_text().splitlines()
            loop.store.db.close()
            return
    else:
        loop, cursor = scoped_run(tmp_path, mode)
    if mode == "ignored":
        assert cursor["state"] == "INCOMPLETE"
        reason = loop.store.records("outcome")[-1][2]["reason"]
        assert "Commit/manifest mismatch" in reason and "dist/index.html" in reason
        assert not (loop.project / "dist/index.html").exists()
        assert loop.store.read(cursor["candidate"]) == work.candidate(work.files(loop.project))
        assert not any(json.loads(line)["phase"] == "build" and json.loads(line)["unit"] == list(loop.store.units())[-1]
                       for line in (tmp_path / "launches.jsonl").read_text().splitlines())
        loop.store.db.close()
        return
    assert cursor["state"] == "SCRIPTED_C", loop.store.records("outcome")
    units = loop.store.units()
    rows = loop.store.records()
    assert all(not u.get("waiting") for u in units.values())
    assert len(units) == 4 and all({"tests", "build"} <= set(u["results"]) for u in units.values())
    result = subprocess.run([sys.executable, "-B", "app.py"], cwd=loop.project, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == ("Alpha2|Beta|Gamma" if mode == "change" else "Alpha|Beta|Gamma")
    assert "Try it yourself" in (loop.project / "TRY.txt").read_text()
    assignments = {r: b for r, k, b in rows if k == "assignment"}
    attempts = {r: b for r, k, b in rows if k == "attempt" and b["operation"] == "work"}
    endings = {b["attempt"]: b for _, k, b in rows if k == "writer_end"}
    intervals = [(a["unit"], set(a["ownedPaths"]), attempts[a["attempt"]]["startedAt"], endings[a["attempt"]]["finishedAt"])
                 for a in assignments.values() if a["phase"] == "build"]
    independent = list(units)[:3]
    overlapping = [(a, b) for a in intervals for b in intervals if a[0] < b[0] and max(a[2], b[2]) < min(a[3], b[3])]
    if mode == "parallel":
        assert all(any(a[0] == ref or b[0] == ref for a, b in overlapping) for ref in independent)
        assert all(not a[1].intersection(b[1]) for a, b in overlapping)
        assert all(len(u["contract"]["requirements"]) == len(u["contract"]["tests"]) == 1 for u in units.values())
    if mode == "conflict":
        assert all(not a[1].intersection(b[1]) for a, b in overlapping)
        assert any(k == "unit_wait" and "shared.txt" in b["reason"].get("files", []) for _, k, b in rows)
        assert (loop.project / "shared.txt").read_text() == "Shared ownership is serialized.\n"
    if mode == "check-repair":
        variant = subprocess.run([sys.executable, "-B", "a.py", "--variant"], cwd=loop.project, capture_output=True, text=True)
        assert variant.returncode == 0 and variant.stdout.strip() == "Alpha2"
        assert sum(k == "judgment" and b["purpose"] == "RED" and b["satisfied"] for _, k, b in rows) == 2
        for ref in list(units)[1:3]:
            assert len(units[ref]["attempts"]) == 2
            for test in units[ref]["contract"]["tests"]:
                assert sum(k == "observation" and b["purpose"] == "GREEN" and b["test"] == test for _, k, b in rows) == 1
        assert not any(k == "repair_proposal" for _, k, b in rows)
    if mode == "reply":
        assert any(k == "recovery" and b.get("report") for _, k, b in rows)
    if mode == "change":
        invalidations = [b["unit"] for _, k, b in rows if k == "invalidation"]
        assert set(invalidations) == {list(units)[0], list(units)[3]}
        for ref in list(units)[1:3]:
            assert len(units[ref]["attempts"]) == 2  # No repeated work for unaffected obligations.
            green = units[ref]["evidence"]["GREEN"]
            assert not green.get("stale")
            assert all(o["applies"] for o in evidence.facts(loop.store, green["observations"], cursor["candidate"], loop.store.plan(cursor["revision"])).values())
        old_a = next(b for _, k, b in rows if k == "observation" and b["purpose"] == "GREEN" and b["test"] == "n3")
        old_ref = next(r for r, k, b in rows if k == "observation" and b == old_a)
        assert not evidence.facts(loop.store, [old_ref], cursor["candidate"], loop.store.plan(cursor["revision"]))[old_ref]["applies"]
    if before:
        for ref, previous in before.items():
            if "build" in previous["results"]:
                assert units[ref]["results"] == previous["results"] and units[ref]["attempts"] == previous["attempts"]
        assert not any(u.get("active") for u in units.values())
        after_launches = (tmp_path / "launches.jsonl").read_text().splitlines()
        for entry in launches:
            assert after_launches.count(entry) == 1
        assert not loop.store.cursor().get("publication")
    for _, kind, body in rows:
        if kind == "integration":
            assert work.git(loop.project, "cat-file", "-t", body["baseRevision"]) == "commit"
            assert work.git(loop.project, "rev-parse", body["revision"] + "^") == body["baseRevision"]
    loop.store.db.close()
