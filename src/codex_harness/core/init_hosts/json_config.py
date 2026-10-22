"""Preserve unrelated JSON values; refuse ambiguous or non-JSON input unchanged."""

import json


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def merge(text, group, entry, *, remove=False, yes=False):
    try:
        data = json.loads(text, object_pairs_hook=_unique) if text.strip() else {}
    except json.JSONDecodeError as error:
        raise ValueError("Expected strict JSON; comments/JSONC require manual merge") from error
    if not isinstance(data, dict) or not isinstance(data.get(group, {}), dict):
        raise ValueError(f"Expected an object with a {group} object")
    servers = data.get(group, {})
    existing = servers.get("seldon")
    owned = (
        isinstance(existing, dict)
        and isinstance(existing.get("env"), dict)
        and existing["env"].get("SELDON_INIT") == "seldon-init v1"
    )
    if remove:
        if not owned:
            return text
        del servers["seldon"]
    else:
        if existing == entry:
            return text
        if "seldon" in servers and not yes:
            raise ValueError(
                "Existing seldon entry differs; inspect the diff and use --yes to replace"
            )
        servers["seldon"] = entry
        data[group] = servers
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
