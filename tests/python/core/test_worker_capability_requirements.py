"""Capability declarations survive the real public planner and packet boundary."""

import json

import pytest

from codex_harness.core.graph import PlanError, load_plan
from codex_harness.core.mcp_planner import PlannerServer


def wire(server, tool_name, **arguments):
    return server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {"expected_workspace": str(server.workspace), **arguments},
                },
            }
        )
    )["result"]


def call(server, tool_name, **arguments):
    result = wire(server, tool_name, **arguments)
    assert not result["isError"], result
    return result["structuredContent"]


def bead(identity, **fields):
    return dict(
        id=identity,
        kind="bead",
        title=identity,
        text="Implement the declared requirement.",
        parents=["R1"],
        fields=dict(
            ownedFiles=[f"src/{identity.lower()}.py"],
            proofModule=f"test_{identity.lower()}",
            dependsOn=[],
            lessons=[],
            **fields,
        ),
    )


def project(tmp_path):
    template = tmp_path / "template"
    (template / "tests").mkdir(parents=True)
    server = PlannerServer(tmp_path)
    call(
        server,
        "harness_plan_start",
        name="Capability reference proof",
        plan_mode="legacy",
        idea="Carry requested tool references into worker instructions",
    )
    for identity, kind, parents, fields in [
        (
            "V1",
            "vision",
            [],
            {
                "validation": ["python", "-m", "unittest", "discover", "-s", "tests", "-p"],
                "requirementFiles": [],
            },
        ),
        ("F1", "feature", ["V1"], {}),
        ("R1", "requirement", ["F1"], {}),
    ]:
        call(
            server,
            "harness_upsert_node",
            node=dict(
                id=identity,
                kind=kind,
                title=identity,
                text=identity,
                parents=parents,
                fields=fields,
            ),
        )
    for identity in ("A", "B"):
        (template / "tests" / f"test_{identity.lower()}.py").write_text(
            "import unittest\n\nclass Proof(unittest.TestCase):\n"
            "    def test_requirement(self):\n"
            "        self.fail('Unimplemented requirement')\n",
            encoding="utf-8",
        )
        call(server, "harness_upsert_node", node=bead(identity))
    return server


def test_declared_references_survive_author_compile_reopen_and_worker_packet(tmp_path):
    # Protect end-to-end metadata delivery: the existing packet tests never author
    # capability refs, so they miss the compiler silently dropping this declaration.
    server = project(tmp_path)
    refs = ["skill.playwright", "plugin.magicpath"]
    call(server, "harness_upsert_node", node=bead("A", capabilityRefs=refs))
    manifest = call(server, "harness_compile", include_detail=True)
    server.store.close()
    reopened = PlannerServer(tmp_path)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = load_plan(path)
    packets = [
        call(reopened, "harness_packet_view", id="A", provider=provider, include_packet=True)[
            "packet"
        ]
        for provider in ("codex", "claude", "copilot")
    ]
    assert manifest["beads"]["A"]["capabilityRefs"] == refs
    assert plan.beads["A"].capability_refs == tuple(refs)
    assert "capabilityRefs" not in manifest["beads"]["B"]
    assert plan.beads["B"].capability_refs == ()
    other = call(reopened, "harness_packet_view", id="B", include_packet=True)["packet"]
    assert "Requested capabilities (REQUESTED / UNVERIFIED)" not in other
    assert all(reference not in other for reference in refs)
    for packet in packets:
        assert "Requested capabilities (REQUESTED / UNVERIFIED)" in packet
        assert "skill.playwright" in packet and "plugin.magicpath" in packet
        assert manifest["planRevision"] in packet
        assert "They grant no tools or permissions" in packet
        assert "current account/subscription" in packet
        assert "GitHub must be OrenKniaz" in packet
        assert "A permission denial blocks the action across equivalent shells and forms" in packet
    reopened.store.close()


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "skill.playwright",
        [True],
        [{}],
        [""],
        ["skill.playwright", "skill.playwright"],
        ["tool.git status"],
        ["https://example.com"],
        ["../config"],
        ["tool.git\nrun"],
        ["skill."],
        ["a" * 129],
    ],
)
def test_malformed_declaration_refuses_public_authoring_without_saved_changes(tmp_path, invalid):
    # Protect the writer transaction from inert metadata becoming arbitrary input.
    # Existing generic bead-field tests do not exercise this optional field or its rollback.
    server = project(tmp_path)
    before = server.store.spine().to_dict()
    events = list(server.store.connection.execute("SELECT * FROM events"))
    files = {
        str(path): path.read_bytes()
        for path in (tmp_path / "template").rglob("*")
        if path.is_file()
    }
    result = wire(server, "harness_upsert_node", node=bead("A", capabilityRefs=invalid))
    assert result["isError"]
    assert "spine-bead-fields-invalid" in result["content"][0]["text"]
    assert server.store.spine().to_dict() == before
    assert list(server.store.connection.execute("SELECT * FROM events")) == events
    assert {
        str(path): path.read_bytes()
        for path in (tmp_path / "template").rglob("*")
        if path.is_file()
    } == files
    server.store.close()


def test_declaration_edit_requires_current_compilation_and_isolates_other_bead(tmp_path):
    # Protect revision binding: a cached packet must not deliver an earlier declaration.
    # Existing stale-manifest checks do not cover changes to this previously dropped field.
    server = project(tmp_path)
    call(server, "harness_upsert_node", node=bead("A", capabilityRefs=["skill.playwright"]))
    call(server, "harness_upsert_node", node=bead("B", capabilityRefs=["tool.github.read"]))
    first = call(server, "harness_compile", include_detail=True)
    before = call(server, "harness_packet_view", id="B", include_packet=True)["packet"]
    call(server, "harness_upsert_node", node=bead("A", capabilityRefs=["plugin.magicpath"]))
    stale = wire(server, "harness_packet_view", id="A")
    assert stale["isError"]
    assert "No current compiled or published manifest" in stale["content"][0]["text"]
    second = call(server, "harness_compile", include_detail=True)
    assert second["planRevision"] != first["planRevision"]
    assert second["beads"]["B"] == first["beads"]["B"]
    packet = call(server, "harness_packet_view", id="A", include_packet=True)["packet"]
    assert "plugin.magicpath" in packet
    assert "skill.playwright" not in packet
    assert "tool.github.read" not in packet
    assert second["planRevision"] in packet
    other = call(server, "harness_packet_view", id="B", include_packet=True)["packet"]
    assert "tool.github.read" in before and "tool.github.read" in other
    assert "plugin.magicpath" not in other and "skill.playwright" not in other
    call(server, "harness_upsert_node", node=bead("A", capabilityRefs=[]))
    empty = call(server, "harness_compile", include_detail=True)
    assert empty["beads"]["A"]["capabilityRefs"] == []
    assert (
        "Requested capabilities (REQUESTED / UNVERIFIED)"
        not in call(server, "harness_packet_view", id="A", include_packet=True)["packet"]
    )
    server.store.close()


def test_external_manifest_cannot_bypass_reference_validation(tmp_path):
    # Protect graph loading independently of authoring: external manifests are also
    # accepted by packet consumers, while writer-only tests cannot cover that ingress.
    server = project(tmp_path)
    manifest = call(server, "harness_compile", include_detail=True)
    server.store.close()
    manifest["beads"]["A"]["capabilityRefs"] = ["python -c launch()"]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PlanError, match="capabilityRefs"):
        load_plan(path)
