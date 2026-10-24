"""Actionable terminal failure must reach planner mail while a sibling is held."""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from tests.python.core.execution_support import source_execution_context
from tests.python.core.test_dispatch import _tiny_plan
from tests.python.core.test_dispatch_cancel import source_only_packet_renderer

from codex_harness.core import dispatch
from codex_harness.core.mail import Mailbox
from codex_harness.core.provider_launch import ProviderNormalLaunch
from codex_harness.core.record_io import read_record_json


@pytest.mark.parametrize("failure_kind", ["nonzero", "packet-refusal"])
def test_terminal_failure_is_mailed_before_independent_sibling_finishes(
    tmp_path, monkeypatch, failure_kind
):
    # Existing mail tests read only after completion and include a dependent bead.
    # They miss a terminal A silently waiting for unrelated B to finish. The
    # nonzero case uses an actual child; packet refusal injects only that source
    # input error, never a mailbox answer or terminal record.
    plan = _tiny_plan(tmp_path)
    source_only_packet_renderer(monkeypatch)
    context = source_execution_context(tmp_path / "canonical", plan, timeout=60)
    ready, release = tmp_path / "B-ready", tmp_path / "release-B"
    notified = threading.Event()
    original_notify = dispatch._notify_failures

    def observe_notification(record, evidence):
        original_notify(record, evidence)
        if record["beads"]["A"]["status"].startswith(("provider ", "prelaunch failed:")):
            notified.set()  # Observe completion of the real event producer.

    monkeypatch.setattr(dispatch, "_notify_failures", observe_notification)
    build_packet = dispatch.worker_packet.build_packet

    def packet(*args, **kwargs):
        if failure_kind == "packet-refusal" and args[1]["id"] == "A":
            raise ValueError("SOURCE-ONLY injected packet refusal")
        return build_packet(*args, **kwargs)

    monkeypatch.setattr(dispatch.worker_packet, "build_packet", packet)

    class LocalAdapter:
        def provider_normal_command(self, *, prompt, **kwargs):
            if "implement bead A." in prompt:
                script = (
                    "# SOURCE-ONLY local process fixture\nimport sys\n"
                    'print(\'{"type":"turn.completed","isMocked":true}\',flush=True)\n'
                    "sys.exit(7)\n"
                )
            else:
                script = (
                    "# SOURCE-ONLY local process fixture\nfrom pathlib import Path\n"
                    "import time,sys\n"
                    f"ready=Path({str(ready)!r}); release=Path({str(release)!r})\n"
                    "ready.write_text('held')\ndeadline=time.monotonic()+45\n"
                    "while not release.exists() and time.monotonic()<deadline: time.sleep(0.02)\n"
                    "if not release.exists(): sys.exit(9)\n"
                    "Path('B.py').write_text('VALUE = 1\\n')\n"
                    'print(\'{"type":"turn.completed","isMocked":true}\',flush=True)\n'
                )
            return ProviderNormalLaunch(
                cli="codex",
                command=(sys.executable, "-B", "-c", script),
                protections=("SOURCE-ONLY finite local child; no coding provider",),
            )

    monkeypatch.setattr(dispatch, "get_adapter", lambda _: LocalAdapter())
    terminal, before, replay, snapshot = None, [], [], None
    producer_called_before_release = False
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            dispatch.run_plan, context["plan"], work_root=context["runs"], **context["kwargs"]
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if future.done():
                    pytest.fail(
                        f"Local run ended before controlled release: {future.result()['status']}"
                    )
                paths = list(context["runs"].glob("*/evidence/run.json"))
                if paths and ready.exists():
                    candidate = read_record_json(paths[0])
                    if (
                        candidate["beads"]["A"]["status"].startswith(
                            ("provider ", "prelaunch failed:")
                        )
                        and candidate["beads"]["B"]["status"] == "running"
                    ):
                        snapshot = candidate
                        break
                time.sleep(0.02)
            assert snapshot is not None, "A must be terminal while actual B is held"
            producer_called_before_release = notified.wait(timeout=0.5)
            mailbox = Mailbox(snapshot["memoryWorkspace"])
            before = [row for row in mailbox.inbox("planner") if row["subject"] == "Replan: A"]
            assert not future.done() and not release.exists()
            assert len(before) == 1, "Actionable A terminal failure is missing while B remains held"
            payload = json.loads(before[0]["body"])
            assert payload["reason"] == snapshot["beads"]["A"]["status"]
            assert payload["runId"] == snapshot["runId"]
            assert all(Path(path).exists() for path in payload["evidencePaths"])
            # The existing canonical event key, not a new receipt/id scheme,
            # must survive both immediate replay and eventual plan completion.
            original_notify(snapshot, Path(paths[0]).parent)
            replay = [row for row in mailbox.inbox("planner") if row["subject"] == "Replan: A"]
            assert [row["id"] for row in replay] == [row["id"] for row in before]
        finally:
            release.write_text("released in finally")
            terminal = future.result(timeout=25)
            (tmp_path / "terminal-mail-evidence.json").write_text(
                json.dumps(
                    dict(
                        sourceOnly=True,
                        nativeProviderCalls=0,
                        failureKind=failure_kind,
                        producerCalledBeforeRelease=producer_called_before_release,
                        observedWhileHeld=snapshot,
                        mailBeforeRelease=before,
                        mailAfterReplay=replay,
                        terminalStatus=terminal["status"],
                        siblingStatus=terminal["beads"]["B"]["status"],
                        releaseWasWritten=release.exists(),
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
    mailbox = Mailbox(terminal["memoryWorkspace"])
    final = [row for row in mailbox.inbox("planner") if row["subject"] == "Replan: A"]
    assert [row["id"] for row in final] == [row["id"] for row in before]
