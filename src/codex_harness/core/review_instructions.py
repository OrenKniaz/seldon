"""Bounded semantic reviewer instructions and advisory finding policy."""

import json

RUNNER_GUARANTEES = (
    "## Already guaranteed by Seldon's runner\n\n"
    "The canonical semantic module runner sets product-root PYTHONPATH even when "
    "action.cwd changes. Interpreter selection is controller-owned, not scenario setup; "
    "a required interpreter version still needs controller/runtime evidence. "
    "The web runner loads root index.html directly through file:// in an offline browser "
    "context and permits only existing in-root local files. External or missing-resource "
    "requests are recorded; its guardrails require zero external requests. "
    "Do not re-raise these guarantees as missing plan setup. They do not prove that this "
    "candidate executes, renders correctly or fulfills the product requirements.\n\n"
)


def has_blocking_findings(rows):
    return any(row["state"] == "open" for row in rows)


def status(row):
    return row["status"]


def instructions(history=()):
    return RUNNER_GUARANTEES + (
        "Report only findings with concrete user/product impact; name that impact. "
        "Judge whether the plan/product delivers the user's requirements and whether "
        "scenarios prove them. No style, format, schema or wording remarks; deterministic "
        "gates enforce schema. Return at most 5 findings per reviewer across all shards, "
        "ranked by severity. For a re-review after repairs, verify the listed repairs; "
        "only add NEW blocker/major findings caused by those repairs. No fresh minor/note "
        "findings or unrelated new review sweep. Every review requires an explicit verdict and rationale; "
        "minor-only or empty findings grant no approval.\n"
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


def focus(semantic):
    return ("Review the plan against the complete accepted dream: expansion, vision integrity, "
            "feasibility, meaningful evidence and missing behavior. Code already checks IDs, "
            "links and provenance. Explain concrete product consequences. Scale depth with "
            "consequence; approximately three rounds is a convergence checkpoint, not approval.\n")


def finding_response(node_ids, *, classify=False):
    return ("Use the shared 1j Judgment contract with an explicit verdict and rationale. "
            "meaning.findings is a list of objects with node_id, severity, finding, suggestion; "
            "severity is blocker, major, minor or note. For plan findings also provide "
            "classification=defect-in-plan|beyond-brief and nonblank scope_reason. "
            "Beyond-brief is advisory only by explicit judgment against the brief. "
            "The empty list is only a finding list, never an approval.\nAllowed node ids: "
            + json.dumps(node_ids, ensure_ascii=False) + "\n")
