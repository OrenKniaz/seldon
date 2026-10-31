"""Explicit public policy declaration through the real writer; no provider or approval."""

import json
import subprocess

import pytest

from codex_harness.core.plan_store import open_plan_store
from codex_harness.core.spine import SCHEMA, Spine, save_spine

TOOL = "harness_adopt_review_policy"
SLOTS = [
    dict(name="Required", provider="codex", required=True),
    dict(name="Optional", provider="claude", required=False),
]


def wire(server, tool_name, **arguments):
    return server.handle_line(
        json.dumps(
            dict(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params=dict(name=tool_name, arguments=arguments),
            )
        )
    )["result"]


@pytest.fixture
def project(tmp_path, monkeypatch):
    template = tmp_path / "template"
    (template / "tests").mkdir(parents=True)
    (template / "tests/test_source.py").write_text(
        "# Protected policy-fixture input; never executed.\n"
    )
    nodes = [
        dict(id=key, kind=kind, title=key, text=key, parents=parents, fields=fields)
        for key, kind, parents, fields in [
            (
                "V1",
                "vision",
                [],
                {
                    "validation": ["-m", "unittest", "discover", "-s", "tests", "-p"],
                    "requirementFiles": ["tests"],
                },
            ),
            ("F", "feature", ["V1"], {}),
            ("R", "requirement", ["F"], {}),
            (
                "B",
                "bead",
                ["R"],
                {
                    "ownedFiles": ["source.py"],
                    "dependsOn": [],
                    "lessons": [],
                    "proofModule": "test_source.py",
                },
            ),
            ("T", "test", ["R"], {"module": "test_source.py", "level": "unit"}),
        ]
    ]
    source = tmp_path / "spine.json"
    save_spine(Spine(dict(schema=SCHEMA, name="Policy source fixture", nodes=nodes)), source)
    with open_plan_store(tmp_path) as store:
        store.import_spine(source)
        revision = store.compile_template(template)["planRevision"]

    def no_provider(*args, **kwargs):
        raise AssertionError("Policy adoption must never launch a process")

    monkeypatch.setattr(subprocess, "Popen", no_provider)
    return tmp_path, revision
