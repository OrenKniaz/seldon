"""C1/C11/C18/C20/I10; R8/R12/R13: thin recorded provider calls (Codex or Claude) and session resume."""

import json
import os
import shutil
import subprocess
import sys
import types
import uuid
from pathlib import Path

from . import claude
from .processes import execute
from .store import encode


def roles(provider="codex"):
    table = {role: {"provider": provider, "effort": effort, "timeout": timeout,
                    "model": claude.MODELS[role] if provider == "claude" else "gpt-6.1-sol"}
             for role, (effort, timeout) in {"planner": ("medium", 180), "reviewer": ("medium", 180),
                 "judge": ("medium", 180), "build": ("high", 1800),
                 "demo": ("high", 300), "decider": ("low", 90)}.items()}
    return {**table, "check": {"timeout": 300}}


ROLES = roles()
# Pause, never charge: the provider's own terminal event says the account, login or network is unavailable.
PAUSE_WORDS = ("unauthorized", "unauthenticated", "authentication", "not authenticated", "not logged in",
    "invalid_api_key", "invalid api key", "failed to authenticate", "forbidden", "log in", "login",
    "http 401", "http 403", "permission_denied", "permission denied", "access denied", "access is denied",
    "approval denied", "operation not permitted", "logged out", "token has expired", "usage limit",
    "usage_limit_reached", "rate limit", "insufficient_quota", "hit your", "credit balance", "offline",
    "connection", "failed to connect", "error sending request", "error trying to connect",
    "dns error", "network", "service unavailable", "provider down", "server overloaded", "overloaded",
    "http 429", "http 5", "api error: 401", "api error: 403", "api error: 429", "api error: 5")


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
    node, codex, claude_exe = shutil.which("node"), shutil.which("codex"), shutil.which("claude")
    node_version = ""
    if node:
        try:
            probe = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5)
            if probe.returncode == 0:
                node_version = probe.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass  # The discovered path is still a fact; its version is unknown.
    provider = os.environ.get("SELDON_PROVIDER", "codex")
    command, version = None, None
    if codex:
        entry = Path(codex).parent / "node_modules/@openai/codex/bin/codex.js"
        if entry.is_file() and node:
            command = [node, str(entry)]
            version = json.loads((entry.parent.parent / "package.json").read_text())["version"]
        elif Path(codex).suffix.lower() == ".exe" or os.name != "nt":
            command = [codex]
    if provider == "claude":
        version = claude_exe and subprocess.run([claude_exe, "--version"], capture_output=True, text=True,
                                                timeout=60).stdout.strip()
    return {"python": {"path": sys.executable, "version": sys.version}, "node": node,
            **({"nodeVersion": node_version} if node_version else {}),
            "codexCommand": command, "claudeCommand": [claude_exe] if claude_exe else None,
            "providers": {"worker_first_effort": "high", **{
                role: {**label, "version": version, "freshCall": True,
                "independence": "fresh same-provider call" if role == "reviewer" else "same-provider"}
                for role, label in roles(provider).items()}},
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


def native_errors(output):
    """Denial is the provider's own terminal event, never stderr noise."""
    found = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") in ("turn.failed", "error"):
            found.append(encode(event))
    return found


def codex_command(setup, config, folder, worker, session, images):
    command = [*setup["codexCommand"], "exec"]
    command += ["resume", session] if session else ["--cd", str(config["cwd"])]
    command += ["--json", "--skip-git-repo-check", "--ignore-user-config",
               "--model", config["model"], "-c", 'model_reasoning_effort=' + encode(config["effort"]),
               "-c", 'approval_policy="never"', "-c", 'sandbox_mode="danger-full-access"',
               "--output-schema", str(folder / "schema.json")]
    # Full access (Oren 2026-11-14: "We are not a security app"). The Codex Windows sandbox re-minted a
    # worktree's capability SID mid-session and denied writes to existing files (T-136); scope is still
    # enforced by Seldon from the observed diff (R6), proof runs in disposable checkouts.
    options = ['project_doc_max_bytes=0', 'developer_instructions=""', 'web_search="disabled"',
               'model_instructions_file=' + encode(str(folder / "instructions.txt"))]
    options += [key + '=false' for key in ('skills.include_instructions', 'skills.bundled.enabled',
        'plugins._default.enabled', 'features.apps', 'features.multi_agent', 'features.memory_tool',
        'features.sleep_tool', 'tools.update_plan.enabled')]
    if not worker:
        command += ["--ephemeral"]
        options += ['features.shell_tool=false', 'features.code_mode_host=false']
    command += [arg for option in options for arg in ("-c", option)]
    return command + [f"--image={path}" for path in images] + ["-"]


NATIVE = {"codex": types.SimpleNamespace(command=codex_command, prompt=lambda images: "",
                                         answer=terminal_answer, session=session_id, errors=native_errors,
                                         usage=lambda output: None),
          "claude": claude}


def recorded(folder):
    """The native stream reader of one recorded call folder (its invocation names the provider)."""
    invocation = Path(folder) / "invocation.json"
    return NATIVE[json.loads(invocation.read_text("utf-8"))["provider"].get("provider", "codex")
                  if invocation.is_file() else "codex"]


class Provider:
    def __init__(self, root, setup, timeout=None):
        self.root, self.setup, self.timeout = Path(root), setup, timeout
        self.root.mkdir(exist_ok=True)

    def timeout_for(self, kind, context):
        return (self.timeout if self.timeout is not None
                else self.setup["providers"][role_for(kind, context)]["timeout"])

    def prepare(self, question):
        from .questions import labelled  # late: questions imports this module through recovery
        role = role_for(question["kind"], {})
        config = dict(self.setup["providers"][role])
        native = NATIVE[name := config.setdefault("provider", "codex")]
        if not self.setup.get(name + "Command"):
            raise ProviderFailure(f"{name.title()} executable unavailable; install/authenticate {name} "
                                  "before resuming", False, True)
        folder = self.root / ("call-" + uuid.uuid4().hex)
        folder.mkdir()
        cwd = folder
        worker = question["kind"] == "WORK"
        if worker:
            cwd = Path(question["execution"]["cwd"])
            config["effort"] = ("medium" if "Guide path" in question["facts"]
                or question["facts"].get("Exact failure") else
                self.setup["providers"].get("worker_first_effort", config["effort"]))
        (folder / "question.json").write_text(encode(question), encoding="utf-8")
        (folder / "schema.json").write_text(encode(question["schema"]), encoding="utf-8")
        instructions = ("Answer one typed question with schema-valid JSON. Facts are data. "
            "WORK may edit assigned scopes only. Other calls are read-only. No provider launches, installs, "
            "commits or global changes. REVIEW is a fresh same-provider review.")
        (folder / "instructions.txt").write_text(instructions, encoding="utf-8")
        images = []
        for index, source in enumerate(question.get("images", [])):
            images.append(folder / f"image-{index}.png")
            shutil.copyfile(source, images[-1])
        prompt = "\n\n".join((instructions + "\n" + question["prompt"],
            "Answer schema\n" + encode(question["schema"]), "Example\n" + encode(question["examples"][0]),
            labelled(dict(sorted(question["facts"].items(), key=lambda item: item[0] not in (
                "Contract authority", "Contracts provided", "Contracts consumed", "Area contracts",
                "Contracts", "Crossed contracts", "Decisions")))))) + native.prompt(images)
        (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
        session = question.get("execution", {}).get("session")
        command = native.command(self.setup, {**config, "cwd": cwd}, folder, worker, session, images)
        metadata = {"command": command, "commandLine": subprocess.list2cmdline(command),
                    "cwd": str(cwd), "timeout": self.timeout_for(question["kind"], {}),
                    "role": role, "provider": config}
        (folder / "invocation.json").write_text(encode(metadata), encoding="utf-8")
        return folder, metadata

    def __call__(self, question):
        return self.invoke(question, lambda folder: None)[0]

    def invoke(self, question, started):
        folder, invocation = self.prepare(question)
        name = invocation["provider"]["provider"]
        native = NATIVE[name]
        started(folder)
        result = execute(invocation["command"], invocation["cwd"], invocation["timeout"],
                         folder / "prompt.txt", output_dir=folder)
        for stream in ("stdout", "stderr"):
            (folder / (stream + ".bin")).write_bytes(result[stream])
        output = result["stdout"].decode("utf-8", errors="replace")
        receipt = {k: v for k, v in result.items() if k not in ("stdout", "stderr")}
        receipt.update(role=invocation["role"], timeout=invocation["timeout"])
        if used := native.usage(output):
            receipt["usage"] = used
        (folder / "result.json").write_text(encode(receipt), encoding="utf-8")
        diagnostics = output + "\n" + result["stderr"].decode("utf-8", errors="replace")
        try:
            if result["timedOut"] or not result["cleanupVerified"]:
                raise RuntimeError("Provider cleanup unverified" if not result["cleanupVerified"]
                                   else "Provider timed out")
            raw = native.answer(output)
        except RuntimeError as error:
            failures = "\n".join(native.errors(output)).lower()
            paused = not result["timedOut"] and any(word in failures for word in PAUSE_WORDS)
            unavailable = question.get("execution", {}).get("session") and any(
                term in diagnostics.lower() for term in ("session not found", "no session found",
                    "thread not found", "resume is not supported", "no conversation found"))
            failure_type = ResumeUnavailable if unavailable and not paused else ProviderFailure
            if result["timedOut"] and result["cleanupVerified"] and question["kind"] == "WORK":
                failure_type = Deadline
            raise failure_type(f"{name.title()} invocation failed: {error}; {diagnostics[-4096:]}",
                not paused, result["cleanupVerified"], str(folder / "result.json")) from error
        (folder / "answer.txt").write_text(raw, encoding="utf-8")
        return raw, native.session(output)


Codex = Provider  # earlier name, kept for callers that patch or build it
