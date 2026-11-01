import base64
import hashlib
import json
import sys

import pytest
from tests.python.core.semantic_runner_support import web_document

from codex_harness.core import dispatch, roundtable
from codex_harness.core.mcp_planner import PlannerServer


@pytest.fixture
def web_plan(semantic_plan):
    store, _ = semantic_plan
    reference = store.workspace / "design" / "direction.png"
    reference.parent.mkdir()
    reference.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8A"
            "AwMCAO+aX1sAAAAASUVORK5CYII="
        )
    )
    vision = store.spine().node("V1")
    document = web_document()
    document["scenarios"][0].update(requirementRefs=["R"], contractRefs=["CLI"])
    vision["fields"].update(
        productSurface="web",
        targets={"page": {"kind": "web", "entry": "index.html"}},
        scenarios=document,
        taste=dict(
            audience="Visitors booking a visit",
            tone="Calm",
            references=["Quiet spaces"],
            brand="Ink on white",
            requiredSections=["Welcome", "Details"],
            sourceRef="user:42",
        ),
        designRefs=[
            dict(
                path="design/direction.png",
                type="image",
                provenance="user:42",
                use="Colour and spacing",
                sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            )
        ],
    )
    store.upsert_node(vision)
    for key in ("C", "B"):
        bead = store.spine().node(key)
        bead["fields"]["ownedFiles"] = ["index.html"]
        store.upsert_node(bead)
    manifest = store.compile_template(store.workspace / "template")
    (store.workspace / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")
    return store, manifest


STUB = "import sys\nprint('SELDON-STUB hello', file=sys.stderr)\nsys.exit(3)\n"


PRODUCT = "print('Hello', end='')\n"


def node(key, kind, parents, **fields):
    return dict(
        id=key,
        kind=kind,
        title=key,
        text=f"{key}: offline greeting CLI",
        parents=parents,
        fields=fields,
    )


@pytest.fixture
def semantic_plan(tmp_path_factory, monkeypatch):
    # This source fixture supplies only Codex; host installations are not its providers.
    monkeypatch.setattr(
        roundtable,
        "_executable",
        lambda provider, _: sys.executable if provider == "codex" else None,
    )
    tmp_path = tmp_path_factory.mktemp("sv")
    server = PlannerServer(tmp_path)
    reply = server.call_tool(
        "harness_plan_start",
        dict(idea="Offline greeting CLI", name="greeting", plan_mode="semantic-v1"),
    )
    assert not reply["isError"], reply
    store = server.store
    for item in [
        node("F", "feature", ["V1"]),
        node("R", "requirement", ["F"]),
        node("D", "decision", ["V1"]),
        node("CLI", "design", ["R"], grammar="hello prints Hello and exits 0"),
        node(
            "C",
            "bead",
            ["CLI"],
            ownedFiles=["hello.py"],
            dependsOn=[],
            lessons=["D"],
            semanticPhase="contracts",
        ),
        node(
            "B",
            "bead",
            ["CLI"],
            ownedFiles=["hello.py", "test_hello.py"],
            dependsOn=["C"],
            lessons=[],
            semanticPhase="build",
        ),
    ]:
        store.upsert_node(item)
    spine = store.spine()
    vision = spine.node("V1")
    vision["fields"].update(
        targets={"greet": {"entry": "hello.py", "argv": []}},
        scenarios={
            "schema": "seldon/scenarios-v1",
            "scenarios": [
                {
                    "id": "first-moment",
                    "requirementRefs": [f"R@{spine.revision('R')}"],
                    "contractRefs": [f"CLI@{spine.revision('CLI')}"],
                    "setup": {"files": []},
                    "actions": [
                        {
                            "id": "hello",
                            "cli": {"target": "greet", "operation": "hello", "argv": []},
                        }
                    ],
                    "observe": [{"action": "hello", "exitEquals": 0, "stdoutEquals": "Hello"}],
                    "verdict": "all",
                }
            ],
        },
    )
    store.upsert_node(vision)
    template = tmp_path / "template"
    template.mkdir()
    manifest = store.compile_template(template)
    (tmp_path / "sample.json").write_text(json.dumps(manifest))
    yield store, manifest
    server.close()


def workers(*, stub=STUB, product=PRODUCT, tamper=False):
    def contracts(root):
        (root / "hello.py").write_text(stub)

    def build(root):
        (root / "hello.py").write_text(product)
        (root / "test_hello.py").write_text(
            "# Behavior: greeting; catches wrong text; scenarios omit function-level checks.\n"
            "import unittest, subprocess, sys\n"
            "class Greeting(unittest.TestCase):\n"
            " def test_greeting(self):\n"
            "  self.assertEqual(subprocess.check_output([sys.executable, 'hello.py']), b'Hello')\n"
        )
        if tamper:
            (root / "SCENARIOS.json").write_text("{}")

    return {"C": contracts, "B": build}


def execute(store, *, work_root=None, **kwargs):
    return dispatch.run_plan(
        store.workspace,
        work_root=work_root or store.workspace / "runs",
        max_parallel=1,
        fake_workers=workers(**kwargs),
    )
