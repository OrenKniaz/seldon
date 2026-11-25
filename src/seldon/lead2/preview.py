"""C20/C26: open an isolated preview with the startup the walker exercised."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from urllib.parse import urlsplit

from .. import desktop, gitwork


def launch_recipe(evidence, files):
    try:
        path = evidence / "preview-launch.json"
        if path.resolve().parent != evidence.resolve():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        argv, ready = value["argv"], value["ready"]
        if not (isinstance(argv, list) and all(isinstance(arg, str) for arg in argv)
                and len(argv) >= 2 and isinstance(ready, str) and ready.startswith("/")
                and not ready.startswith("//")):
            return None
        runtime = Path(argv[0]).stem.casefold()
        args = [arg for arg in argv[1:] if arg not in ("-B", "-u")]
        if runtime not in ("python", "python3", "node") or not args or args[0] not in files:
            return None
        if "127.0.0.1" not in argv or not any("{port}" in arg for arg in argv):
            return None
        return {"argv": [runtime, *argv[1:]], "ready": ready}
    except (OSError, ValueError, TypeError, KeyError):
        return None


@dataclass
class Preview:
    project: Path
    workspace: Path
    folder: Path
    process: subprocess.Popen

    def url(self):
        try:
            url = (self.folder / "url").read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        parts = urlsplit(url)
        return url if parts.scheme == "http" and parts.hostname == "127.0.0.1" and parts.port else None

    def close(self):
        self.process.stdin.close()
        self.process.wait()
        gitwork.git(self.project, "worktree", "remove", "--force", str(self.workspace))


def start(project, scratch, candidate, launch):
    folder = Path(mkdtemp(prefix="preview-", dir=scratch))
    workspace = Path(gitwork.admit(project, folder, candidate))
    runtime, *args = launch["argv"]
    node = shutil.which("node")
    try:
        if not node:
            raise OSError("Node is required for the verified preview launcher.")
        portable = {**launch, "argv": [node if runtime == "node" else sys._base_executable, *args]}
        kit = Path(__file__).resolve().parents[1] / "kit" / "serve.cjs"
        env = desktop.environment({key: str(folder) for key in ("TMP", "TEMP", "TMPDIR")})
        env.pop("SELDON_WALK_ACTIVITY", None)
        env.pop("SELDON_EVIDENCE_DIR", None)
        with (folder / "startup.log").open("ab") as log:
            process = subprocess.Popen(
                [node, str(kit), json.dumps(portable), str(folder / "url")], cwd=workspace, env=env,
                stdin=subprocess.PIPE, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except OSError:
        gitwork.git(project, "worktree", "remove", "--force", str(workspace))
        raise
    return Preview(project, workspace, folder, process)
