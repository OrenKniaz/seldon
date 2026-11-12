"""C8/C12/C14/C18/C23; R2/R6-R9/R11: scripted semantics, real Git, SQLite and processes."""

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from seldon import gitwork, judgment, plan, questions
from seldon.providers import Codex, ProviderFailure, ROLES
from seldon.run import Run
from seldon.store import RunRow, Store, UnitRow, encode

VISION = {"size": "tiny", "understanding": "Read named values", "users": ["CLI user"],
          "scopeIn": ["Named values"], "scopeOut": [], "improvements": [],
          "quality": [{"criterion": "Clear output", "verification": "Read the observed output"},
                      {"criterion": "Persist the exact value", "verification": "Run the real check"}],
          "unknowns": []}


def planned(count, mode):
    edits, scopes = [], []
    for i in range(1, count + 1):
        f, r, c = (f"new:{kind}{i:03}" for kind in "frc")
        directory = f"unit-{i:03}"
        edits.extend([
            {"kind": "feature", "targetHandle": f, "typedBody": {
                "story": f"Read value {i:03}", "acceptance": f"Observe value-{i:03}"}},
            {"kind": "requirement", "targetHandle": r, "typedBody": {
                "obligation": f"Read value-{i:03} from persistent storage", "design": "One named value file",
                "contract": f"Read value-{i:03}", "features": [f]}},
            {"kind": "check", "targetHandle": c, "typedBody": {
                "purpose": f"Read value {i:03}", "mode": "establishes",
                "command": [sys.executable, "-S", "-B", f"{directory}/check.py"],
                "requiredEnvironment": ["Python"], "expectedEvidence": "Actual persisted value",
                "requirements": [r]}}])
        deps = []
        if i == 2 and (mode.startswith("kill") or mode in ("exhaust", "final-rework", "wave-start")):
            deps = [1]
        if mode == "parallel" and i == 4:
            deps = [1, 2, 3]
        if mode == "scale50":
            deps = [i - 1] if i % 5 != 1 else []
            if i % 10 == 0:
                deps = sorted({i - 1, i - 5})
        scopes.append({"scope": [directory + "/**"], "checkPaths": [directory + "/check.py"],
                       "requirements": [r], "dependencies": deps, "instructions": f"Deliver value {i:03}"})
        if mode == "traced-evidence" or mode == "parallel" and i == 4:
            scopes[-1]["checkPaths"] = [directory + "/*.py"]
    if mode == "wave-start":
        edits[4]["typedBody"]["obligation"] = "Read the dependency's value-001"
    if mode == "parallel":
        edits[10]["typedBody"]["obligation"] += "; preserve dependency value-001"
        edits.append({"kind": "check", "targetHandle": "new:wave", "typedBody": {
            "purpose": "Read the three integrated values together", "mode": "preserves",
            "command": [sys.executable, "-S", "-B", "unit-004/compose.py"], "requiredEnvironment": ["Python"],
            "expectedEvidence": "All independent values together", "requirements": ["new:r001", "new:r002"]}})
        edits.append({"kind": "check", "targetHandle": "new:preserve", "typedBody": {
            "purpose": "Preserve the integrated dependency value", "mode": "preserves",
            "command": [sys.executable, "-S", "-B", "-c",
                        "from pathlib import Path; "
                        "assert Path('unit-001/value.txt').read_text()=='value-001'"],
            "requiredEnvironment": ["Python"], "expectedEvidence": "Existing dependency is unchanged",
            "requirements": ["new:r004"]}})
    if mode == "exhaust":
        edits[1]["typedBody"]["obligation"] += "; preserve the claimed existing value"
        edits.append({"kind": "check", "targetHandle": "new:preserve", "typedBody": {
            "purpose": "Preserve the claimed existing value", "mode": "preserves",
            "command": [sys.executable, "-S", "-B", "unit-001/check.py"],
            "requiredEnvironment": ["Python"], "expectedEvidence": "A value already present at base",
            "requirements": ["new:r001"]}})
    if mode == "scale50":
        # Ten features each contain five requirements; a feature-sized DEMO would grow here.
        for i in range(count):
            edits[3 * i + 1]["typedBody"]["features"] = [f"new:f{(i // 5) * 5 + 1:03}"]
        edits = [e for e in edits if e["kind"] != "feature" or
                 int(e["targetHandle"][-3:]) % 5 == 1]
    selected = "new:c002" if mode == "traced-evidence" else "new:c001"
    return {"edits": edits, "units": scopes, "quality": [
        {"criterion": 1, "checks": [selected]},
        {"criterion": 2, "checks": [selected]}]}


NATIVE = r'''
import json, os, subprocess, sys, time, uuid
from pathlib import Path
slot = Path(sys.argv[1])
args = sys.argv[2:]
schema = Path(args[args.index("--output-schema") + 1])
q = json.loads(schema.with_name("question.json").read_text("utf-8"))
facts = q["facts"]
mode = (slot / "mode").read_text()
scope = facts["Write scope"][0]
name = scope.split("/")[0]
guide = "Guide path" in facts
counter = slot / ("turn-" + name)
turn = int(counter.read_text()) + 1 if counter.exists() else 1
if mode in ("fresh", "kill-fresh") and "resume" in args:
    print(json.dumps({"type": "error", "message": "session not found"}), flush=True)
    sys.exit(1)
counter.write_text(str(turn))
thread = args[args.index("resume") + 1] if "resume" in args else str(uuid.uuid4())
print(json.dumps({"type": "thread.started", "thread_id": thread}), flush=True)
with (slot / ("launch-" + name + ".jsonl")).open("a") as log:
    log.write(json.dumps({"turn": turn, "thread": thread, "resumed": "resume" in args,
                          "start": time.time(), "facts": facts}) + "\n")
if name == "unit-001" and mode == "timeout" and not any((slot / "calls").glob("*/result.json")):
    time.sleep(30)
if name == "unit-001" and mode == "native-denied":
    sys.stderr.write("Error: failed to initialize in-process app-server client: "
                     "Access is denied. (os error 5)\n")
    sys.exit(1)
if name == "unit-002" and mode in ("kill-work", "kill-fresh") and not (slot / "resume").exists():
    Path(name).mkdir(exist_ok=True)
    Path(name, "partial.txt").write_text("partial work retained across controller death")
    (slot / "kill-ready").write_text(str(os.getpid()))
    time.sleep(30)
if mode == "parallel" and name in ("unit-001", "unit-002", "unit-003"):
    (slot / ("ready-" + name)).write_text("ready")
    deadline = time.monotonic() + 15
    while len(list(slot.glob("ready-*"))) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(list(slot.glob("ready-*"))) == 3, "Independent workers never overlapped"
if guide:
    path = Path(facts["Guide path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    # Like a real guide, the walkthrough step writes an output folder where it runs (T-84).
    command = [sys.executable, "-S", "-B", "-c", "import os, subprocess, sys; "
               "os.makedirs('guide-output', exist_ok=True); "
               "sys.exit(subprocess.call([sys.executable, '-S', '-B', 'unit-001/check.py']))"]
    if mode == "guide-command" and turn == 1:
        command = [sys.executable, "-S", "-B", "-c", "raise SystemExit(7)"]
    linked = "unit-001/absent.txt" if mode == "guide-path" and turn == 1 else "unit-001/check.py"
    note = "Clarify the expected result" if mode in ("guide-gap", "final-seed") and turn == 1 else \
        "Observe value-001"
    if mode == "final-rework":
        note = "Observe final-compatible value"
    path.write_text("Try it yourself\nRead [the value check](" + linked + ").\n"
                    + "```argv\n" + json.dumps(command) + "\n```\n" + note + "\n")
    if mode == "guide-format" and turn == 1:
        # A shell-string command is refused; commands must be argv arrays (guide.py).
        path.write_text("Try it yourself: read [the value](unit-001/value.txt).\n"
                        "```sh\npython unit-001/check.py\n```\n")
else:
    number = int(name.split("-")[1])
    Path(name).mkdir(exist_ok=True)
    value = f"value-{number:03}"
    source = ("from pathlib import Path\n"
              "import os\n"
              f"assert Path('{name}/value.txt').read_text() == '{value}'\n"
              f"print('observed {value}')\n")
    if mode == "traced-evidence":
        Path(name, "helper.py").write_text(
            "from pathlib import Path\ndef read_value(path):\n    return Path(path).read_text()\n")
        source = "from helper import read_value\n" + source.replace(
            f"Path('{name}/value.txt').read_text()", f"read_value('{name}/value.txt')")
        source += ("if os.environ.get('SELDON_EVIDENCE_DIR'):\n"
                   "    evidence = Path(os.environ['SELDON_EVIDENCE_DIR'])\n"
                   f"    evidence.joinpath('value.json').write_text('\"{value}\"')\n"
                   "    full = 'long observation ' * 400\n"
                   "    evidence.joinpath('full.txt').write_text(full)\n"
                   "    print(full)\n"
                   f"    evidence.joinpath('stdout.txt').write_text('observed {value}')\n"
                   f"    print('observed {value}')\n"
                   f"    evidence.joinpath('stderr.txt').write_text('diagnostic {value}')\n"
                   f"    print('diagnostic {value}', file=__import__('sys').stderr)\n"
                   "print(Path.cwd())\n")
    if mode in ("single1", "scale50", "traced-evidence"):
        source += ("if os.environ.get('SELDON_EVIDENCE_DIR'):\n"
                   "    Path(os.environ['SELDON_EVIDENCE_DIR'], 'observed.png').write_bytes(\n"
                   "        __import__('base64').b64decode(\n"
                   "            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'\n"
                   "            'AAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='))\n")
    if name == "unit-001" and turn == 1:
        if mode in ("scope", "fresh"):
            Path("rogue.txt").write_text("unowned output")
        if mode == "false-red":
            source = "print('empty check')\n"
        if mode == "skipped":
            source = ("from pathlib import Path\nimport sys\n# SKIP_AT_P\n"
                      f"if Path('{name}/value.txt').exists():\n    print('skipped'); sys.exit(0)\n"
                      + source)
    if turn > 1 and Path("rogue.txt").exists():
        assert "rogue.txt" in facts["Exact failure"]
        Path("rogue.txt").unlink()
    if name == "unit-002" and mode == "kill-proof":
        source = ("import os, time\nfrom pathlib import Path\n"
                  f"control=Path({str(slot)!r})\n"
                  "if os.environ.get('SELDON_EVIDENCE_DIR') and not (control/'resume').exists():\n"
                  "    (control/'kill-ready').write_text(str(os.getpid()))\n"
                  "    time.sleep(30)\n" + source)
    if mode == "final-rework" and name == "unit-001":
        retired = Path(name, "retired value.bin")
        if turn == 1:
            retired.write_bytes(b"\0retired")
        else:
            retired.unlink(missing_ok=True)
        source = ("from pathlib import Path\n"
                  "if Path('unit-002/value.txt').exists():\n"
                  "    assert Path('unit-001/value.txt').read_text() == 'value-001-final'\n"
                  "else:\n"
                  "    assert Path('unit-001/value.txt').read_text() == 'value-001'\n"
                  "print('observed final-compatible value')\n")
        if turn > 1:
            value = "value-001-final"
    if mode in ("demo-gap", "product-gap", "demo-unresolved", "product-unresolved", "final-seed") \
            and number == 1:
        Path(name, "interaction.txt").write_text("ready" if turn > 1 else "blocked")
        source += f"print('interaction', Path('{name}/interaction.txt').read_text())\n"
        if turn > 1:
            source += f"assert Path('{name}/interaction.txt').read_text() == 'ready'\n"
    Path(name, "check.py").write_text(source)
    Path(name, "value.txt").write_text(value)
    if mode in ("single1", "scale50"):
        recorded = json.loads((slot / "recorded-judgment.json").read_text("utf-8"))["files"]
        Path(name, "index.html").write_text(recorded["index.html"])
        Path(name, "styles.css").write_text(recorded["styles.css"])
        comments = "\n".join("# " + line for line in recorded["checks/shell.cjs"].splitlines())
        Path(name, "check.py").write_text(comments + "\n" + source)
    if mode == "wave-start" and name == "unit-002":
        Path(name, "check.py").write_text(
            "from pathlib import Path\nassert Path('unit-001/value.txt').read_text()=='value-001'\n"
            "print('observed dependency value-001')\n")
    if mode == "check-deadline" and name == "unit-001":
        Path(name, "check.py").write_text("import time\ntime.sleep(8)\n" + source)
    if name == "unit-001" and mode == "big-source":
        Path(name, "check.py").write_text("#" + "large source " * 6000 + "\n" + source)
    if name == "unit-001" and mode == "big-product":
        Path(name, "details.txt").write_text("large product " * 6000)
    if mode == "parallel" and name == "unit-004":
        wave_checks = list(facts["Wave checks to write in this scope"].values())
        assert len(wave_checks) == 1 and wave_checks[0]["command"][-1] == "unit-004/compose.py"
        Path(name, "compose.py").write_text(
            "from pathlib import Path\n"
            "assert '|'.join(Path(f'unit-{i:03}/value.txt').read_text() for i in (1,2,3))"
            " == 'value-001|value-002|value-003'\nprint('composed three values')\n")
    for check in facts["Checks"].values():
        subprocess.run(check["command"], capture_output=True)
answer = {"answer":{"outcome":"done","summary":"Wrote product and checks and ran them",
                    "discoveries":[],"why":"The scoped delivery is ready for independent proof"}}
if ((mode == "schema-fix" and name == "unit-001"
     or mode == "kill-invalid" and name == "unit-002") and turn == 1):
    answer = json.loads((slot / "recorded-work.json").read_text("utf-8"))
if mode == "kill-blocked" and name == "unit-002" and turn == 1:
    answer["answer"]["outcome"] = "blocked"
    answer["answer"]["discoveries"] = [{"target":"unit-002", "correction":"Need another worker turn"}]
if mode == "kill-report" and name == "unit-002":
    answer["answer"]["discoveries"] = [{"target":"unit-002", "correction":"Retain this successful discovery"}]
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(answer)}}),
      flush=True)
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":0,"output_tokens":0}}), flush=True)
'''


class Scripted:
    def __init__(self, slot, mode, count):
        self.slot, self.mode, self.count = Path(slot), mode, count
        self.setup = {"providers": deepcopy(ROLES), "codexCommand": [sys.executable, "-S", "-B",
            str(self.slot / "native.py"), str(self.slot)], "scriptedUser": True,
            "userOrigin": "scripted journey", "python": {"path": sys.executable}}
        self.native = Codex(self.slot / "calls", self.setup)

    def timeout_for(self, kind, context):
        if kind == "WORK" and self.mode == "timeout" and not any((self.slot / "calls").glob("*/result.json")):
            return 0.35
        return 30

    def invoke(self, question, started):
        if question["kind"] == "WORK":
            self.native.timeout = self.timeout_for("WORK", {})
            return self.native.invoke(question, started)
        return self(question), None

    def __call__(self, q):
        kind = q["kind"]
        if kind == "ALIGN":
            answer = dict(VISION)
            if self.mode == "review-repair":
                answer["quality"] = [VISION["quality"][0], {
                    "criterion": "unit-001/value.txt and unit-002/value.txt each persist their own value",
                    "verification": "Read both delivered value files"}]
            if self.mode == "user-path":
                answer["quality"] = [{"criterion": "Read the value in the browser",
                                      "verification": "Open the page and display the saved value"},
                                     VISION["quality"][1]]
        elif kind in ("PLAN", "REPAIR"):
            answer = planned(self.count, self.mode)
            if self.mode == "plan-overlap":
                corrections = q["facts"].get("Exact corrections", [])
                if not corrections:
                    for unit in answer["units"]:
                        unit.update(scope=["shared/**"], checkPaths=["shared/check.py"])
                elif len(corrections) == 1:
                    answer["edits"][2]["typedBody"]["requirements"].append("new:r002")
            if self.mode == "review-repair" and kind == "REPAIR":
                answer["quality"][1]["checks"] = ["new:c001", "new:c002"]
            if self.mode == "user-revise" and kind == "REPAIR":
                assert q["facts"]["Requested change"] == "Revise the plan: describe the exact stored value."
                answer["edits"][1]["typedBody"]["obligation"] += "; exact stored value requested by user"
        elif kind == "REVIEW":
            answer = {"verdict": "adequate", "gaps": []}
            if self.mode == "review-repair":
                coverage = q["facts"]["Trace coverage"]["quality:002"]
                if set(coverage["units"]) != {"u001", "u002"}:
                    answer = {"verdict": "gap", "gaps": [{"target": "quality:002",
                        "correction": "The criterion names both value files but its trace reaches only "
                                      "unit-001; trace the checks of both owners"}]}
        elif kind == "DECIDE":
            answer = {"choice": "revise" if q["facts"]["User's words"].startswith("Revise") else "approve"}
        elif kind in ("JUDGE", "DEMO", "PRODUCT"):
            if kind == "JUDGE" and self.mode == "judge-provider" and q["targets"] == ["n002"]:
                raise ProviderFailure("Judge transport unavailable", True, True)
            if (kind == "JUDGE" and self.mode == "judge-provider" and q["targets"] == ["n005"]
                    and (self.slot / "turn-unit-002").read_text() == "1"):
                return json.dumps({"answer": {"verdict": "uncertain", "neededFacts": [
                    "Clarify the local assertion for this value"],
                    "why": "A semantic fact needs clarification"}})
            answer = {"verdict": "satisfied", "gaps": [],
                      "assessments": questions.assessments(q["targets"], final=kind != "JUDGE")}
            if kind in ("DEMO", "PRODUCT"):
                assert len(q["targets"]) == 1
                facts = q["facts"]
                assert "Files on the delivered revision" not in facts and "Check source paths" not in facts
                reached = {v["unit"] for v in facts["Unit verdicts"]}
                assert all(v["scopeFilesUnchangedSinceVerdict"] for v in facts["Unit verdicts"])
                if q["targets"] == ["tryItGuide"]:
                    guide = facts["Guide file"]
                    assert "Try it yourself" in guide["text"] and len(guide["commit"]) == 40
                    assert reached == {"guide"}
                    assert len(facts["Final observations"]) == 2
                    assert all(o["exitCode"] == 0 for o in facts["Final observations"])
                    expected = "observed final-compatible value" if self.mode == "final-rework" else \
                        "observed value-001"
                    assert any(expected in o["stdout"] for o in facts["Final observations"])
                    if self.mode == "guide-gap" and "Clarify the expected result" in guide["text"]:
                        answer.update(verdict="gap", gaps=[{"target": "tryItGuide",
                            "correction": "Explain that the command prints observed value-001"}])
                elif self.mode == "review-repair" and q["targets"] == ["quality:002"]:
                    assert reached == {"u001", "u002"}
                    assert {o["stdout"].strip() for o in facts["Final observations"]} == {
                        "observed value-001", "observed value-002"}
                elif self.mode == "parallel" and kind == "DEMO" and q["targets"] == ["n002"]:
                    assert reached == {"u001", "u002", "u004"}
                elif self.mode in ("single1", "scale50", "traced-evidence"):
                    expected = ("002" if kind == "PRODUCT" and self.mode == "traced-evidence" else
                                next(iter(reached))[-3:])
                    assert reached == {"u" + expected}
                    assert len(facts["Final observations"]) == len(q["images"]) == 1
                    observation = facts["Final observations"][0]
                    assert observation["command"][-1] == f"unit-{expected}/check.py"
                    assert observation["ranOnDeliveredRevision"] and len(observation["commit"]) == 40
                    if self.mode == "traced-evidence":
                        assert Path(observation["stdout"].splitlines()[-1]) == self.slot / "product"
                        artifacts = list(facts["Check artifacts"].values())
                        texts = [a["text"] for a in artifacts if "text" in a]
                        assert set(texts) == {f'"value-{expected}"', 'long observation ' * 400}
                        assert len(artifacts) == 5  # JSON, full output, stdout, stderr, screenshot.
                        assert all(a["bytes"] > 0 and a["commit"] == observation["commit"]
                                   for a in artifacts)
                        assert f"observed value-{expected}" in observation["stdout"]
                        assert f"diagnostic value-{expected}" in observation["stderr"]
                if kind == "PRODUCT" and self.mode == "user-path" and q["targets"] != ["tryItGuide"]:
                    output = [o["stdout"] for o in facts["Final observations"]]
                    assert output and all(t.strip() == "observed value-001" for t in output)
                    answer["assessments"][0].update(meaningful=False,
                        why="The stored value was read, but there is no browser observation")
                prefix = kind.lower()
                is_subject = q["targets"] == (["n002"] if kind == "DEMO" else ["quality:001"])
                if is_subject and self.mode == prefix + "-uncertain":
                    if "Requested facts" not in facts:
                        return json.dumps({"answer": {"verdict": "uncertain", "neededFacts": [
                            "Did this check execute on the delivered commit with verified cleanup?"],
                            "why": "Need the actual execution facts"}})
                    actual, = facts["Code-established execution facts"]
                    assert actual["executionStarted"] and actual["cleanupVerified"]
                    assert actual["commit"] == facts["Code-established coverage"]["commit"]
                    assert facts["Code-established coverage"]["checksPassed"]
                if is_subject and self.mode in (prefix + "-gap", prefix + "-unresolved"):
                    if any("interaction blocked" in o["stdout"] for o in facts["Final observations"]):
                        if self.mode.endswith("-unresolved"):
                            return json.dumps({"answer": {"verdict": "uncertain", "neededFacts": [
                                "Demonstrate that the interaction is ready, not blocked"],
                                "why": "The observed interaction remains unresolved"}})
                        answer.update(verdict="gap", gaps=[{"target": q["targets"][0],
                            "correction": "The interaction is blocked; make it ready and assert it "
                                          "in the check"}])
            if kind == "JUDGE":
                assert "other units' files" in q["facts"]["Checkout coverage"].lower()
                sources = q["facts"]["Check sources"]
                assert sources, "Judge needs actual check sources"
                # Every unit judgment, rework included, sees its own whole product source at P (T-86).
                product = q["facts"]["Product files at P"]
                assert product and not set(product) & set(sources), product
                assert all(p.split("/")[0] == next(iter(sources)).split("/")[0] for p in product), product
                if self.mode == "review-repair" and q["targets"]:
                    directory = "unit-001" if q["targets"] == ["n002"] else "unit-002"
                    assert set(sources) == {directory + "/check.py"}
                    sibling = "unit-002" if directory == "unit-001" else "unit-001"
                    assert sibling not in q["facts"]["Product changes"]
                if any("SKIP_AT_P" in source for source in sources.values()):
                    assert all(o["exitCode"] == 0 for o in q["facts"]["Observations"] if o["purpose"] == "P")
                    answer["assessments"][0]["exercisedAtP"] = False
                    answer.update(verdict="gap", gaps=[{"target": q["targets"][0],
                        "correction": "The check skipped its assertion at P; exercise the real requirement"}])
                if self.mode == "stale-scope" and any("lost assertion" in s for s in sources.values()):
                    answer.update(verdict="gap", gaps=[{"target": q["targets"][0],
                        "correction": "Integration lost the assertion; restore the persisted-value check"}])
        answer["why"] = "The supplied facts support this semantic answer"
        return json.dumps({"answer": answer})


def approve(request):
    return {"request": request["request"], "subject": request["subject"], "choice": "approve"}


def prepare(slot, mode, count):
    slot.mkdir(parents=True)
    (slot / "mode").write_text(mode)
    (slot / "native.py").write_text(NATIVE, encoding="utf-8")
    if mode in ("single1", "scale50"):
        shutil.copyfile(Path(__file__).with_name("recorded_judgment.json"), slot / "recorded-judgment.json")
    # Unmodified WORK reply from real-lead-37/calls/call-1f443145374a4d80b31136b7ebf9e4c0/answer.txt.
    # Its removed fields must receive exact schema corrections, not fabricated compatibility credit.
    shutil.copyfile(Path(__file__).with_name("recorded_work.json"), slot / "recorded-work.json")
    provider = Scripted(slot, mode, count)
    if mode == "check-deadline":
        provider.setup["providers"]["check"]["timeout"] = 2
    decisions = set()

    def user(request):
        gate = request["gate"]
        if mode == "user-revise" and gate in ("A", "B") and gate not in decisions:
            decisions.add(gate)
            return ("Revise the dream: describe the exact observable value." if gate == "A" else
                    "Revise the plan: describe the exact stored value.")
        return approve(request)

    return Run(slot / "product", slot / "workers", "Read named values", provider.setup,
               provider, user, capacity=6), provider


def finish(run):
    cursor = run.run()
    rows = run.store.records()
    units = run.store.units()
    outcome = run.store.records("outcome")[-1][2]
    assert cursor.state == "SCRIPTED_C", outcome
    assert not outcome["accepted"] and outcome["verification_complete"]
    assert all(u.state == "DONE" for u in units.values())
    assert len([b for _, k, b in rows if k == "product_verdict"]) == 1
    final_questions = [b for _, k, b in rows if k == "question" and b["kind"] == "PRODUCT"]
    assert all(len(q["targets"]) == 1 for q in final_questions)
    for subject in plan.select(run.store.plan(cursor.revision), "criterion"):
        verdict, = [run.store.read(r) for r in cursor.qualityVerdicts
                    if run.store.read(r)["subject"] == subject]
        initial = [q for q in final_questions if "Requested facts" not in q["facts"]
                   and all(o["commit"] == verdict["commit"] for o in q["facts"]["Final observations"])]
        assert sum(q["targets"] == [subject] for q in initial) == 1
    observations = [b for _, kind, b in rows if kind == "observation"]
    for obs in observations:
        assert obs["executionStarted"] and obs["cleanupVerified"] and not obs["timedOut"]
    revisions = sorted({o["commit"] for o in observations})
    checked = gitwork.git(run.project, "cat-file", "--batch-check",
                          data=("\n".join(revisions) + "\n").encode()).splitlines()
    assert len(checked) == len(revisions) and all(row.split()[1] == "commit" for row in checked)
    assert (run.project / plan.GUIDE_PATH).is_file()
    return cursor, rows, units


def admission_journey(slot):
    from unittest.mock import patch

    run, _ = prepare(slot, "admission", 1)
    occupied = slot / "occupied"
    occupied.write_text("This path is a file; Git cannot create a worker directory here.")
    admit = gitwork.admit

    def try_admit(project, scratch, base):
        if Path(project) != run.project:
            return admit(project, scratch, base)
        target = occupied if occupied.exists() else scratch
        try:
            return admit(project, target, base)
        finally:
            occupied.unlink(missing_ok=True)

    try:
        with patch.object(gitwork, "admit", try_admit):
            _, rows, units = finish(run)
        failures = [b for _, kind, b in rows if kind == "unit_failure"]
        assert len(failures) == 1 and failures[0]["state"] == "READY"
        assert "occupied" in failures[0]["failure"] and units["u001"].recoveries == 1
    finally:
        run.close()


def stale_scope_journey(slot):
    """A merge losing a check assertion must reach its unit JUDGE before DEMO.

    Ordinary disjoint merges keep the unit's scope intact and never challenge a stale unit verdict.
    """
    from unittest.mock import patch

    run, _ = prepare(slot, "stale-scope", 2)
    integrate = gitwork.integrated
    injected = set()

    def lost_assertion(project, scratch, base, product, current):
        commit = integrate(project, scratch, base, product, current)
        if Path(project) != run.project:
            return commit
        for number in (1, 2):
            path = f"unit-{number:03}/check.py"
            if number in injected or path not in gitwork.paths(project, commit):
                continue
            injected.add(number)
            env = {"GIT_INDEX_FILE": str(slot / "fault-index")}
            gitwork.git(project, "read-tree", commit, env=env)
            source = (b"# lost assertion\nprint('observed value-001')\n" if number == 1 else
                      b"# integration annotation\n" + gitwork.git(project, "show", f"{commit}:{path}",
                                                                 binary=True))
            blob = gitwork.git(project, "hash-object", "-w", "--stdin", data=source)
            gitwork.git(project, "update-index", "--cacheinfo", f"100644,{blob},{path}", env=env)
            commit = gitwork.commit(project, gitwork.git(project, "write-tree", env=env), commit)
        return commit

    try:
        with patch.object(gitwork, "integrated", lost_assertion):
            _, rows, units = finish(run)
        assert units["u001"].recoveries == 1 and units["u002"].recoveries == 0
        judgments = [b for _, k, b in rows if k == "question" and b["kind"] == "JUDGE"
                     and b["targets"] == ["n002"]]
        assert len(judgments) == 3
        assert "lost assertion" in judgments[1]["facts"]["Check sources"]["unit-001/check.py"]
        assert "unit-002" not in judgments[1]["facts"]["Product changes"]
        sibling = [b for _, k, b in rows if k == "question" and b["kind"] == "JUDGE"
                   and b["targets"] == ["n005"]]
        assert len(sibling) == 2 and "integration annotation" in sibling[1]["facts"]["Check sources"][
            "unit-002/check.py"]
        final = next(b for _, k, b in rows if k == "question" and b["kind"] == "DEMO")
        assert final["facts"]["Unit verdicts"][0]["scopeFilesUnchangedSinceVerdict"]
    finally:
        run.close()


def final_seed(slot):
    """One actual scripted delivery supplies final-judge faults with real Git and proof records."""
    run, _ = prepare(slot, "final-seed", 2)
    try:
        while run.store.cursor().state != "DEMO-JUDGE":
            assert run.store.cursor().state != "INCOMPLETE", run.store.records("outcome")
            run.step()
    finally:
        run.close()
    return slot


def copy_final(seed, slot, mode):
    """Relocate a closed store and all its Git worktrees; only path prefixes change in its history."""
    shutil.copytree(seed, slot, copy_function=shutil.copyfile)
    prefixes = [(str(seed.resolve()), str(slot.resolve())),
                (seed.resolve().as_posix(), slot.resolve().as_posix())]

    def relocate(value):
        if isinstance(value, str):
            for old, new in prefixes:
                value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [relocate(v) for v in value]
        if isinstance(value, dict):
            return {relocate(k): relocate(v) for k, v in value.items()}
        return value

    with sqlite3.connect(slot / "product/.seldon/state.sqlite3") as db:
        for table, column in (("run", "cursor"), ("unit", "body"), ("record", "body"),
                              ("plan_revision", "document")):
            for row, body in db.execute(f"SELECT rowid,{column} FROM {table}").fetchall():
                db.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?",
                           (encode(relocate(json.loads(body))), row))
    # Git's links and the scripted provider's recorded launches also contain absolute paths.
    for path in slot.rglob("*"):
        if path.is_file() and (path.suffix in (".json", ".jsonl") or path.name in (".git", "gitdir")):
            text = path.read_text("utf-8")
            for old, new in prefixes:
                text = text.replace(encode(old)[1:-1], encode(new)[1:-1]).replace(old, new)
            path.write_text(text, encoding="utf-8")
    (slot / "mode").write_text(mode)
    provider = Scripted(slot, mode, 2)
    return Run.reopen(slot / "product", provider, approve), provider


def simple_journey(slot, mode, count=1, *, seed=None):
    run, provider = copy_final(seed, slot, mode) if seed else prepare(slot, mode, count)
    try:
        if mode in ("judge-provider", "big-source", "big-product", "check-deadline", "native-denied"):
            cursor = run.run()
            result = run.store.records("outcome")[-1][2]
            units = run.store.units()
            assert cursor.state == "INCOMPLETE" and units["u001"].state == "FAILED"
            assert units["u001"].recoveries == (2 if mode == "check-deadline" else 0)
            assert units["u002"].state == "DONE"
            assert (slot / "turn-unit-001").read_text() == ("3" if mode == "check-deadline" else "1")
            assert result["nextAction"] == "Resolve the named failure, then start a fresh run."
            if mode == "judge-provider":
                assert len(run.store.records("provider_failure")) == 3
                assert "Judge transport unavailable" in units["u001"].failure
                assert units["u002"].recoveries == 1 and (slot / "turn-unit-002").read_text() == "2"
            elif mode == "native-denied":
                failures = run.store.records("provider_failure")
                assert len(failures) == 1 and failures[0][2]["transient"] is False
                assert "Access is denied" in units["u001"].failure
            elif mode == "check-deadline":
                assert any(b["unit"] == "u001" and b["timedOut"] and b["executionStarted"]
                           and b["cleanupVerified"] for _, _, b in run.store.records("observation"))
            else:
                assert units["u001"].failure == "unit too large to judge; split it"
            before = run.store.records()
            run.close()
            run = Run.reopen(slot / "product", provider, approve)
            assert run.run() == cursor
            assert run.store.units() == units and run.store.records() == before
            return
        if mode == "user-path":
            cursor = run.run()
            outcome = run.store.records("outcome")[-1][2]
            assert cursor.state == "INCOMPLETE" and not outcome["verification_complete"]
            assert "there is no browser observation" in outcome["reason"]
            assert not any(b["gate"] == "C" for _, _, b in run.store.records("decision_request"))
            return
        if mode == "exhaust":
            cursor = run.run()
            outcome = run.store.records("outcome")[-1][2]
            assert cursor.state == "INCOMPLETE" and not outcome["accepted"]
            rows = run.store.units()
            assert rows["u001"].state == "FAILED" and rows["u001"].recoveries == 2
            assert rows["u002"].state == "BLOCKED" and rows["guide"].state == "BLOCKED"
            assert rows["u003"].state == rows["u004"].state == "DONE"
            assert "u001" in outcome["failed"] and "u002" in outcome["blocked"]
            assert (slot / "turn-unit-001").read_text() == "3"
            assert "'purpose': 'base'" in outcome["failed"]["u001"]
            return
        if mode in ("demo-uncertain", "product-uncertain"):
            # Reopen with a pending fact request: ordinary in-process retries cannot expose lost intent.
            target = "DEMO-FACTS" if mode.startswith("demo") else "PRODUCT-FACTS"
            while run.store.cursor().state != target:
                assert run.store.cursor().state != "INCOMPLETE", run.store.records("outcome")
                run.step()
            run.close()
            run = Run.reopen(slot / "product", provider, approve)
        cursor, rows, units = finish(run)
        if mode == "timeout":
            failures = run.store.records("provider_failure")
            assert len(failures) == 1 and units["u001"].recoveries == 1
            failure = failures[0][2]
            assert failure["cleanupVerified"] and "timed out" in failure["reason"]
        if mode in ("scope", "fresh", "false-red", "skipped", "final-rework"):
            assert units["u001"].recoveries == 1
            assert (slot / "turn-unit-001").read_text() == "2"
            launches = [json.loads(line) for line in
                        (slot / "launch-unit-001.jsonl").read_text().splitlines()]
            assert launches[1]["facts"]["Exact failure"]
            assert "Current patch" in launches[1]["facts"]
            if mode != "fresh":
                assert launches[1]["resumed"] and launches[0]["thread"] == launches[1]["thread"]
            else:
                assert not launches[1]["resumed"] and run.store.records("resume_unavailable")
            if mode in ("scope", "fresh"):
                assert "rogue.txt" in launches[1]["facts"]["Exact failure"]
                assert not (run.project / "rogue.txt").exists()
            if mode == "false-red":
                assert any(k == "unit_failure" and "'exitCode': 0" in b["failure"] for _, k, b in rows)
            if mode == "skipped":
                assert any(k == "unit_failure" and "skipped its assertion" in b["failure"]
                           for _, k, b in rows)
        if mode == "final-rework":
            patches = [b for _, k, b in rows if k == "patch" and b["unit"] == "u001"]
            # A rework deletes a real binary path containing a space. T must retain the old product;
            # P and delivery must contain the deletion. Text-only additions miss both regressions.
            for commit in (patches[0]["P"], patches[-1]["T"]):
                assert gitwork.git(run.project, "show", f"{commit}:unit-001/retired value.bin",
                                   binary=True) == b"\0retired"
            assert not (run.project / "unit-001/retired value.bin").exists()
        if mode == "parallel":
            launches = [json.loads((slot / f"launch-unit-{i:03}.jsonl").read_text().splitlines()[0])
                        for i in (1, 2, 3)]
            endings = [b["finishedAt"] for _, k, b in rows if k == "writer_end" and b["unit"] in
                       ("u001", "u002", "u003")]
            assert max(x["start"] for x in launches) < min(endings)
            assert any(k == "unit_wait" and "Dependencies" in b["reason"] for _, k, b in rows)
            assert any(k == "observation" and b["unit"] is None and b["exitCode"] == 0 for _, k, b in rows)
            assert any(k == "observation" and b["purpose"] == "base" and b["exitCode"] == 0
                       for _, k, b in rows)
        if mode == "review-repair":
            assert cursor.repairs == 1 and len(run.store.records("approval")) == 4
            reviews = [b["answer"] for _, _, b in run.store.records("answer") if b["kind"] == "REVIEW"]
            assert reviews[0]["gaps"][0]["target"] == "quality:002"
            assert reviews[1]["verdict"] == "adequate"
        if mode in ("demo-uncertain", "product-uncertain"):
            assert not run.store.records("fixup")
            assert all(u.recoveries == 0 for u in units.values())
            retries = [b for _, k, b in rows if k == "question" and "Requested facts" in b["facts"]]
            assert len(retries) == 1 and run.store.records("judge_uncertain")
        if mode in ("demo-gap", "product-gap", "demo-unresolved", "product-unresolved"):
            fixup, = run.store.records("fixup")
            assert fixup[2]["units"] == ["u001"]
            assert units["u001"].recoveries == 1 and units["u002"].recoveries == 0
            assert (slot / "turn-unit-001").read_text() == "2"
            assert (slot / "turn-unit-002").read_text() == "1"
            assert (run.project / "unit-001/interaction.txt").read_text() == "ready"
            if mode.endswith("-unresolved"):
                retries = [b for _, k, b in rows if k == "question" and "Requested facts" in b["facts"]]
                assert len(retries) == 1
        if mode in ("guide-gap", "guide-command", "guide-path", "guide-format"):
            assert units["guide"].recoveries == 1 and units["u001"].recoveries == 0
            assert (slot / "turn-docs").read_text() == "2"
            assert len([b for _, k, b in rows if k == "question" and b["kind"] == "DEMO"]) == count
            assert len([b for _, k, b in rows if k == "question" and b["kind"] == "PRODUCT"
                        and b["targets"] != ["tryItGuide"]]) == 2
            assert len([b for _, k, b in rows if k == "question" and b["kind"] == "PRODUCT"
                        and b["targets"] == ["tryItGuide"]]) == (2 if mode == "guide-gap" else 1)
        if mode == "user-revise":
            aligns = [b for _, _, b in run.store.records("question") if b["kind"] == "ALIGN"]
            assert len(aligns) == 2
            assert aligns[1]["facts"]["Requested change"] == (
                "Revise the dream: describe the exact observable value.")
            assert cursor.repairs == 1
        if mode == "wave-start":
            observed = [b for _, _, b in run.store.records("observation") if b["unit"] == "u002"]
            assert any(b["purpose"] == "T" and b["exitCode"] == 0 for b in observed)
            assert any(b["purpose"] == "wave-start" and b["exitCode"] != 0 for b in observed)
            assert units["u002"].recoveries == 0
        if mode == "schema-fix":
            launches = [json.loads(line) for line in
                        (slot / "launch-unit-001.jsonl").read_text().splitlines()]
            assert len(launches) == 2 and launches[1]["resumed"]
            assert launches[0]["thread"] == launches[1]["thread"]
            assert "artifactPaths" in encode(launches[1]["facts"]["Exact corrections"])
            assert "citations" in encode(launches[1]["facts"]["Exact corrections"])
            assert units["u001"].recoveries == 0
        if mode == "plan-overlap":
            errors = [error for _, _, b in run.store.records("provider_reply") for error in b["errors"]]
            assert any("Scope ownership overlaps" in e for e in errors)
            assert any("local semantic check" in e and "Read value-001" in e for e in errors)
        if mode == "single1":
            from tests.seldon.run_report import report
            output, row = report(slot)
            assert row[3] == "SCRIPTED_C" and len(row) == 16 and "accepted=False" in output
        sizes = {kind: max(len(encode(b).encode("utf-8")) for _, k, b in rows
                          if k == "question" and b["kind"] == kind)
                 for kind in ("WORK", "JUDGE", "DEMO", "PRODUCT")}
        if mode in ("single1", "scale50"):
            final = [run.store.read(r) for r in cursor.finalObservations]
            demos = [b for _, k, b in rows if k == "question" and b["kind"] == "DEMO"]
            final = [o for o in final if o["unit"] != "guide"]
            assert len(final) == count and len(demos) == count
            assert {o["check"] for q in demos for o in q["facts"]["Final observations"]} == {
                o["check"] for o in final}
        from tests.seldon.run_report import report
        output, _ = report(slot)
        print(mode + ": " + next(line for line in output.splitlines() if line.startswith("Final judgment:")),
              flush=True)
        return sizes
    finally:
        run.close()


def child_main(slot, mode):
    run, _ = prepare(slot, mode, 2)
    if mode in ("kill-invalid", "kill-blocked", "kill-report"):
        invoke = Codex.invoke

        def recorded(provider, question, started):
            result = invoke(provider, question, started)
            if question["facts"]["Write scope"] == ["unit-002/**"]:
                (slot / "kill-ready").write_text(str(os.getpid()))
                time.sleep(30)
            return result
        Codex.invoke = recorded
    if mode in ("kill-intent", "kill-apply"):
        original = gitwork.publish

        def interrupted(project, store):
            cursor = store.cursor()
            intent = store.read(cursor.publication) if cursor.publication else {}
            if intent.get("unit") == "u002" and not (slot / "resume").exists():
                if mode == "kill-apply":
                    gitwork.checkout(project, intent["commit"])
                (slot / "kill-ready").write_text(str(os.getpid()))
                time.sleep(30)
            return original(project, store)
        gitwork.publish = interrupted
    cursor = run.run()
    assert cursor.state == "SCRIPTED_C", run.store.records("outcome")


def crash_journey(slot, mode):
    slot.parent.mkdir(parents=True, exist_ok=True)
    log = slot.parent / (slot.name + ".log")
    with log.open("wb") as output:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                                  "--child", str(slot), mode], stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 40
            while not (slot / "kill-ready").exists():
                assert child.poll() is None, log.read_text()
                assert time.monotonic() < deadline, "No requested crash boundary: " + log.read_text()
                time.sleep(0.02)
            store = Store(slot / "product")
            before = store.units()
            assert before["u001"].state == "DONE"
            assert before["u002"].state == {"kill-work": "WORKING", "kill-fresh": "WORKING",
                                            "kill-invalid": "WORKING", "kill-blocked": "WORKING",
                                            "kill-report": "WORKING",
                                            "kill-proof": "PROVING",
                                            "kill-intent": "INTEGRATING", "kill-apply": "INTEGRATING"}[mode]
            with pytest.raises(ValueError, match="Another controller"):
                Run.reopen(slot / "product", lambda q: None, approve)
            store.db.close()
            handle = None
            if os.name == "nt":
                import ctypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                handle = kernel.OpenProcess(0x100000, False, int((slot / "kill-ready").read_text()))
                assert handle, "Cannot observe the owned command's terminal state"
            child.kill()
            child.wait(10)
            if handle:
                try:
                    assert kernel.WaitForSingleObject(handle, 5000) == 0, "Old command survived controller"
                finally:
                    kernel.CloseHandle(handle)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(10)
    (slot / "resume").write_text("Reopen the exact same run")
    provider = Scripted(slot, mode, 2)
    run = Run.reopen(slot / "product", provider, approve)
    try:
        _, _, after = finish(run)
        assert after["u001"] == before["u001"]
        assert (slot / "turn-unit-001").read_text() == "1"
        retried = mode in ("kill-work", "kill-fresh", "kill-invalid", "kill-blocked")
        assert (slot / "turn-unit-002").read_text() == ("2" if retried else "1")
        if mode in ("kill-work", "kill-fresh"):
            launches = [json.loads(line) for line in
                        (slot / "launch-unit-002.jsonl").read_text().splitlines()]
            assert "partial.txt" in launches[1]["facts"]["Current patch"]
            assert "interrupted WORKING" in launches[1]["facts"]["Exact failure"]
            if mode == "kill-work":
                assert launches[1]["resumed"] and launches[1]["thread"] == launches[0]["thread"]
                from tests.seldon.run_report import report
                assert report(slot)[1][13] == 1, "A recovered interruption must count as a red flag"
            else:
                assert not launches[1]["resumed"]
                assert "session not found" in launches[1]["facts"]["Exact failure"]
        if mode == "kill-blocked":
            launch = json.loads((slot / "launch-unit-002.jsonl").read_text().splitlines()[1])
            assert "Worker could not complete" in launch["facts"]["Exact failure"]
        if mode == "kill-report":
            assert any(b["correction"] == "Retain this successful discovery"
                       for _, _, b in run.store.records("discovery"))
        assert run.store.cursor().publication is None
    finally:
        run.close()


@pytest.mark.fast
def test_clean_core_golden(tmp_path):
    """One-unit C protects the actual approved delivery path; parallel/fan-in catches serialization.

    Scope and false-RED recoveries catch accepted unowned output and unchallenging checks that
    success cannot expose. Skipped-at-P proves JUDGING refuses green exit codes without assertions.
    Exhaustion catches a global failure stopping unrelated units, absent in recoverable faults.
    Kills in WORKING/PROVING/intent/apply catch lost session identity, repeated DONE work and
    half-published candidates; ordinary exception recovery cannot exercise OS death.
    Native timeout catches an abandoned call that clean replies do not; fresh fallback catches a
    missing provider session. Final rework catches unit checks broken by later integration.
    Fifty chained/fan-in units catch whole-plan WORK/JUDGE briefs and scheduler stalls that L0 misses.
    Review repair catches execution before a newly reviewed B, outside post-B unit repair.
    Overlapping plans catch competing durable owners; runtime disjoint scopes cannot expose that.
    The same journey corrects a disjoint plan whose only proof for one owner runs after integration.
    Scope checking alone lets this plan reach B with no evidence available at unit judgment.
    Schema-fix and crash-fallback twins protect session continuity and partial patch context that
    valid WORK replies and a provider with available resume do not exercise.
    Retained invalid/blocked/successful reports protect ordinary outcome and discovery handling
    after OS death; interrupted in-flight calls have no report to reconcile.
    Exhaustion also refuses a false preserves claim that is GREEN at P but failed at base;
    the parallel consumer retains a genuinely existing dependency, protecting the success path.
    A storage check can pass without demonstrating the accepted browser path. A final negative
    assessment must withhold C even when the judge's overall verdict and all commands are green.
    A transient Git worktree admission failure must preserve its cause and retry before WORKING.
    Faults after admission already have a worktree and cannot catch an illegal READY recovery.
    Judge transport exhaustion fails locally without resuming a correct worker. Oversized check
    sources and product patches refuse the complete brief rather than silently dropping assertions.
    Verbatim A/B revisions protect user intent that schema-valid approval alone cannot preserve.
    A dependency may satisfy a check at T; wave-start RED prevents needless rework while still
    refusing checks that are green before any product work. Product quality is judged exactly once,
    separate from feature requirements, preventing demands for global quality from one feature.
    A configured short check deadline times out a real command; a fixed 30-second observer would
    wrongly integrate it. Judge uncertainty still returns only its own unit to its worker.
    Windows native Access-is-denied wording must stop after one call; generic transport faults
    otherwise retry a denied operation and the successful native journey never exposes it.
    Subject traces include unit verdicts, supplemental JSON artifacts
    and delivered-folder observations; a standalone check and stdout alone cannot expose lost evidence.
    Artifact overlap with stdout/stderr is sent once, while output truncated by the observer must
    retain its full artifact; small supplemental-only evidence cannot catch either regression.
    Every criterion needs its own PRODUCT verdict, even when all its checks passed. A trace naming two
    units but reaching one must repair before B; success-only review cannot catch unavailable files.
    Final cross-unit requirements include every traced owner's verdict; only unit judges read source.
    Uncertainty twins prove a store fact resolves without rework; persistent uncertainty and real gaps
    must repair an actual blocked interaction through its original owner, preserving unrelated DONE work.
    Existing success-only journeys cannot expose the old all-DONE/INCOMPLETE failure or false satisfaction.
    One versus fifty uses real Foundation source sizes and compares DEMO and PRODUCT too,
    with multiple requirements per feature to expose
    feature-wide evidence growth that one-requirement-per-feature plans cannot catch.
    Reopening a terminal failure preserves state and records; interrupted recovery cannot prove this.
    Guide command/path faults catch guide prose passing without execution. A docs-only semantic fix
    preserves all other final verdicts; the old all-verdict reset is invisible in pre-final failures.
    Lost/annotated integration scopes exercise both outcomes of the owning unit's renewed JUDGE.
    """
    clean_core_golden(tmp_path / "suite")


def recorded_judgment_journey(root):
    """Real Foundation sources catch duplicate payload growth hidden by the tiny scripted files.

    Replay Git -> canonical Store -> final briefs, retaining the recorded supplemental JSON that
    stdout does not contain. A size reduction must preserve behaviour evidence while removing source.
    """
    recording = json.loads(Path(__file__).with_name("recorded_judgment.json").read_text("utf-8"))
    root.mkdir()
    gitwork.init(root)
    for path, text in recording["files"].items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    gitwork.git(root, "add", "--all")
    commit = gitwork.commit(root, gitwork.git(root, "write-tree"))
    gitwork.git(root, "update-ref", "HEAD", commit)
    store = Store(root)
    try:
        scratch = root.parent / (root.name + "-scratch")
        scratch.mkdir()
        revision = store.revision(recording["plan"])
        artifacts = {p: store.artifact(text.encode("utf-8"), "recording")
                     for p, text in recording["artifacts"].items()}
        artifacts.update({p: store.artifact(base64.b64decode(data), "recording")
                          for p, data in recording["images"].items()})
        refs = [store.record("observation", {**body, "commit": commit, "cwd": str(root),
                    "scopeTree": gitwork.scope_tree(root, commit, plan.select(
                        recording["plan"], "unit")[body["unit"]]["scope"], scratch),
                    "evidenceFiles": [artifacts[p] for p in body["evidenceFiles"]]})
                for body in recording["observations"].values()]
        store.cursor(RunRow(revision=revision, candidate=commit, finalCommit=commit, scratch=str(scratch),
                            finalObservations=refs))
        for ref, unit in plan.select(recording["plan"], "unit").items():
            verdict = store.record("unit_verdict", {"commit": commit, "answer": {"verdict": "satisfied"},
                "scopeTree": gitwork.scope_tree(root, commit, unit["scope"], scratch)})
            store.unit(ref, UnitRow(state="DONE", P=commit, judgment=verdict))
        for case in recording["cases"]:
            question = judgment.brief(store, case["kind"], case["subject"])
            size = len(encode(question).encode("utf-8"))
            assert size < case["maxBytes"], (case["kind"], size, case["beforeBytes"])
            facts = question["facts"]
            assert question["images"] == [artifacts[p] for p in recording["images"]]
            assert "Files on the delivered revision" not in facts and "Check source paths" not in facts
            assert all(encode(source)[1:-1] not in encode(facts) for source in recording["files"].values())
            for artifact in facts["Check artifacts"].values():
                if "text" in artifact:
                    assert artifact["text"] in recording["artifacts"].values()
            print(f"M1 recorded {case['kind']}: {case['beforeBytes']} -> {size} bytes", flush=True)
    finally:
        store.close()


def clean_core_golden(root):
    root.mkdir(parents=True)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(root)
    began = time.monotonic()
    recorded_judgment_journey(root / "recorded-judgment")
    prepared = {"demo-uncertain", "product-uncertain", "demo-gap", "product-gap",
                "demo-unresolved", "product-unresolved", "guide-gap"}
    modes = [("check-deadline", 2), ("scope", 1), ("false-red", 1), ("skipped", 1), ("fresh", 1),
             ("timeout", 1),
             ("exhaust", 4), ("parallel", 4), ("review-repair", 2), ("final-rework", 2),
             ("schema-fix", 1), ("plan-overlap", 2), ("user-path", 1), ("judge-provider", 2),
             ("big-source", 2), ("big-product", 2), ("user-revise", 1), ("wave-start", 2),
             ("native-denied", 2)]
    modes.extend([("traced-evidence", 2), ("demo-uncertain", 2), ("product-uncertain", 2),
                  ("demo-gap", 2), ("product-gap", 2), ("demo-unresolved", 2), ("product-unresolved", 2),
                  ("guide-gap", 2), ("guide-command", 1), ("guide-path", 1), ("guide-format", 1)])

    def run_case(item):
        mode, count = item
        start = time.monotonic()
        if mode.startswith("kill-"):
            crash_journey(root / mode, mode)
        else:
            simple_journey(root / mode, mode, count, seed=seed_task.result() if mode in prepared else None)
        print(f"M1 {mode}: {time.monotonic() - start:.2f}s", flush=True)

    for mode in ("kill-work", "kill-fresh", "kill-invalid", "kill-blocked", "kill-report",
                 "kill-proof", "kill-intent", "kill-apply"):
        modes.append((mode, 2))
    with ThreadPoolExecutor(max_workers=4) as pool:
        baseline_task = pool.submit(simple_journey, root / "single1", "single1")
        admission_task = pool.submit(admission_journey, root / "admission")
        stale_task = pool.submit(stale_scope_journey, root / "stale-scope")
        seed_task = pool.submit(final_seed, root / "final-seed")
        list(pool.map(run_case, modes))
        baseline = baseline_task.result()
        admission_task.result()
        stale_task.result()
        seed = seed_task.result()
    # A copied fault must never mutate its shared seed through an old store/Git/provider path.
    store = Store(seed / "product")
    try:
        assert store.cursor().state == "DEMO-JUDGE"
        gitwork.unchanged(seed / "product", store.cursor().candidate)
        assert all((seed / name).read_text() == "1" for name in
                   ("turn-unit-001", "turn-unit-002", "turn-docs"))
    finally:
        store.close()
    start = time.monotonic()
    scale = simple_journey(root / "scale50", "scale50", 50)
    scale_seconds = time.monotonic() - start
    assert all(scale[kind] <= baseline[kind] for kind in baseline), (baseline, scale)
    print(f"M1 scale50: {scale_seconds:.2f}s; question bytes {baseline} -> {scale}", flush=True)
    # Target is 60 s on a quiet machine; fail only well above it so host load does not block (T-77).
    assert scale_seconds < 90, f"50-unit bound exceeded: {scale_seconds:.2f}s"
    seconds = time.monotonic() - began
    print(f"M1 suite: {seconds:.2f}s; artifacts {root}", flush=True)
    assert seconds < 270, f"suite bound exceeded: {seconds:.2f}s (target 180 s, T-77)"


if __name__ == "__main__":
    assert sys.argv[1] == "--child"
    child_main(Path(sys.argv[2]), sys.argv[3])
