"""Activation regressions, absent from prior planner/provider tests.

These tests protect the new filesystem writer boundary. They catch overwritten
host settings, rerun drift, cross-project replacement and deletion of unowned
files; existing planner tests do not call project activation at all.
Installed-command/MCP integration is additionally proven from the built wheel.
"""

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
            **values,
        }
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(project_init, "installed_command", lambda: sys.executable)
    return tmp_path


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_all_hosts_preserve_unrelated_settings_rerun_and_remove_only_owned(project):
    """Catch replacement of another MCP server or store data across init/remove."""
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
    assert "default_tools_approval_mode" not in codex["mcp_servers"]["seldon"]
    for host, writer in HOSTS.items():
        entry = project_init.read_entry(host, (project / writer.PATH).read_text())
        assert entry["args"][-1] == str(project.resolve())
        assert Path(entry["command"]).is_absolute()
        assert entry["env"]["SELDON_INIT"] == project_init.MARKER
    (project / ".codex-harness/plan.sqlite3").write_bytes(b"existing plan stays")
    assert project_init.run_init(options(project, remove=True, host=None)) == 0
    assert (project / ".codex-harness/plan.sqlite3").read_bytes() == b"existing plan stays"
    assert not (project / "SELDON.md").exists()
    assert (project / ".codex/config.toml").read_bytes() == original_toml_bytes
    assert json.loads((project / ".mcp.json").read_text()) == original_json
    assert (project / ".gitignore").read_bytes() == original_ignore_bytes


def test_dry_run_prints_complete_files_but_creates_nothing(project, capsys):
    """Catch mkdir/store creation hidden inside rendering or discovery."""
    assert project_init.run_init(options(project, dry_run=True)) == 0
    assert snapshot(project) == {}
    assert list(project.iterdir()) == []
    output = capsys.readouterr().out
    assert "mcp_servers.seldon" in output and "mcpServers" in output
    assert str(project.resolve()) in output and "Would update" in output


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


def test_unowned_docs_and_servers_survive_remove(project):
    """Catch overly broad marker deletion treating all Seldon-named files as owned."""
    (project / "SELDON.md").write_text("my notes")
    (project / ".mcp.json").write_text('{"mcpServers":{"seldon":{"command":"mine"}}}')
    before = snapshot(project)
    assert project_init.run_init(options(project, remove=True, host=None)) == 0
    assert snapshot(project) == before
    with pytest.raises(ValueError, match="unowned file"):
        project_init.run_init(options(project, host="codex"))
    assert snapshot(project) == before


def test_check_refuses_tampering_without_launching_changed_command(project, monkeypatch):
    """Catch check executing an attacker-edited registration rather than validating it."""
    project_init.run_init(options(project))
    target = project / ".mcp.json"
    data = json.loads(target.read_text())
    data["mcpServers"]["seldon"]["command"] = "must-not-execute"
    target.write_text(json.dumps(data))
    monkeypatch.setattr(project_init, "server_check", lambda *_: pytest.fail("unexpected launch"))
    before = snapshot(project)
    with pytest.raises(ValueError, match="differs"):
        project_init.run_init(options(project, check=True))
    assert snapshot(project) == before


def test_malformed_config_aborts_whole_preparation(project):
    """Catch partial setup when a later host's config cannot be safely merged."""
    (project / ".mcp.json").write_text('{"mcpServers":{},"mcpServers":{}}')
    before = snapshot(project)
    with pytest.raises(ValueError, match="Duplicate"):
        project_init.run_init(options(project))
    assert snapshot(project) == before


def test_redirected_paths_and_store_roots_are_refused(project):
    """Catch writes outside the project via a user-supplied host-directory link."""
    store = project / ".codex-harness"
    store.mkdir()
    with pytest.raises(ValueError, match="inside a Seldon store"):
        project_init.run_init(options(store))
    outside = project.parent / (project.name + "-outside")
    outside.mkdir()
    try:
        (project / ".codex").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Windows symlink privilege unavailable: {error}")
    with pytest.raises(ValueError, match="redirected"):
        project_init.run_init(options(project))
    assert list(outside.iterdir()) == []


def test_check_requires_registration_and_marker(project):
    """Catch empty setups incorrectly reported as ready without the new installation evidence."""
    with pytest.raises(ValueError, match="missing"):
        project_init.run_init(options(project, check=True))


@pytest.mark.parametrize(
    "mode,field",
    [
        ("auto", "command"),
        ("prompt", "args"),
        ("writes", "env"),
        ("approve", "startup_timeout_sec"),
    ],
)
def test_explicit_codex_mode_survives_reinit_and_authorized_identity_replacement(
    project, mode, field
):
    """Catch mode loss without masking identity drift; existing reruns have no user-owned mode."""
    project_init.run_init(options(project, host="codex"))
    config = project / ".codex/config.toml"
    line = f'default_tools_approval_mode = "{mode}"\n'
    if mode in {"prompt", "approve"}:
        config.write_text(config.read_text() + line)
    else:
        config.write_text(
            config.read_text().replace(
                "startup_timeout_sec = 20\n", "startup_timeout_sec = 20\n" + line
            )
        )
    original = snapshot(project)
    expected = project_init.read_entry("codex", config.read_text())
    project_init.run_init(options(project, host="codex"))
    assert snapshot(project) == original
    replacement = {
        "command": '"must-not-execute"',
        "args": '["--workspace", "wrong"]',
        "env": '{ SELDON_INIT = "wrong" }',
        "startup_timeout_sec": "999",
    }[field]
    config.write_text(
        "".join(
            f"{field} = {replacement}\n" if line.startswith(field + " = ") else line
            for line in config.read_text().splitlines(True)
        )
    )
    before = snapshot(project)
    with pytest.raises(ValueError, match="differs"):
        project_init.run_init(options(project, host="codex"))
    assert snapshot(project) == before
    project_init.run_init(options(project, host="codex", yes=True))
    assert project_init.read_entry("codex", config.read_text()) == expected
    after = snapshot(project)
    project_init.run_init(options(project, host="codex"))
    assert snapshot(project) == after


@pytest.mark.parametrize("literal", ['"unsupported"', '["approve"]'])
@pytest.mark.parametrize("yes", [False, True])
def test_invalid_explicit_codex_mode_refuses_without_writes_even_with_replacement(
    project, literal, yes
):
    """Catch silent policy deletion; malformed TOML tests miss valid TOML with bad modes."""
    project_init.run_init(options(project, host="codex"))
    config = project / ".codex/config.toml"
    config.write_text(
        config.read_text().replace(
            "startup_timeout_sec = 20\n",
            f"startup_timeout_sec = 20\ndefault_tools_approval_mode = {literal}\n",
        )
    )
    before = snapshot(project)
    with pytest.raises(ValueError, match="approval mode"):
        project_init.run_init(options(project, host="codex", yes=yes))
    assert snapshot(project) == before


def test_owned_remove_retains_safety_and_idempotence_with_invalid_mode(project):
    """Catch validation blocking safe removal; existing removal has no invalid mode."""
    config = project / ".codex/config.toml"
    config.parent.mkdir()
    original = '# retain\nmodel = "user-choice"\n'
    config.write_text(original)
    project_init.run_init(options(project, host="codex"))
    config.write_text(
        config.read_text().replace(
            "startup_timeout_sec = 20\n",
            'startup_timeout_sec = 20\ndefault_tools_approval_mode = ["approve"]\n',
        )
    )
    project_init.run_init(options(project, remove=True, host=None))
    assert config.read_text() == original
    before = snapshot(project)
    project_init.run_init(options(project, remove=True, host=None))
    assert snapshot(project) == before


def test_installed_wheel_registration_starts_bound_canonical_planner(tmp_path):
    """Catch a source-only/PYTHONPATH registration that breaks from a real host cwd.

    Source-writer tests cannot prove console installation, isolated interpreter
    import, MCP startup or consumption of the generated workspace argument.
    """
    wheel = os.environ.get("SELDON_PROVENANCE_WHEEL")
    python = os.environ.get("SELDON_PACKAGING_PYTHON")
    if not wheel or not python:
        pytest.skip("requires actual candidate wheel and existing pip-capable Python")
    environment = tmp_path / "installed"
    subprocess.run(
        [python, "-m", "venv", "--without-pip", str(environment)], check=True, timeout=30
    )
    binaries = environment / ("Scripts" if os.name == "nt" else "bin")
    interpreter = binaries / ("python.exe" if os.name == "nt" else "python")
    console = binaries / ("seldon.exe" if os.name == "nt" else "seldon")
    subprocess.run(
        [
            python,
            "-m",
            "pip",
            "--python",
            str(interpreter),
            "install",
            "--no-index",
            "--no-deps",
            wheel,
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    product = tmp_path / "product a"
    product.mkdir()
    clean_env = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }

    def cli(*args):
        result = subprocess.run(
            [str(console), "init", str(product), *args],
            cwd=tmp_path,
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    cli("--host", "all", "--dry-run")
    assert list(product.iterdir()) == []
    cli("--host", "all")
    before = snapshot(product)
    cli("--check")
    cli("--host", "all")
    assert snapshot(product) == before
    assert not (product / ".codex-harness/plan.sqlite3").exists()
    entry = json.loads((product / ".mcp.json").read_text())["mcpServers"]["seldon"]
    assert entry["command"] == str(interpreter)

    def request(workspace):
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "fixture", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "harness_plan_start",
                    "arguments": {
                        "idea": "A local fixture planner",
                        "name": "Product A",
                        "expected_workspace": str(workspace),
                    },
                },
            },
        ]
        result = subprocess.run(
            [entry["command"], *entry["args"]],
            cwd=tmp_path,
            input="".join(json.dumps(message) + "\n" for message in messages),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return [json.loads(line) for line in result.stdout.splitlines()][-1]

    other = tmp_path / "product b"
    other.mkdir()
    refused = request(other)
    assert "workspace-mismatch" in json.dumps(refused)
    assert not (product / ".codex-harness/plan.sqlite3").exists()
    started = request(product)
    assert not started["result"].get("isError"), started
    assert "align" in json.dumps(started)
    assert (product / ".codex-harness/plan.sqlite3").exists()
    assert not (other / ".codex-harness").exists()
    cli("--remove")
    assert (product / ".codex-harness/plan.sqlite3").exists()
