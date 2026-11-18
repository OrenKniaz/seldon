"""Replay the retained Quiet Bench command through B, WORK and Git-bound proof."""

import json
from pathlib import Path

import pytest

from seldon import gitwork, plan
from seldon.run import Run
from test_journey import NATIVE, Scripted, approve, strict_schema

RETAINED = Path("C:/seldon/tests/m1/quietbench-555101a-c01/product")
REVISION = "17f7344e31be2de62448d8713c9d947a09d1504a"
PRODUCT_PATHS = ("bench.py", "backend/core.py", "simulation/__init__.py", "simulation/run.py")
PRODUCT_SCOPE = ["bench.py", "backend/**", "simulation/**", "checks/bench.py"]
CHECK = """\
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

command, = (line for line in Path("docs/try-it.md").read_text(encoding="utf-8").splitlines()
            if line.startswith("python bench.py run "))
args = [arg[1:-1] if arg[0] == arg[-1] and arg[0] in ('"', "'") else arg
        for arg in shlex.split(command, posix=False)]
with tempfile.TemporaryDirectory() as data:
    args[args.index("--data") + 1] = data
    result = subprocess.run([sys.executable, "-B", *args[1:]], capture_output=True, text=True, timeout=30)
assert result.returncode == 0, result.stderr
session = json.loads(result.stdout)
assert session["id"] and session["processed_samples"] > 0, session
print("Guide produced a saved session")
"""


class QuietBench(Scripted):
    def __call__(self, question):
        facts = question["facts"]
        if question["kind"] == "ALIGN":
            result = json.loads(super().__call__(question))
            result["answer"].update(size="tiny", understanding="Run the Quiet Bench CLI from the guide",
                scopeIn=["Create and inspect a saved simulation session from example settings"])
            return json.dumps(result)
        if question["kind"] == "ARCHITECT":
            result = json.loads(super().__call__(question))
            result["answer"]["areas"][0]["scope"] = PRODUCT_SCOPE
            if "Finished wave" in facts:
                result["answer"]["waves"].append({"goal": "Give export its own delivery scope",
                                                  "commitments": ["scopeIn:1"]})
                result["answer"]["areas"].append({"handle": "export", "scope": ["export/**"],
                    "commitments": ["scopeIn:1"], "responsibility": "Export the saved session"})
            return json.dumps(result)
        if question["kind"] == "DETAIL":
            area = facts["Area"]
            original = facts["What exists here"]
            template = {**facts, "Area": {**area, "scope": ["unit-001/**"]}}
            result = json.loads(super().__call__({**question, "facts": template}))
            answer = result["answer"]
            original_scope = next(iter(original.values()))["scope"] if original else area["scope"]
            answer["units"][0]["scope"] = original_scope
            answer["edits"][0]["typedBody"].update(
                story="Create a simulated bench session", acceptance="Run the bench with example settings")
            answer["edits"][1]["typedBody"]["acceptance"] = "The documented command creates a saved session"
            return json.dumps(result)
        if question["kind"] == "JUDGE" and "Subjects" in facts:
            strict_schema(question["schema"])
            assert facts["Walkthrough checks"]
            findings = {subject: {"verdict": "uncertain", "neededFacts": ["Review the full experience"],
                                  "why": "This replay exercises the first-use guide."}
                        for subject in facts["Subjects"]}
            findings["tryItGuide"] = {"verdict": "satisfied", "gaps": [],
                                      "why": "The documented example creates a saved session."}
            return json.dumps({"answer": {"findings": findings, "why": "Review the first-use guide."}})
        return super().__call__(question)


def scripted_worker(slot):
    sources = {path: gitwork.git(RETAINED, "show", f"{REVISION}:{path}") for path in PRODUCT_PATHS}
    guide = gitwork.git(RETAINED, "show", f"{REVISION}:docs/try-it.md")
    assert "python bench.py run --data $Data --settings '<settings JSON>'" in guide
    settings = next(line for line in guide.splitlines() if line.startswith('{"sample_rate":'))
    assert json.loads(settings)["sample_rate"] == 8000
    broken, fixed = guide, guide.replace("<settings JSON>", settings, 1)
    worker = f"""\
if name != 'docs':
    for path, source in {sources!r}.items():
        target = Path(path)
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_text(source, encoding='utf-8')
    Path('checks').mkdir(exist_ok=True)
    Path('checks/bench.py').write_text(
        "import subprocess, sys, tempfile\\n"
        "with tempfile.TemporaryDirectory() as data:\\n"
        "    result = subprocess.run([sys.executable, '-B', 'bench.py', 'run', '--data', data, "
        "'--settings', 'bad-config'], capture_output=True, text=True, timeout=30)\\n"
        "assert result.returncode == 1 and result.stderr.startswith('Expecting value:'), result.stderr\\n",
        encoding='utf-8')
    answer['checkPaths'] = ['checks/bench.py']
    answer['checks'] = [{{'purpose': 'Reject malformed settings',
        'command': [sys.executable, '-B', 'checks/bench.py'], 'expectedEvidence': 'JSON error and exit 1'}}]
if name == 'docs' or 'docs/try-it.md' in facts['Write scope']:
    folded = name != 'docs'
    Path('docs/try-it').mkdir(parents=True, exist_ok=True)
    guide = {fixed!r} if folded else {broken!r}
    Path('docs/try-it.md').write_text(guide, encoding='utf-8')
    Path('docs/try-it/check.py').write_text({CHECK!r}, encoding='utf-8')
    answer['checkPaths'].append('docs/try-it/check.py')
    answer['checks'].append({{
        'purpose': 'Run the documented command in a fresh checkout',
        'command': [sys.executable, '-B', 'docs/try-it/check.py'],
        'expectedEvidence': 'Guide produced a saved session'}})
"""
    start = NATIVE.index("if mode == 'schema-fix' and name == 'unit-001' and turn == 1:")
    end = NATIVE.index("if 'Existing checks' in facts", start)
    (slot / "native.py").write_text(NATIVE[:start] + worker + NATIVE[end:], encoding="utf-8")


@pytest.mark.fast
@pytest.mark.parametrize("split", ["auto", "force"])
def test_size_guide_journey(tmp_path, monkeypatch, split):
    """Catch valid Markdown that ships unusable inputs; existing kit covers a checker independent of prose."""
    monkeypatch.setenv("SELDON_OFF", "growAlign")
    monkeypatch.setenv("SELDON_SPLIT", split)
    slot = tmp_path / "guide"
    slot.mkdir()
    (slot / "mode").write_text("size-guide")
    scripted_worker(slot)
    provider = QuietBench(slot)
    setup = {**provider.setup, "sizeFirst": True, "split": split}
    run = Run(slot / "product", slot / "workers", "Run the Quiet Bench example", setup, provider, approve)
    try:
        while run.store.cursor().state != "EXECUTE":
            run.step()
        cursor = run.store.cursor()
        document = run.store.read(cursor.B)["subject"]["plan"]
        owners = plan.select(document, "unit")
        product, = (ref for ref, unit in owners.items() if "bench.py" in unit["scope"])
        if split == "force":
            assert plan.guide_owner(document) == "guide" and len(owners) == 2
            return
        assert run.step() == "WAVE-VERIFY"
        candidate = run.store.cursor().candidate
        assert gitwork.contains(run.project, cursor.candidate, candidate)
        run.step()
        observations = [body for _, _, body in run.store.records("observation")
                        if body["purpose"] == "final" and body["command"][-1] == "docs/try-it/check.py"]
        observed, = observations
        assert observed["commit"] == candidate
        assert observed["executionStarted"] and observed["cleanupVerified"]
        assert observed["cwd"] != str(run.project)
        assert gitwork.git(observed["cwd"], "rev-parse", "HEAD") == candidate
        assert observed["exitCode"] == 0, observed["stderr"]
        assert "Guide produced a saved session" in observed["stdout"]
        assert observed["unit"] == product and owners.keys() == {product}
        executed = plan.execution(run.store)
        assert plan.guide_owner(executed) == product
        checks = plan.guide_checks(executed)
        assert checks.keys() == {observed["check"]}
        requirement, = checks[observed["check"]]["requirements"]
        assert product in plan.coverage(executed)[requirement]["units"]
        feature, = plan.select(executed, "requirement")[requirement]["features"]
        assert plan.select(executed, "feature")[feature]["vision"] == ["vision"]

        # Use the same check-in and reopen path when a later wave gives a peer separate work.
        assert run.step() == "CHECK-IN"
        run.user = lambda request: "Revise how export is delivered within the accepted scope"
        assert run.step() == "PRODUCT-JUDGE"
        assert run.step() == "ARCHITECT"
        assert run.step() == "DETAIL"
        run.close()
        run = Run.reopen(slot / "product", provider, approve)
        assert run.step() == "REVIEW"
        assert run.step() == "EXECUTE"
        later = plan.execution(run.store)
        assert plan.guide_owner(later) == product and plan.guide_checks(later) == checks
        later_units = plan.select(later, "unit")
        assert len(later_units) == 2 and "guide" not in later_units
        assert run.store.unit(product).state == "DONE"
        assert run.store.cursor().B == cursor.B and run.store.cursor().candidate == candidate
    finally:
        run.close()
