"""C1/C11/C18: Claude Code headless calls (`claude -p`), the same recorded call shape as Codex."""

import json

from .store import encode

MODELS = {"planner": "claude-opus-5-5", "reviewer": "claude-opus-5-5", "judge": "claude-opus-5-5",
          "demo": "claude-opus-5-5", "build": "claude-sonnet-5-5", "decider": "claude-haiku-5-5"}


def events(output):
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def command(setup, config, folder, worker, session, images):
    # Hermetic like Codex --ignore-user-config: --safe-mode drops CLAUDE.md, memory, skills, plugins, hooks
    # and MCP servers while keeping the owner's login; full access as Codex (scope enforced from the diff).
    line = [*setup["claudeCommand"], "-p", "--output-format", "stream-json", "--verbose",
            "--json-schema", (folder / "schema.json").read_text("utf-8"), "--model", config["model"],
            "--effort", {"minimal": "low"}.get(config["effort"], config["effort"]),
            "--permission-mode", "bypassPermissions"]
    if not config.get("lead"):
        line += ["--safe-mode", "--strict-mcp-config", "--disable-slash-commands"]
    else:
        line += ["--strict-mcp-config", "--setting-sources", ""]
    if session:
        line += ["--resume", session]
    if not worker:
        line += ["--no-session-persistence", "--tools", "Bash,Read" if config.get("walk") else "Read" if images else ""]
    return line


def prompt(images):
    return ("\n\nImages: open each file with the Read tool and inspect it before answering:\n"
            + "\n".join(str(path) for path in images)) if images else ""


def answer(output):
    final = None
    for event in events(output):
        if event.get("type") == "result":
            if event.get("is_error") or event.get("subtype") != "success":
                raise RuntimeError(f"Claude turn failed: {event}")
            final = event
    if final is None:
        raise RuntimeError("Claude stream has no completed terminal answer")
    if final.get("structured_output") is not None:
        return encode(final["structured_output"])
    if not isinstance(final.get("result"), str):
        raise RuntimeError("Claude stream has no completed terminal answer")
    return final["result"]


def session(output):
    return next((e["session_id"] for e in events(output) if e.get("session_id")), None)


def errors(output):
    """The provider's own error events: a failed result and any API error message before it."""
    return [encode(e) for e in events(output) if e.get("is_api_error_message")
            or e.get("type") == "result" and (e.get("is_error") or e.get("subtype") != "success")]


def usage(output):
    """Codex turn.completed names, so token tooling reads either provider from result.json."""
    found = [e["usage"] for e in events(output) if e.get("type") == "result" and e.get("usage")]
    if not found:
        return None
    used = found[-1]
    cached = used.get("cache_read_input_tokens", 0)
    return {"input_tokens": used.get("input_tokens", 0) + used.get("cache_creation_input_tokens", 0) + cached,
            "cached_input_tokens": cached, "output_tokens": used.get("output_tokens", 0)}
