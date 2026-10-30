"""Project-local activation; no global configuration, credentials or provider calls."""

from __future__ import annotations

import difflib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from importlib.metadata import distribution
from pathlib import Path

from codex_harness.core.init_hosts import HOSTS, MARKER, claude, codex, server_entry
from codex_harness.core.preflight import preflight_report

IGNORE_START = f"# {MARKER} begin\n"
IGNORE_END = f"# {MARKER} end\n"
STORE_MARKER = ".codex-harness/.seldon-init"


def project_root(value):
    root = Path(value).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project path must be an existing directory")
    for parent in (root, *root.parents):
        if parent.name == ".codex-harness":
            raise ValueError("Cannot initialize inside a Seldon store")
        if (parent / "src/codex_harness/core/mcp_planner.py").is_file():
            raise ValueError("Cannot initialize inside the Seldon source tree")
    return root


def local_path(root, relative):
    path = root / relative
    for item in (path, *path.parents):
        if item == root:
            break
        if item.is_symlink() or (
            item.exists()
            and getattr(item.stat(), "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ValueError(f"Refusing redirected project path: {item}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Path escapes project: {path}")
    return path


def installed_command():
    # An editable/source-only import is not a stable install for other working directories.
    package = distribution("seldon")
    installed = Path(package.locate_file("codex_harness/core/project_init.py")).resolve()
    if installed != Path(__file__).resolve():
        raise ValueError(
            "Install the Seldon wheel first; source/editable activation is unsupported"
        )
    return str(Path(sys.executable).absolute())


def detect_hosts(root):
    """Executable/app existence only, without reading configuration or authentication."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    return {
        "codex": bool(shutil.which("codex"))
        or bool(os.environ.get("LOCALAPPDATA") and (local / "Programs/Codex/Codex.exe").is_file())
        or Path("/Applications/Codex.app").is_dir(),
        "claude": bool(shutil.which("claude"))
        or bool(os.environ.get("LOCALAPPDATA") and (local / "AnthropicClaude/claude.exe").is_file())
        or Path("/Applications/Claude.app").is_dir(),
        "copilot-vscode": bool(shutil.which("code")) or (root / ".vscode").is_dir(),
    }


def read_text(path):
    return path.read_bytes().decode("utf-8") if path.exists() else ""


def read_entry(host, text):
    if host == "codex":
        servers = tomllib.loads(text).get("mcp_servers", {})
        if not isinstance(servers, dict):
            raise ValueError("Expected mcp_servers table")
        return servers.get("seldon")
    from codex_harness.core.init_hosts.json_config import _unique

    data = json.loads(text, object_pairs_hook=_unique) if text.strip() else {}
    group = "servers" if host == "copilot-vscode" else "mcpServers"
    if not isinstance(data, dict) or not isinstance(data.get(group, {}), dict):
        raise ValueError(f"Expected {group} object")
    return data.get(group, {}).get("seldon")


def registration_diff(host, before, after):
    """Show routing changes without dumping unrelated configuration or server secrets."""

    def routing(text):
        entry = read_entry(host, text)
        if not isinstance(entry, dict):
            return "seldon: absent\n"
        args = entry.get("args", [])
        workspace = "unspecified"
        if isinstance(args, list) and "--workspace" in args:
            index = args.index("--workspace") + 1
            if index < len(args):
                workspace = args[index]
        owned = isinstance(entry.get("env"), dict) and entry["env"].get("SELDON_INIT") == MARKER
        return f"seldon: present\nworkspace: {workspace}\nowned marker: {owned}\n"

    return "".join(
        difflib.unified_diff(
            routing(before).splitlines(True),
            routing(after).splitlines(True),
            fromfile=f"{host}: existing routing",
            tofile=f"{host}: proposed routing",
        )
    )


def ignore_text(text, remove):
    if IGNORE_START in text or IGNORE_END in text:
        if text.count(IGNORE_START) != 1 or text.count(IGNORE_END) != 1:
            raise ValueError("Malformed .gitignore ownership markers")
        start = text.index(IGNORE_START)
        end = text.index(IGNORE_END) + len(IGNORE_END)
        if end <= start or text[start:end] != IGNORE_START + "/.codex-harness/\n" + IGNORE_END:
            raise ValueError("Modified .gitignore ownership block; preserve it manually")
        return text[:start] + text[end:] if remove else text
    if remove or "/.codex-harness/" in text.splitlines():
        return text
    return (
        text
        + ("\n" if text and not text.endswith("\n") else "")
        + (IGNORE_START + "/.codex-harness/\n" + IGNORE_END)
    )


def guide(root, name):
    return (
        f"<!-- {MARKER} -->\n# Seldon: {name}\n\n"
        f"Project: {root}\n\n"
        'Open a chat in this folder and say: "Let\'s plan <your idea> with Seldon".\n\n'
        "Host integrations are experimental until their actual planning journey is verified.\n"
        "Codex: open this folder and choose Trust. Claude Code: approve the project server\n"
        "when prompted. VS Code: review/approve seldon in workspace MCP configuration.\n\n"
        "For the read-only cockpit, run `status_server --help` to see its required plan\n"
        "and server options. Init does not start a cockpit or promise a running URL.\n\n"
        "Check: `seldon init --check`. Remove owned registrations: `seldon init --remove`.\n"
        "Removal preserves existing plan data. No provider has been launched by init.\n"
    )


def server_check(command, root):
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "seldon-init-check", "version": "1"},
        },
    }
    try:
        result = subprocess.run(
            [command, "-I", "-m", "codex_harness.core.seldon_mcp", "--workspace", str(root)],
            input=json.dumps(request) + "\n",
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=15,
            cwd=root,
        )
        response = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise ValueError(f"Installed server handshake failed: {type(error).__name__}") from error
    if (
        result.returncode
        or response.get("id") != 1
        or "serverInfo" not in response.get("result", {})
    ):
        raise ValueError("Installed server handshake failed; reinstall Seldon")


def _atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".seldon-init-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content.encode("utf-8"))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_init(args):
    if args.remove and args.host:
        raise ValueError("--remove removes the project setup; omit --host")
    root = project_root(args.path)
    detected = detect_hosts(root)
    for host, found in detected.items():
        print(f"{host}: {'detected' if found else 'not found'} (existence only)")
    selected = list(HOSTS) if args.host == "all" else [args.host] if args.host else []
    if not selected:
        if args.check or args.remove:
            selected = [
                host
                for host, writer in HOSTS.items()
                if (path := local_path(root, writer.PATH)).exists()
                and read_entry(host, read_text(path)) is not None
            ]
        else:
            selected = [host for host, found in detected.items() if found]
            if not args.yes and selected:
                if not sys.stdin.isatty():
                    raise ValueError("Choose --host explicitly or use --yes for detected hosts")
                answer = input(f"Enable {', '.join(selected)}? [y/N] ")
                if answer.strip().lower() not in {"y", "yes"}:
                    return 1
    if not selected and not args.remove:
        raise ValueError("No registrations selected/found; choose --host")
    command = installed_command() if not args.remove else ""
    entry = server_entry(command, root)
    changes = []
    approval_mode = getattr(args, "approval_mode", None)
    new_grant = False
    if not args.remove:
        print(
            "Permission scope: all current and future Seldon MCP tools, including "
            "worker, acceptance and mail invocations. Approve skips host tool prompts; "
            "prompt requests them. Application Align/Vision, execution, acceptance, "
            "audit, review and publication gates remain unchanged. Stricter tool rules "
            "and host policy still apply. --yes is not permission consent."
        )
    for host in selected:
        writer = HOSTS[host]
        path = local_path(root, writer.PATH)
        before = read_text(path)
        try:
            extra = {"approval_mode": approval_mode} if host == "codex" else {}
            after = writer.render(before, entry, remove=args.remove, yes=args.yes, **extra)
        except ValueError as error:
            if "differs" in str(error) or "unowned" in str(error):
                print(registration_diff(host, before, writer.render(before, entry, yes=True)))
            raise ValueError(f"{path}: {error}") from error
        if args.check:
            if not before or after != before:
                raise ValueError(f"{path}: missing or changed registration, marker or workspace")
        elif after != before:
            changes.append((path, before, after))
        if host == "codex" and not args.remove:
            saved = read_entry(host, after)["default_tools_approval_mode"]
            new_grant |= (
                saved == "approve"
                and (read_entry(host, before) or {}).get("default_tools_approval_mode") != "approve"
            )
            print(f"codex: saved server approval mode {saved}; host effective policy UNKNOWN")
        if host == "claude":
            permissions_path = local_path(root, claude.PERMISSIONS_PATH)
            permissions_before = read_text(permissions_path)
            if args.remove:
                print(
                    "Claude permission settings retained: ownership unknown; "
                    "remove manually if desired."
                )
            else:
                permissions_after, saved, granted = claude.render_permissions(
                    permissions_before, approval_mode
                )
                new_grant |= granted
                if args.check and permissions_after != permissions_before:
                    raise ValueError(f"{permissions_path}: missing or changed saved permissions")
                if not args.check and permissions_after != permissions_before:
                    changes.append((permissions_path, permissions_before, permissions_after))
                print(f"claude: saved server approval mode {saved}; host effective policy UNKNOWN")
        if args.remove and host == "codex" and read_entry(host, after):
            print("Codex policy retained: ownership unknown; remove manually if desired.")
        print(f"{host}: {path} - experimental; {writer.GUIDANCE}")
    if "codex" in selected or args.remove:
        path = local_path(root, "AGENTS.md")
        before = read_text(path)
        after = codex.render_agents(before, remove=args.remove)
        if args.check and after != before:
            raise ValueError(f"{path}: missing or changed owned guidance")
        if args.check:
            print(f"codex: {path} - owned guidance verified")
        if not args.check and after != before:
            changes.append((path, before, after))
    marker_path = local_path(root, STORE_MARKER)
    doc_path = local_path(root, "SELDON.md")
    ignore_path = local_path(root, ".gitignore")
    if args.check:
        if read_text(marker_path) != MARKER + "\n":
            raise ValueError(f"{marker_path}: missing or changed ownership marker")
        if not read_text(doc_path).startswith(f"<!-- {MARKER} -->\n"):
            raise ValueError(f"{doc_path}: missing or changed ownership marker")
        server_check(command, root)
        print("Registration and installed server handshake OK; host planning journey unverified.")
        return 0
    if new_grant and not args.dry_run and approval_mode != "approve":
        if not sys.stdin.isatty():
            raise ValueError(
                "Explicit permission choice required: "
                "--approval-mode approve or --approval-mode prompt"
            )
        answer = input("Approve this server-wide permission scope for this project? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            return 1
    for path, after in (
        (ignore_path, ignore_text(read_text(ignore_path), args.remove)),
        (
            doc_path,
            ""
            if args.remove
            else (
                read_text(doc_path)
                if not args.name and doc_path.exists()
                else guide(root, args.name or root.name)
            ),
        ),
        (marker_path, "" if args.remove else MARKER + "\n"),
    ):
        before = read_text(path)
        if (
            path != ignore_path
            and before
            and not before.startswith(f"<!-- {MARKER} -->\n" if path == doc_path else MARKER + "\n")
        ):
            if args.remove:
                continue
            raise ValueError(f"{path}: unowned file exists; preserve it manually")
        if before != after:
            changes.append((path, before, after))
    for path, before, after in changes:
        # Avoid printing unrelated host configuration, which can contain secrets.
        print(f"{'Would update' if args.dry_run else 'Update'}: {path}")
        if args.dry_run:
            print(after)
        elif path.relative_to(root).as_posix() in {writer.PATH for writer in HOSTS.values()}:
            host = next(name for name, writer in HOSTS.items() if root / writer.PATH == path)
            print(registration_diff(host, before, after))
        elif path.name in {"SELDON.md", ".gitignore", ".seldon-init"}:
            print("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True))))
    if not args.dry_run:
        # Validate every target before the first write. Per-file replacements are atomic.
        for path, _, _ in changes:
            local_path(root, path.relative_to(root))
        for path, _, after in changes:
            if args.remove and not after and path in {doc_path, marker_path}:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, after)
        if args.remove and marker_path.parent.exists() and not any(marker_path.parent.iterdir()):
            marker_path.parent.rmdir()
    if not args.remove:
        report = preflight_report(root)
        offered = [p["provider"] for p in report["providers"] if p["executable"]["path"]]
        print(f"Provider executables found (offered, not verified): {', '.join(offered) or 'none'}")
        print("Configuration prepared; actual host planning journey remains unverified.")
        print('Next: open a chat here and say "Let\'s plan <your idea> with Seldon".')
    else:
        print("Removed owned registrations; plan data and permission policies preserved.")
    return 0
