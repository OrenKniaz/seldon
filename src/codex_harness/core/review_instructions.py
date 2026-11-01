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


def is_minor(row):
    return row["severity"] in ("minor", "note")


def has_blocking_findings(rows):
    return any(row["status"] == "open" and not is_minor(row) for row in rows)


def status(row):
    return row["status"] + ("; advisory (nonblocking)" if is_minor(row) else "")


def instructions(history=()):
    return RUNNER_GUARANTEES + (
        "Report only findings with concrete user/product impact; name that impact. "
        "Judge whether the plan/product delivers the user's requirements and whether "
        "scenarios prove them. No style, format, schema or wording remarks; deterministic "
        "gates enforce schema. Return at most 5 findings per reviewer across all shards, "
        "ranked by severity. For a re-review after repairs, verify the listed repairs; "
        "only add NEW blocker/major findings caused by those repairs. No fresh minor/note "
        "findings or unrelated new review sweep. A round with only open minor/note "
        "findings passes: retain them for the user as advisory, not blocking repairs.\n"
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
    return (
        "Review only four things. (1) Expansion within scope: a clear improvement the "
        "vision invites but the plan misses, or scope the vision does not ask for. "
        "(2) Vision integrity: every locked vision item, requirement and user-stated "
        "scenario reaches the plan unweakened. (3) Spine trickle-down: vision, requirements, "
        "contracts, scenarios and beads link completely and consistently. Scenarios must "
        "cover each requirement's stated behaviour; further edge cases and test depth are "
        "the Build worker's unit-test job, so report them as note at most. "
        "(4) Scenario feasibility: each positive scenario's setup satisfies the preconditions "
        "the brief/requirements state (files, parent dirs, prior state); "
        "each negative scenario's missing precondition is deliberate. Use blocker or "
        "major only for a break in (1)-(4). Do not review code or test completeness.\n"
        "For an initial review, fill this worksheet in working notes for EVERY independent "
        "brief clause: command/cwd, value grammar, error preconditions and preservation:\n"
        "Clause (verbatim) | Scenario ID(s) or GAP | Explanation\n"
        "Use actual current scenario IDs only; explain how setup/actions/observations cover "
        "the clause, or mark GAP. References alone are not coverage. Surface all actual gaps "
        "in this first review, not one gap per round. Missing/truncated evidence is unproven. "
        "Unsupported required setup remains a required gap or explicit recorded deferral, "
        "never fulfilled or beyond-brief. This is semantic reviewer judgment, not machine "
        "coverage certification. Keep the worksheet in working notes; final JSON stays below.\n"
        if semantic
        else "Look for missing features or edge cases versus the vision; requirements without "
        "real verification; weak concurrency or file ownership; risky design choices; "
        "and scope that is too shallow (mocked or basic instead of the real vision). "
        "Return actionable findings tied to exact spine items and suggest fixes.\n"
    )


ACCEPTANCE_RESPONSE = (
    "Reply with ONLY a JSON array, with no prose or Markdown fences. Each item must "
    'have exactly {"node_id", "severity", "finding", "suggestion"}. All values are '
    "strings; finding must be nonempty. Severity is blocker, major, minor or note. "
    'node_id must be one of the ids below, or "plan" for plan-wide findings. '
    "suggestion must be one string; an empty string may mean no suggested fix. "
    "Use [] if there are no findings.\nAllowed node ids: "
)


def finding_response(node_ids, *, classify=False):
    import json

    classification = (
        "For plan review also include classification (defect-in-plan or beyond-brief) and "
        "scope_reason explaining the judgment against the brief. Brief-required obligations, "
        "including unsupported setup, stay required/unproven or explicitly deferred. "
        "Only extra coverage beyond those obligations is advisory beyond-brief; this is "
        "reviewer judgment, never machine certification or a human waiver.\n"
        if classify
        else ""
    )
    response = (
        ACCEPTANCE_RESPONSE.replace(
            '"finding", "suggestion"}', '"finding", "suggestion", "classification", "scope_reason"}'
        )
        if classify
        else ACCEPTANCE_RESPONSE
    )
    return classification + response + json.dumps(node_ids, ensure_ascii=False) + "\n"
