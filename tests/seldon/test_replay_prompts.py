"""Replay journeys using actual as-of-call WORK and folded JUDGE records."""

import importlib.util
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools/replay_prompts.py"
spec = importlib.util.spec_from_file_location("replay_prompts", TOOL)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)

CASES = ("9996c71c", "6a5e00ae")


def replay_cli(tool, *args, ok=True):
    result = subprocess.run([sys.executable, "-B", str(tool), *map(str, args)],
                            capture_output=True, text=True, timeout=45)
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return result.stdout + result.stderr


def pinned_tool(slot):
    # The experiment must exercise the real pin guard even after main's renderer changes.
    source = replay.REPO
    try:
        replay.git(source, "cat-file", "-e", f"{replay.PIN}^{{commit}}")
    except ValueError:
        # Lane snapshots contain the source tree but omit the canonical history.
        source = Path("C:/Users/oren/codex-harness/work/sprint-20261027/semantic-cc")
    replay.git(source, "clone", "--quiet", "--shared", "--no-checkout", str(source), str(slot))
    replay.git(slot, "update-ref", "--no-deref", "HEAD", replay.PIN)
    replay.git(slot, "restore", "--source=HEAD", "--staged", "--worktree", "--", "src/seldon")
    tool = slot / "tools" / TOOL.name
    tool.parent.mkdir()
    shutil.copyfile(TOOL, tool)
    return tool


@pytest.mark.skipif(
    not (replay.RIG / replay.Q / "calls").is_dir(), reason="Needs the original Quiet Bench calls"
)
def test_recorded_work_and_folded_judge_round_trip(tmp_path):
    """A replay built from today's checkout can silently lose the old kit, receipt schema or screenshot."""
    root = tmp_path / "experiment"
    run = partial(replay_cli, pinned_tool(tmp_path / "pinned"))
    run("prepare", root, "--case", CASES[0], "--case", CASES[1])
    private = root / "private"
    key_file = private / "key.sealed.json"
    key = replay.read_json(key_file)
    prepared = replay.read_json(private / "prepared.json")["cases"]
    outputs, blind_diff = {}, {}
    for pair, entry in key.items():
        case, labels = entry["case"], entry["labels"]
        source, = (replay.RIG / replay.Q / "calls").glob(f"call-{case}*")
        a, b = (private / pair / labels[arm] for arm in "AB")
        assert prepared[pair]["state"] == "prepared"
        assert prepared[pair]["base"]["tree"]
        assert (a / "schema.json").read_bytes() == (b / "schema.json").read_bytes() == (
            source / "schema.json").read_bytes()
        q = replay.read_json(source / "question.json")
        assert replay.digest(replay.json_bytes(q["facts"])) == prepared[pair]["factsHash"]
        assert (a / "prompt.txt").read_bytes() != (b / "prompt.txt").read_bytes()
        if case == CASES[0]:
            revision = q["facts"]["Execution facts"]["Base"]
            kit = "checks/kit/browser.cjs"
            assert (private / pair / "base" / kit).read_bytes() == replay.git(
                source.parent.parent / "product", "show", f"{revision}:{kit}")
            assert b"Write code a senior engineer" in (b / "prompt.txt").read_bytes()
            pristine = private / pair / "base"
            work = tmp_path / "sample-work"
            shutil.copytree(pristine, work)
            repo = tmp_path / "sample-git"
            repo.mkdir()
            replay.git(repo, "init", "--quiet", "--template=")
            base = prepared[pair]["base"]["files"]
            tree = replay.git_stage(repo, work, {name: mode for name, (mode, _) in base.items()})
            assert tree == prepared[pair]["base"]["tree"]
            (work / "bench.py").write_text("print('sample replay')\n", encoding="utf-8")
            diff, outside = replay.capture(repo, work, base, tree, q["facts"]["Write scope"])
            assert not outside
            for label in labels.values():
                outputs[label] = {"status": "completed", "answer": q["examples"][0], "diff": diff.decode()}
        else:
            assert (a / "image-0.png").read_bytes() == (b / "image-0.png").read_bytes() == (
                source / "image-0.png").read_bytes()
            assert b"sourceAcceptance" in (b / "prompt.txt").read_bytes()
            for label in labels.values():
                # Authored illustration from the recorded question, never the historical answer.
                outputs[label] = {"status": "completed", "answer": q["examples"][0]}
        blind_diff[pair] = labels
    assert "required" in run("generate", root, ok=False)
    sample_file = tmp_path / "sealed-samples.json"
    replay.put_json(sample_file, outputs)
    run("import-samples", root, sample_file)
    packet_dir = tmp_path / "review"
    run("package", root, packet_dir)
    manifest = packet_dir / "packet.json"
    packet = replay.read_json(manifest)
    assert packet["sample"] is True
    assert set(packet["pairs"]) == set(key)
    work_pair = next(pair for pair, entry in key.items() if entry["case"] == CASES[0])
    assert (packet_dir / work_pair / "base/checks/kit/browser.cjs").is_file()
    assert len(list(packet_dir.rglob("image-0.png"))) == 1
    for label in blind_diff[work_pair].values():
        assert "+++ b/bench.py" in (packet_dir / label / "source.diff").read_text("utf-8")
    assert all(case not in manifest.read_text("utf-8") for case in CASES)
    assert {"answer.txt", "prompt.txt", "instructions.txt", "key.sealed.json"}.isdisjoint(
        path.name for path in packet_dir.rglob("*"))

    ballot = {}
    for pair, row in packet["pairs"].items():
        assert set(row["outputs"]) == set(blind_diff[pair].values())
        assert all(out["status"] == "completed" for out in row["outputs"].values())
        labels = list(row["outputs"])
        if row["kind"] == "WORK":
            ballot[pair] = {axis: {"winner": labels[0], "evidence": "sample walkthrough"}
                            for axis in replay.AXES}
            ballot[pair].update(
                guards={label: {**dict.fromkeys(replay.GUARDS, False), "evidence": "sample review"}
                        for label in labels},
                reruns=dict.fromkeys(labels, "sample check observation"))
        else:
            ballot[pair] = {label: {"preserved": True, "concreteSource": True, "uncertaintyPreserved": True,
                                    "evidence": "sample sourceAcceptance reason"} for label in labels}
    one, two = tmp_path / "reviewer-one.json", tmp_path / "reviewer-two.json"
    replay.put_json(one, ballot)
    work = next(pair for pair, row in packet["pairs"].items() if row["kind"] == "WORK")
    ballot[work]["codeQuality"]["winner"] = "tie"
    replay.put_json(two, ballot)
    lock, result = tmp_path / "locked.json", tmp_path / "revealed.json"
    run("reveal", root, manifest, lock, "--output", result, ok=False)
    assert not result.exists()
    run("lock", manifest, one, two, "--output", lock)
    sample_diff = packet_dir / next(iter(blind_diff[work_pair].values())) / "source.diff"
    original = sample_diff.read_bytes()
    sample_diff.write_bytes(original + b"changed")
    assert "packet files changed" in run("reveal", root, manifest, lock, "--output", result, ok=False)
    sample_diff.write_bytes(original)
    run("reveal", root, manifest, lock, "--output", result)
    revealed = replay.read_json(result)
    assert revealed["key"] == key
    assert revealed["cases"][CASES[0]]["grades"]["codeQuality"] == "tie"
    assert revealed["sample"] and revealed["nextCohort"] == "A" and not revealed["complete"]
