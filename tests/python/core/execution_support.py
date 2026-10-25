"""Source-only canonical execution originals for existing local-process fixtures.

Native forms, review returns and controller Popen return are synthetic inputs.
Audits execute real fixture proofs; actual PlanStore/native-domain/job writers run. The
actual owned controller entry writes started.json before a synthetic handoff;
this does not prove a human response, provider run or product acceptance.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.python.core.planning_support import source_controller_roundtable, source_only_vision_lock

from codex_harness.core import dispatch, planner_jobs
from codex_harness.core.plan_store import PlanStore
from codex_harness.core.planner_audits import audit_synchronously
from codex_harness.core.planner_execution import complete_execution, prepare_execution


def source_process_observations(monkeypatch):
    """Label only the observed Python fixture command; never alter admission."""
    original = dispatch._did_execute_real_provider

    def observed(record):
        command = (record.get("attempt") or {}).get("launch", {}).get("command") or []
        if (
            command
            and Path(command[0]).resolve() == Path(sys.executable).resolve()
            and any("SOURCE-ONLY local process fixture" in part for part in command[1:])
        ):
            return False
        return original(record)

    monkeypatch.setattr(dispatch, "_did_execute_real_provider", observed)
    git = dispatch._git

    def observed_git(*args, **kwargs):
        try:
            return git(*args, **kwargs)
        except subprocess.CalledProcessError as error:
            print(
                "SOURCE-ONLY Git fixture diagnostic: " + str(error.stderr)[:4000], file=sys.stderr
            )
            raise

    monkeypatch.setattr(dispatch, "_git", observed_git)


def publish_source_store(workspace, plan=None, *, spine_file=None):
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    template = Path(spine_file).parent / "template" if spine_file else Path(plan) / "template"
    if template.resolve() != (workspace / "template").resolve():
        shutil.copytree(template, workspace / "template")
    if spine_file:
        data = json.loads(Path(spine_file).read_text())
        # Historical dogfood spine left its integration requirement unassigned.
        # Source-only native publication gives the CLI bead that responsibility;
        # no committed fixture or protected proof is changed.
        if any(n["id"] == "R6" for n in data["nodes"]):
            next(n for n in data["nodes"] if n["id"] == "B")["parents"].append("R6")
    else:
        manifest = json.loads((Path(plan) / "sample.json").read_text())
        fields = dict(
            vision="Source-only local process contract",
            users="Local test controller",
            first_user_moment="Observe a bounded local process",
            scope_in=["Owned local process"],
            scope_out="Provider or model execution",
            assumptions=[],
            validation=manifest["validation"],
            requirementFiles=manifest["requirementFiles"],
        )

        def node(id, kind, parents, *, title=None, fields=None):
            return dict(
                id=id,
                kind=kind,
                title=title or id,
                text=title or id,
                parents=parents,
                fields=fields or {},
            )

        nodes = [node("V1", "vision", [], fields=fields), node("source-feature", "feature", ["V1"])]
        requirements = sorted(
            {r for bead in manifest["beads"].values() for r in bead["requirements"]}
        )
        nodes += [node(r, "requirement", ["source-feature"]) for r in requirements]
        integration = manifest.get("integration") or {}
        integration_requirements = integration.get("requirements", [])
        nodes += [
            node(r, "requirement", ["source-feature"])
            for r in integration_requirements
            if r not in requirements
        ]
        for id, bead in manifest["beads"].items():
            nodes.append(
                node(
                    id,
                    "bead",
                    bead["requirements"],
                    title=bead["title"],
                    fields={
                        "ownedFiles": bead["ownedFiles"],
                        "dependsOn": bead.get("dependsOn", []),
                        "proofModule": bead["proofModule"],
                        "lessons": [],
                    },
                )
            )
            nodes.append(
                node(
                    "proof-" + id,
                    "test",
                    bead["requirements"],
                    fields={"module": bead["proofModule"], "level": "unit"},
                )
            )
        if integration:
            # The integration requirement is also owned by a bead, so ordinary
            # canonical publication retains its coverage prerequisite.
            first = next(n for n in nodes if n["kind"] == "bead")
            first["parents"] = list(dict.fromkeys(first["parents"] + integration_requirements))
            nodes.append(
                node(
                    "source-integration",
                    "test",
                    integration_requirements,
                    fields={"module": integration["proofModule"], "level": "e2e"},
                )
            )
        # A short disposable sample name avoids Windows Git worktree path limits
        # inside the unchanged canonical publication/job/run namespace.
        data = dict(schema="codex-harness/spine-v1", name="p", nodes=nodes)
    store = PlanStore(workspace)
    source = workspace / "source-fixture-spine.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    store.import_spine(source)
    vision = store.spine().node("V1")
    vision["fields"].update(
        vision="Source-only fixture",
        users="Local test controller",
        first_user_moment="Observe source mechanics",
        scope_in=["Recorded local contract"],
        scope_out="Real provider execution",
        assumptions=[],
    )
    store.upsert_node(vision)
    source_only_vision_lock(store)
    for phase in ("features", "requirements", "tech-stack", "plan", "roundtable"):
        if phase == "roundtable":
            store.compile_template(workspace / "template")
        store.advance(phase, by="source-test")
    store.compile_template(workspace / "template")
    known = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"
    declared_oracle = spine_file and Path(spine_file).resolve() == known / "spine.json"
    if declared_oracle:
        # This named fixture has an actual reference; its loop/subTest proofs do
        # not claim the prospective engine's straight-line meaningful-red scope.
        from codex_harness.core.test_audit import audit_revised_tests

        result = audit_revised_tests(
            repo=workspace,
            base_ref="template",
            revised_template=workspace / "template",
            manifest=store.compile_template(workspace / "template"),
            impact={"affectedBeads": store.spine().ids("bead")},
            oracle_dir=known / "reference",
        )
        assert result["ok"], result["findings"]
        store.record_audit(store.manifest_revision(), result)
        proof_level = "oracle-verified"
    else:
        audit = audit_synchronously(store)
        assert audit["proofCreditGranted"], [
            (row["code"], row.get("bead"), row.get("output", "")[:200])
            for row in (audit["result"] or {}).get("findings", [])
        ]
        proof_level = "prospective"
    source_controller_roundtable(
        store,
        process_stub=lambda *_: (0, "[]"),
        reviewers=[
            {"name": "source-codex", "provider": "codex"},
            {"name": "source-claude", "provider": "claude"},
        ],
        executable_for=lambda provider: provider,
    )
    store.advance("publish", by="source-test")
    revision = store.publish(
        by="source-test",
        export_dir=workspace / "export",
        proof_level=proof_level,
        reason="Real fixture audit; SOURCE-ONLY synthetic controller reviews",
    )
    return store, revision


def source_grant(store, revision, workers, *, max_parallel=2, timeout=900, call_cap=16):
    request = prepare_execution(
        store, revision, workers, max_parallel=max_parallel, timeout=timeout, call_cap=call_cap
    )
    result = complete_execution(
        store,
        request["request_id"],
        "accept",
        {
            "approve": True,
            "statement": "SOURCE-ONLY correlated native form fixture",
            "conditions": "",
        },
    )
    assert result["state"] == "approved" and result["human_identity_verified"] is False
    return request


def source_execution_context(workspace, plan, *, max_parallel=2, timeout=900, call_cap=16):
    # Gate-local TEMP may be too deep for Windows Git retry merges.
    # Retain originals for diagnosis; callers still own bounded-child cleanup.
    temp_root = Path(tempfile.gettempdir())
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        user_temp = Path(os.environ["LOCALAPPDATA"]) / "Temp"
        if user_temp.is_dir():
            temp_root = user_temp
    workspace = Path(tempfile.mkdtemp(prefix="n-", dir=temp_root))
    store, revision = publish_source_store(workspace, plan)
    workers = [
        dict(bead=id, provider="codex", model="gpt-6.1-sol", effort="medium")
        for id in store.published_manifest(revision)["beads"]
    ]
    source_grant(
        store, revision, workers, max_parallel=max_parallel, timeout=timeout, call_cap=call_cap
    )
    with patch.object(planner_jobs.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=12345)):
        job = planner_jobs.start_worker_job(
            store, revision, workers, max_parallel=max_parallel, timeout=timeout, call_cap=call_cap
        )
    store.close()

    class SourceHandoff(BaseException):
        pass

    def handoff(*args, **kwargs):
        raise SourceHandoff()

    # Real producer records the current PID; no test-only product authority flag.
    with patch.object(dispatch, "run_plan", handoff):
        try:
            planner_jobs.execute_worker_job(workspace, revision)
        except SourceHandoff:
            pass
    directory = Path(job["evidenceDir"])
    return dict(
        directory=directory,
        plan=directory / "plan",
        runs=directory / "runs",
        revision=revision,
        kwargs=dict(
            execution_job=directory,
            max_provider_calls=call_cap,
            max_parallel=max_parallel,
            timeout=timeout,
            provider_for={w["bead"]: w["provider"] for w in workers},
            model_for={w["bead"]: w["model"] for w in workers},
            effort_for={w["bead"]: w["effort"] for w in workers},
        ),
    )
