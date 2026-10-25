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

from codex_harness.core import project_init, seldon_cli
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


@pytest.mark.parametrize("case", ["fresh", "legacy", "owned-custom", "unowned-custom"])
def test_codex_request_budget_preserves_user_timeout_and_tool_policy(project, case):
    """Protect reading budget through the real writer, replacement and removal.

    Existing policy tests omit tool timeouts, missing short default requests and
    a writer erasing custom budgets while preserving the approval-mode label.
    """
    project_init.run_init(options(project, host="codex"))
    config = project / ".codex/config.toml"
    if case == "fresh":
        assert project_init.read_entry("codex", config.read_text())["tool_timeout_sec"] == 900
        return
    text = "".join(
        line
        for line in config.read_text().splitlines(True)
        if not line.startswith("tool_timeout_sec")
    )
    policy = 'enabled_tools = ["harness_plan_status"]\ndisabled_tools = ["harness_publish"]\n'
    budget = "tool_timeout_sec = 1800\n" if case == "owned-custom" else "tool_timeout_sec = 300.5\n"
    if case == "owned-custom":
        policy += budget
    config.write_text(
        text.replace("# seldon-init v1 end", policy + "# seldon-init v1 end")
        + (budget if case == "unowned-custom" else "")
    )
    before = tomllib.loads(config.read_text())["mcp_servers"]["seldon"]
    project_init.run_init(options(project, host="codex", approval_mode=None))
    expected = {
        **before,
        "tool_timeout_sec": 900 if case == "legacy" else before["tool_timeout_sec"],
    }
    assert project_init.read_entry("codex", config.read_text()) == expected
    config.write_text(
        config.read_text().replace("startup_timeout_sec = 20", "startup_timeout_sec = 99")
    )
    project_init.run_init(options(project, host="codex", yes=True, approval_mode=None))
    assert project_init.read_entry("codex", config.read_text()) == expected
    project_init.run_init(options(project, host=None, remove=True))
    remaining = tomllib.loads(config.read_text())["mcp_servers"]["seldon"]
    assert remaining["tool_timeout_sec"] == expected["tool_timeout_sec"]
    assert remaining["default_tools_approval_mode"] == "prompt"
    assert remaining["enabled_tools"] == ["harness_plan_status"]
    assert remaining["disabled_tools"] == ["harness_publish"]
    assert "command" not in remaining


def test_invalid_codex_request_budget_refuses_without_writes(project):
    """A TOML boolean is not numeric seconds; old invalid-mode tests miss it."""
    project_init.run_init(options(project, host="codex"))
    config = project / ".codex/config.toml"
    config.write_text(
        config.read_text().replace("tool_timeout_sec = 900", "tool_timeout_sec = true")
    )
    before = snapshot(project)
    with pytest.raises(ValueError, match="tool timeout"):
        project_init.run_init(options(project, host="codex", yes=True))
    assert snapshot(project) == before


def test_public_init_requires_disclosed_choice_then_checks_saved_permissions(
    project, monkeypatch, capsys
):
    """Protect consent and real writer/check integration; old tests bypass CLI and save no policy.

    Catch --yes silently granting all tools, malformed host policy, or a check claiming
    effective permissions from saved settings. The handshake uses the qualified DFF install.
    """
    installed = Path(
        os.environ.get(
            "SELDON_CHECK_PYTHON",
            str(Path(__file__).resolve().parents[4] / "runtime-dff93bb/Scripts/python.exe"),
        )
    )
    if not installed.exists():
        pytest.skip("requires the existing qualified DFF interpreter for real MCP handshake")
    monkeypatch.setattr(project_init, "installed_command", lambda: str(installed))
    monkeypatch.setattr(seldon_cli, "version", lambda _name: "source-test")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert seldon_cli.main(["init", str(project), "--host", "all", "--yes"]) == 1
    assert snapshot(project) == {}
    assert "all current and future Seldon MCP tools" in capsys.readouterr().out
    assert (
        seldon_cli.main(["init", str(project), "--host", "all", "--approval-mode", "approve"]) == 0
    )
    saved = tomllib.loads((project / ".codex/config.toml").read_text())
    assert saved["mcp_servers"]["seldon"]["default_tools_approval_mode"] == "approve"
    settings = json.loads((project / ".claude/settings.local.json").read_text())
    assert settings["enabledMcpjsonServers"] == ["seldon"]
    assert settings["permissions"]["allow"] == ["mcp__seldon__*"]
    before = snapshot(project)
    assert seldon_cli.main(["init", str(project), "--check"]) == 0
    assert seldon_cli.main(["init", str(project), "--host", "all"]) == 0
    assert snapshot(project) == before
    output = capsys.readouterr().out
    assert "host effective policy UNKNOWN" in output
    assert "Chat: Manage Tool Approval" in output
    assert not (project / ".vscode/settings.json").exists()


def test_permission_optout_preserves_stricter_rules_and_remove_keeps_user_policy(project):
    """Catch approval overriding narrower rules or removal deleting policy; old tests have none."""
    claude = project / ".claude/settings.local.json"
    claude.parent.mkdir()
    original = {
        "custom": 1,
        "permissions": {
            "deny": ["mcp__seldon__harness_publish"],
            "ask": ["mcp__seldon__harness_worker_step"],
        },
    }
    claude.write_text(json.dumps(original))
    project_init.run_init(options(project, approval_mode="approve"))
    codex = project / ".codex/config.toml"
    codex.write_text(
        codex.read_text().replace(
            "# seldon-init v1 end",
            '[mcp_servers.seldon.tools.harness_publish]\napproval_mode = "prompt"\n'
            "# seldon-init v1 end",
        )
    )
    project_init.run_init(options(project, approval_mode="prompt"))
    config = tomllib.loads(codex.read_text())["mcp_servers"]["seldon"]
    assert config["default_tools_approval_mode"] == "prompt"
    assert config["tools"]["harness_publish"]["approval_mode"] == "prompt"
    settings = json.loads(claude.read_text())
    assert settings["permissions"]["deny"] == original["permissions"]["deny"]
    assert settings["permissions"]["ask"] == [*original["permissions"]["ask"], "mcp__seldon__*"]
    assert "mcp__seldon__*" not in settings["permissions"]["allow"]
    before = snapshot(project)
    project_init.run_init(options(project, approval_mode=None))
    assert snapshot(project) == before
    policy = claude.read_bytes()
    project_init.run_init(options(project, remove=True, host=None))
    assert claude.read_bytes() == policy
    remaining = tomllib.loads(codex.read_text())["mcp_servers"]["seldon"]
    assert remaining == {"default_tools_approval_mode": "prompt", "tools": config["tools"]}


def test_default_approve_requires_an_affirmative_interactive_answer(project, monkeypatch):
    """Catch default approval on decline; explicit CLI choices miss interactive consent."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["no", "yes"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert project_init.run_init(options(project, host="codex", approval_mode=None, yes=True)) == 1
    assert snapshot(project) == {}
    assert project_init.run_init(options(project, host="codex", approval_mode=None)) == 0
    config = tomllib.loads((project / ".codex/config.toml").read_text())
    assert config["mcp_servers"]["seldon"]["default_tools_approval_mode"] == "approve"


@pytest.mark.parametrize("action", ["reinit", "check", "remove"])
def test_codex_tool_filters_survive_public_cli(project, monkeypatch, action):
    """Catch --yes broadening a saved approve policy; existing tests cover only nested tools.

    Check must accept the filtered canonical registration, replacement must preserve
    its filters, and removal must retain them as user policy rather than deleting them.
    """
    installed = Path(__file__).resolve().parents[4] / "runtime-dff93bb/Scripts/python.exe"
    if not installed.exists():
        pytest.skip("requires existing qualified DFF interpreter for check handshake")
    monkeypatch.setattr(project_init, "installed_command", lambda: str(installed))
    monkeypatch.setattr(seldon_cli, "version", lambda _name: "source-test")
    assert (
        seldon_cli.main(["init", str(project), "--host", "codex", "--approval-mode", "approve"])
        == 0
    )
    config = project / ".codex/config.toml"
    filters = 'enabled_tools = ["harness_plan_status"]\ndisabled_tools = ["harness_publish"]\n'
    config.write_text(
        config.read_text().replace("# seldon-init v1 end", filters + "# seldon-init v1 end")
    )
    if action == "reinit":
        config.write_text(
            config.read_text().replace("startup_timeout_sec = 20", "startup_timeout_sec = 99")
        )
        args = ["--host", "codex", "--yes"]
    else:
        args = ["--" + action]
    assert seldon_cli.main(["init", str(project), *args]) == 0
    saved = tomllib.loads(config.read_text())["mcp_servers"]["seldon"]
    assert saved["enabled_tools"] == ["harness_plan_status"]
    assert saved["disabled_tools"] == ["harness_publish"]
    assert saved["default_tools_approval_mode"] == "approve"
    assert ("command" in saved) == (action != "remove")


def test_malformed_claude_permission_policy_refuses_all_writes(project):
    """Catch partial setup before bad policy; old malformed tests cover registration only."""
    target = project / ".claude/settings.local.json"
    target.parent.mkdir()
    target.write_text('{"permissions":{"allow":"all"}}')
    before = snapshot(project)
    with pytest.raises(ValueError, match="allow"):
        project_init.run_init(options(project, approval_mode="approve"))
    assert snapshot(project) == before


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
    assert (project / ".codex/config.toml").read_bytes().startswith(original_toml_bytes)
    remaining = tomllib.loads((project / ".codex/config.toml").read_text())
    assert remaining["mcp_servers"]["seldon"] == {"default_tools_approval_mode": "prompt"}
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
    config.write_text(config.read_text().replace('default_tools_approval_mode = "prompt"\n', ""))
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
    project_init.run_init(options(project, host="codex", approval_mode=None))
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
        project_init.run_init(options(project, host="codex", approval_mode=None))
    assert snapshot(project) == before
    project_init.run_init(options(project, host="codex", yes=True, approval_mode=None))
    assert project_init.read_entry("codex", config.read_text()) == expected
    after = snapshot(project)
    project_init.run_init(options(project, host="codex", approval_mode=None))
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
            'default_tools_approval_mode = "prompt"\n',
            f"default_tools_approval_mode = {literal}\n",
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
            'default_tools_approval_mode = "prompt"\n',
            'default_tools_approval_mode = ["approve"]\n',
        )
    )
    project_init.run_init(options(project, remove=True, host=None))
    assert (
        config.read_text()
        == original
        + '[mcp_servers.seldon]\ndefault_tools_approval_mode = ["approve"]\n'
        + "tool_timeout_sec = 900\n"
    )
    before = snapshot(project)
    project_init.run_init(options(project, remove=True, host=None))
    assert snapshot(project) == before


def test_installed_wheel_registration_starts_bound_canonical_planner(tmp_path):
    """Catch a source-only/PYTHONPATH registration that breaks from a real host cwd.

    Source-writer tests cannot prove console installation, isolated interpreter
    import, MCP startup or consumption of the generated workspace argument.
    User filters outside the marked block must also survive installed CLI
    replacement/removal; the source filter cases cover fields inside that block.
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
    cli("--host", "all", "--approval-mode", "approve")
    config = product / ".codex/config.toml"
    config.write_text(
        config.read_text()
        + 'enabled_tools = ["harness_plan_status"]\ndisabled_tools = ["harness_publish"]\n'
    )
    before = snapshot(product)
    cli("--check")
    cli("--host", "all")
    assert snapshot(product) == before
    registered = tomllib.loads(config.read_text())
    config.write_text(
        config.read_text().replace("startup_timeout_sec = 20", "startup_timeout_sec = 99")
    )
    cli("--host", "all", "--yes")
    assert tomllib.loads(config.read_text()) == registered
    assert {p: b for p, b in snapshot(product).items() if p != Path(".codex/config.toml")} == {
        p: b for p, b in before.items() if p != Path(".codex/config.toml")
    }
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
    policy = tomllib.loads(config.read_text())["mcp_servers"]["seldon"]
    assert policy == {
        "default_tools_approval_mode": "approve",
        "enabled_tools": ["harness_plan_status"],
        "disabled_tools": ["harness_publish"],
    }
    assert (product / ".codex-harness/plan.sqlite3").exists()
