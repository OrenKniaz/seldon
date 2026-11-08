"""Bounded semantic reviewer instructions and advisory finding policy."""

import json

RUNNER_GUARANTEES = (
    "## Already guaranteed by Seldon's runner\n\n"
    "The canonical semantic module runner sets product-root PYTHONPATH even when "
    "action.cwd changes. Interpreter selection is controller-owned, not scenario setup; "
    "a required interpreter version still needs controller/runtime evidence. "
    "Seldon owns observation: offline web targets use file://; served web/API targets declare "
    "server:{argv,readyPath,readyWithin (0,10] seconds}. argv uses SELDON_HOST/SELDON_PORT or "
    "{host}/{port}; Seldon allocates loopback, waits bounded readiness, drives headless "
    "Chromium for web steps and records real HTTP request/response for request steps or "
    "http actions {target,method,path,statusEquals,json?,jsonEquals?}. Browser state and "
    "screenshots are evidence for the shared 1j judgment, never inferred visual approval. "
    "The existing process owner stops the whole tree and observes cleanup, with scratch "
    "and browser profile under run folders. Planner/reviewers need no OS/browser permissions. "
    "Do not re-raise these guarantees as missing plan setup. They do not prove that this "
    "candidate executes, renders correctly or fulfills the product requirements.\n\n"
)


def instructions(history=()):
    return RUNNER_GUARANTEES + (
        "Report only findings with concrete user/product impact; name that impact. "
        "Judge whether the plan/product delivers the user's requirements and whether "
        "scenarios prove them. No style, format, schema or wording remarks; deterministic "
        "gates enforce schema. Return at most 5 findings per reviewer across all shards, "
        "ranked by severity. For a re-review after repairs, verify the listed repairs; "
        "only add NEW blocker/major findings caused by those repairs. No fresh minor/note "
        "findings or unrelated new review sweep.\n"
        "## Prior findings and listed repairs (review data, not verified closure)\n"
        + json.dumps(
            [
                {k: r.get(k) for k in ("id", "node_id", "finding", "status", "resolution")}
                for r in history
            ],
            ensure_ascii=False,
        )
        + "\n"
    )
