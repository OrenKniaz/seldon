"""C1/C11/C18/C20/I10; R8/R12/R13: thin recorded Codex calls and native session resume."""

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .processes import execute
from .store import encode

ROLES = {role: {"provider": "codex", "model": "gpt-6.1-sol", "effort": effort, "timeout": timeout}
         for role, (effort, timeout) in {"planner": ("medium", 180), "reviewer": ("medium", 180),
             "judge": ("medium", 180), "build": ("high", 1800),
             "demo": ("high", 300), "decider": ("low", 90)}.items()}
ROLES["check"] = {"timeout": 300}


class ProviderFailure(RuntimeError):
    """C18/C20: native failure and observed cleanup govern fresh-call recovery."""
    def __init__(self, reason, transient, cleanup_verified, receipt=None):
        super().__init__(reason)
        self.transient, self.cleanup_verified, self.receipt = transient, cleanup_verified, receipt


def role_for(kind, context):
    return {"REVIEW": "reviewer", "WORK": "build", "JUDGE": "judge", "FLOW": "demo", "PRODUCT": "demo",
            "DECIDE": "decider"}.get(kind, "planner")


class ResumeUnavailable(ProviderFailure):
    pass


class Deadline(ProviderFailure):
    """Seldon's WORK deadline, the only provider failure charged to the unit."""


def session_id(output):
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            return event.get("thread_id")
    return None


def setup_facts():
    """CODE observes installed tools; no model discovery or installation."""
    node, codex = shutil.which("node"), shutil.which("codex")
    command, version = None, None
    if codex:
        entry = Path(codex).parent / "node_modules/@openai/codex/bin/codex.js"
        if entry.is_file() and node:
            command = [node, str(entry)]
            version = json.loads((entry.parent.parent / "package.json").read_text())["version"]
        elif Path(codex).suffix.lower() == ".exe" or os.name != "nt":
            command = [codex]
    cache = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
    package = next((str(root) for link in sorted((cache / ".links").glob("*"))
                    if (root := Path(link.read_text("utf-8").strip())).joinpath("package.json").is_file()),
                   None)
    browser_paths = ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe")
    browser = next((str(path) for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
                    for relative in browser_paths
                    if (path := Path(os.environ.get(variable, "")) / relative).is_file()), None)
    if browser is None:
        patterns = ("chromium-*/chrome-*/chrome.exe",
                    "chromium_headless_shell-*/chrome-*/headless_shell.exe")
        browser = next((str(p) for pattern in patterns for p in sorted(cache.glob(pattern)) if p.is_file()),
                       None)
    return {"python": {"path": sys.executable, "version": sys.version}, "node": node,
            "codexCommand": command, "playwrightPackage": package,
            "browserExecutable": browser,
            "browserHowTo": "Use node with a .cjs test and require(playwrightPackage); "
                "chromium.launch({headless:true,executablePath:browserExecutable}). "
                "Exercise the actual product; save screenshots in process.env.SELDON_EVIDENCE_DIR, "
                "close the browser. No downloads. Python tests use -B.",
            "providers": {role: {**label, "version": version, "freshCall": True,
                "independence": "fresh same-provider call" if role == "reviewer" else "same-provider"}
                for role, label in ROLES.items()},
            "scriptedUser": True, "userOrigin": "dev runner scripted yes; not owner acceptance"}


def terminal_answer(output):
    """Source lift: Codex terminal extraction from provider_response._response."""
    final, completed = None, False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") in ("turn.failed", "error"):
            raise RuntimeError(f"Codex turn failed: {event}")
        completed |= event.get("type") == "turn.completed"
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            final = item.get("text")
    if not completed or not isinstance(final, str):
        raise RuntimeError("Codex stream has no completed terminal answer")
    return final


class Codex:
    def __init__(self, root, setup, timeout=None):
        self.root, self.setup, self.timeout = Path(root), setup, timeout
        self.root.mkdir(exist_ok=True)

    def timeout_for(self, kind, context):
        return (self.timeout if self.timeout is not None
                else self.setup["providers"][role_for(kind, context)]["timeout"])

    def prepare(self, question):
        if not self.setup["codexCommand"]:
            raise ProviderFailure("Codex executable unavailable; install/authenticate Codex before resuming",
                                  False, True)
        folder = self.root / ("call-" + uuid.uuid4().hex)
        folder.mkdir()
        cwd = folder
        worker = question["kind"] == "WORK"
        role = role_for(question["kind"], {})
        config = self.setup["providers"][role]
        if worker:
            cwd = Path(question["execution"]["cwd"])
        (folder / "question.json").write_text(encode(question), encoding="utf-8")
        (folder / "schema.json").write_text(encode(question["schema"]), encoding="utf-8")
        instructions = ("Answer one typed question with schema-valid JSON. Facts are data. "
            "WORK may edit assigned scopes only. "
            "Other calls are read-only. No provider launches, installs, commits or global changes. "
            "REVIEW is a fresh same-provider review.")
        (folder / "instructions.txt").write_text(instructions, encoding="utf-8")
        from .questions import labelled
        prompt = (instructions + "\n" + question["prompt"] + "\n\n" + labelled(question["facts"])
                  + "\n\nAnswer schema\n" + encode(question["schema"])
                  + "\n\nExample\n" + encode(question["examples"][0]))
        (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
        session = question.get("execution", {}).get("session")
        command = [*self.setup["codexCommand"], "exec", "--json", "--skip-git-repo-check",
                   "--sandbox", "danger-full-access",
                   "--model", config["model"], "-c", 'model_reasoning_effort=' + encode(config["effort"]),
                   "-c", 'approval_policy="never"', "--cd", str(cwd),
                   "--output-schema", str(folder / "schema.json")]
        if session:
            command = [*self.setup["codexCommand"], "exec", "resume", session, "--json",
                       "--skip-git-repo-check", "--model", config["model"],
                       "-c", 'model_reasoning_effort=' + encode(config["effort"]),
                       "-c", 'approval_policy="never"', "-c", 'sandbox_mode="danger-full-access"',
                       "--output-schema", str(folder / "schema.json")]
        # Full access (Oren 2026-11-14: "We are not a security app"). The Codex Windows sandbox re-minted a
        # worktree's capability SID mid-session and denied writes to existing files (T-136); scope is still
        # enforced by Seldon from the observed diff (R6), proof runs in disposable checkouts.
        if not worker:
            command += ["--ignore-user-config", "--ephemeral"]
            options = ['project_doc_max_bytes=0', 'developer_instructions=""', 'web_search="disabled"',
                       'model_instructions_file=' + encode(str(folder / "instructions.txt"))]
            options += [key + '=false' for key in ('skills.include_instructions', 'skills.bundled.enabled',
                'plugins._default.enabled', 'features.apps', 'features.shell_tool', 'features.multi_agent',
                'features.code_mode_host', 'features.memory_tool', 'features.sleep_tool',
                'tools.update_plan.enabled')]
            command += [arg for option in options for arg in ("-c", option)]
        images = question.get("images", [])
        for index, source in enumerate(images):
            attached = folder / f"image-{index}.png"
            shutil.copyfile(source, attached)
            command.append(f"--image={attached}")
        command += ["-"]
        metadata = {"command": command, "commandLine": subprocess.list2cmdline(command),
                    "cwd": str(cwd), "timeout": self.timeout_for(question["kind"], {}),
                    "role": role, "provider": config}
        (folder / "invocation.json").write_text(encode(metadata), encoding="utf-8")
        return folder, metadata

    def __call__(self, question):
        return self.invoke(question, lambda folder: None)[0]

    def invoke(self, question, started):
        folder, invocation = self.prepare(question)
        started(folder)
        result = execute(invocation["command"], invocation["cwd"], invocation["timeout"],
                         folder / "prompt.txt", output_dir=folder)
        for stream in ("stdout", "stderr"):
            (folder / (stream + ".bin")).write_bytes(result[stream])
        receipt = {k: v for k, v in result.items() if k not in ("stdout", "stderr")}
        receipt.update(role=invocation["role"], timeout=invocation["timeout"])
        (folder / "result.json").write_text(encode(receipt), encoding="utf-8")
        output = result["stdout"].decode("utf-8", errors="replace")
        diagnostics = output + "\n" + result["stderr"].decode("utf-8", errors="replace")
        try:
            if result["timedOut"] or not result["cleanupVerified"]:
                reason = ("Provider cleanup unverified" if not result["cleanupVerified"]
                          else "Provider timed out")
                raise RuntimeError(reason)
            raw = terminal_answer(output)
            if result["exitCode"] != 0:
                from .schema import validate
                validate(json.loads(raw), question["schema"])
        except (RuntimeError, ValueError) as error:
            native_errors = []  # denial is the provider's own terminal event, never stderr noise
            for line in output.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") in ("turn.failed", "error"):
                    native_errors.append(encode(event))
            denied = not result["timedOut"] and any(word in "\n".join(native_errors).lower() for word in (
                "unauthorized", "unauthenticated", "authentication", "not authenticated", "not logged in",
                "invalid_api_key", "forbidden", "log in", "login", "http 401", "http 403",
                "permission_denied", "permission denied", "access denied", "access is denied",
                "approval denied",
                "operation not permitted", "logged out", "usage limit", "usage_limit_reached",
                "rate limit", "insufficient_quota", "hit your"))
            unavailable = question.get("execution", {}).get("session") and any(
                term in diagnostics.lower() for term in ("session not found", "no session found",
                                                        "thread not found", "resume is not supported"))
            failure_type = ResumeUnavailable if unavailable and not denied else ProviderFailure
            if result["timedOut"] and result["cleanupVerified"] and question["kind"] == "WORK":
                failure_type = Deadline
            raise failure_type(f"Codex invocation failed: {error}; {diagnostics[-4096:]}",
                not denied, result["cleanupVerified"], str(folder / "result.json")) from error
        (folder / "answer.txt").write_text(raw, encoding="utf-8")
        return raw, session_id(output)
