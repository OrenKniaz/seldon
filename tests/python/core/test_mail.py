"""Real SQLite and headless CLI mail, without any provider."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from codex_harness.core.mail import Mailbox, MailError


def test_send_ack_reply_thread_and_reopen(tmp_path):
    mailbox = Mailbox(tmp_path)
    message_id = mailbox.send(
        from_agent="worker:A:codex",
        to_agent="planner",
        subject="Question",
        body="Which requirement applies?",
        refs=["R1", "A"],
        kind="question",
    )
    message = Mailbox(tmp_path).inbox("planner")[0]
    assert message == {
        "id": message_id,
        "thread_id": message_id,
        "from_agent": "worker:A:codex",
        "to_agent": "planner",
        "subject": "Question",
        "body": "Which requirement applies?",
        "refs": ["R1", "A"],
        "kind": "question",
        "created_at": message["created_at"],
        "acked_at": None,
        "acked_by": None,
    }
    assert mailbox.inbox("worker:A:codex") == []
    with pytest.raises(MailError, match="mail-ack-wrong-agent"):
        mailbox.ack(message_id, "worker:B:claude")
    assert mailbox.inbox("planner")[0]["acked_at"] is None
    mailbox.ack(message_id, "planner")
    acknowledged = mailbox.inbox("planner", unacked_only=False)[0]
    assert acknowledged["acked_by"] == "planner" and acknowledged["acked_at"]
    mailbox.ack(message_id, "planner")
    assert mailbox.inbox("planner", unacked_only=False)[0] == acknowledged
    assert mailbox.inbox("planner") == []
    reply_id = mailbox.reply(message_id, from_agent="planner", body="Use R1.")
    reply = mailbox.inbox("worker:A:codex")[0]
    assert reply["id"] == reply_id and reply["kind"] == "answer"
    assert reply["refs"] == ["R1", "A"]
    second_id = mailbox.reply(reply_id, from_agent="worker:A:codex", body="Understood.")
    assert [item["id"] for item in mailbox.thread(message_id)] == [message_id, reply_id, second_id]
    with sqlite3.connect(mailbox.path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.parametrize(
    "secret", ["sk-abc", "ghp_abc", "-----BEGIN KEY", "password=abc", "api_key=abc", "API_KEY=abc"]
)
def test_secret_refusal(tmp_path, secret):
    mailbox = Mailbox(tmp_path)
    with pytest.raises(MailError, match="^mail-secret-refused$"):
        mailbox.send(from_agent="user", to_agent="planner", subject="Note", body=secret)
    assert mailbox.inbox("planner", unacked_only=False) == []


def test_event_delivery_is_atomic_and_survives_ack_and_reopen(tmp_path):
    Mailbox(tmp_path)

    def send(_):
        return Mailbox(tmp_path).send_event(
            "same-event",
            from_agent="harness-dispatcher",
            to_agent="planner",
            subject="Failure",
            body="Failed A",
            kind="replan-trigger",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(send, range(8)))
    mailbox = Mailbox(tmp_path)
    assert len(set(ids)) == 1 and len(mailbox.inbox("planner")) == 1
    mailbox.ack(ids[0], "planner")
    assert send(None) == ids[0]
    assert mailbox.inbox("planner") == []
    assert len(mailbox.inbox("planner", unacked_only=False)) == 1
