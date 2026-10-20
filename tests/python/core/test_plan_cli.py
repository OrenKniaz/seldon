"""Subprocess-only planning, including the guide's exact worked example."""

import json
import re
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from tests.python.core.planning_support import source_only_confirmation
from tests.python.core.test_plan_store import draft_inputs, draft_intake, mock_catalog, mock_choice
from tests.python.core.test_preflight import inject_mock_discovery

from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_cli import main, next_command
from codex_harness.core.plan_store import open_plan_store

ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs/operator/planner-guide.md"


def cli(workspace, *args, code=0, cwd=None, machine=False):
    command = [sys.executable, "-m", "codex_harness.core.plan_cli", "--workspace", str(workspace)]
    if machine:
        command.append("--json")
    result = subprocess.run(
        [*command, *args],
        cwd=cwd or workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == code, result.stdout + result.stderr
    if machine:
        output = json.loads(result.stdout)
        text = output["text"]
        assert output["isError"] == (code != 0)
        assert "\nNext: " not in text
        assert result.stdout.count('"next":') == 1
    else:
        output = result.stdout
        text = output
    assert text.startswith("Harness Plan Mode: ")
    if not machine:
        assert text.rstrip().splitlines()[-1].startswith("Next: ")
        assert sum(line.startswith("Next:") for line in text.splitlines()) == 1
    return output


def node(id, kind="feature", parents=None, **fields):
    return dict(id=id, kind=kind, title=id, text=id, parents=parents or ["V1"], fields=fields)


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_preflight_cold_missing_workspace_reports_without_creating(tmp_path, monkeypatch, capsys):
    inject_mock_discovery(monkeypatch)
    workspace = tmp_path / "missing" / "project"
    assert main(["--workspace", str(workspace), "--json", "preflight", "--provider", "codex"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["data"]["workspace"]["status"] == "missing"
    assert report["data"]["planDatabase"]["status"] == "missing"
    assert report["data"]["executionReadiness"] == "not-proven"
    assert report["data"]["didRunProvider"] is False
    assert report["status"]["phase"] == "unknown"
    assert not workspace.parent.exists()


def test_preflight_existing_store_and_invalid_inputs_never_open_state(started, monkeypatch, capsys):
    inject_mock_discovery(monkeypatch)
    before = {
        path.relative_to(started): path.read_bytes()
        for path in started.rglob("*")
        if path.is_file()
    }

    def forbidden(*args, **kwargs):
        pytest.fail("Preflight must bypass normal status/store opening")

    monkeypatch.setattr(PlannerServer, "_status", forbidden)
    with pytest.raises(SystemExit) as help_exit:
        main(["--workspace", str(started), "preflight", "--help"])
    assert help_exit.value.code == 0
    assert "--provider" in capsys.readouterr().out
    for flags, code in (
        ([], 0),
        (["--provider", "unsupported"], 2),
        (["--bad-option"], 2),
        (["--provider"], 2),
    ):
        assert main(["--workspace", str(started), "--json", "preflight", *flags]) == code
        report = json.loads(capsys.readouterr().out)
        assert report["status"]["phase"] == "unknown"
        assert report["isError"] == (code != 0)
        assert report["next"] and "\nNext: " not in report["text"]
        if code == 0:
            assert report["data"]["planDatabase"]["status"] == "available"
            assert report["data"]["isMocked"] is True
    after = {
        path.relative_to(started): path.read_bytes()
        for path in started.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.fixture
def started(tmp_path):
    cli(tmp_path, "start", "--idea", "Tiny", "--name", "Tiny")
    return tmp_path


def test_next_chain_once_in_machine_and_first_command_in_text(started):
    cli(started, "ask", "--question", "First?")
    cli(started, "ask", "--question", "Second?")
    output = cli(started, "status", machine=True)
    # Align may first require a confirmation card; either chain remains complete.
    assert " then " in output["next"]
    text = cli(started, "status")
    assert text.rstrip().splitlines()[-1] == "Next: " + output["next"].split(" then ", 1)[0]


@pytest.fixture
def native_worker_project(tmp_path, monkeypatch):
    from tests.python.core import test_planner_replay_gaps

    yield from test_planner_replay_gaps.project.__wrapped__(tmp_path, monkeypatch)


def test_worker_cli_routes_exact_tuple_and_reattaches_without_launch(
    native_worker_project, monkeypatch, capsys
):
    """Canonical synthetic native original replaces the old fake store; real CLI/jobs."""
    from types import SimpleNamespace

    from tests.python.core import test_planner_resume

    from codex_harness.core import planner_jobs

    store, launches = native_worker_project, []
    options = test_planner_resume._published(store)
    options["max_parallel"] = 1
    options["timeout"] = 45
    # Source-only native-store fixture, not a CLI-quoted grant or real human proof.
    from codex_harness.core.planner_execution import complete_execution, prepare_execution

    proposal = {
        key: options[key]
        for key in ("expected_revision", "workers", "timeout", "call_cap", "max_parallel")
    }
    prepared = prepare_execution(store, **proposal)
    assert (
        complete_execution(
            store,
            prepared["request_id"],
            "accept",
            {"approve": True, "statement": "SOURCE-ONLY native decision", "conditions": ""},
        )["state"]
        == "approved"
    )
    monkeypatch.setattr(
        planner_jobs.subprocess,
        "Popen",
        lambda *a, **k: launches.append((a, k)) or SimpleNamespace(pid=456),
    )
    prefix = ["--workspace", str(store.workspace), "--json"]
    workers = options["workers"]
    assert (
        main(
            [
                *prefix,
                "worker-start",
                "--expected-revision",
                options["expected_revision"],
                "--workers",
                json.dumps(workers),
                "--max-parallel",
                "1",
                "--timeout",
                "45",
                "--call-cap",
                "1",
            ]
        )
        == 0
    )
    started = json.loads(capsys.readouterr().out)
    assert started["data"]["options"] == dict(workers=workers, maxParallel=1, timeout=45, callCap=1)
    assert "worker-status --job-id " + options["expected_revision"] in started["next"]
    assert main([*prefix, "worker-status", "--job-id", options["expected_revision"]]) == 0
    reattached = json.loads(capsys.readouterr().out)
    assert reattached["data"] == started["data"] and len(launches) == 1


def test_cli_mcp_sqlite_reopen_and_chat_draft_roundtrip(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
    revision = inputs["expected_spine_revision"]

    def mutate(command, envelope):
        path = tmp_path / f"{command}.json"
        save(path, envelope)
        return cli(tmp_path, command, "--json", "@" + str(path), machine=True)["data"]

    intake = mutate("intake", dict(node=draft_intake(), expected_spine_revision=revision))
    assert intake["intake"]["recipe"] == "outcome-waves"
    # Actual subprocess CLI and protocol MCP alternate against the same canonical SQLite authority.
    server = PlannerServer(tmp_path)
    catalog = server.call_tool(
        "harness_reviewer_catalog",
        dict(catalog=mock_catalog(), expected_spine_revision=intake["spineRevision"]),
    )
    assert not catalog["isError"]
    server.close()
    choice = mutate(
        "reviewer-choice",
        dict(
            node=mock_choice(catalog["structuredContent"]["catalog"], "user"),
            expected_spine_revision=intake["spineRevision"],
        ),
    )
    inputs["expected_spine_revision"] = choice["spineRevision"]
    mapped = mutate("wave-map", inputs)
    output = cli(tmp_path, "waves", machine=True)
    assert output["data"]["accounting"]["fullVision"]["totalObligations"] == 5
    assert output["data"]["accounting"]["fullVision"]["completedRealObligations"] == 0
    assert (
        "Later meaningful refinement" in output["text"] and "Current exit facts" in output["text"]
    )
    assert "wave-map" not in output["next"] and "waves" in output["next"]
    # catalog CLI route uses exactly the same envelope as MCP, with no new observation authority.
    assert (
        mutate(
            "reviewer-catalog",
            dict(catalog=mock_catalog(), expected_spine_revision=mapped["spineRevision"]),
        )["catalog"]
        == catalog["structuredContent"]["catalog"]
    )
    server = PlannerServer(tmp_path)
    try:
        reopened = server.call_tool("harness_waves", {})
        assert reopened["structuredContent"] == output["data"]
        assert {
            key: reopened["structuredContent"]["nodeRevisions"][key]
            for key in mapped["nodeRevisions"]
        } == mapped["nodeRevisions"]
        assert "Later owner" in server.call_tool("harness_plan_status", {})["content"][0]["text"]
    finally:
        server.close()
    refused = cli(
        tmp_path, "wave-map", "--json", "@" + str(tmp_path / "wave-map.json"), machine=True, code=2
    )
    assert "plan-spine-stale" in refused["text"] and "status" in refused["next"]


def test_review_cli_malformed_fields_and_typed_map_decision_handling(tmp_path):
    with open_plan_store(tmp_path) as store:
        inputs = draft_inputs(store)
        store.record_wave_map(**inputs)
        before = store.spine().plan_revision()
        events = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
    node = draft_intake()
    node["fields"] = []
    path = tmp_path / "malformed.json"
    save(path, dict(node=node, expected_spine_revision=before))
    refused = cli(tmp_path, "intake", "--json", "@" + str(path), machine=True, code=2)
    assert "plan-draft-fields-invalid" in refused["text"]
    wrong_route = cli(
        tmp_path, "decision", "--id", "MAP", "--choose", "anything", machine=True, code=2
    )
    assert "Typed decisions must use" in wrong_route["text"]
    with open_plan_store(tmp_path) as store:
        assert store.spine().plan_revision() == before
        assert store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == events
    server = PlannerServer(tmp_path)
    try:
        status = {**server._status(), "phase": "tech-stack", "pending_gate": None}
        hint = next_command(server, status)
        assert "--choose" not in hint and "--id MAP" not in hint
        assert 'decision --json "@decision.json"' in hint
        assert server.store.phase() == "align"
    finally:
        server.close()


def test_guide_example_stops_at_synthetic_review_gate(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()  # Guide replay starts in a non-git project.
    guide = GUIDE.read_text(encoding="utf-8")
    assert len(guide.splitlines()) <= 140
    for name, block in zip(
        ("features", "requirements", "beads"),
        re.findall(r"```jsonl\n(.*?)\n```", guide, re.S),
        strict=True,
    ):
        (tmp_path / f"{name}.jsonl").write_text(block, encoding="utf-8")
    save(
        tmp_path / "decision.json",
        dict(
            id="D1",
            question="Runtime?",
            options=[dict(name="Python", pros="Stdlib", cons="Requires Python")],
            recommendation="Python",
            parents=["V1"],
        ),
    )
    for provider in ("codex", "claude"):
        save(
            tmp_path / f"{provider}-reviewer.json",
            dict(name=provider + "-reviewer", provider=provider),
        )
    save(tmp_path / "response.json", [])
    tests = workspace / "template/tests_cli"
    tests.mkdir(parents=True)
    for module, import_line, assertion in (
        ("one", "from mini_one import inc", "self.assertEqual(inc(2), 3)"),
        ("two", "from mini_two import twice", "self.assertEqual(twice(2), 6)"),
        (
            "end_to_end",
            "from mini_one import inc\nfrom mini_two import twice",
            "self.assertEqual(twice(inc(1)), 6)",
        ),
    ):
        (tests / f"test_{module}.py").write_text(
            f"import unittest\n{import_line}\nclass Proof(unittest.TestCase):\n"
            f"    def test_requirement(self):\n        {assertion}\n",
            encoding="utf-8",
        )
    for filename, function in (("mini_one.py", "inc"), ("mini_two.py", "twice")):
        (workspace / "template" / filename).write_text(
            f"def {function}(n):\n    raise NotImplementedError\n", encoding="utf-8"
        )
    oracle = workspace / "oracle"
    oracle.mkdir()
    (oracle / "mini_one.py").write_text("def inc(n): return n + 1\n", encoding="utf-8")
    (oracle / "mini_two.py").write_text(
        "from mini_one import inc\ndef twice(n): return 2 * inc(n)\n", encoding="utf-8"
    )
    commands = re.findall(
        r"^(?:p (.+)|# Native MCP: harness_record_confirmation gate=(align|vision-lock).*)$",
        guide,
        re.M,
    )
    assert len(commands) == 26
    question = None
    for line, gate in commands:
        if gate:
            # Native forms are represented by the shared explicit source-only
            # store fixture; this CLI guide replay never claims a human response.
            with open_plan_store(workspace) as store:
                source_only_confirmation(store, gate)
            continue
        args = shlex.split(line.replace("<Q>", question or "<Q>"))
        if args[0] == "roundtable":
            args += ["--test-only-review-output", str(tmp_path / "response.json")]
        if args == ["advance", "--to", "publish"]:
            refused = cli(workspace, *args, cwd=tmp_path, code=2)
            assert "successful review" in refused
            break  # Injected guide replay stops at the real review gate.
        output = cli(workspace, *args, cwd=tmp_path, code=2 if args == ["quality"] else 0)
        if args[0] == "ask":
            question = re.search(r"Question id: (\w+)", output)[1]
        if args[0] == "audit":
            assert "Test audit: PASS" in output
            assert "Integration not checked" not in output
    status = cli(workspace, "status", machine=True)["status"]
    assert status["phase"] == "roundtable" and not status["publication"]["up_to_date"]
    with open_plan_store(workspace) as store:
        assert not store.successful_reviews(store.manifest_revision())
        rows = list(store.connection.execute("SELECT isMocked, didRunProvider FROM review_runs"))
        assert rows and all(row["isMocked"] == 1 and row["didRunProvider"] == 0 for row in rows)
    assert "roundtable" in cli(workspace, "status")
    # Audit rejects contradictory tests through the real oracle path.
    proof = tests / "test_two.py"
    proof.write_text(proof.read_text().replace("twice(2), 6", "twice(2), 99"))
    cli(workspace, "node", "--json", json.dumps(node("R2", "requirement", ["S1"], changed=True)))
    audit = cli(workspace, "audit", "--oracle", "oracle", code=2, machine=True)
    assert audit["data"]["integrationChecked"] is True
    assert "revised-tests-unsatisfiable" in audit["text"]


def test_refused_gates_next_steps_and_attribution(started):
    workspace = started
    out = cli(workspace, "advance", "--to", "expand", code=2)
    assert "plan-approval-required" in out and "align needs approval" in out
    assert "card --vision" in out.splitlines()[-1]
    cli(workspace, "confirm", "--gate", "align", "--statement", "   ", code=2)
    missing = cli(workspace, "confirm", "--gate", "align", code=2, machine=True)
    assert "--statement" in missing["text"]
    cli(workspace, "advance", "--to", "requirements", code=2)
    cli(
        workspace,
        "confirm",
        "--gate",
        "align",
        "--by",
        "opus-user-proxy",
        "--statement",
        "invented",
        code=2,
    )
    cli(
        workspace,
        "card",
        "--vision",
        "Tiny",
        "--users",
        "Me",
        "--first-moment",
        "Hello",
        "--scope-in",
        "CLI",
        "--scope-out",
        "GUI",
    )
    assert "harness_record_confirmation gate=align" in cli(workspace, "status").splitlines()[-1]
    refusal = cli(
        workspace,
        "confirm",
        "--gate",
        "align",
        "--by",
        "opus-user-proxy",
        "--statement",
        "Opus (user proxy): Delegated test approval.",
        code=2,
    )
    assert "plan-confirmation-origin-unavailable" in refusal
    with open_plan_store(workspace) as store:
        assert store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
        source_only_confirmation(store, "align")
    assert "advance --to expand" in cli(workspace, "status").splitlines()[-1]
    with sqlite3.connect(workspace / ".codex-harness/plan.sqlite3") as db:
        assert db.execute('SELECT "by" FROM approvals').fetchone()[0] == "mcp-client-elicitation"
    cli(workspace, "node", "--json", json.dumps(node("F1")))
    assert "plan-approval-stale" in cli(workspace, "advance", "--to", "expand", code=2)
    assert "Compile a manifest before impact" in cli(workspace, "impact", code=2)
    assert "plan-publish-phase-required" in cli(workspace, "publish", code=2)


def test_batch_is_atomic_and_order_independent(started):
    workspace = started
    batch = workspace / "nodes.jsonl"
    batch.write_text(
        "\n".join(json.dumps(item) for item in [node("R1", "requirement", ["F1"]), node("F1")])
    )
    cli(workspace, "node", "--json", "@" + str(batch), machine=True)
    before = cli(workspace, "status", machine=True)["status"]["plan_revision"]
    batch.write_text(
        "\n".join(
            json.dumps(item)
            for item in [node("F1", changed=True), node("BAD", parents=["missing"])]
        )
    )
    assert "spine-unknown-parent" in cli(workspace, "node", "--json", "@" + str(batch), code=2)
    assert cli(workspace, "status", machine=True)["status"]["plan_revision"] == before
    batch.write_text(json.dumps(node("F2")) + "\n{broken")
    cli(workspace, "node", "--json", "@" + str(batch), code=2)
    assert cli(workspace, "status", machine=True)["status"]["plan_revision"] == before
    duplicate = [node("F2"), node("F2")]
    cli(workspace, "node", "--json", json.dumps(duplicate), code=2)
    cli(workspace, "node", "--json", "[]", code=2)
    cli(workspace, "remove-node", "--id", "F1", code=2)
    cli(workspace, "remove-node", "--id", "R1")
    cli(workspace, "remove-node", "--id", "F1")


def test_questions_assumptions_render_and_findings(started):
    workspace = started
    question = cli(workspace, "ask", "--question", "Who?", "--node", "V1", machine=True)
    question_id = re.search(r"Question id: (\w+)", question["text"])[1]
    cli(workspace, "answer", "--id", question_id, "--text", "Operator")
    assumed = cli(workspace, "assume", "--text", "Offline")
    assumption = re.search(r"Assumption id: (\w+)", assumed)[1]
    cli(
        workspace,
        "resolve-assumption",
        "--id",
        assumption,
        "--rejected",
        "--rationale",
        "Scope excluded",
        "--by",
        "planner",
    )
    with open_plan_store(workspace) as store:
        source_only_confirmation(store, "align")
    cli(workspace, "advance", "--to", "expand")
    cli(workspace, "render", "--out", "views")
    assert (workspace / "views/VISION.md").is_file()
    cli(workspace, "render", "--out", "../escape", code=2)
    response = workspace / "response.json"
    save(
        response,
        [
            dict(
                node_id="V1",
                severity="major",
                finding="Clarify scope",
                suggestion="Document limits",
            )
        ],
    )
    cli(
        workspace,
        "node",
        "--json",
        json.dumps(
            dict(
                id="V1",
                kind="vision",
                title="Tiny",
                text="Tiny",
                parents=[],
                fields={"validation": ["-m", "unittest"], "requirementFiles": []},
            )
        ),
    )
    args = [
        "roundtable",
        "--reviewer",
        json.dumps(dict(name="test", provider="codex")),
        "--test-only-review-output",
        str(response),
    ]
    cli(workspace, *args)
    finding = cli(workspace, "findings", machine=True)["data"]["findings"][0]
    cli(workspace, "resolve", "--id", finding["id"], "--resolution", "   ", code=2)
    cli(
        workspace,
        "resolve",
        "--id",
        finding["id"],
        "--resolution",
        "Limits already explicit",
        "--rejected",
    )
    rows = cli(workspace, "findings", machine=True)["data"]["findings"]
    assert rows[0]["status"] == "rejected" and rows[0]["resolution"] == "Limits already explicit"
    report = cli(workspace, "quality", machine=True, code=2)
    assert report["data"]["planRevision"] == report["status"]["plan_revision"]
    response.write_text("not a finding array")
    cli(workspace, *args, code=2)


def test_roundtable_policy_contract_example_runs_real_parser_and_records_roles(tmp_path):
    from tests.python.core.test_plan_store import FIXTURE, _link_r6, _to

    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        _link_r6(store)
        _to(store, "roundtable")
        before = list(store.connection.iterdump())
        contract = cli(tmp_path, "roundtable", "--policy-schema", machine=True)["data"]
        assert list(store.connection.iterdump()) == before
        assert "Explicit --policy" in contract["adoption"]
        assert "latest-success" in contract["adoption"]
        for name, data in contract["exampleFiles"].items():
            (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
        response = tmp_path / "source-review.json"
        response.write_text("[]", encoding="utf-8")
        result = cli(
            tmp_path,
            *contract["exampleArgs"],
            "--test-only-review-output",
            str(response),
            machine=True,
        )
        state = result["data"]["reviewPolicy"]
        assert state["active"] and state["declaredRevision"] == store.manifest_revision()
        assert state["slots"][0]["name"] == contract["example"]["slots"][0]["name"]
        assert state["eligible"] is False  # Explicit synthetic review cannot earn native credit.
        assert result["data"]["isMocked"] is True and result["data"]["didRunProvider"] is False
        recorded = store._plan_review_policies()[0]["payload"]
        assert recorded["slots"] == contract["example"]["slots"]
        assert recorded["manifestRevision"] == contract["example"]["expectedManifestRevision"]
        current_contract = cli(tmp_path, "roundtable", "--policy-schema", machine=True)["data"]
        assert current_contract["example"] == contract["example"]


def test_policy_public_help_and_schema_warn_without_adoption(tmp_path):
    from tests.python.core.test_plan_store import FIXTURE, _link_r6, _to

    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        _link_r6(store)
        _to(store, "roundtable")
        before = list(store.connection.iterdump())
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "codex_harness.core.plan_cli",
                "--workspace",
                str(tmp_path),
                "roundtable",
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0
        help_text = " ".join(result.stdout.split()).lower()
        assert "no supported verified finding-closure producer" in help_text
        assert "quality and publication held" in help_text
        response = cli(tmp_path, "roundtable", "--policy-schema", machine=True)
        warning = response["data"]["adoptionWarning"]
        assert "no supported verified finding-closure producer" in warning.lower()
        assert "quality and publication held" in warning.lower()
        assert "ordinary policy-free work is unchanged" in warning.lower()
        assert "roundtable --policy @" not in response["next"]
        assert list(store.connection.iterdump()) == before
        assert not store._plan_review_policies()


def test_direct_policy_warns_before_actual_writer_and_preserves_public_json(
    tmp_path, monkeypatch, capsys
):
    from tests.python.core.test_plan_store import FIXTURE, _link_r6, _to

    from codex_harness.core.plan_store import PlanStore

    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        _link_r6(store)
        _to(store, "roundtable")
        policy = cli(tmp_path, "roundtable", "--policy-schema", machine=True)["data"]["example"]
        response = tmp_path / "source-only-review.json"
        response.write_text("[]", encoding="utf-8")
        original = PlanStore.record_plan_review_policy
        observed = []

        def writer(current, slots, revision):
            # Actual parser/main/SQLite writer; observe warning BEFORE mutation.
            emitted = capsys.readouterr()
            assert "no supported verified finding-closure producer" in emitted.err.lower()
            assert "quality and publication held" in emitted.err.lower()
            assert not current._plan_review_policies()
            observed.append(emitted.err)
            return original(current, slots, revision)

        monkeypatch.setattr(PlanStore, "record_plan_review_policy", writer)
        code = main(
            [
                "--workspace",
                str(tmp_path),
                "--json",
                "roundtable",
                "--policy",
                json.dumps(policy),
                "--reviewer",
                json.dumps({"name": "reviewer", "provider": "codex"}),
                "--test-only-review-output",
                str(response),
            ]
        )
        output = json.loads(capsys.readouterr().out)
        assert code == 0 and len(observed) == 1
        assert output["data"]["reviewPolicy"]["active"]
        assert output["data"]["isMocked"] and not output["data"]["didRunProvider"]
        assert store._plan_review_policies()[0]["payload"]["slots"] == policy["slots"]


@pytest.mark.parametrize("adopt", [False, True])
def test_direct_public_policy_warning_and_ordinary_reciprocal(tmp_path, adopt):
    from tests.python.core.test_plan_store import FIXTURE, _link_r6, _to

    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        _link_r6(store)
        _to(store, "roundtable")
        policy = cli(tmp_path, "roundtable", "--policy-schema", machine=True)["data"]["example"]
        response = tmp_path / "source-only-review.json"
        response.write_text("[]", encoding="utf-8")
        args = [
            sys.executable,
            "-B",
            "-m",
            "codex_harness.core.plan_cli",
            "--workspace",
            str(tmp_path),
            "--json",
            "roundtable",
            "--reviewer",
            json.dumps({"name": "reviewer", "provider": "codex"}),
            "--test-only-review-output",
            str(response),
        ]
        if adopt:
            args.extend(["--policy", json.dumps(policy)])
        result = subprocess.run(
            args,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        data = json.loads(result.stdout)["data"]
        assert ("No supported verified finding-closure producer" in result.stderr) is adopt
        assert ("quality and publication held" in result.stderr) is adopt
        assert bool(store._plan_review_policies()) is adopt
        assert data["isMocked"] and not data["didRunProvider"]
        assert ("reviewPolicy" in data) is adopt
        # Canonical producer reservation IDs bind the actual stored run, even
        # with a synthetic response; no relabelling of native/provider credit.
        rows = list(
            store.connection.execute(
                "SELECT a.rowid, a.id, r.id FROM review_attempts a "
                "JOIN review_runs r ON r.id=a.id ORDER BY a.rowid"
            )
        )
        assert len(rows) == 1 and rows[0][1] == rows[0][2]
