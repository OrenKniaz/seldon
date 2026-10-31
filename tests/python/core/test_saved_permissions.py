"""Saved project configuration is an observation, never active permission truth."""

import json

import pytest
from tests.python.core.test_project_memory import _link_directory

from codex_harness.core import status_api


@pytest.mark.parametrize("mode", ["auto", "prompt", "writes", "approve"])
def test_project_config_status_excludes_secrets_and_preserves_source(tmp_path, mode):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        'approval_policy = "never"\nsandbox_mode = "workspace-write"\n'
        f'[mcp_servers.seldon]\ndefault_tools_approval_mode = "{mode}"\n'
        'command = "PRIVATE_COMMAND_SENTINEL"\n'
        '[mcp_servers.seldon.env]\nTOKEN = "PRIVATE_TOKEN_SENTINEL"\n',
        encoding="utf-8",
    )
    before = config.read_bytes(), config.stat().st_mtime_ns
    payload = status_api.snapshot(workspace=tmp_path)
    saved = payload["savedPermissions"]
    assert saved["availability"] == "available"
    assert saved["saved"] == {
        "approval_policy": "never",
        "sandbox_mode": "workspace-write",
        "seldon_tool_approval": mode,
    }
    assert saved["activePolicy"] == saved["chatConnection"] == "unknown"
    assert "PRIVATE_" not in json.dumps(payload)
    assert (config.read_bytes(), config.stat().st_mtime_ns) == before
    assert not (tmp_path / ".codex-harness").exists()


def test_escaping_config_is_not_read(tmp_path):
    """Reject escaped config through a real reparse point, without file-link privilege.

    Catch exposing settings outside the project; ordinary config snapshots never
    cross that boundary. A Windows junction exercises it without skipping the check.
    """
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.toml").write_text('approval_policy = "never"', encoding="utf-8")
    _link_directory(project / ".codex", outside)
    saved = status_api.snapshot(workspace=project)["savedPermissions"]
    assert saved["availability"] == "unknown"
    assert set(saved["saved"].values()) == {"unknown"}
    assert not (project / ".codex-harness").exists()
