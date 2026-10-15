"""Acceptance projections from the canonical spine."""

from codex_harness.core.spine import SCHEMA, Spine
from codex_harness.core.spine_diagrams import text_fallback, trace_view
from codex_harness.core.spine_render import render_markdown


def test_acceptance_renders_commands_expectations_and_trace_style():
    spine = Spine(
        {
            "schema": SCHEMA,
            "name": "Acceptance",
            "nodes": [
                {
                    "id": "V1",
                    "kind": "vision",
                    "title": "Vision",
                    "text": "",
                    "parents": [],
                    "fields": {},
                },
                {
                    "id": "AC1",
                    "kind": "acceptance",
                    "title": "First use",
                    "text": "Fresh install",
                    "parents": ["V1"],
                    "fields": {
                        "given": "No saved state",
                        "firstMoment": True,
                        "cleanState": True,
                        "steps": [
                            "python --version",
                            {"command": "python -V", "expect": {"exit": 0}},
                        ],
                        "expect": {"stdout": "Python"},
                    },
                },
            ],
        }
    )
    docs = render_markdown(spine)
    requirements = docs["REQUIREMENTS.md"]
    assert "## Acceptance scenarios" in requirements
    assert "AC1: First use" in requirements
    assert "Parents: V1" in requirements
    assert "given: No saved state" in requirements
    assert "firstMoment: True" in requirements and "cleanState: True" in requirements
    assert '```sh\npython --version\npython -V\n# Expect: {"exit": 0}\n```' in requirements
    assert 'Expect: {"stdout": "Python"}' in requirements
    assert docs == render_markdown(Spine(spine.to_dict()))
    diagram = trace_view(spine)
    assert 'AC1["AC1#58; First use"]' in diagram
    assert "classDef acceptance " in diagram and "class AC1 acceptance" in diagram
    assert "AC1: First use" in text_fallback(spine, "trace")
    data = spine.to_dict()
    data["nodes"][0]["fields"]["steps"] = []
    assert "class AC1 gap" in trace_view(Spine(data))
