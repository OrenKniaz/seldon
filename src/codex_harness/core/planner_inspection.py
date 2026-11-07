"""Read-only canonical node and worker packet inspection."""

from __future__ import annotations
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qsl, unquote, urlsplit


def validate_setup(setup, workspace):
    keys = {"projectFolder", "gitName", "gitEmail", "githubLogin", "repoUrl"}
    if (
        not isinstance(setup, dict)
        or setup.keys() - keys
        or any(not isinstance(value, str) or not value.strip() for value in setup.values())
    ):
        raise ValueError("setup accepts only the five documented optional strings")
    if "repoUrl" in setup:
        url = urlsplit(unquote(setup["repoUrl"]))
        if (
            re.match(r"^[^/@\s]+:[^/@\s]+@", setup["repoUrl"])
            or url.password is not None
            or url.username not in (None, "git")
            or any(
                re.search(r"token|password|secret|credential|authorization|api.?key", key, re.I)
                for key, _ in parse_qsl(url.query)
            )
        ):
            raise ValueError("Repository URL contains credentials; supply a clean address")
    if "projectFolder" in setup:
        folder = Path(setup["projectFolder"]).expanduser()
        folder = (workspace / folder).resolve()
        if not folder.is_dir() or folder != workspace.resolve():
            raise ValueError("Folder mismatch: reconnect Seldon there; server workspace is fixed")


def packet_view(store, bead_id: str, provider: str = "codex") -> dict:
    """Use the canonical builder without compiling, rendering or changing planning state."""
    from codex_harness.core.graph import load_plan
    from codex_harness.core.worker_packet import build_packet

    current = store.manifest_revision()
    manifest = None
    published = store._meta()["published_revision"]
    if published:
        candidate = store.published_manifest(published)
        if current in (candidate["planRevision"], candidate.get("sourceManifestRevision")):
            manifest = candidate
    if manifest is None:
        for row in store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='manifest-compiled' ORDER BY id DESC"
        ):
            candidate = json.loads(row[0])["manifest"]
            if candidate["planRevision"] == current:
                manifest = candidate
                break
    if manifest is None:
        raise ValueError(
            "No current compiled or published manifest. "
            "Next: answer the author question or use harness_steer with missing canonical content"
        )
    if bead_id not in manifest["beads"]:
        raise ValueError(f"Unknown bead: {bead_id}")
    locked = store._meta()["locked_vision_json"]
    with TemporaryDirectory(prefix="planner-packet-") as directory:
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        packet = build_packet(
            load_plan(path),
            {"id": bead_id, **manifest["beads"][bead_id]},
            store.workspace / "template",
            None,
            provider=provider,
            proof_level=manifest.get("proofLevel"),
            setup=store.spine().node(store.spine().ids("vision")[0])["fields"].get("setup", {}),
            intent_nodes=[json.loads(locked), *store.list_nodes("requirement")] if locked else None,
        )
    result = {"packet": packet, "characters": len(packet), "bytes": len(packet.encode("utf-8"))}
    if manifest.get("semantic"):
        semantic = manifest["semantic"]
        result["semanticSuite"] = {
            "mode": manifest["planMode"],
            "scenarioIds": [s["id"] for s in semantic["scenarios"]["scenarios"]],
            "targets": semantic["targets"],
            "teaching": "Controller acceptance uses semantic.scenarios and semantic.targets. "
            "The separate legacy Current acceptance scenarios section may be empty. "
            "These are planned scenarios, not execution proof.",
        }
    return result


def _node_test_blocks(source: str) -> list[tuple[str, str]]:
    """Best-effort node:test literal test()/it() calls; never execute source."""
    # Hide comments and literals while retaining offsets for balanced call extraction.
    tokens = r"//[^\n]*|/\*[\s\S]*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`"
    masked = re.sub(tokens, lambda m: " " * len(m[0]), source)
    calls = r"\b(?:test|it)\s*\(\s*(['\"])((?:\\.|(?!\1)[^\\])*)\1"
    blocks = []
    for match in re.finditer(calls, source):
        if not re.match(r"(?:test|it)\s*\(", masked[match.start() :]):
            continue
        opening = source.index("(", match.start())
        depth, end = 0, len(source)
        for offset in range(opening, len(masked)):
            if masked[offset] == "(":
                depth += 1
            elif masked[offset] == ")":
                depth -= 1
                if depth == 0:
                    end = offset + 1
                    if source[end : end + 1] == ";":
                        end += 1
                    break
        name = re.sub(r"\\(['\"\\])", r"\1", match[2])
        blocks.append((name, source[match.start() : end]))
    return blocks


def show_node(store, node_id: str, *, body=False, test=None) -> dict:
    """Keep Python inspection and add best-effort static command-runner inspection."""
    from codex_harness.core.spine import compile_manifest, requirement_file_sources

    spine = store.spine()
    verification = spine.node(spine.ids("vision")[0])["fields"].get("verification")
    if verification is None or verification["runner"] != "command":
        return store.show_node(node_id, body=body, test=test)
    if body and not test:
        raise ValueError("--body requires --test <name>")
    if node_id not in spine.ids():
        raise ValueError(f"Unknown node: {node_id}; use harness_nodes for available IDs")
    node = spine.node(node_id)
    result = {**node, "children": spine.children(node_id), "testFiles": []}
    if node["kind"] not in ("requirement", "test"):
        return result
    modules = {
        spine.node(key)["fields"]["module"]
        for key in {node_id, *spine.descendants(node_id)}
        if spine.node(key)["kind"] == "test"
    }
    template = store.workspace / "template"
    manifest = compile_manifest(spine, template)
    sources = requirement_file_sources(template, manifest["requirementFiles"])
    remaining, found = 8192, False
    for name in sorted(modules & sources.keys()):
        blocks = _node_test_blocks(sources[name].read_text(encoding="utf-8-sig"))
        entry = {
            "path": name,
            "functions": [key for key, _ in blocks],
            "discovery": "best effort: node:test test()/it() literal call names",
        }
        if body:
            selected = [text for key, text in blocks if key == test]
            if len(selected) > 1:
                raise ValueError(f"Ambiguous test {test} in {name}")
            if selected:
                found = True
                encoded = selected[0].encode("utf-8")
                clipped = encoded[:remaining].decode("utf-8", errors="ignore")
                remaining -= len(clipped.encode("utf-8"))
                entry.update(
                    test=test, body=clipped, truncated=len(encoded) > len(clipped.encode("utf-8"))
                )
        result["testFiles"].append(entry)
    if body and not found:
        raise ValueError(f"Test not found in linked protected files: {test}")
    return result
