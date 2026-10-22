"""Saved project configuration is an observation, never active permission truth."""

import json
import subprocess
from pathlib import Path

import pytest

from codex_harness.core import status_api


def test_project_config_reaches_shipped_read_only_card_without_secrets(tmp_path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        'approval_policy = "never"\nsandbox_mode = "workspace-write"\n'
        '[mcp_servers.seldon]\ndefault_tools_approval_mode = "approve"\n'
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
        "seldon_tool_approval": "approve",
    }
    assert saved["activePolicy"] == saved["chatConnection"] == "unknown"
    assert "PRIVATE_" not in json.dumps(payload)
    assert (config.read_bytes(), config.stat().st_mtime_ns) == before
    assert not (tmp_path / ".codex-harness").exists()
    # Execute the shipped DOM construction, not an imitation of its logic.
    script = r"""
const fs=require('fs'),vm=require('vm');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.textContent='';}
  append(...items){this.children.push(...items);}
  setAttribute(){}
  addEventListener(){}
}
const source=fs.readFileSync(process.argv[1],'utf8');
const prefix=source.slice(0,source.indexOf('function acceptance()'));
const card=vm.runInNewContext(prefix+'\nreturn permissionCard;})()',{
  URLSearchParams,location:{search:''},Node:Element,
  document:{querySelector:()=>null,createElement:tag=>new Element(tag),
    createTextNode:text=>Object.assign(new Element('#text'),{textContent:text})}
});
console.log(JSON.stringify(card(JSON.parse(fs.readFileSync(0,'utf8')))));
"""
    rendered = subprocess.run(
        ["node", "-e", script, str(Path(status_api.__file__).with_name("cockpit.js"))],
        input=json.dumps(saved),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "Saved project permissions" in rendered
    assert "never" in rendered and "workspace-write" in rendered and "approve" in rendered
    assert "unknown" in rendered and "do not prove" in rendered
    assert not any(f'"tag":"{tag}"' in rendered for tag in ("button", "input", "form"))


@pytest.mark.parametrize("content", [None, "not TOML !", 'approval_policy = "PRIVATE_VALUE"'])
def test_missing_malformed_or_invalid_values_do_not_invent_permissions(tmp_path, content):
    if content is not None:
        config = tmp_path / ".codex" / "config.toml"
        config.parent.mkdir()
        config.write_text(content, encoding="utf-8")
    saved = status_api.snapshot(workspace=tmp_path)["savedPermissions"]
    assert set(saved["saved"].values()) == {"unknown"}
    assert saved["activePolicy"] == saved["chatConnection"] == "unknown"
    assert "PRIVATE_" not in json.dumps(saved)
    assert not (tmp_path / ".codex-harness").exists()
    if content is None:
        assert not (tmp_path / ".codex").exists()


def test_escaping_config_is_not_read(tmp_path):
    project = tmp_path / "project"
    (project / ".codex").mkdir(parents=True)
    outside = tmp_path / "outside.toml"
    outside.write_text('approval_policy = "never"', encoding="utf-8")
    try:
        (project / ".codex" / "config.toml").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"File symlink setup unavailable: {error.winerror}")
    saved = status_api.snapshot(workspace=project)["savedPermissions"]
    assert saved["availability"] == "unknown"
    assert set(saved["saved"].values()) == {"unknown"}
    assert not (project / ".codex-harness").exists()


@pytest.mark.parametrize("kind", ["directory", "oversized", "invalid-utf8"])
def test_unreadable_or_unbounded_config_has_no_saved_claim(tmp_path, kind):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    if kind == "directory":
        config.mkdir()
    else:
        config.write_bytes(b" " * 65537 if kind == "oversized" else b"\xff")
    observed = status_api.snapshot(workspace=tmp_path)["savedPermissions"]
    assert observed["availability"] == "unknown"
    assert set(observed["saved"].values()) == {"unknown"}
    assert not (tmp_path / ".codex-harness").exists()
