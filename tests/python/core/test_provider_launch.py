from __future__ import annotations

import json

import pytest

from codex_harness.core.provider_launch import (
    BYPASS_FLAGS,
    PROVIDER_NORMAL_PROFILE,
    ProviderNormalLaunch,
    ProviderNormalPolicyError,
    get_adapter,
    reject_bypass,
)


@pytest.mark.parametrize("cli", ["codex", "claude", "copilot"])
def test_stdin_prompt_is_preserved_outside_argv(cli):
    prompt = "Review this large packet\n" + "\u03bb" * 40_000
    launch = get_adapter(cli).provider_normal_command(prompt=prompt, prompt_via_stdin=True)
    assert prompt not in launch.command
    assert launch.stdin_text == prompt
    assert launch.to_payload()["stdinText"] == prompt
    if cli == "codex":
        assert launch.command[1] == "exec" and launch.command[-1] == "-"
    elif cli == "claude":
        assert launch.command[1] == "-p"
        assert launch.command[-1] == "Read,Edit,Write,Glob,Grep"
    else:
        assert "-p" not in launch.command and "--prompt" not in launch.command
    reject_bypass(launch.command)
    assert get_adapter(cli).provider_normal_command(prompt="small").stdin_text is None


def test_codex_runs_exec_in_workspace_write_sandbox_without_bypass() -> None:
    launch = get_adapter("codex").provider_normal_command(
        prompt="Add the parser\nwith tests", model="gpt-5.5", reasoning_effort="high"
    )

    assert launch.command == (
        "codex",
        "exec",
        "--json",
        "--color",
        "never",
        "--sandbox",
        "workspace-write",
        "-c",
        'approval_policy="never"',
        "--model",
        "gpt-5.5",
        "-c",
        'model_reasoning_effort="high"',
        "Add the parser\nwith tests",
    )
    assert "--ignore-user-config" not in launch.command
    assert "not repo-only" in launch.protections[0]


def test_claude_uses_permission_rules_and_explicit_tools() -> None:
    launch = get_adapter("claude").provider_normal_command(
        prompt="Fix the bug",
        model="claude-sonnet-5-5",
        reasoning_effort="high",
        allowed_tools=("Bash(uv run pytest:*)",),
        platform="linux",
    )

    assert launch.command == (
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Read,Edit,Write,Glob,Grep,Bash(uv run pytest:*)",
        "--model",
        "claude-sonnet-5-5",
        "--effort",
        "high",
        "Fix the bug",
    )
    assert "--bare" not in launch.command
    assert "--strict-mcp-config" not in launch.command
    assert any("not a security boundary" in line for line in launch.protections)


def test_claude_on_native_windows_states_the_missing_shell_sandbox() -> None:
    launch = get_adapter("claude").provider_normal_command(prompt="x", platform="win32")

    assert any("shell sandbox is unavailable" in line for line in launch.protections)


def test_copilot_uses_explicit_tool_rules_and_denies_push() -> None:
    launch = get_adapter("copilot").provider_normal_command(
        prompt="Write docs", allowed_tools=("shell(uv run pytest)",), model="gpt-5.5"
    )

    assert launch.command == (
        "copilot",
        "-p",
        "Write docs",
        "--no-ask-user",
        "--allow-tool",
        "write",
        "--allow-tool",
        "shell(uv run pytest)",
        "--deny-tool",
        "shell(git push)",
        "--model",
        "gpt-5.5",
    )
    assert "--yolo" not in launch.command
    assert "--autopilot" not in launch.command


@pytest.mark.parametrize("cli", ["claude", "copilot"])
@pytest.mark.parametrize("rule", ["", "*", "all", "--yolo"])
def test_wildcard_or_flag_tool_rules_are_refused(cli: str, rule: str) -> None:
    with pytest.raises(ProviderNormalPolicyError):
        get_adapter(cli).provider_normal_command(prompt="x", allowed_tools=(rule,))


@pytest.mark.parametrize("flag", sorted(BYPASS_FLAGS))
def test_every_bypass_flag_is_refused(flag: str) -> None:
    with pytest.raises(ProviderNormalPolicyError) as error:
        reject_bypass(["codex", "exec", flag, "prompt"])

    assert error.value.code == "provider-normal-bypass-flag"


@pytest.mark.parametrize(
    "command",
    [
        ["codex", "exec", "--sandbox", "danger-full-access", "p"],
        ["codex", "exec", "--sandbox=danger-full-access", "p"],
        ["codex", "exec", "-s", "danger-full-access", "p"],
        ["claude", "-p", "--permission-mode", "bypassPermissions", "p"],
        ["claude", "-p", "--permission-mode=bypassPermissions", "p"],
        ["codex", "exec", "-c", 'sandbox_mode="danger-full-access"', "p"],
        ["codex", "exec", "--config", "sandbox=x", "p"],
        ["claude", "-p", "--dangerously-skip-permissions=true", "p"],
    ],
)
def test_protection_disabling_values_are_refused(command: list[str]) -> None:
    with pytest.raises(ProviderNormalPolicyError):
        reject_bypass(command)


_CODE_PROMPT = (
    "Implement R2 exactly as documented:\n"
    "```python\n"
    "def split(total, people):  # integer cents only\n"
    "    base, extra = divmod(total, people)\n"
    "```\n"
    "Do not edit tests/. Mention --yolo only to say it is forbidden."
)


@pytest.mark.parametrize("cli", ["codex", "claude", "copilot"])
def test_multiline_code_prompt_is_preserved_exactly(cli: str) -> None:
    launch = get_adapter(cli).provider_normal_command(prompt=_CODE_PROMPT)

    assert _CODE_PROMPT in launch.command
    assert launch.command.count(_CODE_PROMPT) == 1


@pytest.mark.parametrize(
    "flag",
    [
        "--dangerously-bypass-hook-trust",
        "--dangerously-anything-new",
        "--ignore-rules",
        "--ignore-user-config",
    ],
)
def test_review_counterexample_flags_are_refused(flag: str) -> None:
    with pytest.raises(ProviderNormalPolicyError):
        ProviderNormalLaunch(cli="codex", command=("codex", "exec", flag, "p"), protections=())


@pytest.mark.parametrize("cli", ["codex", "claude", "copilot"])
def test_protections_state_requested_options_not_verified_enforcement(cli: str) -> None:
    text = " ".join(get_adapter(cli).provider_normal_command(prompt="x").protections)

    assert "not verified by Harness" in text
    assert "anything else is denied" not in text
    assert "refused without asking" not in text


def test_a_launch_record_cannot_carry_a_bypass_command() -> None:
    with pytest.raises(ProviderNormalPolicyError):
        ProviderNormalLaunch(cli="codex", command=("codex", "--yolo"), protections=())


@pytest.mark.parametrize("cli", ["codex", "claude", "copilot"])
def test_payload_is_json_and_names_the_profile(cli: str) -> None:
    payload = get_adapter(cli).provider_normal_command(prompt="x").to_payload()

    assert json.loads(json.dumps(payload))["profile"] == PROVIDER_NORMAL_PROFILE
    assert payload["protections"]


# Payload hashes captured from the unchanged legacy provider-normal builders.
# The payload includes exact prompt, argv, protections, environment and stdin.
_BASELINE_PAYLOAD_SHA256 = {
    "codex:win32:False": "4249dc2faf99cb8187709fdb7bac5ecc184970ab3c54f2e59683e4ef8a19ea28",
    "codex:win32:True": "8ec5293d13549f948b55c438ab512d75e580ca04f3aa93b4d2a66b6d0e39bd4b",
    "codex:linux:False": "a8bb2a4f59323f8432567a7d64339f8a463b0a8efd208624b2491d2f4dabff45",
    "codex:linux:True": "c1d26fb6584ff47dae3d92d1b2319d117bce401f66712bd8f92123bb6dc5b52a",
    "claude:win32:False": "f491189c56e7c43a2fe2479997319536fd806040ce3206bbc821d1d6c9898f45",
    "claude:win32:True": "2168ceb1b5b7b474ae1738e4d93ab3a78b7c59c194e4de8fd7ffd22940f606e0",
    "claude:linux:False": "6f82da132efa0cfbd3f6744fdba125d08fc510b790968ff089f5cd19bf76c691",
    "claude:linux:True": "71b2c3f1c02ec81667aabefea3e359f047c14aff144b6fb1e912cff5de4aebe1",
    "copilot:win32:False": "84b35f0190a893f86ae0329d2c9fd44950410eced8e8e5e09f41f8d091bbe11d",
    "copilot:win32:True": "d102ee65555766c670445c1c4523f799efd7d61df655fed2a468bfe44c4a2df8",
    "copilot:linux:False": "84b35f0190a893f86ae0329d2c9fd44950410eced8e8e5e09f41f8d091bbe11d",
    "copilot:linux:True": "d102ee65555766c670445c1c4523f799efd7d61df655fed2a468bfe44c4a2df8",
}


@pytest.mark.parametrize("cli", ["codex", "claude", "copilot"])
@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("stdin", [False, True])
def test_exact_qualified_provider_payload_parity(cli, platform, stdin):
    import hashlib

    tools = (
        ("Bash(python -B -m unittest:*)", "PowerShell(python -B -m unittest:*)")
        if cli == "claude"
        else ("shell(python -B -m unittest)",)
        if cli == "copilot"
        else ()
    )
    payload = (
        get_adapter(cli)
        .provider_normal_command(
            prompt='Keep\n  exact λ and "quotes"; --yolo is forbidden.',
            prompt_via_stdin=stdin,
            model="exact-model",
            reasoning_effort="high",
            executable="C:/provider dir/native.cmd",
            platform=platform,
            allowed_tools=tools,
        )
        .to_payload()
    )
    actual = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    assert actual == _BASELINE_PAYLOAD_SHA256[f"{cli}:{platform}:{stdin}"]


def test_provider_lookup_preserves_normalization_and_unknown_error():
    assert get_adapter(" CODEX ") is get_adapter("codex")
    with pytest.raises(
        ValueError, match="Unsupported CLI adapter 'foreign'. Supported: claude, codex, copilot"
    ):
        get_adapter("foreign")
