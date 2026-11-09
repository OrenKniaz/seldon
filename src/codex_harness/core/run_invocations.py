"""Provider-normal semantic invocations; run_loop owns when and why they run."""

import json

from codex_harness.core import approvals, review_provider
from codex_harness.core.plan_types import PlanStoreError, _now
from codex_harness.core.run_context import packet as fresh_packet


def invoke(store, selected, pending, timeout, binding):
    from codex_harness.core.run_ownership import effect_guard, ensure_effect
    from codex_harness.core.run_setup import require_invocation

    require_invocation(store, pending["role"], selected)
    executable = review_provider._executable(selected["provider"], None)
    question = fresh_packet(store, pending)
    packet = (
        "Return only structured semantics for the supplied answerSchema. "
        "Do not write source/tests, schedule work, grant consent or infer a human identity.\n"
        "## Run question\n" + json.dumps(question, ensure_ascii=False)
    )
    directory = store.path.parent / "semantic-invocations" / binding["effectId"]
    directory.mkdir(parents=True)
    launch = review_provider._launch_review(selected, packet, executable, answer_schema=pending["answerSchema"], schema_path=directory / "answer-schema.json")
    stop = store.path.parent / ("effect-stop-" + binding["effectId"])
    code, output, stderr, cleanup = review_provider._run(
        launch,
        directory,
        timeout,
        launch_guard=lambda: effect_guard(binding),
        stop_when=lambda: "user-control" if stop.exists() else None,
        with_control=True,
    )
    with store._transaction():
        try:
            ensure_effect(store, binding["generation"], binding["effectId"])
            eligible = True
        except PlanStoreError:
            eligible = False
        store._event(
            "run-invocation-observed",
            dict(
                effectId=binding["effectId"],
                binding=binding,
                eligible=eligible,
                completedAt=_now(),
                cleanup=cleanup,
                exitCode=code,
                stdout=output,
                facts=question["facts"],
                stderr=stderr,
            ),
        )
    if not cleanup.get("quiescenceVerified"):
        raise ValueError(
            "Semantic invocation cleanup is UNVERIFIED; retain evidence before direction"
        )
    if code:
        raise ValueError(
            f"Semantic invocation exited {code}: {stderr.strip() or output.strip()}; inspect the retained provider result before direction"
        )
    return output


def completed_response(store, effect, pending, roles):
    """Read one bound, verified raw return without launching or choosing continuation."""
    rows = [
        r
        for r in approvals.records(store, "run-invocation-observed")
        if r["effectId"] == effect["id"]
    ]
    if len(rows) != 1 or not pending or pending["requestId"] != effect["requestId"]:
        return None
    observed = rows[0]
    if not observed.get("eligible") or observed.get("binding") != dict(
        workspace=str(store.workspace), generation=effect["generation"], effectId=effect["id"]
    ):
        return None
    if (
        observed["exitCode"]
        or not observed["cleanup"].get("quiescenceVerified")
        or pending["revision"] != store._revision()
    ):
        return None
    selected = next(r for r in roles if r["role"] == pending["role"])
    return selected, observed["stdout"]
