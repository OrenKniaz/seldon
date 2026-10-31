"""Activation writer and offline reviewer observation proofs.

Real files catch overwrite, resolver and cache bugs absent from injected value tests."""

import json
import os
import subprocess
import sys
import tomllib
from argparse import Namespace
from pathlib import Path

import pytest

from codex_harness.core import project_init
from codex_harness.core.init_hosts import HOSTS, server_entry


@pytest.fixture
def offline_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))

    def no_launch(*args, **kwargs):
        raise AssertionError("Offline observation must not launch a process")

    monkeypatch.setattr(subprocess, "Popen", no_launch)
    for name in ("codex", "claude", "copilot"):
        executable = tmp_path / (name + (".cmd" if os.name == "nt" else ""))
        executable.write_text("exit 99\n")
        executable.chmod(0o755)
    cache = tmp_path / ".codex/models_cache.json"
    cache.parent.mkdir()
    cache.write_text(
        json.dumps(
            {
                "client_version": "fixture-1",
                "identity": "PRIVATE",
                "tokens": "SECRET",
                "models": [
                    {
                        "slug": "observed-model",
                        "identity": "PRIVATE",
                        "supported_reasoning_levels": [{"effort": "high", "description": "SECRET"}],
                    }
                ],
            }
        )
    )
    return cache


def options(path, **values):
    return Namespace(
        **{
            "path": str(path),
            "host": "all",
            "name": None,
            "yes": False,
            "dry_run": False,
            "check": False,
            "remove": False,
            "approval_mode": "prompt",
            **values,
        }
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(project_init, "installed_command", lambda: sys.executable)
    return tmp_path


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "original",
    [
        b"",
        b"user notes",
        b"user\nnotes\n",
        b"user\r\nnotes\r\n",
        b"user\r\nnotes",
        "שלום".encode(),
        "\ufeffשלום\r\nno final newline".encode(),
    ],
)
def test_codex_agents_routes_products_and_preserves_user_bytes(
    project, original, monkeypatch, capsys
):
    """Catch AGENTS overwrite/newline drift and lost routing through init/check/remove.

    Existing registration tests never consume project guidance. Only the unrelated
    installed-server handshake is stubbed; owned guidance uses the real init path.
    """
    target = project / "AGENTS.md"
    if original:
        target.write_bytes(original)
    assert project_init.run_init(options(project, host="codex")) == 0
    merged = target.read_bytes()
    assert b"harness_plan_start first" in merged
    assert b"before Seldon's plan is published and worker execution is approved" in merged
    assert b"higher-priority user instructions" in merged
    assert b"Unrelated discussion and maintenance need no extra process" in merged
    assert (
        b"Existing project files (briefs, docs, specs, any file not created by Seldon) are "
        b"read-only inputs during planning; never overwrite, rewrite, rename or summarize "
        b"them in place. Plan content lives in Seldon (harness tools). Only workers change "
        b"product files, after the execution go-ahead."
    ) in merged
    bom = b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b""
    assert merged.startswith(bom + b"<!-- seldon-init v1 begin -->\n")
    assert merged.endswith(original[len(bom) :])
    # Exercise an owned span between user prefix/suffix, including a UTF-8 BOM.
    prefix = "user prefix Ω\r\n".encode()
    target.write_bytes(bom + prefix + merged[len(bom) :])
    before = snapshot(project)
    assert project_init.run_init(options(project, host="codex")) == 0
    monkeypatch.setattr(project_init, "server_check", lambda *_: None)
    assert project_init.run_init(options(project, host=None, check=True)) == 0
    assert "AGENTS.md - owned guidance verified" in capsys.readouterr().out
    assert snapshot(project) == before
    assert project_init.run_init(options(project, host=None, remove=True)) == 0
    assert target.read_bytes() == bom + prefix + original[len(bom) :]
    before = snapshot(project)
    assert project_init.run_init(options(project, host=None, remove=True)) == 0
    assert snapshot(project) == before


def test_all_hosts_preserve_unrelated_settings_rerun_and_remove_only_owned(project):
    """Catch user-setting loss and owned-only AGENTS retention; old removal omitted guidance."""
    (project / ".codex").mkdir()
    original_toml = '# keep\nmodel = "user-choice"\n[mcp_servers.other]\ncommand = "other"\n'
    (project / ".codex/config.toml").write_text(original_toml, encoding="utf-8")
    original_toml_bytes = (project / ".codex/config.toml").read_bytes()
    original_json = {"mcpServers": {"other": {"command": "user-server"}}, "custom": [1, 2]}
    (project / ".mcp.json").write_text(json.dumps(original_json), encoding="utf-8")
    (project / ".gitignore").write_text("keep-me\n", encoding="utf-8")
    original_ignore_bytes = (project / ".gitignore").read_bytes()
    assert project_init.run_init(options(project, name="Named project")) == 0
    before = snapshot(project)
    assert project_init.run_init(options(project)) == 0
    assert snapshot(project) == before
    codex = tomllib.loads((project / ".codex/config.toml").read_text())
    assert codex["model"] == "user-choice"
    assert codex["mcp_servers"]["seldon"]["default_tools_approval_mode"] == "prompt"
    for host, writer in HOSTS.items():
        entry = project_init.read_entry(host, (project / writer.PATH).read_text())
        assert entry["args"][-1] == str(project.resolve())
        assert Path(entry["command"]).is_absolute()
        assert entry["env"]["SELDON_INIT"] == project_init.MARKER
    (project / ".codex-harness/plan.sqlite3").write_bytes(b"existing plan stays")
    assert project_init.run_init(options(project, remove=True, host=None)) == 0
    assert (project / ".codex-harness/plan.sqlite3").read_bytes() == b"existing plan stays"
    assert not (project / "SELDON.md").exists()
    assert (project / "AGENTS.md").read_bytes() == b""
    assert (project / ".codex/config.toml").read_bytes().startswith(original_toml_bytes)
    remaining = tomllib.loads((project / ".codex/config.toml").read_text())
    assert remaining["mcp_servers"]["seldon"] == {
        "default_tools_approval_mode": "prompt",
        "tool_timeout_sec": 900,
    }
    assert json.loads((project / ".mcp.json").read_text()) == original_json
    assert (project / ".gitignore").read_bytes() == original_ignore_bytes


@pytest.mark.parametrize("host", list(HOSTS))
def test_wrong_workspace_conflict_is_refused_before_any_write(project, host):
    """Catch silently rebinding an existing project's server with other files written first."""
    writer = HOSTS[host]
    target = project / writer.PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(writer.render("", server_entry(sys.executable, project / "elsewhere")))
    before = snapshot(project)
    with pytest.raises(ValueError, match="differs"):
        project_init.run_init(options(project))
    assert snapshot(project) == before
    assert project_init.run_init(options(project, yes=True)) == 0
    assert project_init.read_entry(host, target.read_text())["args"][-1] == str(project)


def test_malformed_config_aborts_whole_preparation(project):
    """Catch partial setup when a later host's config cannot be safely merged."""
    (project / ".mcp.json").write_text('{"mcpServers":{},"mcpServers":{}}')
    before = snapshot(project)
    with pytest.raises(ValueError, match="Duplicate"):
        project_init.run_init(options(project))
    assert snapshot(project) == before
