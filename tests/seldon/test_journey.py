"""C1/C8/C14/C15: one complete offline journey; inference alone is scripted."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from seldon.loop import Loop

PRODUCT_TEST = '''import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
result = subprocess.run([sys.executable, "-B", str(root / "greet.py"), "Ada"],
                        capture_output=True, text=True, timeout=5)
if result.returncode:
    print(result.stderr, file=sys.stderr)
    raise SystemExit(result.returncode)
assert result.stdout == "Hello, Ada!\\n", result.stdout
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
    return {"edits": [
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
        put("test", "walkthrough", purpose="Demonstrate the command with the guide's input",
            stage="walkthrough", command=[sys.executable, "-B", "greet.py", "Ada"],
            requiredEnvironment=[f"Python interpreter: {sys.executable}"],
            expectedEvidence="Live stdout Hello, Ada!", requirements=["name"]),
        put("bead", "tests", task="tests", instructions="Write the CLI semantic test first",
            ownedPaths=["check_greeting.py"], dependencies=[], requirements=["name"],
            tests=["personal-greeting"]),
        put("bead", "build", task="build", instructions="Implement the approved greeting CLI",
            ownedPaths=["greet.py"], dependencies=["tests"], requirements=["name"],
            tests=["personal-greeting"]),
        put("bead", "guide", task="guide", instructions="Write a usable try-it-yourself guide",
            ownedPaths=["TRY.txt"], dependencies=["build"], requirements=["name"],
            tests=["walkthrough"]),
        put("wave", "delivery", goal="Deliver the greeting and check in before final walkthrough",
            beads=["tests", "build"]),
    ]}


@pytest.mark.fast
def test_journey_delivers_tested_demonstrated_candidate(tmp_path):
    """Protect delivery and C binding against canned proof or skipped RED/integration.

    Legacy journeys import the old runtime; none exercise this clean producer-to-consumer spine.
    The script writes files only; independent assertions inspect actual outputs and persisted truth.
    """
    calls = Counter()
    requests = []
    worker_roots = []
    scripts = {
        ("ALIGN", 1): {"size": "tiny", "understanding": "A personal greeting command",
            "users": ["Terminal users"], "scopeIn": ["Greet a supplied name"],
            "scopeOut": ["Interactive UI"],
            "improvements": ["Personalize rather than a fixed greeting"],
            "quality": [{"criterion": "Clear stdout and a usable guide",
                         "verification": "Execute the CLI and follow the delivered guide"}],
            "unknowns": []},
        ("PLAN", 1): scripted_plan(),
        ("REVIEW", 1): {"verdict": "adequate", "gaps": [], "neededFacts": []},
        ("STEER", 1): {"choice": "continue", "targets": [], "action": {"kind": "user_decision"}},
    }
    for occurrence in range(1, 5):
        scripts[("DECIDE", occurrence)] = {"choice": "approve", "requestedChange": None}
    materializations = {
        ("WORK", 1): {"check_greeting.py": PRODUCT_TEST},
        ("WORK", 2): {"greet.py": PRODUCT},
        ("WORK", 3): {"TRY.txt": GUIDE},
    }
    for key, files in materializations.items():
        scripts[key] = {"outcome": "done", "summary": "Assigned files written",
                        "artifactPaths": list(files), "discoveries": []}
    for occurrence, why in enumerate((
            "The functioning test launches Python; it fails because greet.py does not exist",
            "Integrated CLI passes; trace, quality and no-missing checks are satisfied",
            "Live output demonstrates the greeting and TRY.txt explains how to reproduce it"), 1):
        scripts[("JUDGE", occurrence)] = {"verdict": "satisfied", "assessments": [],
                                         "gaps": [], "neededFacts": [], "why": why}

    def provider(question):
        kind = question["kind"]
        calls[kind] += 1
        key = kind, calls[kind]
        answer = dict(scripts[key])
        facts = question["facts"]
        if kind == "WORK":
            assignment = next(f["boundedContent"] for f in facts.values()
                              if isinstance(f["boundedContent"], dict)
                              and "worktree" in f["boundedContent"])
            root = Path(assignment["worktree"])
            worker_roots.append(root)
            for name, content in materializations[key].items():
                (root / name).write_text(content, encoding="utf-8")
        if kind == "JUDGE":
            answer["assessments"] = [{"target": target, "satisfied": True, "why": answer["why"]}
                                     for target in question["context"]["assessments"]]
        if kind == "STEER":
            answer["targets"] = [next(iter(facts))]
        answer.setdefault("why", "The supplied facts support the scripted semantic answer")
        answer["citations"] = list(facts)
        return json.dumps(answer)

    user_words = ["Yes, that is the dream I want", "Build that plan",
                  "Looks good, show me how to use it",
                  "I tried the demonstrated greeting and accept this delivery"]

    def user(request):
        requests.append(request)
        return user_words[len(requests) - 1]

    loop = Loop(tmp_path / "product", tmp_path / "workers",
                "Make a command that greets the person whose name I supply", {
                    "providers": {role: f"scripted:{role}"
                                  for role in ("planner", "reviewer", "worker", "judge")},
                    "interpreter": sys.executable, "userOrigin": "scripted journey conversation",
                }, provider, user)
    cursor = loop.run()
    loop.store.db.close()

    # Read the durable result through a separate real SQLite connection.
    with sqlite3.connect(loop.project / ".seldon" / "state.sqlite3") as db:
        rows = [(f"r{i}", kind, json.loads(body)) for i, kind, body in
                db.execute("SELECT id,kind,body FROM record ORDER BY id")]
        persisted = json.loads(db.execute("SELECT cursor FROM run").fetchone()[0])
    records = {ref: body for ref, _, body in rows}
    observations = [(ref, body) for ref, kind, body in rows if kind == "observation"]
    assert cursor["state"] == persisted["state"] == "ACCEPTED"
    assert [r["gate"] for r in requests] == ["A", "B", "check-in", "C"]
    assert [o["purpose"] for _, o in observations] == ["RED", "GREEN", "demo"]
    red, green, demo = [body for _, body in observations]
    assert red["executionStarted"] and red["exitCode"] != 0 and not red["timedOut"]
    red_stderr = (loop.project / ".seldon" / "artifacts" / red["outputRefs"][1]).read_text()
    assert "greet.py" in red_stderr
    assert "No such file" in red_stderr or "cannot find" in red_stderr
    assert "greet.py" not in records[red["candidate"]]["files"]
    assert "check_greeting.py" in records[red["candidate"]]["files"]
    assert green["exitCode"] == demo["exitCode"] == 0
    green_stdout = (loop.project / ".seldon" / "artifacts" / green["outputRefs"][0]).read_text()
    assert "PASS: the CLI greets the supplied person" in green_stdout
    assert all(root.is_dir() and (root / ".git").is_file() for root in worker_roots)
    assert len(set(worker_roots)) == 3
    assert (loop.project / "TRY.txt").read_text() == GUIDE
    c_subject = requests[-1]["subject"]
    assert c_subject["candidate"] == persisted["candidate"] == demo["candidate"]
    assert records[c_subject["candidate"]]["files"] == {
        name: hashlib.sha256((loop.project / name).read_bytes()).hexdigest()
        for name in ("check_greeting.py", "greet.py", "TRY.txt")}
    assert c_subject["evidence"] == [ref for ref, _ in observations]
    assert all(records[ref]["satisfied"] for ref in c_subject["judgments"])
    # A fresh user interaction with a different input independently proves delivered behavior.
    result = subprocess.run([sys.executable, "-B", "greet.py", "Grace"], cwd=loop.project,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and result.stdout == "Hello, Grace!\n"
