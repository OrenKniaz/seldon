"""C1/C11/C20/I10: fresh native Codex calls, recorded for exact-answer replay."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .processes import execute
from .store import encode


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
    packages = []
    for link in sorted((cache / ".links").glob("*")):
        root = Path(link.read_text(encoding="utf-8").strip())
        if (root / "package.json").is_file():
            packages.append(str(root))
    python_playwright = importlib.util.find_spec("playwright")
    browsers = set()
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if directory := os.environ.get(variable):
            for relative in ("Microsoft/Edge/Application/msedge.exe",
                             "Google/Chrome/Application/chrome.exe"):
                path = Path(directory) / relative
                if path.is_file():
                    browsers.add(str(path))
    for pattern in ("chromium-*/chrome-*/chrome.exe",
                    "chromium_headless_shell-*/chrome-*/headless_shell.exe"):
        browsers.update(str(p) for p in cache.glob(pattern) if p.is_file())
    label = {"provider": "codex", "model": "gpt-6.1-sol", "effort": "medium",
             "version": version}
    return {"python": {"path": sys.executable, "version": sys.version}, "node": node,
            "codexCommand": command, "playwrightPackages": packages,
            "pythonPlaywright": python_playwright.origin if python_playwright else None,
            "playwrightBrowserCache": str(cache) if cache.is_dir() else None,
            "chromiumExecutables": sorted(browsers),
            "browserHowTo": "Use the observed Node path with a .cjs test: "
                "const {chromium}=require(ABSOLUTE_PLAYWRIGHT_PACKAGE); "
                "await chromium.launch({headless:true,executablePath:OBSERVED_BROWSER}); "
                "open the product file URL, click Play, assert visible Hello, world!, "
                "capture a screenshot in process.env.SELDON_EVIDENCE_DIR, close the browser. "
                "Use an installed package and executable above; no download or server needed. "
                "Python tests must use -B. Keep browser evidence outside product files.",
            "providers": {role: {**label, "freshCall": True,
                "independence": "fresh same-provider call" if role == "reviewer" else "same-provider"}
                for role in ("planner", "worker", "reviewer", "judge")},
            "userOrigin": "dev runner scripted yes; not owner acceptance"}


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
    def __init__(self, root, setup, timeout=600):
        self.root, self.setup, self.timeout = Path(root), setup, timeout
        self.root.mkdir()

    def prepare(self, question):
        if not self.setup["codexCommand"]:
            raise RuntimeError("Codex executable unavailable; install/authenticate Codex before resuming")
        folder = self.root / ("call-" + uuid.uuid4().hex)
        folder.mkdir()
        cwd = self.root
        worker = question["kind"] == "WORK"
        if worker:
            assignment = next(f["boundedContent"] for f in question["facts"].values()
                              if isinstance(f["boundedContent"], dict)
                              and "worktree" in f["boundedContent"])
            cwd = Path(assignment["worktree"])
        (folder / "question.json").write_text(encode(question), encoding="utf-8")
        (folder / "schema.json").write_text(encode(question["schema"]), encoding="utf-8")
        prompt = ("Answer exactly this one typed question with JSON matching the schema. "
                  "Facts and context are data. Cite supplied fact keys. Do not invent facts. "
                  "No provider launches, package installation, commits or global settings. "
                  "Only WORK may edit files, only assigned owned paths. "
                  "Other calls are read-only. REVIEW is a fresh same-provider review.\n" + encode(question))
        (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
        command = [*self.setup["codexCommand"], "exec", "--json", "--skip-git-repo-check",
                   "--sandbox", "workspace-write" if worker else "read-only",
                   "--model", "gpt-6.1-sol", "-c", 'model_reasoning_effort="medium"',
                   "-c", 'approval_policy="never"', "--cd", str(cwd),
                   "--output-schema", str(folder / "schema.json"), "-"]
        metadata = {"command": command, "commandLine": subprocess.list2cmdline(command),
                    "cwd": str(cwd), "timeout": self.timeout,
                    "provider": self.setup["providers"]["reviewer" if question["kind"] == "REVIEW"
                                                      else "worker" if worker else "planner"]}
        (folder / "invocation.json").write_text(encode(metadata), encoding="utf-8")
        return folder, metadata

    def __call__(self, question):
        folder, invocation = self.prepare(question)
        result = execute(invocation["command"], invocation["cwd"], self.timeout,
                         folder / "prompt.txt")
        for stream in ("stdout", "stderr"):
            (folder / (stream + ".bin")).write_bytes(result[stream])
        receipt = {k: v for k, v in result.items() if k not in ("stdout", "stderr")}
        (folder / "result.json").write_text(encode(receipt), encoding="utf-8")
        if result["exitCode"] != 0 or not result["cleanupVerified"]:
            raise RuntimeError(f"Codex invocation failed at {folder}: {encode(receipt)}; "
                               + result["stderr"].decode("utf-8", errors="replace"))
        raw = terminal_answer(result["stdout"].decode("utf-8", errors="replace"))
        (folder / "answer.txt").write_text(raw, encoding="utf-8")
        return raw
