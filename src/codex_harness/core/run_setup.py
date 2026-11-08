"""Frozen kickoff choices own invocation authority; provider hosts own permissions."""

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
    for role in roles:
        if (set(role) != {"role", "name", "provider", "model", "effort"}
                or role["role"] not in {"planner", "judge", "mechanical", "reviewer"}
                or any(not isinstance(v, str) or not v.strip() for v in role.values())):
            raise ValueError("setup role tuple requires role, name, provider, model, effort with nonblank values")
    delivery_review_selection(dict(reviewers=setup["reviewers"], stages=["plan", "full-vision"]))
    if store._data()["nodes"]:
        setup["planMode"] = "semantic-v1" if store.spine().semantic else "legacy"
    if setup["planMode"] not in {"semantic-v1", "legacy"}:
        raise ValueError("setup.planMode: use semantic-v1 or legacy")
    execution_limits(setup["planMode"] == "semantic-v1", setup["max_parallel"], setup["timeout"])


def frozen_setup(run):
    """Retained authority is never reconstructed from current settings or defaults."""
    from codex_harness.core.plan_types import PlanStoreError

    setup = run.get("setup")
    if not setup or not {"planner", "judge", "mechanical"} <= {r["role"] for r in setup.get("roleTuples", [])}:
        error = PlanStoreError("frozen-kickoff-missing",
            "This retained run is missing its frozen kickoff provider/model roles; current settings cannot restore invocation authority")
        error.nextAction = "Start a new run in a new workspace and choose provider/model roles at kickoff"
        raise error
    return setup


def require_invocation(store, role, selected):
    """Only frozen kickoff tuples may be launched; no product consent gate."""
    from codex_harness.core.approvals import reviewer_tuple
    from codex_harness.core.plan_types import PlanStoreError
    from codex_harness.core.run_loop import cursor

    run = cursor(store)
    if run is None:
        raise PlanStoreError("kickoff-required", "Start the run with your dream to freeze provider/model choices")
    setup = frozen_setup(run)
    roles = setup["roleTuples"] if role != "reviewer" else [dict(role="reviewer", **r) for r in setup["reviewers"]]
    if dict(role=role, **reviewer_tuple(selected)) not in roles:
        raise PlanStoreError("invocation-tuple-not-selected", "Use the role/provider/model/effort chosen at kickoff")


def initialize(store, *, setup=None):
    """Establish frozen kickoff once before any continuation can consume it."""
    from uuid import uuid4
    from codex_harness.core.run_loop import cursor, save

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
        else:
            frozen_setup(run)
    return run
