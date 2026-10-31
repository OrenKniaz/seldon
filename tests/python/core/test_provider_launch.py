from __future__ import annotations

import pytest

from codex_harness.core.provider_launch import (
    BYPASS_FLAGS,
    ProviderNormalLaunch,
    ProviderNormalPolicyError,
    reject_bypass,
)


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


def test_a_launch_record_cannot_carry_a_bypass_command() -> None:
    with pytest.raises(ProviderNormalPolicyError):
        ProviderNormalLaunch(cli="codex", command=("codex", "--yolo"), protections=())


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
