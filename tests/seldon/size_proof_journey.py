"""R19 journey: the recorded Quiet Bench guide reaches its owner and closes on current proof."""

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from seldon import gitwork, plan
from seldon.run import Run, eligible
from test_journey import Scripted, approve, strict_schema


RETAINED = Path("C:/seldon/tests/oracle-qb3-ev")
WALKTHROUGH = r"""
import json
import re
import subprocess
import sys
from pathlib import Path

guide = Path('docs/try-it.md').read_text(encoding='utf-8')
settings, = re.findall(r"^python bench.py run --data \$Data --settings '([^']+)'$", guide, re.M)
assert "python bench.py show ID --data $Data" in guide
# Replay the c01 fresh-copy interpreter's treatment of a non-inline settings argument.
if not settings.startswith("{"):
    settings = Path(settings).read_text(encoding="utf-8")
command = [sys.executable, "-B", "bench.py"]
result = subprocess.check_output(command + ["run", "--data", "scratch-data", "--settings", settings])
saved = json.loads(result)
assert saved["processed_samples"] == 8192
again = subprocess.check_output(command + ["show", saved["id"], "--data", "scratch-data"])
assert json.loads(again) == saved
print(f"Reloaded {saved['processed_samples']} delivered samples")
"""



class QuietBenchProof(Scripted):
    def __init__(self, slot, recording):
        super().__init__(slot)
        self.setup.update(sizeFirst=True, split="auto")
        self.recording = recording
        raw = (recording / "docs/try-it.md").read_text(encoding="utf-8")
        command = next(line for line in raw.splitlines() if line.startswith("python bench.py run"))
        show = next(line for line in raw.splitlines() if line.startswith("python bench.py show"))
        self.guide = f"# Try the saved CLI example\n\n{command}\n{show}\n"
        self.settings = raw.split("\x60\x60\x60json\n", 1)[1].splitlines()[0]
        self.folded = []
        self.fixes = []

    def invoke(self, q, started):
        strict_schema(q["schema"])
        if q["kind"] == "WORK":
            return self.write(q), "quiet-bench-owner"
        if q["kind"] == "ALIGN" and "vision" in q["schema"]["properties"]["answer"]["properties"]:
            answer = q["examples"][0]["answer"]
            vision = answer["vision"]
            vision["understanding"] = "Run and reload a Quiet Bench example"
            vision["stories"][0].update(handle="saved-cli", feature="Saved CLI run",
                                        story="Run an example and reload it",
                                        outcome="Reload the same 8192 samples", sources=["kickoff"])
            vision["quality"][0].update(criterion="Browser and CLI agree",
                                         verification="Compare saved runs from both surfaces")
            return json.dumps({"answer": answer}), None
        if q["kind"] == "ARCHITECT":
            goals = list(q["facts"]["Commitment handles"])
            return json.dumps({"answer": {
                "areas": [{"handle": "cli", "responsibility": "Deliver the saved CLI run",
                           "scope": ["bench.py", "backend/**", "simulation/**"], "commitments": goals}],
                "waves": [{"goal": "Run and reload the example", "commitments": goals}],
                "contracts": [], "decisions": [], "why": "The saved CLI example has one owner."}}), None
        if q["kind"] == "DETAIL":
            facts = q["facts"]
            commitments = [r for r in facts["Accepted commitments"] if r.startswith("scopeIn:")]
            return json.dumps({"answer": {"verdict": "planned", "edits": [
                {"kind": "feature", "targetHandle": "saved-run", "typedBody": {
                    "story": "Reload a saved Quiet Bench run", "acceptance": "Reload the same 8192 samples",
                    "commitments": commitments}},
                {"kind": "requirement", "targetHandle": "reload", "typedBody": {
                    "features": ["saved-run"], "acceptance": "The documented CLI run reloads unchanged."}}],
                "units": [{"scope": facts["Area"]["scope"], "requirements": ["reload"], "dependencies": [],
                           "instructions": "Ship the saved CLI run and its walkthrough."}],
                "why": "One reader can exercise the documented CLI commands."}}), None
        if q["kind"] in ("JUDGE", "FLOW", "PRODUCT"):
            self.calls.append(q["kind"])
            shape = q["schema"]["properties"]["answer"]
            if "findings" not in shape.get("properties", {}):
                return json.dumps({"answer": {"verdict": "satisfied", "gaps": [], "why": "Legacy control"}}), None
            facts = q["facts"]
            self.folded.append(q)
            assert facts["Observations"]
            assert any(o["purpose"] == "T" and o["exitCode"] != 0 for o in facts["Observations"])
            assert any(o["purpose"] == "final" and o["exitCode"] == 0 for o in facts["Observations"])
            assert all(o["executionStarted"] and o["cleanupVerified"] for o in facts["Delivered execution"])
            browser = "Browser parity remains unresolved after the recorded OverflowError."
            findings = {subject: {
                "verdict": "satisfied", "gaps": [], "why": "The delivered CLI ran and reloaded its saved session."
            } for subject in q["targets"]}
            quality = next(s for s in findings if s.startswith("quality:"))
            findings[quality] = {"verdict": "uncertain", "neededFacts": ["A real browser parity observation"],
                                 "why": browser}
            return json.dumps({"answer": {"findings": findings, "why": "Reviewed the delivered CLI and guide."}}), None
        return super().invoke(q, started)

    def write(self, q):
        root = Path(q["execution"]["cwd"])
        facts = q["facts"]
        checks = []
        if "Exact failure" in facts:
            self.fixes.append(facts["Exact failure"])
        else:
            for name in ("bench.py", "backend/core.py", "simulation/__init__.py", "simulation/run.py"):
                target = root / name
                target.parent.mkdir(exist_ok=True, parents=True)
                target.write_bytes((self.recording / name).read_bytes())
            path = root / "docs" / "try-it" / "walk.py"
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_text(WALKTHROUGH, encoding="utf-8")
            checks.append({"purpose": "Follow the shipped CLI walkthrough from an isolated checkout",
                "command": [sys.executable, "-B", "docs/try-it/walk.py"],
                "expectedEvidence": "Stored session reloads its 8192 delivered samples.",
                "requirements": list(facts["Requirements"])})
        (root / plan.GUIDE_PATH).write_text(
            self.guide.replace("<settings JSON>", self.settings), encoding="utf-8")
        return json.dumps({"answer": {"outcome": "done", "summary": "Ship the literal c01 setup and reload it.",
            "checkPaths": ["docs/try-it/walk.py"], "checks": checks, "discoveries": [], "why": "Follow the guide."}})


@pytest.mark.fast
def test_size_proof_recorded_walkthrough(tmp_path, monkeypatch):
    """Separate judges miss false reuse across final subjects; the retained guide breaks on a literal argument."""
    oracle = json.loads((RETAINED / "oracle.json").read_bytes())
    browser, guide = [r for r in oracle["checks"] if r["name"] in (
        "page start/browse/reload/compare parity", "fresh-copy guide commands start and stop")]
    assert browser["status"] == guide["status"] == "fail"
    assert "OverflowError" in browser["evidence"] and "<settings JSON>" in guide["evidence"]
    monkeypatch.delenv("SELDON_OFF", raising=False)
    monkeypatch.setenv("SELDON_SPLIT", "auto")
    provider = QuietBenchProof(tmp_path, RETAINED / "fresh-copy")

    def user(item):
        if item["gate"] == "A" and "groups" in item["ask"]:
            return {"request": item["request"], "replyId": "align-done",
                    "selections": [{"groupId": "round", "optionId": "done"}]}
        return approve(item)

    run = Run(tmp_path / "product", tmp_path / "workers", "Run and reload a Quiet Bench example",
              provider.setup, provider, user, capacity=1)
    store = run.store
    try:
        for _ in range(12):
            if store.cursor().state == "WAVE-VERIFY":
                break
            run.step()
        cursor = store.cursor()
        assert cursor.state == "WAVE-VERIFY"
        owner, = store.units()

        # A new integrated candidate with the recorded broken guide must run new checks.
        (run.project / plan.GUIDE_PATH).write_text(provider.guide, encoding="utf-8")
        _, broken, _ = gitwork.snapshots(run.project, cursor.candidate, [plan.GUIDE_PATH], [], cursor.scratch)
        gitwork.checkout(run.project, broken)
        store.cursor(replace(cursor, candidate=broken))
        assert run.step() == "EXECUTE"
        failures = [b for _, _, b in store.records("observation") if b["purpose"] == "final"]
        failed, = failures
        assert failed["commit"] == broken and failed["unit"] == owner and failed["exitCode"] != 0
        assert "<settings JSON>" in failed["stderr"]
        assert not provider.folded
        run.close()
        monkeypatch.setenv("SELDON_SPLIT", "force")
        run = Run.reopen(tmp_path / "product", provider, user)
        store = run.store
        for _ in range(10):
            if store.cursor().state == "FLOW-JUDGE":
                break
            run.step()
        cursor = store.cursor()
        assert cursor.state == "FLOW-JUDGE"
        assert len(provider.folded) == 1 and provider.calls.count("JUDGE") == 1
        assert len(provider.fixes) == 1 and "<settings JSON>" in provider.fixes[0]
        assert cursor.candidate != broken and cursor.candidate == cursor.finalCommit
        original = store.unit(owner)
        row = store.read(original.judgment)
        for corrupt in (
            {**row, "findings": {k: v for k, v in row["findings"].items() if k != "tryItGuide"}},
            {**row, "scopeTree": gitwork.scope_tree(run.project, broken,
                plan.select(plan.execution(store), "unit")[owner]["scope"], cursor.scratch)},
        ):
            wrong = store.record("unit_verdict", corrupt)
            store.unit(owner, replace(original, judgment=wrong))
            with pytest.raises(ValueError, match="omitted|stale"):
                run.step()
            assert store.cursor().state == "FLOW-JUDGE"
        store.unit(owner, original)
        for _ in range(8):
            if store.cursor().state == "SCRIPTED_C":
                break
            run.step()
        cursor = store.cursor()
        assert cursor.state == "SCRIPTED_C"
        eligible(store)
        assert not {"FLOW", "PRODUCT"}.intersection(provider.calls)
        feature, = [store.read(r) for r in cursor.demoVerdicts]
        requirement, = [store.read(r) for r in cursor.requirementVerdicts]
        assert feature["scopeTrees"] == requirement["scopeTrees"] == {owner: row["scopeTree"]}
        assert feature["observations"] == cursor.finalObservations
        assert original.judgment in requirement["sources"] and cursor.demoVerdicts[0] in requirement["sources"]
        document = plan.execution(store)
        traced = document["nodes"][feature["feature"]]["body"]["commitments"]
        assert traced == ["scopeIn:saved-cli"]
        story, = document["nodes"]["vision"]["body"]["stories"]
        assert story["outcome"] == "Reload the same 8192 samples"
        assert story["sources"] == ["kickoff"]
        assert store.read(cursor.kickoff)["words"] == "Run and reload a Quiet Bench example"
        assert store.read(cursor.finalObservations[0])["requirements"] == [requirement["subject"]]
        assert all(store.read(r)["source"] == original.judgment for r in cursor.qualityVerdicts)
        assert any(store.read(r)["answer"]["verdict"] == "not judged" for r in cursor.qualityVerdicts)
    finally:
        run.close()
