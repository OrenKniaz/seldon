"""Four typed semantic questions; the run loop supplies identities and consumers."""

import json
from copy import deepcopy
from uuid import uuid4

from codex_harness.core.planner_schema import JUDGMENT_RESPONSE_SCHEMA
from codex_harness.core.run_content import CONTENT_SCHEMA

TEXT = {"type": "string", "minLength": 1}
OPTIONS = {
    "author": ("draft", "ready", "research", "ask_user"),
    "direction": ("continue", "repair", "replan", "research", "ask_user", "pause", "stop"),
    "user-interpretation": ("approve", "changes_requested", "decline", "unclear"),
}


def decode_answer(pending, raw):
    """Decode typed author data; malformed bytes remain with the judgment owner."""
    if pending["family"] in {"author", "direction"}:
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw


def question(store, family, purpose, *, judgment=None, context=None):
    if judgment is not None and family not in {"author", "direction"}:
        schema = deepcopy(JUDGMENT_RESPONSE_SCHEMA)
        if family == "user-interpretation":
            schema["properties"]["meaning"] = dict(
                type="object",
                properties=dict(decision=dict(type="string", enum=list(OPTIONS[family]))),
                required=["decision"],
                additionalProperties=False,
            )
    else:
        schema = dict(
            type="object",
            properties=dict(
                option=dict(type="string", enum=list(OPTIONS[family])),
                rationale=TEXT,
                content=CONTENT_SCHEMA,
            ),
            required=["option", "rationale", "content"],
            additionalProperties=False,
        )
    return dict(
        requestId=uuid4().hex,
        family=family,
        role="planner" if family in {"author", "direction"} else "judge",
        purpose=purpose,
        revision=store._revision(),
        candidate=judgment.get("candidate") if judgment else None,
        binding=judgment["binding"] if judgment else store._revision(),
        evidenceRefs=judgment["evidenceRefs"] if judgment else [],
        sourceRefs=judgment["sourceRefs"] if judgment else [],
        answerSchema=schema,
        judgment=judgment,
        context=context or {},
        rationale="Meaning is pending; shape and continuation belong to the controller.",
    )


def ask_question(store, run, family, purpose, *, judgment=None, context=None):
    pending = question(store, family, purpose, judgment=judgment, context=context)
    if judgment is None:
        store._event(
            "loop-question",
            dict(
                family=family,
                purpose=purpose,
                answerSchema=pending["answerSchema"],
                context=context or {},
            ),
        )
        event_id = store.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        judgment = store.prepare_judgment(
            question="loop-" + family,
            subject_ids=store.spine().ids(),
            source_event_ids=[event_id]
            + ([run["userDecisionEventId"]] if run.get("userDecisionEventId") else []),
        )
        pending = question(store, family, purpose, judgment=judgment, context=context)
    pending["requestId"] = judgment["id"]
    run.update(
        question=pending,
        nextAction="Answer the outstanding typed question",
        reason=purpose,
        effect=None,
    )
