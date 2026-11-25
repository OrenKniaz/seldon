"""C18/C20/I5; R8/R11: bounded process lifetime and crash-durable provider streams.

This contains normal CreateProcess descendants, not arbitrary hostile OS activity.
POSIX commands run in a private session/process group with verified group teardown.
"""

import ctypes
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryFile


class UncertainCleanup(RuntimeError):
    pass


def reconcile_call(store, attempt=None):
    """Check ownership of a provider call left by a previous controller."""
    started = [row for row in store.records("call_started") if attempt is None or row[2]["attempt"] == attempt]
    if not started:
        return
    _, _, call = started[-1]
    if any(row["attempt"] == call["attempt"] for _, _, row in store.records("attempt_end")):
        return
    folder = Path(call["folder"])
    receipt = folder / "result.json"
    if receipt.is_file():
        if not json.loads(receipt.read_text("utf-8"))["cleanupVerified"]:
            raise UncertainCleanup("Provider cleanup is unverified. Inspect " + str(receipt))
    elif not interrupted_streams_closed(folder):
        raise UncertainCleanup("Waiting for the interrupted provider's process cleanup: " + str(folder))


_BOOTSTRAP = r"""
import json, msvcrt, os, subprocess, sys
gate_handle, status_handle, stdin_devnull = map(int, sys.argv[1:])
gate = os.fdopen(msvcrt.open_osfhandle(gate_handle, os.O_RDONLY | os.O_BINARY), 'rb')
status = os.fdopen(
    msvcrt.open_osfhandle(status_handle, os.O_WRONLY | os.O_BINARY), 'wb', buffering=0)
os.set_handle_inheritable(gate_handle, False)
os.set_handle_inheritable(status_handle, False)
def send(value):
    status.write((json.dumps(value) + '\n').encode('utf-8'))
send({'kind': 'ready'})
data = gate.read()
gate.close()
if not data:
    status.close()
    sys.exit(77)
try:
    config = json.loads(data)
    child = subprocess.Popen(
        config['args'], cwd=config['cwd'], env=config['env'],
        stdin=subprocess.DEVNULL if stdin_devnull else 0,
        stdout=sys.stdout, stderr=sys.stderr,
        creationflags=subprocess.CREATE_NO_WINDOW)
    send({'kind': 'launched', 'pid': child.pid})
    code = child.wait()
    send({'kind': 'complete', 'returncode': code})
except BaseException as error:
    send({'kind': 'error', 'type': type(error).__name__, 'message': str(error)[:512]})
status.close()
"""


class _WindowsJob:
    def __init__(self):
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", w.DWORD),
                ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t),
                ("active_limit", w.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("times", ctypes.c_longlong * 4),
                ("faults", w.DWORD),
                ("total", w.DWORD),
                ("active", w.DWORD),
                ("terminated", w.DWORD),
            ]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "IsProcessInJob": ([w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL),
            "QueryInformationJobObject": (
                [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p],
                w.BOOL,
            ),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "GetModuleFileNameW": ([w.HMODULE, w.LPWSTR, w.DWORD], w.DWORD),
            "PeekNamedPipe": (
                [
                    w.HANDLE,
                    ctypes.c_void_p,
                    w.DWORD,
                    ctypes.c_void_p,
                    ctypes.POINTER(w.DWORD),
                    ctypes.c_void_p,
                ],
                w.BOOL,
            ),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.accounting_type = Accounting
        self.handle = self._call("CreateJobObjectW", None, None)
        try:
            limits = Extended()
            limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway flags.
            self._call(
                "SetInformationJobObject",
                self.handle,
                9,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            )
        except BaseException:
            self.api.CloseHandle(self.handle)  # No process has been created.
            raise

    def _failure(self, operation):
        error = ctypes.WinError(ctypes.get_last_error())
        error.process_native_failure = operation
        return error

    def _call(self, name, *args):
        value = getattr(self.api, name)(*args)
        if not value:
            raise self._failure(name)
        return value

    def image(self):
        buffer = ctypes.create_unicode_buffer(32768)
        count = self._call("GetModuleFileNameW", None, buffer, len(buffer))
        if count == len(buffer) or os.path.basename(buffer.value).lower() not in {
            "python.exe",
            "pythonw.exe",
        }:
            raise OSError("Process bootstrap requires the real Windows CPython image")
        return buffer.value

    def assign(self, process):
        from ctypes import wintypes as w

        handle = int(process._handle)  # Held native handle, never a PID lookup.
        self._call("AssignProcessToJobObject", self.handle, handle)
        member = w.BOOL()
        self._call("IsProcessInJob", handle, self.handle, ctypes.byref(member))
        if not member.value:
            raise OSError("Process bootstrap Job membership unverified; payload not released")

    def active(self):
        value = self.accounting_type()
        self._call(
            "QueryInformationJobObject",
            self.handle,
            1,
            ctypes.byref(value),
            ctypes.sizeof(value),
            None,
        )
        return value.active

    def finish(self, process, control):
        control["teardownStarted"] = True
        active = self.active()
        control["activeProcessesBeforeTeardown"] = active
        if active:
            self._call("TerminateJobObject", self.handle, 19)
        if self.api.WaitForSingleObject(int(process._handle), 5000) != 0:
            raise OSError("Process bootstrap handle wait did not complete")
        self.quiescence(control)
        process.wait(timeout=1)

    def quiescence(self, control):
        deadline = time.monotonic() + 5
        while (active := self.active()) and time.monotonic() < deadline:
            time.sleep(0.01)
        control["activeProcessesAfterTeardown"] = active
        if active:
            raise OSError("Process Job still has active processes; quiescence unverified")
        control["quiescenceVerified"] = True

    def read(self, fd, output):
        import msvcrt
        from ctypes import wintypes as w

        available = w.DWORD()
        if not self.api.PeekNamedPipe(
            msvcrt.get_osfhandle(fd), None, 0, None, ctypes.byref(available), None
        ):
            if ctypes.get_last_error() in {109, 232}:  # Broken/closed pipe after final bytes.
                return True
            raise self._failure("PeekNamedPipe")
        if available.value:
            output.extend(os.read(fd, min(available.value, 65536)))
        return False


def _quiet_watch(files, output_dir, timeout, silence_window):
    deadline = time.monotonic() + (silence_window if silence_window is not None else timeout)
    if silence_window is None:
        return lambda: time.monotonic() >= deadline
    # A separate handle leaves the provider's write offset alone. Count complete native events only.
    stream = files.enter_context((output_dir / "stdout.bin").open("rb"))
    tail = b""
    poll_at = 0

    def quiet():
        nonlocal deadline, tail, poll_at
        now = time.monotonic()
        if now >= poll_at or now >= deadline:
            poll_at = now + .1
            lines = (tail + stream.read()).split(b"\n")
            tail = lines.pop()
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type"):
                    deadline = now + silence_window
        return now >= deadline

    return quiet


def _run_portable_process(args, cwd, timeout, env, stdin, output_dir, silence_window):
    import signal

    control = dict(
        backend="posix-process-group",
        membershipVerifiedBeforePayload=False,
        payloadReleased=False,
        quiescenceVerified=False,
        activeProcessesAfterTeardown=None,
    )
    process = None
    with ExitStack() as files:
        stdout, stderr = (files.enter_context(open(output_dir / (name + ".bin"), "w+b")
                          if output_dir else TemporaryFile(dir=cwd)) for name in ("stdout", "stderr"))

        def members():
            rows = subprocess.run(
                ["ps", "-e", "-o", "pid=,pgid=,stat="],
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=1,
                check=True,
            ).stdout.splitlines()
            return tuple(
                int(pid)
                for pid, pgid, state in map(str.split, rows)
                if pgid == str(process.pid) and not state.startswith("Z")
            )

        def exited():
            # Observe zombie state without reaping: the child pins PID/PGID through teardown.
            return process.pid not in members()

        try:
            try:
                quiet = _quiet_watch(files, output_dir, timeout, silence_window)
                process = subprocess.Popen(args, cwd=cwd, env=env, stdin=stdin,
                    stdout=stdout, stderr=stderr, start_new_session=True)
                control.update(
                    membershipVerifiedBeforePayload=True,
                    payloadReleased=True,
                    payloadPid=process.pid,
                )
                while not exited() and not quiet():
                    time.sleep(0.01)
                timed_out = not exited() and quiet()
            finally:
                if process is not None:
                    control["teardownStarted"] = True
                    try:
                        control["activeProcessesBeforeTeardown"] = len(members())
                    finally:  # Observation failures must still stop the owned group.
                        try:
                            os.killpg(
                                process.pid, signal.SIGKILL
                            )  # Leader still pins PGID; no denied retry.
                        except ProcessLookupError:
                            pass
                        drain_deadline = time.monotonic() + 5
                        # Unreaped zombies retain identity but cannot execute or own live descendants.
                        while members() and time.monotonic() < drain_deadline:
                            time.sleep(0.01)
                        if members():
                            raise OSError(
                                "Process process group still has live members; quiescence unverified"
                            )
                        process.wait(timeout=5)  # No group lookup or signal may follow this reap.
                        control.update(activeProcessesAfterTeardown=0, quiescenceVerified=True)
            output = []
            for stream in (stdout, stderr):
                stream.seek(0)
                output.append(stream.read())
            if timed_out:
                raise subprocess.TimeoutExpired(args, timeout, output=output[0], stderr=output[1])
            code = process.returncode
            control["payloadReturnCode"] = code
            result = subprocess.CompletedProcess(args, code, *output)
            result.process_control = control
            return result
        except BaseException as error:
            error.process_control = control
            raise


def _run_process(args, cwd, timeout, env, stdin, output_dir=None, silence_window=None):
    """The one execution contract: argv, captured bytes, bounded owned lifetime."""
    env = {**(os.environ if env is None else env), "PYTHONDONTWRITEBYTECODE": "1"}
    if sys.platform != "win32":
        return _run_portable_process(args, cwd, timeout, env, stdin, output_dir, silence_window)
    if sys.implementation.name != "cpython":
        raise OSError("Windows process containment requires CPython")
    import msvcrt

    job = _WindowsJob()
    process = None
    fds = set()
    control = {
        "backend": "windows-job",
        "membershipVerifiedBeforePayload": False,
        "payloadReleased": False,
        "quiescenceVerified": False,
        "activeProcessesAfterTeardown": None,
    }
    status = bytearray()
    records = []
    native_failed = False
    bootstrap_exited = False
    unreleased_job_pending = False
    gate_transfer_started = False
    ready_started = None
    bootstrap_status = bytearray()
    files = ExitStack()
    try:
        stdout, stderr = (files.enter_context(open(output_dir / (name + ".bin"), "w+b")
                          if output_dir else TemporaryFile()) for name in ("stdout", "stderr"))
        image = job.image()
        control["bootstrapImage"] = image
        gate_read, gate_write = os.pipe()
        fds.update((gate_read, gate_write))
        status_read, status_write = os.pipe()
        fds.update((status_read, status_write))
        handles = [msvcrt.get_osfhandle(gate_read), msvcrt.get_osfhandle(status_write)]
        startup = subprocess.STARTUPINFO(lpAttributeList={"handle_list": handles})
        try:
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            process = subprocess.Popen(
                [
                    image,
                    "-I",
                    "-S",
                    "-B",
                    "-c",
                    _BOOTSTRAP,
                    *map(str, handles),
                    str(int(stdin == subprocess.DEVNULL)),
                ],
                cwd=os.path.dirname(image),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                startupinfo=startup,
                close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        finally:
            for handle in handles:
                os.set_handle_inheritable(handle, False)
        for fd in (gate_read, status_write):
            os.close(fd)
            fds.remove(fd)
        control["bootstrapPid"] = process.pid
        job.assign(process)
        control["membershipVerifiedBeforePayload"] = True

        def pump():
            before = len(status)
            closed = job.read(status_read, status)
            if not gate_transfer_started:
                bootstrap_status.extend(status[before:])
                del bootstrap_status[:-4096]
            while b"\n" in status:
                line, _, remainder = status.partition(b"\n")
                status[:] = remainder
                record = json.loads(line)
                if record["kind"] == "launched":
                    control["payloadPid"] = record["pid"]
                else:
                    records.append(record)
            return closed

        ready_started = time.monotonic()
        # Native captured-input witness: 15.172s to READY; three times, rounded up.
        if silence_window is None:
            quiet = _quiet_watch(files, output_dir, timeout, None)
        ready_deadline = ready_started + min(timeout if silence_window is None else 46, 46)
        while not records and time.monotonic() < ready_deadline and not pump():
            time.sleep(0.005)
        if not records or records.pop(0) != {"kind": "ready"}:
            raise OSError("Process trusted bootstrap did not reach its private gate")
        config = json.dumps(
            {
                "args": args,
                "cwd": os.fspath(cwd),
                "env": dict(os.environ if env is None else env),
            }
        ).encode("utf-8")
        with os.fdopen(gate_write, "wb") as gate:
            fds.remove(gate_write)
            gate_transfer_started = True
            gate.write(config)
        control["payloadReleased"] = True
        if silence_window is not None:
            quiet = _quiet_watch(files, output_dir, timeout, silence_window)
        while not records and not quiet() and not pump():
            time.sleep(0.005)
        timed_out = not records and quiet()
        record = records[0] if records else None
        job.finish(process, control)
        stdout.seek(0)
        stderr.seek(0)
        out, err = stdout.read(), stderr.read()
        if timed_out:
            raise subprocess.TimeoutExpired(
                args,
                timeout,
                output=out,
                stderr=err,
            )
        if record is None or record.get("kind") != "complete":
            raise OSError(f"Process bootstrap refused payload: {record}")
        control["payloadReturnCode"] = record["returncode"]
        result = subprocess.CompletedProcess(
            args,
            record["returncode"],
            out,
            err,
        )
        result.process_control = control
        return result
    except BaseException as error:
        if ready_started is not None and not gate_transfer_started:
            # These channels precede config delivery: trusted bootstrap diagnostics only.
            control["bootstrapReadyWaitSeconds"] = round(time.monotonic() - ready_started, 6)
            stderr.seek(max(0, os.fstat(stderr.fileno()).st_size - 4096))
            control["bootstrapStderrTail"] = stderr.read(4096).decode("utf-8", errors="replace")
            control["bootstrapStatusTail"] = bytes(bootstrap_status).decode(
                "utf-8", errors="replace"
            )
        native_failed = bool(getattr(error, "process_native_failure", None))
        if native_failed:
            control["nativeFailureOperation"] = error.process_native_failure
        if (
            process is not None
            and control["payloadReleased"] is True
            and not native_failed
            and not control.get("teardownStarted")
        ):
            try:
                job.finish(process, control)
            except OSError as teardown_error:
                native_failed = bool(getattr(teardown_error, "process_native_failure", None))
                control["teardownError"] = str(teardown_error)
        if gate_transfer_started and not control["payloadReleased"]:
            control["payloadReleased"] = None  # Partial gate delivery cannot attest non-execution.
        if process is not None and control["payloadReleased"] is False:
            if gate_write in fds:
                os.close(gate_write)
                fds.remove(gate_write)
            try:
                try:
                    process.wait(timeout=5)  # Gate EOF; never release or use an alternative kill.
                    bootstrap_exited = True
                except subprocess.TimeoutExpired:
                    pass
                if control["membershipVerifiedBeforePayload"] and not native_failed:
                    unreleased_job_pending = True
                    if bootstrap_exited:
                        job.quiescence(control)
                    else:
                        job.finish(process, control)
                    unreleased_job_pending = False
            except OSError as teardown_error:
                native_failed = bool(getattr(teardown_error, "process_native_failure", None))
                if native_failed:
                    control["nativeFailureOperation"] = teardown_error.process_native_failure
                control["unreleasedTeardownWarning"] = str(teardown_error)[:256]
        error.process_control = control
        raise
    finally:
        files.close()
        for fd in fds:
            os.close(fd)
        if (
            process is None
            or (
                bootstrap_exited
                and control["payloadReleased"] is False
                and not unreleased_job_pending
            )
            or control["quiescenceVerified"]
        ):
            if not job.api.CloseHandle(job.handle):
                control["closeHandleWarning"] = ctypes.get_last_error()
        else:
            # Never turn CloseHandle/KILL_ON_CLOSE into an alternative termination after refusal.
            control["jobHandleRetained"] = True
            control["nativeFailure"] = native_failed


def interrupted_streams_closed(folder):
    """After controller loss, wait for inherited Windows Job stream handles to close."""
    streams = [folder / name for name in ("stdout.bin", "stderr.bin")]
    if sys.platform != "win32":
        return not any(path.exists() for path in streams)
    from ctypes import wintypes as w

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = api.CreateFileW
    open_file.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    open_file.restype = w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    deadline = time.monotonic() + 5
    for path in streams:
        while True:
            handle = open_file(str(path), 0x80000000, 1, None, 3, 0, None)  # Read, share read, existing file.
            if handle != ctypes.c_void_p(-1).value:
                api.CloseHandle(handle)
                break
            error = ctypes.get_last_error()
            if error == 2:
                break  # The controller died before opening this stream.
            if error != 32 or time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
    return True


def execute(command, cwd, timeout=30, input_path=None, env=None, output_dir=None, *, silence_window=None):
    """Return honest observations; callers stop if descendant cleanup is unverified."""
    start = time.monotonic()
    control = {}
    stdout, stderr = b"", b""
    code, timed_out, started = None, False, False
    with ExitStack() as inputs:
        stdin = inputs.enter_context(open(input_path, "rb")) if input_path else subprocess.DEVNULL
        try:
            result = _run_process(command, cwd, timeout, env, stdin, output_dir, silence_window)
            control = result.process_control
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as error:
            control = error.process_control
            stdout, stderr = error.stdout or b"", error.stderr or b""
            timed_out = True
        except OSError as error:
            control = getattr(error, "process_control", {})
            stderr = str(error).encode("utf-8")
        started = (bool(control.get("payloadPid")) if os.name == "nt"
                   else control.get("payloadReleased", False))
    return {"exitCode": code, "timedOut": timed_out, "executionStarted": started,
            "cleanupVerified": control.get("quiescenceVerified", False),
            "seconds": time.monotonic() - start, "processControl": control,
            "stdout": stdout, "stderr": stderr}
