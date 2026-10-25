"""Actionable terminal failure must reach planner mail while a sibling is held."""

import hashlib
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
from codex_harness.core.status_api import snapshot


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


@pytest.mark.parametrize(
    "candidate_kind",
    [
        "placeholder",
        "repaired",
        "unparseable",
        "report-write",
        "report-read",
        "mail",
        "uncertain-mail",
    ],
)
def test_candidate_steering_reaches_planner_before_real_fast_forward(
    tmp_path, monkeypatch, candidate_kind
):
    # The checker-only tests never cross candidate production, proof, durable
    # report, mail and public board. Unrelated passing proof can miss an API stub.
    plan = _tiny_plan(tmp_path)
    manifest_path = plan / "sample.json"
    manifest = read_record_json(manifest_path)
    if candidate_kind != "mail":
        manifest["beads"].pop("B")
    manifest["beads"]["A"]["ownedFiles"].append("extra.py")
    manifest_path.write_text(json.dumps(manifest))
    (plan / "template/extra.py").write_text("# Source-only extra module\n")
    source_only_packet_renderer(monkeypatch)
    context = source_execution_context(tmp_path / "canonical", plan, timeout=60)
    faults = {"report-write", "report-read", "mail", "uncertain-mail"}
    body = "pass" if candidate_kind == "placeholder" or candidate_kind in faults else "return 42"
    source = f"VALUE = 1\ndef public_api():\n    {body}\n"
    extra = "def broken(:\n" if candidate_kind == "unparseable" else "# Source-only extra module\n"
    launch = ProviderNormalLaunch(
        cli="codex",
        command=(
            sys.executable,
            "-B",
            "-c",
            "# SOURCE-ONLY local process fixture\nfrom pathlib import Path\n"
            f"Path('A.py').write_text({source!r})\nPath('extra.py').write_text({extra!r})\n"
            'print(\'{"type":"turn.completed","isMocked":true}\',flush=True)\n',
        ),
        protections=("SOURCE-ONLY finite local child; no coding provider",),
    )

    class LocalAdapter:
        def provider_normal_command(self, **kwargs):
            if "implement bead B." in kwargs["prompt"]:
                return ProviderNormalLaunch(
                    cli="codex",
                    command=(
                        sys.executable,
                        "-B",
                        "-c",
                        "# SOURCE-ONLY local process fixture\nfrom pathlib import Path\n"
                        f"release=Path({str(tmp_path / 'release-B')!r})\n"
                        "import time,sys\ndeadline=time.monotonic()+20\n"
                        "while not release.exists() and time.monotonic()<deadline:\n"
                        "    time.sleep(.02)\n"
                        'print(\'{"type":"turn.completed","isMocked":true}\',flush=True)\n'
                        "sys.exit(7)\n",
                    ),
                    protections=("SOURCE-ONLY finite sibling failure; no coding provider",),
                )
            return launch

    monkeypatch.setattr(dispatch, "get_adapter", lambda _: LocalAdapter())
    write_record, read_bytes, send_event = (
        dispatch._write_record,
        Path.read_bytes,
        Mailbox.send_event,
    )
    fault_active, mail_attempts = [True], []

    def write(path, record):
        if candidate_kind == "report-write" and Path(path).name == "steering.json":
            raise PermissionError("Controlled advisory report write refusal")
        return write_record(path, record)

    def read(path):
        if candidate_kind == "report-read" and path.name == "steering.json":
            raise PermissionError("Controlled advisory digest read refusal")
        return read_bytes(path)

    def send(mailbox, key, **message):
        if message["subject"] == "Steering: A":
            mail_attempts.append(key)
            if candidate_kind in {"mail", "uncertain-mail"} and fault_active[0]:
                (tmp_path / "release-B").write_text("released by advisory failure")
                if candidate_kind == "uncertain-mail":
                    send_event(mailbox, key, **message)  # Real commit, then lost acknowledgment.
                raise PermissionError("Controlled steering-only mail refusal")
        return send_event(mailbox, key, **message)

    monkeypatch.setattr(dispatch, "_write_record", write)
    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Mailbox, "send_event", send)
    git = dispatch._git
    before_merge = []

    def observe_git(repo, *args):
        if "--ff-only" in args:
            run_path = next(context["runs"].glob("*/evidence/run.json"))
            run = read_record_json(run_path)
            report_path = run_path.parent / "A/steering.json"
            board = snapshot(workspace=context["directory"].parents[2], runs_root=context["runs"])
            before_merge.append(
                dict(
                    integrationHead=git(repo, "rev-parse", "HEAD"),
                    candidateCommit=args[-1],
                    receipt=read_record_json(run_path.parent / "A/integrated.json"),
                    steering=read_record_json(report_path) if report_path.exists() else None,
                    mail=Mailbox(run["memoryWorkspace"]).inbox("planner"),
                    boardMail=board["mail"]["runs"][run["runId"]],
                )
            )
        return git(repo, *args)  # Always execute the original merge, including on RED.

    monkeypatch.setattr(dispatch, "_git", observe_git)
    try:
        result = dispatch.run_plan(context["plan"], work_root=context["runs"], **context["kwargs"])
    finally:
        (tmp_path / "release-B").write_text("released in finally")
    (tmp_path / "steering-evidence.json").write_text(
        json.dumps(dict(sourceOnly=True, beforeMerge=before_merge, terminal=result), indent=2)
    )
    assert len(before_merge) == 1
    observed = before_merge[0]
    bead = result["beads"]["A"]
    assert bead["attempt"]["status"] == "exited" and bead["attempt"]["exitCode"] == 0
    assert bead["status"] == "requirement-validated"
    assert observed["receipt"]["validation"]["passed"] is True
    assert bead["integrated"]["validation"]["passed"] is True
    assert result["didExecuteRealProvider"] is False and result["isLiveProductProof"] is False
    assert git(Path(result["repo"]), "rev-parse", "HEAD") == observed["candidateCommit"]
    if candidate_kind in faults:
        saved = read_record_json(Path(result["repo"]).parent / "evidence/run.json")
        observation = saved["beads"]["A"]["steering"]
        assert observation["candidateCommit"] == observed["candidateCommit"]
        assert observation["state"] == (
            "unavailable" if candidate_kind.startswith("report-") else "observed"
        )
        mailbox = Mailbox(result["memoryWorkspace"])
        if candidate_kind.startswith("report-"):
            assert observation["error"]["type"] == "PermissionError"
            assert observation["summary"] is None and "reportSha256" not in observation
            assert observation["evidencePaths"] == []
        else:
            assert observation["delivery"]["state"] == "unavailable"
            assert observation["delivery"]["error"]["type"] == "PermissionError"
        if candidate_kind == "mail":
            assert result["beads"]["B"]["status"] == "provider exited nonzero (7)"
            assert any(row["subject"] == "Replan: B" for row in mailbox.inbox("planner"))
        else:
            assert not result["status"].startswith("plan-failed:")
        fault_active[0] = False
        dispatch._notify_failures(result, Path(result["repo"]).parent / "evidence")
        messages = [row for row in mailbox.inbox("planner") if row["subject"] == "Steering: A"]
        assert len(messages) == 1
        assert len(set(mail_attempts)) == 1  # Includes uncertain delivery and successful retry.
        event = json.loads(messages[0]["body"])
        if candidate_kind.startswith("report-"):
            assert "reportSha256" not in event
            assert event["evidencePaths"] == [
                str(Path(result["repo"]).parent / "evidence/run.json")
            ]
        return
    report = observed["steering"]
    assert report is not None, "Passing unrelated proof reached merge without automatic steering"
    assert report["candidateCommit"] == observed["candidateCommit"]
    assert report["baseCommit"] == observed["integrationHead"]
    assert report["baseTree"] == git(
        Path(result["repo"]), "rev-parse", f"{report['baseCommit']}^{{tree}}"
    )
    assert report["candidateTree"] == bead["candidate"]["tree"]
    assert report["planRevision"] == context["revision"] and report["runId"] == result["runId"]
    assert report["bead"] == "A" and report["advisoryOnly"] is True
    messages = [row for row in observed["mail"] if row["subject"] == "Steering: A"]
    if candidate_kind == "repaired":
        assert report["state"] == "observed" and report["report"]["summary"]["total"] == 0
        assert messages == []
    else:
        assert len(messages) == 1
        assert any(row["id"] == messages[0]["id"] for row in observed["boardMail"])
        event = json.loads(messages[0]["body"])
        assert event["candidateCommit"] == report["candidateCommit"]
        report_path = Path(bead["evidenceDir"]) / "steering.json"
        assert event["reportSha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
        assert event["evidencePaths"] == [str(report_path)]
        assert event["specRefs"] == dispatch._identity_refs(bead)
        assert "proof outcome unchanged" in event["reason"]
        if candidate_kind == "placeholder":
            assert report["state"] == "observed"
            finding = next(
                item for item in report["report"]["findings"] if item["code"] == "stub-left"
            )
            assert finding["path"] == "A.py" and finding["line"] == 2 and finding["bead"] == "A"
        else:
            assert report["state"] == "unavailable" and report["report"] is None
            assert report["error"]["type"] == "SyntaxError"
        mailbox = Mailbox(result["memoryWorkspace"])
        dispatch._notify_failures(result, Path(result["repo"]).parent / "evidence")
        if candidate_kind == "placeholder":
            # Supported same-controller reattachment, not reworking a completed
            # bead or transferring controller authority. No new provider call.
            resumed = dispatch.run_plan(
                context["plan"],
                work_root=context["runs"],
                resume_run_dir=Path(result["repo"]).parent,
                **context["kwargs"],
            )
            assert resumed["runId"] == result["runId"]
            assert resumed["resumeSummary"]["redispatched"] == []
            assert resumed["beads"]["A"]["steering"] == bead["steering"]
        final = [row for row in mailbox.inbox("planner") if row["subject"] == "Steering: A"]
        assert [row["id"] for row in final] == [row["id"] for row in messages]
