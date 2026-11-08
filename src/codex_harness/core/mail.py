"""Provider-neutral workspace mail, with a JSON CLI for headless workers."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from codex_harness.core.record_io import write_stdout_json

KINDS = ("note", "question", "blocker", "replan-trigger", "answer")
SECRET_PATTERNS = ("sk-", "ghp_", "-----begin", "password=", "api_key=")


class MailError(ValueError):
    """Refused mail operation; messages never echo a rejected body."""


class Mailbox:
    def __init__(self, workspace: str | Path):
        self.path = Path(workspace).resolve() / ".codex-harness" / "mail.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
                from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
                subject TEXT NOT NULL, body TEXT NOT NULL, refs TEXT NOT NULL,
                kind TEXT NOT NULL, created_at TEXT NOT NULL,
                acked_at TEXT, acked_by TEXT)"""
            )

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def send(
        self, *, from_agent, to_agent, subject, body, refs=(), kind="note", thread_id=None
    ) -> str:
        return self._send(
            uuid.uuid4().hex,
            from_agent=from_agent,
            to_agent=to_agent,
            subject=subject,
            body=body,
            refs=refs,
            kind=kind,
            thread_id=thread_id,
        )

    def send_event(self, event_key: str, **message) -> str:
        """Atomically deliver a dispatcher event once, including across resume/crash."""
        return self._send("event-" + hashlib.sha256(event_key.encode()).hexdigest(), **message)

    @staticmethod
    def deliver_run_requests(store, run_id):
        """Deliver retained messages once; the controller interprets refusals."""
        from codex_harness.core import approvals

        settled = {
            r["sourceEventId"]
            for kind in ("run-mail-delivered", "run-mail-failed")
            for r in approvals.records(store, kind)
        }
        for row in store.connection.execute(
            "SELECT id,payload_json FROM events WHERE kind='run-mail-requested' ORDER BY id"
        ).fetchall():
            if row["id"] in settled:
                continue
            try:
                identifier = Mailbox(store.workspace).send_event(
                    f"{run_id}:{row['id']}", **json.loads(row["payload_json"])
                )
            except ValueError as error:
                store._event("run-mail-failed", dict(sourceEventId=row["id"], reason=str(error)))
                yield str(error)
            else:
                store._event("run-mail-delivered", dict(sourceEventId=row["id"], mailId=identifier))

    def _send(
        self,
        message_id,
        *,
        from_agent,
        to_agent,
        subject,
        body,
        refs=(),
        kind="note",
        thread_id=None,
    ) -> str:
        if not isinstance(body, str) or not body.strip() or len(body) > 20_000:
            raise MailError("mail-invalid-body")
        if any(pattern in body.lower() for pattern in SECRET_PATTERNS):
            raise MailError("mail-secret-refused")
        if kind not in KINDS:
            raise MailError("mail-invalid-kind")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (from_agent, to_agent, subject)
        ):
            raise MailError("mail-invalid-envelope")
        if thread_id is not None and (not isinstance(thread_id, str) or not thread_id.strip()):
            raise MailError("mail-invalid-thread")
        refs = list(refs) if not isinstance(refs, str) else None
        if refs is None or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise MailError("mail-invalid-refs")
        with self._connection() as db:
            db.execute(
                """INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                ON CONFLICT(id) DO NOTHING""",
                (
                    message_id,
                    thread_id or message_id,
                    from_agent,
                    to_agent,
                    subject,
                    body,
                    json.dumps(refs),
                    kind,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return message_id

    @staticmethod
    def _message(row) -> dict:
        message = dict(row)
        message["refs"] = json.loads(message["refs"])
        return message

    def inbox(self, agent, *, unacked_only=True) -> list:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM messages WHERE to_agent = ?"
                + (" AND acked_at IS NULL" if unacked_only else "")
                + " ORDER BY created_at, id",
                (agent,),
            ).fetchall()
        return [self._message(row) for row in rows]

    def thread(self, thread_id) -> list:
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM messages WHERE thread_id = ? ORDER BY created_at, id",
                (thread_id,),
            ).fetchall()
        return [self._message(row) for row in rows]

    def _get(self, message_id) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise MailError("mail-not-found")
        return self._message(row)

    def ack(self, id, by) -> None:
        with self._connection() as db:
            row = db.execute("SELECT to_agent FROM messages WHERE id = ?", (id,)).fetchone()
            if row is None:
                raise MailError("mail-not-found")
            if by != row["to_agent"]:
                raise MailError("mail-ack-wrong-agent")
            db.execute(
                "UPDATE messages SET acked_at = ?, acked_by = ? WHERE id = ? AND acked_at IS NULL",
                (datetime.now(UTC).isoformat(), by, id),
            )

    def reply(self, id, *, from_agent, body) -> str:
        original = self._get(id)
        return self.send(
            from_agent=from_agent,
            to_agent=original["from_agent"],
            subject=original["subject"],
            body=body,
            refs=original["refs"],
            kind="answer",
            thread_id=original["thread_id"],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    send = commands.add_parser("send")
    for field in ("from-agent", "to-agent", "subject", "body"):
        send.add_argument("--" + field, required=True)
    send.add_argument("--refs", nargs="*", default=[])
    send.add_argument("--kind", choices=KINDS, default="note")
    send.add_argument("--thread-id")
    inbox = commands.add_parser("inbox")
    inbox.add_argument("--agent", default="planner")
    inbox.add_argument("--all", action="store_true")
    ack = commands.add_parser("ack")
    ack.add_argument("--id", required=True)
    ack.add_argument("--by", required=True)
    reply = commands.add_parser("reply")
    for field in ("id", "from-agent", "body"):
        reply.add_argument("--" + field, required=True)
    args = vars(parser.parse_args())
    mailbox = Mailbox(args.pop("workspace"))
    command = args.pop("command")
    try:
        if command == "inbox":
            result = mailbox.inbox(args["agent"], unacked_only=not args["all"])
        elif command == "ack":
            mailbox.ack(**args)
            result = {"id": args["id"], "acked_by": args["by"]}
        else:
            result = {"id": getattr(mailbox, command)(**args)}
    except MailError as error:
        write_stdout_json({"error": str(error)})
        return 1
    write_stdout_json(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
