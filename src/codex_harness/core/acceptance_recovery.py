"""Local checkpoints for ordinary acceptance review; never an external importer.

Only the producer's prelaunch event and controlled return hook create receipts.
Resume reads their originals. This is local provenance, not DB-owner authentication.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path
from uuid import uuid4

from codex_harness.core import roundtable
from codex_harness.core.plan_store import PlanStoreError
from codex_harness.core.spine import Spine, compile_manifest, requirement_file_sources
from codex_harness.core.spine_render import render_markdown

STARTED = "acceptance-review-started"
COMPLETED = "acceptance-review-completed"
ABANDONED = "acceptance-review-unrecoverable"
_ID = re.compile(r"[0-9a-f]{32}\Z")
_SESSION = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


class CaptureFailure(RuntimeError):
    """Fatal: must escape the shard's ValueError/OSError retry handling."""


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def seal(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def source_identity():
    return digest(
        {path.name: seal(path.read_bytes()) for path in sorted(Path(__file__).parent.glob("*.py"))}
    )


def identities(store, repo, run, *, execution_assessment_ids=None):
    """No nested BEGIN: safe both outside and inside the final writer transaction."""
    spine = Spine(store._data())
    template = store.workspace / "template"
    manifest = compile_manifest(spine, template if template.is_dir() else None)
    protected = {}
    if template.is_dir():
        paths = requirement_file_sources(template, manifest["requirementFiles"])
        paths.update(
            {
                item["name"]: template / item["name"]
                for item in roundtable._protected_tests(store, spine=spine)
            }
        )
        protected = {name: seal(path.read_bytes()) for name, path in sorted(paths.items())}
    result = {
        "source": source_identity(),
        "candidate": digest(
            {
                "workspace": str(store.workspace.resolve()),
                "repo": str(Path(repo).resolve()),
                "spine": spine.to_dict(),
                "manifest": manifest,
                "protectedBytes": protected,
                "scenarios": store.acceptance_scenarios(),
                "run": run,
            }
        ),
        "context": digest(
            {
                "planning": roundtable.findings(store, expand=True),
                "acceptance": store.acceptance_findings(),
                "clarifications": [
                    dict(row)
                    for row in store.connection.execute("SELECT * FROM clarifications ORDER BY id")
                ],
            }
        ),
    }
    if execution_assessment_ids is not None:
        from codex_harness.core.acceptance import selected_execution_support

        result["executionSupport"] = digest(
            selected_execution_support(store, execution_assessment_ids, repo=repo)
        )
    return result


def _executable_identity(executable):
    resolved = shutil.which(executable)
    if not resolved:
        return None
    path = Path(resolved).resolve()
    return {"path": str(path), **seal(path.read_bytes())}


def _launch_identity(launch, executable):
    return {
        "argv": list(launch.command),
        "stdin": seal((launch.stdin_text or "").encode("utf-8")),
        "executable": _executable_identity(executable),
    }


def _directory(store, attempt):
    if not isinstance(attempt, str) or not _ID.fullmatch(attempt):
        raise ValueError("Recovery requires a registered 32-hex attempt ID")
    return store.path.parent / "acceptance-review-attempts" / attempt


def _safe(path, root):
    # root is derived from the registered store, never from caller paths.
    anchor = root.parents[2]  # workspace/.codex-harness/acceptance-review-attempts/id
    if not root.resolve().is_relative_to(anchor.resolve()) or not path.resolve().is_relative_to(
        root.resolve()
    ):
        raise ValueError("Review receipt escapes its registered directory")
    parts = path.relative_to(anchor).parts
    for size in range(len(parts) + 1):
        candidate = anchor.joinpath(*parts[:size])
        if candidate.exists() or candidate.is_symlink():
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Review receipt has a symlink/reparse component")
    return path


def _write(root, name, data):
    path = _safe(root / name, root)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return seal(data)


def _read(root, name, expected):
    path = _safe(root / name, root)
    if not path.is_file():
        raise ValueError("Original review receipt is missing")
    raw = path.read_bytes()
    if seal(raw) != expected:
        raise ValueError("Original review receipt was changed")
    return raw.decode("utf-8")


def _events(store, kind, attempt=None):
    rows = [
        (row[0], json.loads(row[1]))
        for row in store.connection.execute(
            "SELECT id, payload_json FROM events WHERE kind=? ORDER BY id", (kind,)
        )
    ]
    return [
        (key, item) for key, item in rows if attempt is None or item.get("attemptId") == attempt
    ]


def _coverage(store, selected, dossiers, evidence):
    """Eligibility only: do not repair or silently broaden the ordinary packet."""
    required = []
    protected = {}
    documents = render_markdown(store.spine())
    template = store.workspace / "template"
    if template.is_dir():
        manifest = compile_manifest(Spine(store._data()), template)
        for name, path in requirement_file_sources(template, manifest["requirementFiles"]).items():
            try:
                required.append((name, path.read_text(encoding="utf-8")))
            except UnicodeError:
                return "Protected body is not UTF-8"
        protected = {item["name"]: item for item in roundtable._protected_tests(store)}
        for name in protected:
            try:
                required.append((name, (template / name).read_text(encoding="utf-8")))
            except UnicodeError:
                return f"Protected helper body is not UTF-8: {name}"
    for reviewer in selected:
        shards = dossiers[reviewer["provider"]]
        if any(shard["truncatedNodes"] for shard in shards):
            return "Truncated planned review coverage"
        if set().union(*(set(shard["nodeIds"]) for shard in shards)) != set(store.spine().ids()):
            return "Incomplete primary node coverage"
        packets = [shard["packet"] for shard in shards]
        for packet in packets:
            marker = "## Read-only candidate evidence"
            start = packet.index("{", packet.index(marker))
            candidate, _ = json.JSONDecoder().raw_decode(packet[start:])
            if candidate != evidence:
                return "Candidate evidence is incomplete in packet"
        for name, body in required:
            if name in protected and protected[name]["body"] == body:
                owner = min(protected[name]["owners"])
                blocks = (
                    f"### {owner}: {name}\n\n{body}\n",
                    f"### Protected test: {name}\n{body}\n",
                )
                if any(block in packet for block in blocks for packet in packets):
                    continue
            elif documents.get(name) == body:
                # Only the producer's structured renderedDocs section counts.
                included = False
                for packet in packets:
                    for line in packet.splitlines():
                        if not line.startswith("{"):
                            continue
                        try:
                            view = json.loads(line)
                        except ValueError:
                            continue
                        rendered = view.get("renderedDocs") if isinstance(view, dict) else None
                        if isinstance(rendered, dict) and rendered.get(name) == body:
                            included = True
                if included:
                    continue
            return f"Protected body missing from packet: {name}"
    return None


def runtime_identity(provider, reviewer, output, stderr):
    """Conservatively recognize saved main-session telemetry, never requested flags alone."""
    if not reviewer.get("model"):
        raise ValueError("Recovery needs an explicitly requested and observed model")
    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    if not events or not all(isinstance(item, dict) for item in events):
        raise ValueError("Unknown native review protocol")
    if provider == "codex":
        if any(
            event.get("type")
            not in {
                "thread.started",
                "turn.started",
                "turn.completed",
                "item.started",
                "item.completed",
            }
            for event in events
        ):
            raise ValueError("Failed or unknown Codex review event")
        for event in events:
            if event.get("type", "").startswith("item.") and event.get("item", {}).get(
                "type"
            ) not in {"agent_message", "reasoning"}:
                raise ValueError("Codex tool/error/unknown item cannot earn recovery credit")
        starts = [item.get("thread_id") for item in events if item.get("type") == "thread.started"]
        if len(starts) != 1 or sum(item.get("type") == "turn.completed" for item in events) != 1:
            raise ValueError("Incomplete or ambiguous Codex session")
        session = starts[0]
        lines = [
            line
            for line in stderr.splitlines()
            if "Codex initialized with event: SessionConfiguredEvent {" in line
        ]
        if len(lines) != 1:
            raise ValueError("Missing or ambiguous observed Codex configuration")
        line = lines[0]
        for field, kind in (("session_id", "SessionId"), ("thread_id", "ThreadId")):
            if re.findall(field + r": " + kind + r" \{ uuid: ([0-9a-f-]+) \}", line) != [session]:
                raise ValueError("Codex main session identity mismatch")
        if re.findall(r'model: "([^"]+)"', line) != [reviewer["model"]] or not all(
            field + ": None," in line for field in ("forked_from_id", "parent_thread_id")
        ):
            raise ValueError("Codex observed model/main identity mismatch")
        efforts = re.findall(r"reasoning_effort: Some\((\w+)\)", line)
        effort = efforts[0].casefold() if len(efforts) == 1 else None
    elif provider == "claude":
        init = [
            item
            for item in events
            if item.get("type") == "system" and item.get("subtype") == "init"
        ]
        final = [item for item in events if item.get("type") == "result"]
        messages = [item for item in events if item.get("type") == "assistant"]
        if len(init) != 1 or len(final) != 1 or not messages or len(events) != 2 + len(messages):
            raise ValueError("Incomplete or unknown Claude review protocol")
        session = init[0].get("session_id")
        if init[0].get("model") != reviewer["model"] or any(
            item.get("session_id") != session for item in events
        ):
            raise ValueError("Claude observed model/session mismatch")
        if (
            final[0].get("subtype") != "success"
            or final[0].get("is_error") is not False
            or final[0].get("permission_denials") != []
        ):
            raise ValueError("Claude review failed or permission result is unknown")
        for item in messages:
            message = item.get("message", {})
            content = message.get("content")
            if (
                message.get("model") != reviewer["model"]
                or not isinstance(content, list)
                or not content
                or any(block.get("type") != "text" for block in content)
            ):
                raise ValueError("Claude tool/unknown content or model mismatch")
        effort = init[0].get("reasoning_effort")
    else:
        raise ValueError("Unsupported recovery provider")
    if not isinstance(session, str) or not _SESSION.fullmatch(session):
        raise ValueError("Unknown native session identity")
    if reviewer.get("effort") and (
        not isinstance(effort, str) or effort.casefold() != reviewer["effort"].casefold()
    ):
        raise ValueError("Unknown or mismatched observed reasoning effort")
    # Extra recovery eligibility, using the ordinary final-text and finding validators.
    final_text = roundtable._response(output, provider)
    if not isinstance(json.loads(final_text), list):
        raise ValueError("Recovery requires a final JSON array without surrounding prose")
    return {"provider": provider, "session": session, "model": reviewer["model"], "effort": effort}


class ReviewAttempt:
    def __init__(self, store, header, event_id):
        self.store, self.header, self.event_id = store, header, event_id
        self.id = header["attemptId"]
        self.root = _directory(store, self.id)
        self.abandoned = False
        self.next_slot = {}

    @classmethod
    def start(
        cls,
        store,
        repo,
        run,
        selected,
        dossiers,
        executables,
        evidence,
        *,
        mocked,
        expected_identity,
    ):
        from codex_harness.core.acceptance import current_run

        attempt = uuid4().hex
        root = _directory(store, attempt)
        _safe(root, root)
        root.mkdir(parents=True)
        header = {
            "schema": 1,
            "attemptId": attempt,
            "workspace": str(store.workspace.resolve()),
            "repo": str(Path(repo).resolve()),
            "run": run,
            "identities": expected_identity,
            "reviewers": selected,
            "isMocked": mocked,
            "slots": [],
            "ineligible": _coverage(store, selected, dossiers, evidence),
        }
        support = evidence.get("executionSupport")
        if support is not None:
            if digest(support) != expected_identity.get("executionSupport"):
                raise ValueError("execution-support-preparation-raced")
            header["executionSupport"] = support
        for reviewer in selected:
            provider = reviewer["provider"]
            for shard in dossiers[provider]:
                slot = uuid4().hex
                launch = roundtable._launch_review(reviewer, shard["packet"], executables[provider])
                header["slots"].append(
                    {
                        "id": slot,
                        "reviewer": reviewer,
                        "shard": {k: v for k, v in shard.items() if k != "packet"},
                        "packet": _write(root, slot + ".packet", shard["packet"].encode("utf-8")),
                        "launch": _launch_identity(launch, executables[provider]),
                    }
                )
        with store._transaction():
            if (
                current_run(store) != run
                or identities(
                    store,
                    repo,
                    run,
                    **(
                        {"execution_assessment_ids": support["registrationIds"]}
                        if support is not None
                        else {}
                    ),
                )
                != expected_identity
            ):
                raise PlanStoreError("acceptance-revision-changed", "Rerun acceptance review")
            if _events(store, STARTED, attempt):
                raise ValueError("Duplicate review registration")
            store._event(STARTED, header)
            event_id = store.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        return cls(store, header, event_id)

    def capture(self, reviewer, shard, launch, executable, response):
        if self.abandoned:
            return
        try:
            slots = [slot for slot in self.header["slots"] if slot["reviewer"] == reviewer]
            position = self.next_slot.get(reviewer["name"], 0)
            if position >= len(slots) or slots[position]["shard"] != {
                k: v for k, v in shard.items() if k != "packet"
            }:
                raise ValueError("Return does not match one registered review slot")
            slot = slots[position]
            if (
                _launch_identity(launch, executable) != slot["launch"]
                or seal(shard["packet"].encode("utf-8")) != slot["packet"]
            ):
                raise ValueError("Actual review launch differs from its registration")
            code, output = response[:2]
            stderr = response[2] if len(response) > 2 else ""
            try:
                runtime = runtime_identity(reviewer["provider"], reviewer, output, stderr)
            except (ValueError, TypeError, KeyError, AttributeError):
                runtime = None  # Unknown stays explicit without changing ordinary review.
            payload = {
                "schema": 1,
                "attemptId": self.id,
                "headerHash": digest(self.header),
                "slot": slot["id"],
                "launch": slot["launch"],
                "exitCode": code,
                "isMocked": self.header["isMocked"],
                "didRunProvider": not self.header["isMocked"],
                "runtime": runtime,
                "stdout": _write(self.root, slot["id"] + ".stdout", output.encode("utf-8")),
                "stderr": _write(self.root, slot["id"] + ".stderr", stderr.encode("utf-8")),
            }
            with self.store._transaction():
                if _events(self.store, STARTED, self.id) != [(self.event_id, self.header)]:
                    raise ValueError("Review registration changed or is ambiguous")
                if any(
                    row["slot"] == slot["id"] for _, row in _events(self.store, COMPLETED, self.id)
                ):
                    raise ValueError("Duplicate review completion")
                self.store._event(COMPLETED, payload)
            self.next_slot[reviewer["name"]] = position + 1
        except Exception as error:
            raise CaptureFailure(
                f"Attempt {self.id}: could not seal original review completion: {error}"
            ) from error

    def abandon(self):
        try:
            with self.store._transaction():
                self.store._event(
                    ABANDONED, {"attemptId": self.id, "reason": "unplanned overflow reshard"}
                )
            self.abandoned = True
        except Exception as error:
            raise CaptureFailure(f"Attempt {self.id}: cannot seal overflow abandonment") from error

    @classmethod
    def load(cls, store, attempt):
        _directory(store, attempt)
        rows = _events(store, STARTED, attempt)
        if len(rows) != 1 or rows[0][1].get("schema") != 1:
            raise ValueError("Original Harness review registration is missing or ambiguous")
        return cls(store, rows[0][1], rows[0][0])

    def check_execution_support(self):
        """Prospective opt-in prelaunch check; no child or permission is produced."""
        support = self.header.get("executionSupport")
        if support is None:
            return
        if self.abandoned or _events(self.store, ABANDONED, self.id):
            raise ValueError("execution-support-original-review-abandoned")
        if _events(self.store, STARTED, self.id) != [(self.event_id, self.header)]:
            raise ValueError("execution-support-review-registration-changed")
        from codex_harness.core.acceptance import selected_execution_support

        if (
            selected_execution_support(
                self.store, support["registrationIds"], repo=self.header["repo"]
            )
            != support
        ):
            raise ValueError("execution-support-review-binding-stale")

    def validate(self, repo, run, *, admitted=False):
        header = self.header
        if _events(self.store, STARTED, self.id) != [(self.event_id, header)]:
            raise ValueError("Review registration changed or is ambiguous")
        if header["workspace"] != str(self.store.workspace.resolve()) or header["repo"] != str(
            Path(repo).resolve()
        ):
            raise ValueError("Review registration belongs to a different project/product")
        support = header.get("executionSupport")
        if support is not None and not admitted:
            self.check_execution_support()
        current = identities(
            self.store,
            repo,
            run,
            **(
                {"execution_assessment_ids": support["registrationIds"]}
                if support is not None and not admitted
                else {}
            ),
        )
        if header["run"] != run or any(
            current[key] != header["identities"][key]
            for key in (("source", "candidate") if admitted else current)
        ):
            raise ValueError("Review source, candidate or context is stale")
        if header["isMocked"] or header["ineligible"] or _events(self.store, ABANDONED, self.id):
            raise ValueError(
                "Review is mocked, incomplete or unrecoverable: " + str(header["ineligible"])
            )
        completions = _events(self.store, COMPLETED, self.id)
        slots = header["slots"]
        if len(completions) != len(slots) or len({row["slot"] for _, row in completions}) != len(
            slots
        ):
            raise ValueError("Incomplete or duplicate original review completions")
        records, sessions = [], set()
        for slot in slots:
            matches = [(key, row) for key, row in completions if row["slot"] == slot["id"]]
            if len(matches) != 1:
                raise ValueError("Missing original review slot")
            event_id, row = matches[0]
            if (
                row.get("schema") != 1
                or row["headerHash"] != digest(header)
                or row["launch"] != slot["launch"]
                or not slot["launch"]["executable"]
                or row["isMocked"] is not False
                or row.get("didRunProvider") is not True
                or row.get("runtime") is None
                or type(row["exitCode"]) is not int
                or row["exitCode"] != 0
            ):
                raise ValueError("Unknown, mocked or failed native completion")
            packet = _read(self.root, slot["id"] + ".packet", slot["packet"])
            output = _read(self.root, slot["id"] + ".stdout", row["stdout"])
            stderr = _read(self.root, slot["id"] + ".stderr", row["stderr"])
            identity = runtime_identity(
                slot["reviewer"]["provider"], slot["reviewer"], output, stderr
            )
            if identity != row["runtime"]:
                raise ValueError("Observed runtime differs from the sealed completion")
            session = (identity["provider"], identity["session"])
            if session in sessions:
                raise ValueError("Duplicate native review session")
            sessions.add(session)
            shard = {**slot["shard"], "packet": packet}
            record, items = roundtable._parse_review_return(
                self.store, slot["reviewer"], shard, (row["exitCode"], output, stderr), mocked=False
            )
            if record["invalid"] or not record["packetBound"]:
                raise ValueError("Invalid or tool-expanded original review output")
            records.append((slot["reviewer"], record, items, event_id))
        # A session's registration cannot be migrated/relabelled into another attempt.
        for _, other in _events(self.store, COMPLETED):
            if other["attemptId"] == self.id:
                continue
            other_attempt = self.load(self.store, other["attemptId"])
            match = [slot for slot in other_attempt.header["slots"] if slot["id"] == other["slot"]]
            if len(match) != 1:
                raise ValueError("Conflicting completion registration")
            slot = match[0]
            if other["isMocked"] or not other.get("didRunProvider") or other.get("runtime") is None:
                continue
            if (
                other["headerHash"] != digest(other_attempt.header)
                or other["launch"] != slot["launch"]
            ):
                raise ValueError("Conflicting original completion registration")
            # Damaging an older file never releases its privately sealed session.
            identity = other["runtime"]
            if (identity["provider"], identity["session"]) in sessions:
                raise ValueError("Native session was already registered to another attempt")
        return records

    def references(self):
        return {
            "attemptId": self.id,
            "startedEventId": self.event_id,
            "receiptEventIds": [key for key, _ in _events(self.store, COMPLETED, self.id)],
        }


def refusal(error):
    return PlanStoreError("acceptance-review-recovery-refused", str(error))


def attempt_summaries(store):
    """Read-only discovery after a lost response; counts never assert eligibility."""
    completed = _events(store, COMPLETED)
    abandoned = {item["attemptId"] for _, item in _events(store, ABANDONED)}
    admitted = {row[0] for row in store.connection.execute("SELECT id FROM acceptance_reviews")}
    return [
        {
            "id": item["attemptId"],
            "runId": item["run"]["id"],
            "plannedSlots": len(item["slots"]),
            "completedSlots": sum(row["attemptId"] == item["attemptId"] for _, row in completed),
            "isMocked": item["isMocked"],
            "abandoned": item["attemptId"] in abandoned,
            "admitted": item["attemptId"] in admitted,
        }
        for _, item in _events(store, STARTED)
    ]
