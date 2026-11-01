"""Captured audit commands; Windows uses a held Job before releasing the payload.

This contains normal CreateProcess descendants, not arbitrary hostile OS activity.
Other platforms retain their existing subprocess behavior without containment claims.
"""

import ctypes
import json
import locale
import os
import subprocess
import sys
import time

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
        error.audit_native_failure = operation
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
            raise OSError("Audit bootstrap requires the real Windows CPython image")
        return buffer.value

    def assign(self, process):
        from ctypes import wintypes as w

        handle = int(process._handle)  # Held native handle, never a PID lookup.
        self._call("AssignProcessToJobObject", self.handle, handle)
        member = w.BOOL()
        self._call("IsProcessInJob", handle, self.handle, ctypes.byref(member))
        if not member.value:
            raise OSError("Audit bootstrap Job membership unverified; payload not released")

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
        active = self.active()
        control["activeProcessesBeforeTeardown"] = active
        if active:
            self._call("TerminateJobObject", self.handle, 19)
        if self.api.WaitForSingleObject(int(process._handle), 5000) != 0:
            raise OSError("Audit bootstrap handle wait did not complete")
        deadline = time.monotonic() + 5
        while (active := self.active()) and time.monotonic() < deadline:
            time.sleep(0.01)
        control["activeProcessesAfterTeardown"] = active
        if active:
            raise OSError("Audit Job still has active processes; quiescence unverified")
        process.wait(timeout=1)
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


def run_audit_process(
    args,
    *,
    cwd,
    capture_output=True,
    text=True,
    timeout=120,
    env=None,
    stdin=subprocess.DEVNULL,
    stop_when=None,
):
    """Run a noninteractive audit; preserve any explicitly supplied stdin file or pipe."""
    env = {**(os.environ if env is None else env), "PYTHONDONTWRITEBYTECODE": "1"}
    if stdin is None:
        stdin = subprocess.DEVNULL
    if sys.platform != "win32":
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
            env=env,
            stdin=stdin,
        )
    if sys.implementation.name != "cpython":
        raise OSError("Windows audit containment requires CPython")
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
    channels, buffers = {}, {"stdout": bytearray(), "stderr": bytearray(), "status": bytearray()}
    records = []
    native_failed = False
    bootstrap_exited = False
    unreleased_job_pending = False
    gate_transfer_started = False
    ready_started = None
    bootstrap_status = bytearray()
    try:
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
                stdout=subprocess.PIPE if capture_output else None,
                stderr=subprocess.PIPE if capture_output else None,
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
        if process.stdin is not None:
            process.stdin.close()  # subprocess.run(input=None) supplies EOF for stdin=PIPE.
        channels["status"] = status_read
        for name in ("stdout", "stderr"):
            if stream := getattr(process, name):
                channels[name] = stream.fileno()
        job.assign(process)
        control["membershipVerifiedBeforePayload"] = True

        def pump():
            for name, fd in list(channels.items()):
                before = len(buffers[name])
                if job.read(fd, buffers[name]):
                    del channels[name]
                if name == "status" and not gate_transfer_started:
                    bootstrap_status.extend(buffers[name][before:])
                    del bootstrap_status[:-4096]
            while b"\n" in buffers["status"]:
                line, _, remainder = buffers["status"].partition(b"\n")
                buffers["status"][:] = remainder
                records.append(json.loads(line))

        ready_started = time.monotonic()
        # Native captured-input witness: 15.172s to READY; three times, rounded up.
        ready_deadline = ready_started + min(timeout, 46)
        while not records and "status" in channels and time.monotonic() < ready_deadline:
            pump()
            time.sleep(0.005)
        if not records or records.pop(0) != {"kind": "ready"}:
            raise OSError("Audit trusted bootstrap did not reach its private gate")
        config = json.dumps(
            {"args": args, "cwd": os.fspath(cwd), "env": dict(os.environ if env is None else env)}
        ).encode("utf-8")
        with os.fdopen(gate_write, "wb") as gate:
            fds.remove(gate_write)
            gate_transfer_started = True
            gate.write(config)
        control["payloadReleased"] = True
        deadline = time.monotonic() + timeout
        while not records and "status" in channels and time.monotonic() < deadline:
            pump()
            if not records and stop_when and (reason := stop_when()):
                control["stopReason"] = reason
                break
            time.sleep(0.005)
        timed_out = not records and time.monotonic() >= deadline
        record = records[0] if records else None
        job.finish(process, control)
        drain_deadline = time.monotonic() + 5
        while channels and time.monotonic() < drain_deadline:
            pump()
            time.sleep(0.005)
        if channels:
            raise OSError("Audit output pipes did not drain after Job quiescence")
        stdout, stderr = bytes(buffers["stdout"]), bytes(buffers["stderr"])
        if text:
            encoding = "utf-8" if sys.flags.utf8_mode else locale.getencoding()
            stdout, stderr = (
                value.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
                for value in (stdout, stderr)
            )
        if timed_out:
            raise subprocess.TimeoutExpired(
                args,
                timeout,
                output=stdout if capture_output else None,
                stderr=stderr if capture_output else None,
            )
        if control.get("stopReason"):
            record = dict(kind="complete", returncode=19)
        if record is None or record.get("kind") != "complete":
            raise OSError(f"Audit bootstrap refused payload: {record}")
        control["payloadReturnCode"] = record["returncode"]
        result = subprocess.CompletedProcess(
            args,
            record["returncode"],
            stdout if capture_output else None,
            stderr if capture_output else None,
        )
        result.audit_control = control
        return result
    except BaseException as error:
        if ready_started is not None and not gate_transfer_started:
            # These channels precede config delivery: trusted bootstrap diagnostics only.
            control["bootstrapReadyWaitSeconds"] = round(time.monotonic() - ready_started, 6)
            control["bootstrapStderrTail"] = bytes(buffers["stderr"][-4096:]).decode(
                "utf-8", errors="replace"
            )
            control["bootstrapStatusTail"] = bytes(bootstrap_status).decode(
                "utf-8", errors="replace"
            )
        native_failed = bool(getattr(error, "audit_native_failure", None))
        if native_failed:
            control["nativeFailureOperation"] = error.audit_native_failure
        if gate_transfer_started and not control["payloadReleased"]:
            control["payloadReleased"] = None  # Partial gate delivery cannot attest non-execution.
        if process is not None and control["payloadReleased"] is False:
            if gate_write in fds:
                os.close(gate_write)
                fds.remove(gate_write)
            try:
                process.wait(timeout=5)  # Gate EOF; never release or use an alternative kill.
                bootstrap_exited = True
                if control["membershipVerifiedBeforePayload"] and not native_failed:
                    unreleased_job_pending = True
                    try:
                        deadline = time.monotonic() + 5
                        active = job.active()
                        while active and time.monotonic() < deadline:
                            time.sleep(0.01)
                            active = job.active()
                        control["activeProcessesAfterTeardown"] = active
                        if not active:
                            control["quiescenceVerified"] = True
                            unreleased_job_pending = False
                    except OSError as teardown_error:
                        native_failed = bool(getattr(teardown_error, "audit_native_failure", None))
                        if native_failed:
                            control["nativeFailureOperation"] = teardown_error.audit_native_failure
                        control["unreleasedTeardownWarning"] = str(teardown_error)[:256]
            except subprocess.TimeoutExpired:
                if control["membershipVerifiedBeforePayload"] and not native_failed:
                    unreleased_job_pending = True
                    try:
                        job.finish(process, control)
                        unreleased_job_pending = False
                    except OSError as teardown_error:
                        native_failed = bool(getattr(teardown_error, "audit_native_failure", None))
                        if native_failed:
                            control["nativeFailureOperation"] = teardown_error.audit_native_failure
                        control["unreleasedTeardownWarning"] = str(teardown_error)[:256]
        error.audit_control = control
        raise
    finally:
        for fd in fds:
            os.close(fd)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
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
