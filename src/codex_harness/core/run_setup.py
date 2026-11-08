"""Operator kickoff preferences; code chooses structure, approvals grant permission."""

from codex_harness.core.record_io import read_record_json

DEFAULT_WORKER = dict(provider="codex", model="gpt-6.1-sol", effort="medium")
DEFAULT_PLANNER = dict(provider="codex", model="gpt-6-astra", effort="high")


def kickoff(workspace):
    path = workspace / ".codex-harness/setup.json"
    supplied = read_record_json(path) if path.is_file() else {}
    from codex_harness.core.planner_inspection import validate_setup
    validate_setup(supplied.get("project", {}), workspace)
    worker = dict(DEFAULT_WORKER, **supplied.get("worker", {}))
    planner = next((r for r in supplied.get("roleTuples", []) if r.get("role") == "planner"), DEFAULT_PLANNER)
    review_default = dict(provider="codex", model="gpt-6.1-sol", effort="high") if planner["provider"] == "claude" else dict(provider="claude", model="claude-sonnet-5-5", effort="high")
    reviewers = supplied.get("reviewers", [dict(name=f"reviewer-{i}", **review_default) for i in (1, 2)])
    return dict(worker=worker, reviewers=reviewers, roleTuples=supplied.get("roleTuples", []),
                project=supplied.get("project", {}), planMode=supplied.get("planMode", "semantic-v1"),
                max_parallel=supplied.get("max_parallel", 1), timeout=supplied.get("timeout", 900),
                source=str(path) if path.is_file() else "deterministic defaults")


def select_reviewers(store, run):
    """Materialize the existing canonical selection once, without planner negotiation."""
    selected = run["setup"]["reviewers"]
    from codex_harness.core.planner_review import selection_inputs, delivery_review_selection
    _, existing, _ = selection_inputs(store)
    if existing and delivery_review_selection(existing["fields"])[0] == selected:
        run["reviewerNodeId"] = existing["id"]
        return
    fields = dict(schema="dot-reviewer-selection-v1", catalogId=None, catalogDigest=None,
                  reviewers=selected, stages=["plan", "full-vision"])
    store.record_reviewer_selection(dict(id=run["reviewerNodeId"], kind="decision", title="Run reviewers",
        text="Code-selected reviewers; permission and independence require their existing evidence owners",
        parents=store.spine().ids("vision"), fields=fields), store._revision())


def workers(store, run):
    return [dict(bead=b, **run["setup"].get("workers", {}).get(b, run["setup"]["worker"])) for b in store.spine().ids("bead")]


def resolve_roles(setup):
    roles = setup["roleTuples"]
    named = [r.get("role") for r in roles if r.get("role") != "reviewer"]
    if len(named) != len(set(named)):
        raise ValueError("setup.roleTuples: configure each semantic role once")
    planner = next((r for r in roles if r.get("role") == "planner"), DEFAULT_PLANNER)
    for role in ("planner", "judge", "mechanical"):
        if not any(r.get("role") == role for r in roles):
            defaults = DEFAULT_WORKER if role == "mechanical" else planner
            roles.append(dict(role=role, name=role, **{k: defaults[k] for k in DEFAULT_WORKER}))


def validate(store, setup):
    """Reject operator errors before freezing preferences; reuse the actual tuple owners."""
    from codex_harness.core import approvals
    from codex_harness.core.provider_launch import get_adapter
    from codex_harness.core.intake import _model
    from codex_harness.core.planner_execution import execution_limits
    from codex_harness.core.planner_review import delivery_review_selection
    from codex_harness.core.plan_types import _nonempty

    resolve_roles(setup)
    for selected in [setup["worker"], *setup["reviewers"], *setup["roleTuples"]]:
        get_adapter(selected.get("provider"))
        _model(selected.get("model"))
        _nonempty(selected.get("effort"), "setup.effort")
    roles = [*setup["roleTuples"], *[dict(role="reviewer", **r) for r in setup["reviewers"]]]
    if roles:
        try:
            approvals.subject(store, "P", dict(roleTuples=roles))
        except ValueError as error:
            raise ValueError("setup.roleTuples/reviewers: " + str(error)) from error
    delivery_review_selection(dict(reviewers=setup["reviewers"], stages=["plan", "full-vision"]))
    if store._data()["nodes"]:
        setup["planMode"] = "semantic-v1" if store.spine().semantic else "legacy"
    if setup["planMode"] not in {"semantic-v1", "legacy"}:
        raise ValueError("setup.planMode: use semantic-v1 or legacy")
    execution_limits(setup["planMode"] == "semantic-v1", setup["max_parallel"], setup["timeout"])


def adopt(store, run):
    """Freeze retained authorized choices once; settings cannot revoke paid run facts."""
    from uuid import uuid4
    from codex_harness.core import approvals
    from codex_harness.core.planner_review import selection_inputs, delivery_review_selection

    setup = kickoff(store.workspace)
    granted = approvals.current(store, "B")
    if granted and granted["state"] == "approved":
        execution = granted["subject"]["execution"]
        setup.update(worker=execution["workers"][0], max_parallel=execution["maxParallel"], timeout=execution["timeout"],
                     workers={w["bead"]: {k: w[k] for k in DEFAULT_WORKER} for w in (run.get("workers") or [])})
    permission = approvals.current(store, "P")
    if permission and permission["state"] == "approved":
        setup["roleTuples"] = permission["subject"]["roleTuples"]
        _, selected, _ = selection_inputs(store)
        if selected:
            reviewers = delivery_review_selection(selected["fields"])[0]
            if all(dict(role="reviewer", **r) in setup["roleTuples"] for r in reviewers):
                setup["reviewers"] = reviewers
    validate(store, setup)
    run.update(setup=setup, max_parallel=setup["max_parallel"],
               timeout=setup["timeout"], reviewerNodeId="REVIEWERS_" + uuid4().hex)


def initialize(store, *, setup=None):
    """Establish frozen kickoff once before any continuation can consume it."""
    from uuid import uuid4
    from codex_harness.core.run_loop import cursor, save, _interrupt, TERMINAL

    with store._transaction():
        run = cursor(store)
        if run is None:
            if setup is None:
                return
            run = dict(
                runId=uuid4().hex,
                state="PLAN",
                step="trace" if store._meta()["vision_locked_revision"] else "understanding",
                generation=0,
                question=None,
                userRequest=None,
                effect=None,
                jobId=None,
                workers=None,
                max_parallel=setup["max_parallel"],
                timeout=setup["timeout"],
                setup=setup,
                reviewerNodeId="REVIEWERS_" + uuid4().hex,
                nextAction="Author understanding and expansion",
                reason="New dream",
            )
            select_reviewers(store, run)
            save(store, run)
        elif "setup" not in run:
            adopt(store, run)
            pending = run.get("question")
            if (pending and pending["family"] == "author" and run["phase"] == "roundtable"
                    and not pending.get("context") and run["state"] in {"PLAN", "REVIEW"}):
                _interrupt(store, run)
                run.update(state="REVIEW", step="plan-review")
            save(store, run)
        elif not {"planner", "judge", "mechanical"} <= {r["role"] for r in run["setup"]["roleTuples"]}:
            validate(store, run["setup"])
            run.pop("roleTuples", None)
            save(store, run)
    return run
