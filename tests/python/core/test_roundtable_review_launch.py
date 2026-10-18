"""Source-only launch requests; no provider or restriction-enforcement credit."""

import json

import pytest

from codex_harness.core import roundtable
from codex_harness.core.provider_launch import get_adapter

PACKET = "Exact read-only worker packet\nUnicode: \u05d0\n"
INSTRUCTIONS = "Return only the exact readiness review JSON schema."


@pytest.mark.parametrize("instructions", [None, INSTRUCTIONS])
def test_claude_review_requests_no_tools_without_changing_normal_policy(instructions):
    reviewer = {"provider": "claude", "model": "claude-sonnet-5-5", "effort": "high"}
    adapter = get_adapter("claude")
    normal = adapter.provider_normal_command(
        prompt=PACKET,
        prompt_via_stdin=True,
        model=reviewer["model"],
        reasoning_effort=reviewer["effort"],
        executable="pinned-claude.exe",
    )
    before = normal.to_payload()
    launch = roundtable._launch_review(
        reviewer,
        PACKET,
        "pinned-claude.exe",
        instructions=instructions,
    )
    suffix = ("--append-system-prompt", instructions) if instructions else ()
    assert launch.command == (*normal.command, "--tools", "", *suffix)
    assert launch.stdin_text == normal.stdin_text == PACKET
    assert launch.environment == normal.environment == {"PYTHONUTF8": "1"}
    assert launch.protections[:-1] == normal.protections
    assert "Requested review-only tool restriction" in launch.protections[-1]
    assert "not verified" in launch.protections[-1]
    assert normal.to_payload() == before
    assert (
        adapter.provider_normal_command(
            prompt=PACKET,
            prompt_via_stdin=True,
            model=reviewer["model"],
            reasoning_effort=reviewer["effort"],
            executable="pinned-claude.exe",
        ).to_payload()
        == before
    )


def test_normal_claude_worker_argv_remains_exact():
    launch = get_adapter("claude").provider_normal_command(
        prompt=PACKET,
        prompt_via_stdin=True,
        model="claude-sonnet-5-5",
        reasoning_effort="high",
        executable="pinned-claude.exe",
    )
    assert launch.command == (
        "pinned-claude.exe",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Read,Edit,Write,Glob,Grep",
        "--model",
        "claude-sonnet-5-5",
        "--effort",
        "high",
    )
    assert launch.stdin_text == PACKET
    assert "--tools" not in launch.command


@pytest.mark.parametrize("instructions", [None, INSTRUCTIONS])
def test_codex_review_argv_and_instruction_channel_are_unchanged(instructions):
    reviewer = {"provider": "codex", "model": "gpt-6.1-sol", "effort": "high"}
    normal = get_adapter("codex").provider_normal_command(
        prompt=PACKET,
        prompt_via_stdin=True,
        model=reviewer["model"],
        reasoning_effort=reviewer["effort"],
        executable="pinned-codex.exe",
    )
    expected = (*normal.command[:-1], "--skip-git-repo-check", normal.command[-1])
    if instructions:
        expected = (
            *expected[:-1],
            "-c",
            "developer_instructions=" + json.dumps(instructions),
            expected[-1],
        )
    launch = roundtable._launch_review(
        reviewer,
        PACKET,
        "pinned-codex.exe",
        instructions=instructions,
    )
    assert launch.command == expected
    assert launch.stdin_text == normal.stdin_text == PACKET
    assert launch.protections == normal.protections
    assert launch.environment == normal.environment
    assert "--tools" not in launch.command
