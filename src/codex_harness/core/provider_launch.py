"""Provider-normal worker launch policy (North Star v3).

Harness wraps each provider CLI under the provider's own normal protections
instead of bypassing them. Workers get the CLI's supported headless mode with
its sandbox and permission rules intact, and every launch carries a truthful
statement of the protections that actually apply on this platform. Bypass
flags are refused outright; legacy launch specs remain outside this standalone leaf.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

PROVIDER_NORMAL_PROFILE = "provider-normal"

BYPASS_FLAGS = frozenset(
    {
        "--dangerously-bypass-approvals-and-sandbox",
        "--yolo",
        "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions",
        "--bare",
        "--allow-all",
        "--allow-all-tools",
        "--allow-all-paths",
        "--allow-all-urls",
        "--ignore-rules",
        # Provider-normal keeps the user's own config and auth.
        "--ignore-user-config",
    }
)
# Any provider flag named "dangerously..." disables a protection by design.
_BYPASS_PREFIX = "--dangerously"
# Flags whose listed values switch the provider's protection off.
_BYPASS_VALUES: dict[str, frozenset[str]] = {
    "--sandbox": frozenset({"danger-full-access"}),
    "-s": frozenset({"danger-full-access"}),
    "--permission-mode": frozenset({"bypassPermissions"}),
}
# Config overrides may not change the sandbox; it is set by flag only.
_CONFIG_FLAGS = frozenset({"-c", "--config"})
_FORBIDDEN_CONFIG_KEYS = frozenset({"sandbox_mode", "sandbox"})


class ProviderNormalPolicyError(ValueError):
    """A command would weaken the provider's normal protections."""

    def __init__(self, code: str, *, flag: str) -> None:
        self.code = code
        self.flag = flag
        super().__init__(f"{code}: {flag}")


def _split(token: str) -> tuple[str, str | None]:
    if token.startswith("--") and "=" in token:
        name, value = token.split("=", 1)
        return name, value
    return token, None


def reject_bypass(command: Sequence[str]) -> None:
    """Raise unless ``command`` keeps the provider's normal protections."""

    parts = [str(part) for part in command]
    for index, token in enumerate(parts):
        name, inline_value = _split(token)
        is_flag = not any(character.isspace() for character in token)
        if name in BYPASS_FLAGS or (is_flag and name.startswith(_BYPASS_PREFIX)):
            raise ProviderNormalPolicyError("provider-normal-bypass-flag", flag=token)
        value = inline_value
        if value is None and index + 1 < len(parts):
            value = parts[index + 1]
        if name in _BYPASS_VALUES and value in _BYPASS_VALUES[name]:
            raise ProviderNormalPolicyError("provider-normal-bypass-value", flag=f"{name} {value}")
        if name in _CONFIG_FLAGS and value is not None:
            key = value.split("=", 1)[0].strip()
            if key in _FORBIDDEN_CONFIG_KEYS:
                raise ProviderNormalPolicyError("provider-normal-sandbox-override", flag=value)


def require_tool_rules(tools: Sequence[str]) -> tuple[str, ...]:
    """Explicit, non-wildcard tool rules only."""

    rules = tuple(str(tool).strip() for tool in tools)
    for rule in rules:
        if not rule or rule in {"*", "all"} or rule.startswith("-"):
            raise ProviderNormalPolicyError(
                "provider-normal-tool-rule-invalid", flag=rule or "<empty>"
            )
    return rules


@dataclass(frozen=True)
class ProviderNormalLaunch:
    """A headless worker command plus the protections that actually apply."""

    cli: str
    command: tuple[str, ...]
    protections: tuple[str, ...]
    environment: dict[str, str] = field(default_factory=dict)
    stdin_text: str | None = None

    def __post_init__(self) -> None:
        reject_bypass(self.command)

    def to_payload(self) -> dict[str, object]:
        return {
            "profile": PROVIDER_NORMAL_PROFILE,
            "cli": self.cli,
            "command": list(self.command),
            "protections": list(self.protections),
            "environment": dict(self.environment),
            "stdinText": self.stdin_text,
        }


class _CodexProvider:
    def provider_normal_command(
        self,
        *,
        prompt: str,
        prompt_via_stdin: bool = False,
        model: str | None = None,
        reasoning_effort: str | None = None,
        executable: str | None = None,
        allowed_tools: Sequence[str] = (),
        platform: str = sys.platform,
    ) -> ProviderNormalLaunch:
        # Codex gates by sandbox, not per-tool rules; allowed_tools is unused.
        del allowed_tools
        command: list[str] = [
            executable or "codex",
            "exec",
            "--json",
            "--color",
            "never",
            "--sandbox",
            "workspace-write",
            "-c",
            'approval_policy="never"',
        ]
        if model:
            command.extend(["--model", model])
        if reasoning_effort:
            command.extend(["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"])
        # The exact prompt is one argv element; collapsing it would merge
        # code and comments in the task text.
        command.append("-" if prompt_via_stdin else prompt)
        protections = [
            "Requested sandbox workspace-write: per Codex documentation, writes are limited "
            "to the workspace and temp directories, which is not repo-only. Enforcement is "
            "not verified by Harness.",
            "Requested approval policy never: blocked actions fail instead of prompting.",
            "Network access and other sandbox settings follow the user's own Codex config.",
        ]
        if platform == "win32":
            protections.append(
                "Windows sandbox engine is whatever the user's Codex config selects; "
                "Harness does not change it."
            )
        return ProviderNormalLaunch(
            cli="codex",
            command=tuple(command),
            protections=tuple(protections),
            environment={"PYTHONUTF8": "1"},
            stdin_text=prompt if prompt_via_stdin else None,
        )


CLAUDE_PROVIDER_NORMAL_BASE_TOOLS = ("Read", "Edit", "Write", "Glob", "Grep")


def _model_and_effort(model: str | None, reasoning_effort: str | None) -> list[str]:
    args: list[str] = []
    if model:
        args.extend(["--model", model])
    if reasoning_effort:
        args.extend(["--effort", reasoning_effort])
    return args


class _ClaudeProvider:
    def provider_normal_command(
        self,
        *,
        prompt: str,
        prompt_via_stdin: bool = False,
        model: str | None = None,
        reasoning_effort: str | None = None,
        executable: str | None = None,
        allowed_tools: Sequence[str] = (),
        platform: str = sys.platform,
    ) -> ProviderNormalLaunch:
        tools = tuple(
            dict.fromkeys((*CLAUDE_PROVIDER_NORMAL_BASE_TOOLS, *require_tool_rules(allowed_tools)))
        )
        command = [
            executable or "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            ",".join(tools),
            *_model_and_effort(model, reasoning_effort),
            *([] if prompt_via_stdin else [prompt]),
        ]
        protections = [
            "Requested permission mode acceptEdits with allowed tools: "
            + ", ".join(tools)
            + ". Effective permissions also depend on the user's Claude Code settings and "
            "are not verified by Harness.",
            "A git worktree separates working files but is not a security boundary.",
        ]
        if platform == "win32":
            protections.append(
                "Native Windows: Claude Code's shell sandbox is unavailable; permission "
                "rules still apply and allowed shell commands run with the user's rights."
            )
        else:
            protections.append("Shell sandbox follows the user's Claude Code settings.")
        return ProviderNormalLaunch(
            cli="claude",
            command=tuple(command),
            protections=tuple(protections),
            environment={"PYTHONUTF8": "1"},
            stdin_text=prompt if prompt_via_stdin else None,
        )


class _CopilotProvider:
    def provider_normal_command(
        self,
        *,
        prompt: str,
        prompt_via_stdin: bool = False,
        model: str | None = None,
        reasoning_effort: str | None = None,
        executable: str | None = None,
        allowed_tools: Sequence[str] = (),
        platform: str = sys.platform,
    ) -> ProviderNormalLaunch:
        del platform
        tools = tuple(dict.fromkeys(("write", *require_tool_rules(allowed_tools))))
        command: list[str] = [
            executable or "copilot",
            *([] if prompt_via_stdin else ["-p", prompt]),
            "--no-ask-user",
        ]
        for tool in tools:
            command.extend(["--allow-tool", tool])
        # Workers never publish; the single integrator owns pushes.
        command.extend(["--deny-tool", "shell(git push)"])
        if model:
            command.extend(["--model", model])
        if reasoning_effort:
            command.extend(["--reasoning-effort", reasoning_effort])
        protections = (
            "Requested tool rules: allow "
            + ", ".join(tools)
            + "; deny shell(git push); --no-ask-user. Effective behavior also depends on "
            "the user's Copilot configuration and is not verified by Harness.",
            "The user's own Copilot configuration and MCP servers apply unchanged.",
        )
        return ProviderNormalLaunch(
            cli="copilot",
            command=tuple(command),
            protections=protections,
            environment={"PYTHONUTF8": "1"},
            stdin_text=prompt if prompt_via_stdin else None,
        )


_ADAPTERS = {"codex": _CodexProvider(), "claude": _ClaudeProvider(), "copilot": _CopilotProvider()}


def get_adapter(cli_name: str):
    """Return only provider-normal command construction; no legacy launch surface."""
    normalized = cli_name.strip().lower()
    try:
        return _ADAPTERS[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(_ADAPTERS))
        raise ValueError(f"Unsupported CLI adapter {cli_name!r}. Supported: {supported}") from exc
