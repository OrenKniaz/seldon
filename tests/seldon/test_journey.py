"""C8/C12/C14/C18/C23; R2/R6-R9/R11: scripted semantics, real Git, SQLite and processes."""

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from seldon import flow_review, gitwork, guide, judgment, plan, proof, questions
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
    selected = "new:c002" if mode == "traced-evidence" else "new:c001"
    edits.append({"kind": "area", "targetHandle": "new:area", "typedBody": {
        "responsibility": "Deliver named values", "scope": ["**"]}})
    for unit in scopes:
        unit.update(area="new:area", provides=[], consumes=[], reads=[])
    if mode == "wave-start":
        scopes[1]["reads"] = ["unit-001/value.txt"]
    if mode == "parallel":
        scopes[3]["reads"] = [f"unit-{i:03}/value.txt" for i in (1, 2, 3)]
    if count > 1 and mode in ("records", "records-guide-gap", "scale50"):
        provider = 49 if mode == "scale50" else 1
        consumers = [50] if mode == "scale50" else [2, 3]
        edits.append({"kind": "contract", "targetHandle": "new:page", "typedBody": {
            "kind": "file-format" if mode == "scale50" else "ui-structure",
            "text": "UTF-8 value-NNN." if mode == "scale50" else "Shared page exposes a named content slot.",
            "provider": provider, "consumers": consumers}})
        scopes[provider - 1]["provides"] = ["new:page"]
        for number in consumers:
            scopes[number - 1]["consumes"] = ["new:page"]
            scopes[number - 1]["dependencies"] = sorted({*scopes[number - 1]["dependencies"], provider})
            scopes[number - 1]["reads"] = [f"unit-{provider:03}/value.txt" if mode == "scale50"
                                           else "unit-001/page.json"]
        if mode == "scale50":
            # Mutual interface texts must not create an execution cycle. Only unit 50 needs 49's file.
            edits.append({"kind": "contract", "targetHandle": "new:feedback", "typedBody": {
                "kind": "file-format", "text": "Consumers acknowledge the agreed UTF-8 value format.",
                "provider": 50, "consumers": [49]}})
            scopes[49]["provides"] = ["new:feedback"]
            scopes[48]["consumes"] = ["new:feedback"]
        edits.append({"kind": "decision", "targetHandle": "new:decision", "typedBody": {
            "choice": "Use one shared page", "reason": "Consumers agree on the content slot",
            "origin": "architecture", "areas": ["new:area"]}})
        if mode in ("records", "records-guide-gap"):
            edits.append({"kind": "obligation", "targetHandle": "new:placeholder", "typedBody": {
                "what": "Replace the skeleton's third content slot with its persistent value",
                "owner": 3, "closingRequirement": "new:r003"}})
    return {"edits": edits, "units": scopes, "skeleton": list(range(1, count + 1)) if count > 1 else [],
            "quality": [
        {"criterion": 1, "checks": [selected]},
        {"criterion": 2,
         "checks": [selected]}]}


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
    linked = "../unit-001/absent.txt" if mode == "guide-path" and turn == 1 else "../unit-001/check.py"
    note = "Clarify the expected result" if mode in ("guide-gap", "final-seed", "records-guide-gap") \
        and turn == 1 else \
        "Observe value-001"
    if mode == "final-rework":
        note = "Observe final-compatible value"
    path.write_text("Try it yourself\nRead [the value check](" + linked + ").\n"
                    + "```argv\n" + json.dumps(command) + "\n```\n" + note + "\n")
    if mode == "guide-format" and turn == 1:
        # A shell-string command is refused; commands must be argv arrays (guide.py).
        path.write_text("Try it yourself: read [the value](../unit-001/value.txt).\n"
                        "```sh\npython unit-001/check.py\n```\n")
else:
    number = int(name.split("-")[1])
    Path(name).mkdir(exist_ok=True)
    value = f"value-{number:03}"
    if mode == "scale50" and number == 49:
        # The feedback contract's text is usable while its provider's implementation is absent.
        assert facts["Contracts consumed"]
        assert not Path('unit-050/value.txt').exists()
    if mode == "scale50" and number == 50 and facts.get("Contracts consumed"):
        # A build-time consumer uses the existing file-format contract without growing its runtime check.
        value = 'value-' + f"{int(Path('unit-049/value.txt').read_text().split('-')[1]) + 1:03}"
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
    if mode in ("single1", "scale50", "traced-evidence", "visual-unit"):
        source += ("if os.environ.get('SELDON_EVIDENCE_DIR'):\n"
                   "    Path(os.environ['SELDON_EVIDENCE_DIR'], 'observed.png').write_bytes(\n"
                   "        __import__('base64').b64decode(\n"
                   "            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'\n"
                   "            'AAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='))\n")
    if mode == "visual-unit":
        split = source.index("if os.environ.get('SELDON_EVIDENCE_DIR'):")
        capture = source[split:].replace("'observed.png'", "'baseline.png' if not Path('unit-001/value.txt').exists() else 'candidate.png'")
        capture = capture.replace("ASUVORK5CYII='))", "ASUVORK5CYII=') + "
                                  "(b'baseline' if not Path('unit-001/value.txt').exists() else b'candidate'))")
        source = "from pathlib import Path\nimport os\n" + capture + source[:split]
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
        # The check also needs a sibling's check helper (unit-001/check.py), like a shared support.cjs (T-104).
        Path(name, "check.py").write_text(
            "from pathlib import Path\n"
            "if not Path('unit-001/check.py').exists(): raise SystemExit('sibling check helper missing')\n"
            "assert Path('unit-001/value.txt').read_text()=='value-001'\n"
            "print('observed dependency value-001')\n")
    if mode in ("records", "records-guide-gap"):
        if name == "unit-001":
            Path(name, "page.json").write_text('{"slot": "content"}')
        if facts.get("Contracts consumed"):
            with Path(name, "check.py").open("a") as check:
                check.write("import json\nassert json.loads(Path('unit-001/page.json').read_text())"
                            "['slot']=='content'\nprint('shared page slot content')\n")
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
        try:
            subprocess.run(check["command"], capture_output=True,
                           timeout=2 if mode == "check-deadline" else None)
        except subprocess.TimeoutExpired:
            pass  # The deliberate deadline fault remains for Seldon's independent observer to reject.
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
        if kind in ("ARCHITECT", "DETAIL"):
            from test_architect import retained_area_answer
            return retained_area_answer(self, q)
        if kind == "ALIGN":
            answer = dict(VISION)
            if self.mode in ("mixed-criterion", "single1", "scale50"):
                answer["quality"] = [VISION["quality"][0], {
                    "criterion": "Each value module owns its persistence and keeps working when a sibling is absent",
                    "verification": "Observe each value independently"}]
            if self.mode == "mixed-criterion":
                answer["quality"][1] = json.loads(Path(__file__).with_name(
                    "recorded_judge_minimal.json").read_text("utf-8"))["criterion"]
            if self.mode == "review-repair":
                answer["quality"] = [VISION["quality"][0], {
                    "criterion": "unit-001/value.txt and unit-002/value.txt each persist their own value",
                    "verification": "Read both delivered value files"}]
            if self.mode == "user-path":
                answer["quality"] = [{"criterion": "Read the value in the browser",
                                      "verification": "Open the page and display the saved value"},
                                     VISION["quality"][1]]
        elif kind == "PLAN":
            answer = planned(self.count, self.mode)
            if self.mode == "mixed-criterion":
                answer["quality"][1]["checks"] = ["new:c001", "new:c002"]
            if self.mode == "plan-overlap":
                corrections = q["facts"].get("Exact corrections", [])
                if not corrections:
                    for unit in answer["units"]:
                        unit.update(scope=["shared/**"], checkPaths=["shared/check.py"])
                elif len(corrections) == 1:
                    answer["edits"][2]["typedBody"]["requirements"].append("new:r002")
            if self.mode == "review-repair" and "Merge correction" in q["facts"]:
                answer["quality"][1]["checks"] = ["new:c001", "new:c002"]
            if self.mode == "user-revise" and getattr(self, "plan_revised", False):
                answer["edits"][1]["typedBody"]["obligation"] += "; exact stored value requested by user"
            if fault := getattr(self, "plan_fault", None):
                fault(answer)
        elif kind == "REVIEW":
            answer = {"verdict": "adequate", "gaps": []}
            if self.mode == "review-repair" and q["facts"]["Review subject"] != "cross-area":
                coverage = q["facts"]["Trace coverage"]["quality:002"]
                if set(coverage["units"]) != {"u001", "u002"}:
                    answer = {"verdict": "gap", "gaps": [{"target": "quality:002",
                        "correction": "The criterion names both value files but its trace reaches only "
                                      "unit-001; trace the checks of both owners"}]}
        elif kind == "DECIDE":
            answer = {"choice": "revise" if q["facts"]["User's words"].startswith("Revise") else "approve"}
        elif kind in ("JUDGE", "FLOW", "PRODUCT"):
            if kind == "JUDGE" and self.mode == "judge-provider" and q["targets"] == ["n002", *questions.SOURCE_TARGETS]:
                raise ProviderFailure("Judge transport unavailable", True, True)
            if (kind == "JUDGE" and self.mode == "judge-provider" and q["targets"] == ["n005", *questions.SOURCE_TARGETS]
                    and (self.slot / "turn-unit-002").read_text() == "1"
                    or kind == "JUDGE" and self.mode == "unit-uncertain"):
                return json.dumps({"answer": {"verdict": "uncertain", "neededFacts": [
                    "Clarify the local assertion for this value"],
                    "why": "A semantic fact needs clarification"}})
            answer = {"verdict": "satisfied", "gaps": [],
                      "assessments": questions.assessments(q["targets"], final=kind != "JUDGE")}
            if kind == "PRODUCT":
                assert len(q["targets"]) == 1
                facts = q["facts"]
                assert "Files on the delivered revision" not in facts and "Check source paths" not in facts
                reached = {"guide"} if q["targets"] == ["tryItGuide"] else {
                    f"u{int(o['command'][-1].split('/')[0].split('-')[-1]):03}"
                    for o in facts["Final observations"] if o["command"][-1].startswith("unit-")}
                summary = facts["Source judgment"]
                assert "satisfied" in summary
                assert "scope trees unchanged" in summary
                assert "source is judged at the unit" in facts["Source availability"].lower().lower()
                if q["targets"] == ["tryItGuide"]:
                    guide = facts["Guide file"]
                    assert "Try it yourself" in guide["text"] and len(guide["commit"]) == 40
                    assert reached == {"guide"}
                    assert len(facts["Final observations"]) == 2
                    assert all(o["exitCode"] == 0 for o in facts["Final observations"])
                    expected = "observed final-compatible value" if self.mode == "final-rework" else \
                        "observed value-001"
                    assert any(expected in o["stdout"] for o in facts["Final observations"])
                    if self.mode in ("guide-gap", "records-guide-gap") \
                            and "Clarify the expected result" in guide["text"]:
                        answer.update(verdict="gap", gaps=[{"target": "tryItGuide",
                            "correction": "Explain that the command prints observed value-001"}])
                elif self.mode == "review-repair" and q["targets"] == ["quality:002"]:
                    assert reached == {"u001", "u002"}
                    assert {o["stdout"].strip() for o in facts["Final observations"]} == {
                        "observed value-001", "observed value-002"}
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
                        assert set(texts) == {f'"value-{expected}"', ('long observation ' * 400)[-4096:]}
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
            if kind == "FLOW":
                facts = q["facts"]
                assert set(q["targets"]) == set(facts["Requirements"])
                assert facts["Source judgment"]["scopeTreesUnchanged"]
                assert "Product files at P" not in facts and "Check sources" not in facts
                assert all(o["exitCode"] == 0 and o["executionStarted"] and o["cleanupVerified"]
                           for o in facts["Final observations"])
            if kind in ("FLOW", "PRODUCT"):
                facts = q["facts"]
                if kind == "FLOW" and self.mode == "demo-facts-unavailable" and q["targets"] == ["n002"]:
                    return json.dumps({"answer": {"verdict": "uncertain", "neededFacts": [
                        "The missing manual observation of the demonstrated interaction"], "why": "Not observed"}})
                prefix = "demo" if kind == "FLOW" else "product"
                is_subject = q["targets"] == (["n002"] if kind == "FLOW" else ["quality:001"])
                if is_subject and (self.mode == prefix + "-uncertain"
                                   or kind == "PRODUCT" and self.mode in ("single1", "scale50")):
                    if "Requested facts" not in facts:
                        return json.dumps({"answer": {"verdict": "uncertain", "neededFacts": [
                            "Did this check execute on the delivered commit with verified cleanup?"],
                            "why": "Need the actual execution facts"}})
                    actual, = facts["Final observations"]
                    assert actual["executionStarted"] and actual["cleanupVerified"]
                    if kind == "PRODUCT":
                        assert actual["ranOnDeliveredRevision"]
                if (kind == "PRODUCT" and is_subject
                        and self.mode in ("product-source-resolved", "product-source-unresolved",
                                          "product-source-oversize", "product-facts-unavailable")):
                    if "Requested facts" in facts:
                        assert "source is judged at the unit" in facts["Source availability"].lower()
                        assert "satisfied" in facts["Source judgment"]
                    if "Requested facts" not in facts or self.mode != "product-source-resolved":
                        return json.dumps({"answer": {"verdict": "uncertain", "neededFacts": [
                            "Supply the script sources to inspect module ownership"] *
                            (100 if self.mode == "product-source-oversize" else 1),
                            "why": "The criterion needs source inspection"}})
                if is_subject and self.mode in (prefix + "-gap", prefix + "-unresolved"):
                    if any("interaction blocked" in o["stdout"] for o in facts["Final observations"]):
                        if self.mode.endswith("-unresolved") and "Requested facts" not in facts:
                            return json.dumps({"answer": {"verdict": "uncertain", "neededFacts": [
                                "Demonstrate that the interaction is ready, not blocked"],
                                    "why": "The observed interaction remains unresolved"}})
                        answer.update(verdict="gap", gaps=[{"target": q["targets"][0],
                            "correction": "The interaction is blocked; make it ready and assert it "
                                          "in the check"}])
            if kind == "JUDGE":
                if self.mode == "visual-unit":
                    observations = q["facts"]["Observations"]
                    assert {o["purpose"] for o in observations} == {"T", "P"}
                    assert len(q["images"]) == 2
                    assert all(Path(p).read_bytes().startswith(b"\x89PNG") for p in q["images"])
                assert "other units' files" in q["facts"]["Checkout coverage"].lower()
                sources = q["facts"]["Check sources"]
                assert sources, "Judge needs actual check sources"
                # Every unit judgment, rework included, sees its own whole product source at P (T-86).
                product = q["facts"]["Product files at P"]
                assert product and not set(product) & set(sources), product
                assert all(p.split("/")[0] == next(iter(sources)).split("/")[0] for p in product), product
                if self.mode == "review-repair" and q["targets"]:
                    directory = "unit-001" if q["targets"] == ["n002", *questions.SOURCE_TARGETS] else "unit-002"
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


@pytest.mark.fast
@pytest.mark.parametrize("fault,fix", [
    ("unlinked-read", "needs a contract"),
    ("duplicate-provider", "one provider"),
    ("missing-endpoint", "existing provider"),
    ("unbound-consumer", "every named consumer"),
    ("area-escape", "inside its named area"),
    ("architecture-units", "unexpected field"),
    ("wrong-closing-owner", "owner and its closing requirement"),
])
def test_plan_refuses_broken_agreements(tmp_path, fault, fix):
    """Refuse an unsafe PLAN before B or WORK; runtime disjoint scopes miss undeclared file reads,
    multiple interface writers, dangling endpoints, escaped areas and unclosable placeholders.
    """
    run, provider = prepare(tmp_path / fault, "records", 3)

    def corrupt(answer):
        contract = next(e["typedBody"] for e in answer["edits"] if e["kind"] == "contract")
        if fault == "unlinked-read":
            contract["consumers"] = [3]
            answer["units"][1]["consumes"] = []
            answer["units"][1]["dependencies"] = []
        elif fault == "duplicate-provider":
            answer["units"][1]["provides"] = ["new:page"]
        elif fault == "missing-endpoint":
            contract["provider"] = 4
        elif fault == "unbound-consumer":
            answer["units"][1]["consumes"] = []
        elif fault == "area-escape":
            next(e["typedBody"] for e in answer["edits"] if e["kind"] == "area")["scope"] = ["else/**"]
        else:
            next(e["typedBody"] for e in answer["edits"] if e["kind"] == "obligation")["owner"] = 2

    if fault == "architecture-units":
        provider.architecture_fault = lambda answer: answer.update(units=[])
    else:
        provider.plan_fault = corrupt
    try:
        assert run.run().state == "INCOMPLETE"
        assert fix in run.store.records("outcome")[-1][2]["reason"]
        assert not run.store.units()
        assert {b["gate"] for _, _, b in run.store.records("decision_request")} == {"A"}
    finally:
        run.close()


def records_and_skeleton_journey(slot):
    """Shared-page consumers receive the agreement and exercise it; isolated units miss lost briefs.

    Open obligations block C until final requirement verdicts; an unrelated guide repair must preserve
    closure. A text-only contract repair revokes consumers even with unchanged scope trees, which the
    source-change journey misses. A fresh canonical area plan reaches the revision writer, review/B and reopen,
    then the ordinary EXECUTE/WAVE-VERIFY/CHECK-IN path. This catches stale proof reuse after a
    changed agreement, which a source-change fixup cannot expose. No extra repair entry is added.
    """
    from dataclasses import replace
    from seldon import obligations
    from seldon.run import eligible

    began = time.monotonic()
    run, provider = prepare(slot, "records-guide-gap", 3)
    try:
        while run.store.cursor().state != "FLOW-JUDGE":
            assert run.store.cursor().state != "INCOMPLETE", run.store.records("outcome")
            run.step()
        assert obligations.remaining(run.store)
        with pytest.raises(ValueError, match="after check-in"):
            eligible(run.store)
        assert run.step() == "CHECK-IN"
        assert not obligations.remaining(run.store)
        assert run.step() == "PRODUCT-JUDGE"
        before = run.store.units()
        observations = run.store.records("observation")
        checks_before = {run.store.read(r)["check"] for r in run.store.cursor().finalObservations}
        candidate_before = run.store.cursor().candidate
        checkins_before = sum(b["gate"] == "CHECK-IN" for _, _, b in run.store.records("decision_request"))
        retained = run.store.cursor().requirementVerdicts[0]

        def correct(answer):
            answer["contracts"][0]["text"] += " Its slot is named content."

        revised, revision_provider = prepare(slot / "contract-correction", "records", 3)
        revision_provider.architecture_fault = correct
        try:
            while revised.store.cursor().state != "B":
                revised.step()
            document = revised.store.plan(revised.store.cursor().revision)
        finally:
            revised.close()
        revision = run.store.revision(document, run.store.cursor().revision)
        run.store.cursor(replace(run.store.cursor(), state="REVIEW", B=None, review=None, revision=revision))
        assert run.store.unit("u001").judgment == before["u001"].judgment
        assert all(run.store.unit(u).state == "DONE" and run.store.unit(u).judgment is None
                   for u in ("u002", "u003"))
        run.close()
        run = Run.reopen(slot / "product", provider, approve)
        while run.store.cursor().state != "B":
            assert run.store.cursor().state != "INCOMPLETE", run.store.records("outcome")
            run.step()
        assert run.step() == "EXECUTE"
        assert run.step() == "WAVE-VERIFY"
        assert run.step() == "FLOW-JUDGE"
        fresh = run.store.records("observation")[len(observations):]
        assert {b["check"] for _, _, b in fresh} == checks_before
        assert len(fresh) == len(checks_before)
        assert all(b["purpose"] == "final" and b["commit"] == run.store.cursor().candidate
                   for _, _, b in fresh)
        assert run.step() == "CHECK-IN"
        assert run.step() == "PRODUCT-JUDGE"
        assert sum(b["gate"] == "CHECK-IN" for _, _, b in run.store.records("decision_request")) == \
            checkins_before + 1
        after = run.store.units()
        assert retained in run.store.cursor().requirementVerdicts
        assert after["u001"].judgment == before["u001"].judgment
        assert all(after[u].judgment != before[u].judgment for u in ("u002", "u003"))
        assert all(after[u].P == before[u].P and after[u].recoveries == 0 for u in before)
        cursor, rows, _ = finish(run)
        changed = [b for _, k, b in rows if k == "question" and b["kind"] == "JUDGE"
                   and "Its slot is named content." in encode(b["facts"])]
        assert len(changed) == 2
        assert not obligations.remaining(run.store)
        assert run.store.unit("guide").recoveries == 1
        assert cursor.candidate != candidate_before
        final = [run.store.read(r) for r in cursor.finalObservations]
        assert {b["check"] for b in final} == checks_before
        assert all(b["commit"] == cursor.candidate and b["exitCode"] == 0 for b in final)
        assert all(run.store.read(r)["commit"] == cursor.candidate for r in cursor.requirementVerdicts)
        # C must refuse a lost closure even when all commands and flow headlines are green.
        run.store.cursor(replace(cursor, requirementVerdicts=[]))
        with pytest.raises(ValueError, match="open skeleton obligations"):
            eligible(run.store)
        run.store.cursor(cursor)
        for kind in ("WORK", "JUDGE"):
            briefs = [b["facts"] for _, k, b in rows if k == "question" and b["kind"] == kind
                      and "Guide path" not in b["facts"]]
            assert all("Shared page exposes a named content slot." in encode(b) for b in briefs)
        assert len(plan.select(run.store.plan(cursor.revision), "area")) == 3
        print(f"M2 records, obligations, contract repair/reopen: {time.monotonic() - began:.2f}s", flush=True)
    finally:
        run.close()


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
    assert not outcome["accepted"]
    assert outcome["verification_complete"] == (not judgment.not_judged(run.store))
    assert all(u.state == "DONE" for u in units.values())
    assert len([b for _, k, b in rows if k == "product_verdict"]) == 1
    final_questions = [b for _, k, b in rows if k == "question" and b["kind"] == "PRODUCT"]
    assert all(len(q["targets"]) == 1 for q in final_questions)
    for subject, body in plan.select(run.store.plan(cursor.revision), "criterion").items():
        verdict, = [run.store.read(r) for r in cursor.qualityVerdicts
                    if run.store.read(r)["subject"] == subject]
        initial = [q for q in final_questions if "Requested facts" not in q["facts"]
                   and all(o["commit"] == verdict["commit"] for o in q["facts"]["Final observations"])]
        assert sum(q["targets"] == [subject] for q in initial) == 1
    observations = [b for _, kind, b in rows if kind == "observation"]
    for obs in observations:
        if obs["check"] == "guide:format":
            assert not obs["executionStarted"] and obs["cleanupVerified"] and obs["exitCode"] is None
            assert obs["command"] == [] and obs["stderr"] and obs["unit"] == "guide"
            continue
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
    """A merge losing a check assertion must reach its unit JUDGE before FLOW.

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
                     and b["targets"] == ["n002", *questions.SOURCE_TARGETS]]
        assert len(judgments) == 3
        assert "lost assertion" in judgments[1]["facts"]["Check sources"]["unit-001/check.py"]
        assert "unit-002" not in judgments[1]["facts"]["Product changes"]
        sibling = [b for _, k, b in rows if k == "question" and b["kind"] == "JUDGE"
                   and b["targets"] == ["n005", *questions.SOURCE_TARGETS]]
        assert len(sibling) == 2 and "integration annotation" in sibling[1]["facts"]["Check sources"][
            "unit-002/check.py"]
        final = next(b for _, k, b in rows if k == "question" and b["kind"] == "FLOW")
        assert final["facts"]["Source judgment"]["scopeTreesUnchanged"]
    finally:
        run.close()


def final_seed(slot):
    """One actual scripted delivery supplies final-judge faults with real Git and proof records."""
    run, _ = prepare(slot, "final-seed", 2)
    try:
        while run.store.cursor().state != "FLOW-JUDGE":
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
                assert units["u002"].recoveries == 0 and (slot / "turn-unit-002").read_text() == "1"
                assert run.store.read(units["u002"].judgment)["answer"]["verdict"] == "not judged"
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
            target = "FLOW-FACTS" if mode.startswith("demo") else "PRODUCT-FACTS"
            while run.store.cursor().state != target:
                assert run.store.cursor().state != "INCOMPLETE", run.store.records("outcome")
                run.step()
            run.close()
            run = Run.reopen(slot / "product", provider, approve)
        cursor, rows, units = finish(run)
        if mode in ("mixed-criterion", "product-source-resolved", "product-source-unresolved",
                    "product-source-oversize", "product-facts-unavailable", "demo-facts-unavailable",
                    "unit-uncertain"):
            assert not run.store.records("unit_fixup") and cursor.repairs == 0
            assert all(u.recoveries == 0 for u in units.values())
            local = [b for _, k, b in rows if k == "question" and b["kind"] == "JUDGE"]
            assert all(not any(t.startswith("quality:") for t in q["targets"]) for q in local)
            if mode == "mixed-criterion":
                assert any(b["targets"] == ["quality:002"] for _, k, b in rows
                           if k == "question" and b["kind"] == "PRODUCT")
            if mode in ("product-source-unresolved", "product-source-oversize", "product-facts-unavailable",
                        "demo-facts-unavailable", "unit-uncertain"):
                target = ("n002" if mode == "demo-facts-unavailable" else
                          "u001" if mode == "unit-uncertain" else "quality:001")
                assert target in judgment.not_judged(run.store)
                c, = [b for _, _, b in run.store.records("decision_request") if b["gate"] == "C"]
                assert c["subject"]["notJudged"] == judgment.not_judged(run.store)
                assert c["subject"]["deviation"] == "DV6"
                assert not run.store.records("outcome")[-1][2]["verification_complete"]
                from run_report import report
                assert target + ": not judged:" in report(slot)[0]
        reasks = [b for _, k, b in rows if k == "question" and "Requested facts" in b["facts"]]
        if mode == "product-source-oversize":
            assert not reasks, "An oversized FACTS question must never reach the provider"
        for q in reasks:
            first = next(b for _, k, b in rows if k == "question" and b["kind"] == q["kind"]
                         and b["targets"] == q["targets"] and "Requested facts" not in b["facts"])
            assert len(encode(q).encode()) <= 2 * len(encode(first).encode())
            assert "Code-established execution facts" not in q["facts"]
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
            reviews = [b["answer"] for _, _, b in run.store.records("plan_review")]
            assert reviews[0]["gaps"][0]["target"] == "quality:002"
            assert reviews[1]["verdict"] == "adequate"
        if mode in ("demo-uncertain", "product-uncertain"):
            assert not run.store.records("unit_fixup")
            assert all(u.recoveries == 0 for u in units.values())
            retries = [b for _, k, b in rows if k == "question" and "Requested facts" in b["facts"]]
            assert len(retries) == 1 and run.store.records("judge_uncertain")
        if mode in ("demo-gap", "product-gap", "demo-unresolved", "product-unresolved"):
            fixup, = run.store.records("unit_fixup")
            assert fixup[2]["owner"] == "u001"
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
            assert len([b for _, k, b in rows if k == "question" and b["kind"] == "FLOW"]) == count
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
            # RED on the baseline comes from missing product, never from missing check machinery.
            assert not any(b["purpose"] == "wave-start" and "helper missing" in b["stderr"] for b in observed)
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
            document = run.store.plan(cursor.revision)
            assert len(plan.select(document, "area")) == 1
            assert len(plan.select(document, "unit")) == 2  # One product unit plus CODE's delivery guide.
            assert not document["skeleton"] and not plan.select(document, "contract")
            assert not plan.select(document, "obligation")
            from run_report import report
            output, row = report(slot)
            assert row[3] == "SCRIPTED_C" and len(row) == 16 and "accepted=False" in output
        sizes = {kind: max(len(encode(b).encode("utf-8")) for _, k, b in rows
                          if k == "question" and b["kind"] == kind)
                 for kind in ("WORK", "JUDGE", "FLOW", "PRODUCT")}
        if mode in ("single1", "scale50"):
            sizes["same-unit JUDGE"] = max(len(encode(b).encode("utf-8")) for _, k, b in rows
                if k == "question" and b["kind"] == "JUDGE"
                and "unit-001/check.py" in b["facts"]["Check sources"])
            sizes["re-ask"] = max(len(encode(q).encode()) for q in reasks)
        if mode in ("single1", "scale50"):
            final = [run.store.read(r) for r in cursor.finalObservations]
            demos = [b for _, k, b in rows if k == "question" and b["kind"] == "FLOW"]
            final = [o for o in final if o["unit"] != "guide"]
            assert len(final) == count and len(demos) == count
            assert {o["check"] for q in demos for o in q["facts"]["Final observations"]} == {
                o["check"] for o in final}
        from run_report import report
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
                from run_report import report
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
    wrongly integrate it. Missing unit judge facts remain not judged without reworking a correct worker.
    Windows native Access-is-denied wording must stop after one call; generic transport faults
    otherwise retry a denied operation and the successful native journey never exposes it.
    Subject traces include unit verdicts, supplemental JSON artifacts
    and delivered-folder observations; a standalone check and stdout alone cannot expose lost evidence.
    Artifact overlap with stdout/stderr is sent once, while output truncated by the observer must
    retain its bounded artifact tail and full stored bytes; small supplemental-only evidence misses both.
    Every product criterion needs its own PRODUCT verdict, even when all its checks passed. A trace naming two
    units but reaching one must repair before B; success-only review cannot catch unavailable files.
    Final cross-unit requirements include every traced owner's verdict; only unit judges read source.
    Uncertainty twins prove a store fact resolves without rework; persistent uncertainty and real gaps
    must repair an actual blocked interaction through its original owner, preserving unrelated DONE work.
    Existing success-only journeys cannot expose the old all-DONE/INCOMPLETE failure or false satisfaction.
    One versus fifty uses the same feature and real Foundation source sizes to compare FLOW,
    PRODUCT and JUDGE. The shared-page journey covers multiple requirements per feature.
    Reopening a terminal failure preserves state and records; interrupted recovery cannot prove this.
    Guide command/path faults catch guide prose passing without execution. A docs-only semantic fix
    preserves all other final verdicts; the old all-verdict reset is invisible in pre-final failures.
    Lost/annotated integration scopes exercise both outcomes of the owning unit's renewed JUDGE.
    A visual unit retains its own T and P screenshots in its JUDGE; final-only image checks
    cannot catch a producer that leaves local source judgment without its observation images.
    Mixed source/behaviour criteria reach PRODUCT unchanged without a placement review or rework;
    classification success twins previously hid mixed prose that had no legal marking. Persistent
    uncertainty in a criterion, requirement or unit reaches C as not judged with verification
    incomplete; a success-only final subject cannot expose false unit failure. A gap repairs only its
    owner. FACTS requests remain bounded to twice the first ask, including oversize requests.
    Shared-page contracts reach provider and consumer briefs; undeclared reads and broken ownership
    are refused at PLAN. Skeleton obligations need final requirement verdicts and retain closure
    across an unrelated docs repair. Text-only contract repair/reopen renews only consumer judges;
    unchanged scope trees alone cannot expose this stale evidence. Scale retains an actual build-time
    file-format contract while comparing the same local sources at one and fifty units.
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
        cases = [(case, feature) for case in recording["cases"] for feature in (
            plan.select(recording["plan"], "requirement")[case["subject"]]["features"]
            if case["kind"] == "DEMO" else [None])]
        for case, feature in cases:
            if feature and set(flow_review.trace(recording["plan"], feature)[1]) - {
                    store.read(r)["check"] for r in refs}:
                # This retained requirement recording cannot prove the rest of its feature.
                with pytest.raises(ValueError, match="every traced check"):
                    flow_review.brief(store, feature)
                continue
            question = (flow_review.brief(store, feature) if feature else
                        judgment.brief(store, case["kind"], case["subject"]))
            size = len(encode(question).encode("utf-8"))
            assert size < case["maxBytes"], (case["kind"], size, case["beforeBytes"])
            facts = question["facts"]
            assert question["images"] == [artifacts[p] for p in recording["images"]]
            assert "Files on the delivered revision" not in facts and "Check source paths" not in facts
            assert all(encode(source)[1:-1] not in encode(facts) for source in recording["files"].values())
            for artifact in facts.get("Check artifacts", {}).values():
                if "text" in artifact:
                    assert artifact["text"] in {text.encode("utf-8")[-4096:].decode("utf-8", errors="replace")
                                                for text in recording["artifacts"].values()}
            print(f"M1 recorded {case['kind']}: {case['beforeBytes']} -> {size} bytes", flush=True)
    finally:
        store.close()


def recorded_minimal_journey(root):
    """Replay c01's real visual requirement through Git -> observer -> JUDGE with local Edge images.

    Its original local producer saved none; detector-only fixtures miss that cause. The corrected
    local check captures its own desktop/mobile/focus views, never the wave's screenshots. c02's
    accepted mixed sentence stays intact in its PRODUCT brief, rather than being reclassified.
    """
    recording = json.loads(Path(__file__).with_name("recorded_judge_minimal.json").read_text("utf-8"))
    original = recording["question"]
    assert not original["images"]
    root.mkdir()
    scratch = root / "scratch"
    scratch.mkdir()
    project = root / "product"
    project.mkdir()
    base = gitwork.init(project)
    sources = dict(original["facts"]["Check sources"])
    capture = ""
    for name, commands in (("desktop", ""), ("mobile", "await page.setViewportSize({width:375,height:812});"),
                           ("focus", "await page.locator('button').first().focus();")):
        capture += (commands + "await page.screenshot({path:require('node:path').join("
                    f"process.env.SELDON_EVIDENCE_DIR,'{name}.png'),fullPage:true}});\n")
    sources["checks/page/local.cjs"] = sources["checks/page/local.cjs"].replace(
        "  await assertGeometry(page);", "  await assertGeometry(page);\n" + capture)
    for path, text in sources.items():
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    gitwork.git(project, "add", "--all")
    T = gitwork.commit(project, gitwork.git(project, "write-tree"), base)
    for path, text in original["facts"]["Product files at P"].items():
        (project / path).write_text(text, encoding="utf-8")
    gitwork.git(project, "add", "--all")
    P = gitwork.commit(project, gitwork.git(project, "write-tree"), T)
    gitwork.git(project, "update-ref", "HEAD", P)
    answer = questions.example_plan()
    requirement, = original["facts"]["Requirements"].values()
    answer["edits"][1]["typedBody"] = {**requirement, "features": ["new:f"]}
    check, = original["facts"]["Checks"].values()
    answer["edits"][2]["typedBody"] = {**check, "requirements": ["new:r"]}
    scope = ["index.html", "styles.css", "checks/page/**"]
    answer["edits"][3]["typedBody"]["scope"] = scope
    answer["units"][0].update(scope=scope, checkPaths=["checks/page/**"], instructions="Own the shared page")
    vision = {**VISION, "quality": [recording["criterion"]]}
    document = plan.create(vision, answer)
    store = Store(project)
    try:
        kickoff = store.record("kickoff", {"setup": {"providers": ROLES}})
        store.cursor(RunRow(kickoff=kickoff, scratch=str(scratch), revision=store.revision(document),
                            candidate=P, finalCommit=P))
        checks = plan.assigned(document)["u001"]
        gitwork.checkout(project, T)
        red = proof.observe(store, project, T, checks, "T", "u001")
        gitwork.checkout(project, P)
        tree = gitwork.scope_tree(project, P, scope, scratch)
        green = proof.observe(store, project, P, checks, "P", "u001", tree)
        assert not proof.failures(store, red, red=True) and not proof.failures(store, green)
        store.unit("u001", UnitRow(state="DONE", worktree=str(project), T=T, P=P, observations=red + green))
        question = questions.judge_brief(store, "u001")
        assert len(question["images"]) == 3
        assert question["facts"]["Requirements"]["n002"] == requirement
        assert all(Path(p).read_bytes().startswith(b"\x89PNG") for p in question["images"])
        assert not any(t.startswith("quality:") for t in question["targets"])
        verdict = store.record("unit_verdict", {"commit": P, "scopeTree": tree,
            "answer": {"verdict": "satisfied"}})
        store.unit("u001", replace(store.unit("u001"), judgment=verdict))
        store.cursor(replace(store.cursor(), finalObservations=green))
        work = questions.work_brief(store, "u001")
        product = judgment.brief(store, "PRODUCT", "quality:001")
        flow = flow_review.brief(store, "n001")
        assert product["facts"]["Subject"] == recording["criterion"]
        assert len(product["images"]) == 3 and "Product files at P" not in product["facts"]
        expanded = deepcopy(document)
        expanded["nodes"]["n004"]["body"]["scope"] = ["**"]
        for number in range(2, 51):
            r, c = f"r{number}", f"c{number}"
            expanded["nodes"][f"f{number}"] = {"kind": "feature", "body": {
                **document["nodes"]["n001"]["body"]}}
            expanded["nodes"][r] = {"kind": "requirement", "body": {
                **document["nodes"]["n002"]["body"], "features": [f"f{number}"],
                "obligation": f"Independent value {number}"}}
            expanded["nodes"][c] = {"kind": "check", "body": {
                **document["nodes"]["n003"]["body"], "requirements": [r],
                "command": [sys.executable, "-B", f"unit-{number}/check.py"]}}
            expanded["nodes"][f"u{number:03}"] = {"kind": "unit", "body": {
                **document["nodes"]["u001"]["body"], "requirements": [r],
                "scope": [f"unit-{number}/**"], "checkPaths": [f"unit-{number}/check.py"]}}
        expanded["skeleton"] = list(plan.select(expanded, "unit"))
        plan.validate(expanded)
        store.cursor(replace(store.cursor(), revision=store.revision(expanded)))
        assert questions.judge_brief(store, "u001") == question
        assert questions.work_brief(store, "u001") == work
        assert judgment.brief(store, "PRODUCT", "quality:001") == product
        assert flow_review.brief(store, "n001") == flow
        print("P10 recorded c01 JUDGE: " + str(len(encode(original).encode())) + " -> "
              + str(len(encode(question).encode())) + " bytes; 0 -> 3 local images; c02 PRODUCT: "
              + str(len(encode(product).encode())) + " bytes; identical at 1/50 units", flush=True)
        return {"WORK": len(encode(work).encode()), "JUDGE": len(encode(question).encode()),
                "PRODUCT": len(encode(product).encode()),
                "FLOW": len(encode(flow).encode())}
    finally:
        store.close()


def guide_links_journey(root):
    """Walk Markdown links from docs and argv from root, catching the recorded T-96 contradiction.

    A root file deliberately exists while docs/index.html does not: checking from cwd would
    falsely pass the wrong link. Existing command failures do not exercise link resolution.
    """
    root.mkdir()
    commit = gitwork.init(root)
    (root / "docs").mkdir()
    (root / "index.html").write_text("Foundation", encoding="utf-8")
    (root / "assets").mkdir()
    command = [sys.executable, "-B", "-c",
               "from pathlib import Path; assert Path('index.html').read_text() == 'Foundation'"]
    text = "Read [the page]({}).\n```argv\n" + json.dumps(command) + "\n```\n"
    store = Store(root)
    try:
        kickoff = store.record("kickoff", {"setup": {"providers": ROLES}})
        scratch = root / "scratch"
        scratch.mkdir()
        store.cursor(RunRow(kickoff=kickoff, scratch=str(scratch)))
        for link, passes in (("../index.html", True), ("../assets/", True), ("index.html", False)):
            (root / plan.GUIDE_PATH).write_text(text.format(link), encoding="utf-8")
            refs = proof.observe(store, root, commit, guide.checks(root), "guide", disposable=True)
            assert bool(proof.failures(store, refs)) is not passes
        (root / plan.GUIDE_PATH).write_text(text.format("../../outside.html"), encoding="utf-8")
        with pytest.raises(ValueError):
            guide.checks(root)
    finally:
        store.close()


# Lossless recorded PRODUCT questions, compressed only to keep this owned fixture compact.
# Image bytes reuse recorded_judgment.json; the original text artifacts and observations are exact.
RECORDED_ARTIFACT_QUESTIONS = (
    # foundation-2ddc91e-c02/call-73e4764e4a9a4ba58023d5a347dc8b8d/question.json
    (
        "eJztfe1y2zi24KtwfH90d1ZxSAD8Smpqq2+6e7r39lclPTO1Fac2IADanMikhqTs+Han6v7aB9g/+2f3afZN7pPsOQApkRJF"
        "ybZsywl873QkEcQ5ODjfwAF+P1If+Plsqqqj529+P+J5danKo+fwqapUVZ2rvDZPzhXPs/w0nU+PntflXE2O8uJ1PU+qnwrx"
        "vvopqyp42j6qeXmq6qPnR3V59UP9l3km1dHk6PLsCn767Uw5peJTJ8trVXJRZ0XuILSyrpxaP/znPCuVdIoEfrzgyVQ5iTrj"
        "F1lRHn18Ozk65TPECT5dqFJmAgFVvM6qNFOyB8f0AF31YOXSEWdKvHeqYl4KVTnVfDYryhqgZ5XTkOAjQlIflJjjS0CRj5Oj"
        "FHqokDgv9eu8rLPlT89PTio1lUV+clKrqq5OTs69k5O0mOeSYxdPiZQi9tRT4ZKTk1lZyLmoT06O25cWvZ2c0CjwqWS+CGgq"
        "UhlKFgSKpsqPY5GGnvS4cmNJXJcRmRJGAxW7LE1EzLmKBQ0Qn+SqxjklJKDR5EgU5+cZ0olRwePEg9dSj0nf5UkQ+yFLGE0J"
        "vOyKkAAkkhzBcPc5JOYSL/VZoohyhXCV58ZBlMbC90gUCimC2KMx9xOeRBGPg5AFNGYeFYFQUrE07AwpDFnIbjIk4Ev1Ad/4"
        "/SR3nJMjzQQnR8/hY+6S6ORoYn5vGBB5f/HUXT4tiuZnpM+SQgsaGSptptPJyWVRvldltfz4lKk4klwFPgyVhUrFlAqPUS+g"
        "vs8jRhbQZygpEuFrSTM/JmVxCYz+clqsP6vVOfA2L69eFrNMVa/UeXGx1kjwvMgzwaevv/+a+AE+1TTCZ1ku1Yfjs/p8akZN"
        "lQvEDJgrVawEiYBHJYMZ86nne8B8QsI0B5GvaOz61FcBdwNg54AniUpiETRDwa6r+goUz7GoKtO1UEK5XDBGFIkTmHhKXegl"
        "oZHwAx+guS4wfCjCOE0Z9JUGJFYqBfgejVicdLsWZTarq2dnajotjv/RAogSV3hBBCTlxBNRlHLiRq4X+EGiUkVYSqiSqSsp"
        "ipMnkpQFkQqjMFJKxoEYAJDzc7Xon4YCXnMVSWLAFHqniRt7oc8IDf04Ad4kKvUiGXoCiJOkhMQwkBAeRmFCYzLQ/5KLFlC8"
        "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"
        "bFWtivnH/w9WFFYP"
    ),
    # foundation-2ddc91e-c03/call-1a384022b6e24d808dc4a5aeb91b7721/question.json
    (
        "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"
        "xYr6IphQYwvBBAQTEEzYWXYIJjSLE8EEBBOeR20RwYTjiBTBBAQTBmnwCCYcUaIIJiCYUBMnggn7po0IJizBhFVM8QyxhKr1"
        "VKAEQptG6ChQAh75hfBBSQkr8MGmhlbQg9rFncADo+7jcmIM16uVIzfcr92jeGKSxl5Wtue2drK2QmuXytEi9za3BuSdzFFD"
        "784Yn4Y3arnb4o5O1gzz0Ux1xBtW63RX0dPtKEEzm6tYtU7lFhYrMarTdOt29irBlFHCQPqjA9vZsY2t7BjG2N4KDahYs50n"
        "xxrrRh03WPNkeGOrjakDAwO0zd3Uf1/zb23TWMcbO9ujPMPYpq1m2/0boV5TLxuRHjTZI0TuxgfahEZAqZpp7yk7CPnGbnPy"
        "2lN6ljUmemMefirx9YAIWuc4g4xNt9Gb7TbNGXaz091pKjDcnoUEt572bEjStfcW5LYKwlnECp3S60zY3P2Rqo6aQZsA21jf"
        "TYBNU9vOAiQNNJ5G/7qRgM6wroc994nsnoU5d6IA5xQkP5FR9yn+d4mx07b7iPHMTbtvzb8rt+lh39vTm16W3ZHePLVh9yz3"
        "n0Oe+ERm3b/I3yHETtveLsReVt1V3H8yo95e2m/LWSyHdohsa8Zi614t1d0hXTHs8TEylfWXwVf0QXj3ME9g9R7+usbaPdbu"
        "sXZfafPk9e1zSkt25e0cYrK2ia2ip1i717B2r2HtHmv3WLsfQk0Aa/dYu8fa/bMxZ6zdY+1+KKaNtfvjCBNr91i7x9r9DukK"
        "1u6XtXv54iv3n9TLxrFyj5V7rNyXyMPK/dF4O4eIrG1aq+gpVu41rNxrWLnHyj1W7odQEcDKPVbusXL/bMwZK/dYuR+KaWPl"
        "/jjCxMo9Vu6xcr9DuoKV+2XlPlV16xddue86MWez1I6leyzdY+keS/dYusfS/ZodLN1j6R5L91i6x9L92Vb5sHSPpftBmTOW"
        "7rF0PxTTxtL9cYSJpXss3WPpfod0BUv3y9L9ski998k5a5436h3zZLIws3Air169evX66lXB66uc0VdT8qqZxVezJBZznr0C"
        "gYUzaJkH1ePf01q9VSpEYDFHy+zq6vrjxy8/vPvw+q/Xb7788Pcvr9+9vX7/efw2ShUPquX69i0F4QNSriKHQxO+/HM7ftLK"
        "3bqorcZRCi2LtQmMqgYjGs8TLq+0fiRdnGC8e43XGXC00oOOcTx/pGun10y3ZcuIeyHuhbgX4l4NvJ1DPtMWFFb0FHEvDXEv"
        "DXEvxL0Q9xpCPQ1xL8S9EPd6NuaMuBfiXkMxbcS9jiNMxL0Q90Lca4d0BXGvJe61jvbx1c9HK++3xA9Y3cfqPlb3sbrfwNs5"
        "RG1tU19FT7G6r2F1X8PqPlb3sbo/hKoBVvexuo/V/Wdjzljdx+r+UEwbq/vHESZW97G6j9X9HdIVrO4vq/ur8GDgxX31AUKF"
        "z9H3i1xyS7ktpKZk1iaxxR6Dm5vx8iaWZGHAuLqRGCzgvusxZgeW8BjltmESz3MMKuF3x+IBcQzpSFsSyjzP1Q1dUOEbnuCO"
        "YO7o6s+R/wBUjK4MYun2BTA/nYbZ6Gpk6g61Amn6emAz6jKb2rbnygD6Yg4nhDPqcNsWh2aJUp14kgHlAbUNQ3BddywTfuSB"
        "ZRNKgoAIn3DHoqZnubbjEIspSikN7EAyo8QS9WzHHAJLTuDpwtJ9N2DE8LgwHdN1ncAXwEsAZOhOYNjEM1zh+tClB+GUpwfC"
        "gWG0HF+UWbLJfixd5KU9uCP3hZuKelH8qHbA/Pr47TBhJOT9+C6bLoOktf19XqESPz9oyn4VMPGpENmy7VawaUn3oi3Y73yS"
        "lYGCP9uMsWSl3dgaoW7Z+VfANV1vcpDbwbU0YyCxSRzlbTbhtajE4sbJcO3oWKWUeTOq4TltAi7fUnL6F2fY8/LPXmhUz7Gt"
        "XKkgWLX7KhBW/epOGBYx3bb4zGhLi1bxhmm0ttmosDc3WomGulZ3DmZ3RmvQQ1f40dSk6OWndXDdFvSVm+wQ9eWCNj2nI9ft"
        "lrfj0u50F8WdW5Rpb88+uqVNiFHPofYQdEcWMmA5NztTmHOKzO/RPmaLCHfzMRDEHEbpCakLqTYanTlOU5PN0Wh60uZwQJtD"
        "u5k+Iu/jZlDiPT1NH4F3eJresu6qdwxa1E9ZCLACptuWJT3HZpDTSNcxme1RC74QD+5XjfOsX0Vb9xCttSb86aMTfXnP5Wwd"
        "eC+E0bBS74nSjGtMMjDJwCSj0mQoUS8mGScVNyYZmGQ0jMWzD3kxyTi1xDHJeIlJRvM7659/ilF/Uz2mGMfoGVMMTDEwxRi0"
        "uDHFwBSjYSyefcCLKcapJY4pxktMMZpfrvn8U4zHLZbadrZw74zjJ9XFhQbjOBH/crwk4Rwfg4kJJiaYmAxa3JiYYGLSMBbP"
        "PkzGxOTUEsfE5CUmJstwe7XH6kyTlAOfloZAyDF63i/f2Dq07enGSz+RrMtF9tm53OkgGyKiTf9ourvORC/t2IenkfJL2X1/"
        "BOkeKJ94OWftdAdaDc+pBVrlc+0O40me3cknTyXoF3MKxVEEfBYpw/JYhvPekvGIPAF3ZGCWgFnCYOJXzBIwSxi0dDFLGEJs"
        "hVnCcASNWcKLyRLOeU/FI3IE3FKBOQLmCIOJXjFHwBxh0NLFHGEIkRXmCMMRNOYILyZHOOdNEU+23gj3RAxxTwRmFhsCee4x"
        "L2YWmFkMWrqYWQwhHsPMYjiCxszixWQWz2VXw3G3Xm+Qg1jEfj3vlzHgLupWT/kCtvXiLuqTiht3UeMu6oaxePZ7enEX9akl"
        "jruoX+Iu6nVc3TvRWDNYUaSb0fzxb+gD6YQzaJkjBePf01r8KlVKUzwljFL17j71dd2mrOnHI2/dbC8am+LuvVI29XGcF3ga"
        "PucBJ4wQ4UuTQk4tde54lhAMFIlxYTDig55xyX1q+66l675nBrpOiceky8sv8NRdxxjA2y4NDr2bnm140naZen+nqTOX+par"
        "Gx6QbXP4R13TDBxdMso8YQhJDM5caTFOaYkl4rnWEF7gaVqCM52LwPR8gzpe4FkwFlJ6pusZ0jKJCxd1YQS26QMllqSmcHxK"
        "7MA2dIew8gs8Xc/b6zWrvV7g6efcFVmlyte/XYl4qr3SVKJ/m7BMCi2RbHKpTFO7Fit0ECaX+NGecinCXu/mXLxDXT2QsyiO"
        "Qg5kLd1lxT8eowgiwpT5k0J8G5WM1mLMekbZ9M8Hnl1cUAjiMJP5wjd1l/m+ZwtuOBSUD/TKUo2XA6q6rMvvMozCLGSTjblm"
        "iJSu3txcTEVnRLCa4M+J3tJkmlPdNDtCmJJ1hR95m+W0m6tZbWZuTfgaqmmtP5V/+a00q7dHHiXKqmq1G4HbVgscmVqlU4cl"
        "tqtUeUjq1xp2eh4+38FV+C+7k9qPJTraOGvS/thPZVKqYldmm0T+c56b8iYHB31T9iE6TbOHiUzHPK2HzEfMGB7fde5Qj9Lz"
        "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"
    ),
)


def recorded_artifact_journey(root):
    """Real 165/176 KB questions replay Store -> PRODUCT, without providers (T-95).

    Large JSON artifacts escaped the stdout bound; tiny scripted artifacts and source-only size
    checks missed this. Preserve artifact provenance, bounded tails and images in the actual brief.
    """
    image_fixture = json.loads(Path(__file__).with_name("recorded_judgment.json").read_text("utf-8"))
    image_data = base64.b64decode(next(iter(image_fixture["images"].values())))
    for cohort, encoded in zip(("c02", "c03"), RECORDED_ARTIFACT_QUESTIONS):
        original = json.loads(zlib.decompress(base64.b64decode(encoded)))
        project = root / cohort
        project.mkdir(parents=True)
        commit = gitwork.init(project)
        subject, = original["targets"]
        if subject == "tryItGuide":
            target = project / plan.GUIDE_PATH
            target.parent.mkdir(parents=True)
            target.write_bytes(original["facts"]["Guide file"]["text"].encode("utf-8"))
            gitwork.git(project, "add", "--all")
            commit = gitwork.commit(project, gitwork.git(project, "write-tree"), commit)
            gitwork.git(project, "update-ref", "HEAD", commit)
        store = Store(project)
        try:
            document = plan.create({**VISION, "quality": [original["facts"]["Subject"]]},
                                   questions.example_plan())
            checks = original["facts"]["Checks"]
            owner = "guide" if subject == "tryItGuide" else "u001"
            if owner == "guide":
                document = plan.add_guide(document)
            else:
                document["nodes"].pop("n003")
                for ref, check in checks.items():
                    document["nodes"][ref] = {"kind": "check", "body": {**check, "requirements": ["n002"]}}
                criterion = document["nodes"].pop("quality:001")
                criterion["body"]["checks"] = list(checks)
                document["nodes"][subject] = criterion
            scratch = project / "scratch"
            scratch.mkdir()
            kickoff = store.record("kickoff", {"setup": {"providers": ROLES}})
            store.cursor(RunRow(kickoff=kickoff, scratch=str(scratch)))
            largest = max(original["facts"]["Check artifacts"].values(), key=lambda b: len(b.get("text", "")))
            source = scratch / "report.json"
            source.write_bytes(largest["text"].encode("utf-8"))
            command = [sys.executable, "-B", "-c",
                "import os,sys; from pathlib import Path; data=Path(sys.argv[1]).read_bytes(); "
                "Path(os.environ['SELDON_EVIDENCE_DIR'],'report.json').write_bytes(data); "
                "sys.stdout.buffer.write(data)", str(source)]
            observed, = proof.observe(store, project, commit, {"recorded": {
                "command": command, "requirements": []}}, "recorded", disposable=True)
            canonical = store.read(observed)
            assert not proof.failures(store, [observed])
            artifacts = {}
            for path, body in original["facts"]["Check artifacts"].items():
                data = body["text"].encode("utf-8") if "text" in body else image_data
                artifacts[path] = store.artifact(data, "recorded-real-question")
            tree = gitwork.scope_tree(project, commit, plan.select(document, "unit")[owner]["scope"], scratch)
            verdict = store.record("unit_verdict", {"scopeTree": tree, "answer": {"verdict": "satisfied"}})
            store.unit(owner, UnitRow(state="DONE", P=commit, judgment=verdict))
            observations = original["facts"]["Final observations"]
            refs = [store.record("observation", {**body, "unit": owner, "cwd": str(project),
                "commit": commit, "scopeTree": tree,
                "evidenceFiles": list(artifacts.values()) if i == 0 else []})
                for i, body in enumerate(observations)]
            store.cursor(RunRow(revision=store.revision(document), candidate=commit, finalCommit=commit,
                                kickoff=kickoff, scratch=str(scratch), finalObservations=refs))
            question = judgment.brief(store, "PRODUCT", subject)
            size = len(encode(question).encode("utf-8"))
            assert size < 65536, (cohort, size)
            assert question["targets"] == original["targets"]
            if subject == "tryItGuide":
                assert question["facts"]["Guide file"]["text"] == original["facts"]["Guide file"]["text"]
                assert question["facts"]["Subject"]["verification"] == plan.GUIDE_WORK
            else:
                assert question["facts"]["Subject"] == original["facts"]["Subject"]
            assert question["images"] == list(dict.fromkeys(artifacts[p] for p in original["images"]))
            for path, body in original["facts"]["Check artifacts"].items():
                result = question["facts"]["Check artifacts"][artifacts[path]]
                assert result["commit"] == commit
                if "text" in body:
                    data = body["text"].encode("utf-8")
                    assert result["bytes"] == len(data)
                    assert result["text"] == data[-4096:].decode("utf-8", errors="replace")
                    if body == largest:
                        assert canonical["evidenceFiles"] == [artifacts[path]]
                        assert result["text"] == canonical["stdout"]
            before = len(encode(original).encode("utf-8"))
            print(f"T-95 recorded {cohort}: {before} -> {size} bytes", flush=True)
        finally:
            store.close()


def clean_core_golden(root):
    from test_architect import architect_journeys
    from test_review import review_journeys

    root.mkdir(parents=True)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(root)
    began = time.monotonic()
    guide_links_journey(root / "guide-links")
    recorded_artifact_journey(root / "recorded-artifacts")
    recorded_judgment_journey(root / "recorded-judgment")
    recorded_sizes = recorded_minimal_journey(root / "recorded-minimal")
    prepared = {"demo-uncertain", "product-uncertain", "demo-gap", "product-gap",
                "demo-unresolved", "product-unresolved", "guide-gap",
                "product-source-resolved", "product-source-unresolved", "product-source-oversize",
                "product-facts-unavailable", "demo-facts-unavailable"}
    modes = [("check-deadline", 2), ("scope", 1), ("false-red", 1), ("skipped", 1), ("fresh", 1),
             ("timeout", 1),
             ("exhaust", 4), ("parallel", 4), ("review-repair", 2), ("final-rework", 2),
             ("schema-fix", 1), ("plan-overlap", 2), ("user-path", 1), ("judge-provider", 2),
             ("big-source", 2), ("big-product", 2), ("user-revise", 1), ("wave-start", 2),
             ("native-denied", 2)]
    modes.extend([("traced-evidence", 2), ("demo-uncertain", 2), ("product-uncertain", 2),
                  ("demo-gap", 2), ("product-gap", 2), ("demo-unresolved", 2), ("product-unresolved", 2),
                  ("guide-gap", 2), ("guide-command", 1), ("guide-path", 1), ("guide-format", 1)])
    modes.extend([("unit-uncertain", 1), ("demo-facts-unavailable", 2), ("visual-unit", 1), ("mixed-criterion", 2), ("product-source-resolved", 2), ("product-source-unresolved", 2)])
    modes.extend([("product-source-oversize", 2), ("product-facts-unavailable", 2)])

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
    with ThreadPoolExecutor(max_workers=8) as pool:
        baseline_task = pool.submit(simple_journey, root / "single1", "single1")
        admission_task = pool.submit(admission_journey, root / "admission")
        stale_task = pool.submit(stale_scope_journey, root / "stale-scope")
        seed_task = pool.submit(final_seed, root / "final-seed")
        records_task = pool.submit(records_and_skeleton_journey, root / "records")
        architect_task = pool.submit(architect_journeys, root / "architect")
        review_task = pool.submit(review_journeys, root / "review")
        list(pool.map(run_case, modes))
        baseline = baseline_task.result()
        admission_task.result()
        stale_task.result()
        records_task.result()
        architect_task.result()
        review_task.result()
        seed = seed_task.result()
    # A copied fault must never mutate its shared seed through an old store/Git/provider path.
    store = Store(seed / "product")
    try:
        assert store.cursor().state == "FLOW-JUDGE"
        gitwork.unchanged(seed / "product", store.cursor().candidate)
        assert all((seed / name).read_text() == "1" for name in
                   ("turn-unit-001", "turn-unit-002", "turn-docs"))
    finally:
        store.close()
    start = time.monotonic()
    scale = simple_journey(root / "scale50", "scale50", 50)
    scale_seconds = time.monotonic() - start
    print(f"M1 executed question bytes {baseline} -> {scale}", flush=True)
    # The required real replay is part of the measured corpus, with exact 1/50 equality above.
    # Independent u001 is also compared directly; consumer topology is distinct subject data.
    for kind, size in recorded_sizes.items():
        baseline[kind] = max(baseline[kind], size)
        scale[kind] = max(scale[kind], size)
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
