"""Codex project TOML: preserve unrelated text and validate the resulting document."""

import json
import math
import re
import tomllib

PATH = ".codex/config.toml"
GUIDANCE = "Open this folder in Codex and choose Trust; start a chat here."
START = "# seldon-init v1 begin"
END = "# seldon-init v1 end"


def render_agents(text, *, remove=False):
    start, end = "<!-- seldon-init v1 begin -->", "<!-- seldon-init v1 end -->"
    block = (
        f"{start}\nFor requests to plan, build or implement a product in this project, use Seldon "
        "harness_chat first; no special wording or plan mode request is needed.\n"
        "Do not write product source directly before Seldon's plan is published and worker "
        "execution is approved. Preserve Align/Vision/execution gates and higher-priority user "
        "instructions. Unrelated discussion and maintenance need no extra process.\n"
        "Existing project files (briefs, docs, specs, any file not created by Seldon) are "
        "read-only inputs during planning; never overwrite, rewrite, rename or summarize "
        "them in place. Plan content lives in Seldon (harness tools). Only workers change "
        "product files, after the execution go-ahead.\n"
        f"{end}\n\n"
    )
    if start in text or end in text:
        if text.count(start) != 1 or text.count(end) != 1 or block not in text:
            raise ValueError("Malformed or changed AGENTS.md ownership block")
        return text.replace(block, "", 1) if remove else text
    index = int(text.startswith("\ufeff"))
    return text if remove else text[:index] + block + text[index:]


def render(text, entry, *, remove=False, yes=False, approval_mode=None):
    data = tomllib.loads(text)
    if not isinstance(data.get("mcp_servers", {}), dict):
        raise ValueError("Expected mcp_servers table")
    existing = data.get("mcp_servers", {}).get("seldon")
    timeout_key = "tool_timeout_sec"
    expected = {**entry, "startup_timeout_sec": 20, timeout_key: 900}
    if not remove and isinstance(existing, dict) and timeout_key in existing:
        value = existing[timeout_key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Unsupported Seldon tool timeout; preserve it manually")
        expected[timeout_key] = value
    mode_key = "default_tools_approval_mode"
    if not remove and isinstance(existing, dict) and mode_key in existing:
        mode = existing[mode_key]
        if not isinstance(mode, str) or mode not in {"auto", "prompt", "writes", "approve"}:
            raise ValueError("Unsupported Seldon tool approval mode; preserve it manually")
        expected[mode_key] = approval_mode or mode
    elif not remove:
        expected[mode_key] = approval_mode or "approve"
    if isinstance(existing, dict) and "tools" in existing:
        expected["tools"] = existing["tools"]
    filter_keys = ("enabled_tools", "disabled_tools")
    filters = {}
    if isinstance(existing, dict):
        for key in filter_keys:
            if key in existing:
                value = existing[key]
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    raise ValueError(f"Unsupported Seldon {key}; preserve it manually")
                expected[key] = value
                filters[key] = value
    identity = (
        {k: v for k, v in existing.items() if k not in {mode_key, timeout_key}}
        if isinstance(existing, dict)
        else existing
    )
    if (
        not remove
        and existing is not None
        and identity != {k: v for k, v in expected.items() if k not in {mode_key, timeout_key}}
        and not yes
    ):
        raise ValueError("Existing seldon entry differs; inspect it and use --yes to replace")
    emit_mode = mode_key in expected
    emit_timeout = True
    policy_tables = ""
    if START in text or END in text:
        if text.count(START) != 1 or text.count(END) != 1:
            raise ValueError("Malformed seldon ownership markers")
        start = text.index(START)
        end = text.index(END) + len(END)
        if text[end : end + 2] == "\r\n":
            end += 2
        elif text[end : end + 1] == "\n":
            end += 1
        if end <= start:
            raise ValueError("Malformed seldon ownership markers")
        block = text[start:end]
        block_data = tomllib.loads(block)
        if set(block_data) != {"mcp_servers"} or set(block_data["mcp_servers"]) != {"seldon"}:
            raise ValueError(
                "Ownership block contains unrelated configuration; preserve it manually"
            )
        emit_timeout = (
            timeout_key in block_data["mcp_servers"]["seldon"] or timeout_key not in existing
        )
        if emit_mode:
            block_server = block_data["mcp_servers"]["seldon"]
            emit_mode = (
                mode_key in block_server
                or not isinstance(existing, dict)
                or mode_key not in existing
            )
            if not emit_mode and approval_mode and existing[mode_key] != approval_mode:
                raise ValueError("Unowned approval mode requires manual merge")
        filters = {
            key: value
            for key, value in filters.items()
            if key in block_data["mcp_servers"]["seldon"]
        }
        table = re.search(r"(?m)^\[mcp_servers\.seldon\.tools(?:\.|\])", block)
        if table:
            policy_tables = block[table.start() : block.index(END)]
        elif "tools" in block_data["mcp_servers"]["seldon"]:
            raise ValueError("Inline tool policy requires manual merge")
    elif existing is not None:
        if remove:
            return text
        if not yes:
            raise ValueError(
                "Existing seldon entry is unowned; inspect it and use --yes to replace"
            )
        # Only simple explicit tables are safely replaceable without a TOML serializer.
        match = re.search(r"(?m)^\[mcp_servers\.seldon\][ \t]*(?:#.*)?\r?$", text)
        if (
            match is None
            or isinstance(existing, dict)
            and any(isinstance(value, dict) for value in existing.values())
        ):
            raise ValueError("Complex existing seldon TOML requires manual merge")
        start = match.start()
        following = re.search(r"(?m)^\[", text[match.end() :])
        end = match.end() + following.start() if following else len(text)
    else:
        if remove:
            return text
        start = end = len(text)
    if not remove and existing == expected:
        return text
    replacement = ""
    if (
        remove
        and isinstance(existing, dict)
        and (
            mode_key in existing
            or timeout_key in existing
            or "tools" in existing
            or any(key in existing for key in filter_keys)
        )
    ):
        # Retain policy as unowned TOML; only the marked registration is removed.
        replacement = "[mcp_servers.seldon]\n"
        if mode_key in block_data["mcp_servers"]["seldon"]:
            replacement += f"{mode_key} = {json.dumps(existing[mode_key])}\n"
        if timeout_key in block_data["mcp_servers"]["seldon"]:
            replacement += f"{timeout_key} = {json.dumps(existing[timeout_key])}\n"
        for key, value in filters.items():
            replacement += f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
        replacement += policy_tables
    if not remove:
        replacement = START + "\n[mcp_servers.seldon]\n"
        for key in ("command", "args"):
            replacement += f"{key} = {json.dumps(entry[key], ensure_ascii=False)}\n"
        replacement += 'env = { SELDON_INIT = "seldon-init v1" }\nstartup_timeout_sec = 20\n'
        if emit_timeout:
            replacement += f"{timeout_key} = {json.dumps(expected[timeout_key])}\n"
        if emit_mode:
            replacement += f"{mode_key} = {json.dumps(expected[mode_key])}\n"
        for key, value in filters.items():
            replacement += f"{key} = {json.dumps(value, ensure_ascii=False)}\n"
        replacement += policy_tables
        replacement += END + "\n"
        if start and text[start - 1] != "\n":
            replacement = "\n" + replacement
    result = text[:start] + replacement + text[end:]
    after = tomllib.loads(result)
    before_other = dict(data.get("mcp_servers", {}))
    after_other = dict(after.get("mcp_servers", {}))
    before_other.pop("seldon", None)
    after_other.pop("seldon", None)
    if before_other != after_other or {k: v for k, v in data.items() if k != "mcp_servers"} != {
        k: v for k, v in after.items() if k != "mcp_servers"
    }:
        raise ValueError("Refusing a change to unrelated TOML settings")
    if not remove and after.get("mcp_servers", {}).get("seldon") != expected:
        raise ValueError("Cannot represent seldon in this TOML layout; manual merge required")
    return result
