"""Codex project TOML: preserve unrelated text and validate the resulting document."""

import json
import re
import tomllib

PATH = ".codex/config.toml"
GUIDANCE = "Open this folder in Codex and choose Trust; start a chat here."
START = "# seldon-init v1 begin"
END = "# seldon-init v1 end"


def render(text, entry, *, remove=False, yes=False):
    data = tomllib.loads(text)
    if not isinstance(data.get("mcp_servers", {}), dict):
        raise ValueError("Expected mcp_servers table")
    existing = data.get("mcp_servers", {}).get("seldon")
    expected = {**entry, "startup_timeout_sec": 20}
    if not remove and existing is not None and existing != expected and not yes:
        raise ValueError("Existing seldon entry differs; inspect it and use --yes to replace")
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
    replacement = ""
    if not remove:
        replacement = START + "\n[mcp_servers.seldon]\n"
        for key in ("command", "args"):
            replacement += f"{key} = {json.dumps(entry[key], ensure_ascii=False)}\n"
        replacement += (
            'env = { SELDON_INIT = "seldon-init v1" }\nstartup_timeout_sec = 20\n' + END + "\n"
        )
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
    if not remove and existing == expected:
        return text
    return result
