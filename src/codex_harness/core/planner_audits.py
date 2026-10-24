"""Captured-input prospective audits. Only the caller's serial thread writes stores."""

from __future__ import annotations

import hashlib
import json
import queue
import shutil
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from codex_harness.core.spine import compile_manifest

CONTRACT = "captured-input-v1"


def input_files(template):
    template = Path(template)
    if template.is_symlink() or not template.is_dir():
        raise ValueError("audit-template-invalid")
    files = {}
    for path in sorted(template.rglob("*")):
        relative = path.relative_to(template)
        if path.is_symlink() or not path.resolve().is_relative_to(template.resolve()):
            raise ValueError("audit-input-path-invalid")
        if any(part.lower() in {".git", "reference"} for part in relative.parts):
            raise ValueError("audit-input-hidden-path-refused")
        if path.is_file():
            files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.is_dir():
            raise ValueError("audit-input-file-invalid")
    return files


def digest(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


class CapturedAudit:
    def __init__(self, store):
        self.temp = TemporaryDirectory(prefix="harness-audit-inputs-")
        try:
            source = store.workspace / "template"
            if source.resolve() != source:
                raise ValueError("Audit template must stay in the fixed workspace")
            before = input_files(source)
            self.spine = store.spine()
            self.manifest = compile_manifest(self.spine, source)
            store.check_requirement_files(self.manifest, source)
            self.template = Path(self.temp.name) / "template"
            shutil.copytree(source, self.template)
            self.files = input_files(self.template)
            if before != self.files or input_files(source) != self.files:
                raise ValueError("audit-inputs-changed-during-capture")
            if compile_manifest(self.spine, self.template) != self.manifest:
                raise ValueError("audit-manifest-changed-during-capture")
            self.input_digest = digest(self.files)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.temp.cleanup()

    def run(self):
        from codex_harness.core.test_audit import PROSPECTIVE_WARNING, audit_prospective_tests

        return {
            **audit_prospective_tests(
                template=self.template, manifest=self.manifest, spine=self.spine
            ),
            "warning": PROSPECTIVE_WARNING,
        }


class AuditOwner:
    def __init__(self, wake=None):
        self.instance = uuid4().hex
        self.wake = wake
        self.prepared = None
        self.thread = None
        self.results = queue.Queue()

    def start(self, store):
        captured = CapturedAudit(store)
        try:
            attempt, created = store.admit_audit(
                self.instance,
                captured.manifest["planRevision"],
                captured.input_digest,
                captured.files,
            )
            if created:
                self.prepared = (attempt["attemptId"], captured)
                captured = None
            return attempt
        finally:
            if captured is not None:
                captured.close()

    def dispatch_after_flush(self, store):
        if self.prepared is None:
            return
        attempt_id, captured = self.prepared
        self.prepared = None
        store.run_audit_attempt(attempt_id, self.instance)

        def execute():
            result, error = None, None
            try:
                result = captured.run()
            except Exception as failure:
                error = f"{type(failure).__name__}: {failure}"
            finally:
                captured.close()
            self.results.put((attempt_id, result, error))
            if self.wake:
                self.wake()

        self.thread = threading.Thread(target=execute, name="planner-audit")
        try:
            self.thread.start()
        except RuntimeError as error:
            self.thread = None
            captured.close()
            store.complete_audit_attempt(
                attempt_id, self.instance, None, str(error), did_run_validation=False
            )

    def drain(self, store):
        while not self.results.empty():
            attempt_id, result, error = self.results.get_nowait()
            store.complete_audit_attempt(attempt_id, self.instance, result, error)

    def close(self, store):
        if self.prepared:
            self.prepared[1].close()
            self.prepared = None
        if self.thread:
            self.thread.join()  # Existing engine stage limits, not a single 120s deadline.
        if store is not None:
            self.drain(store)


def audit_synchronously(store):
    owner = AuditOwner()
    try:
        attempt = owner.start(store)
        if owner.prepared is None:
            return attempt
        owner.dispatch_after_flush(store)
        owner.thread.join()
        owner.drain(store)
        return store.audit_attempt(attempt["attemptId"], owner.instance)
    finally:
        owner.close(store)
