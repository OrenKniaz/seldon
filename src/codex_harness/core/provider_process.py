"""The existing bounded provider process path and its retained cleanup observation."""

from pathlib import Path
from tempfile import TemporaryFile
import os
from codex_harness.core.audit_process import run_audit_process


def _run(
    launch,
    cwd: Path,
    timeout: int,
    *,
    raw=False,
    launch_guard=None,
    stop_when=None,
    with_control=False,
):
    with TemporaryFile() as prompt:
        if launch.stdin_text is not None:
            prompt.write(launch.stdin_text.encode("utf-8"))
            prompt.seek(0)
        completed = run_audit_process(
            launch.command,
            cwd=cwd,
            env={**os.environ, **launch.environment},
            stdin=prompt,
            text=False,
            timeout=timeout,
            launch_guard=launch_guard,
            stop_when=stop_when,
        )
    result = (
        completed.returncode,
        completed.stdout if raw else completed.stdout.decode("utf-8"),
        completed.stderr if raw else completed.stderr.decode("utf-8"),
    )
    return (*result, completed.audit_control) if with_control else result
