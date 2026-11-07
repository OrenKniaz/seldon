"""Scripted clients and providers; real stdio MCP, processes, verifier and delivery."""

import hashlib
import json
import os
import queue
import runpy
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import venv
from pathlib import Path

import pytest

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.fast


def cold_start_golden(workspace):
    """Replay ShelfLife r10 through installed entry points in fresh -I interpreters.

    The provider host preimports dispatch, hiding the approvals/roundtable cycle.
    Build/install this candidate, exercise every console entry plus module callers,
    then observe init persistence, its registered MCP handshake and owned removal.
    """
    started = time.monotonic()
    workspace.mkdir()
    env = {**os.environ, "TEMP": str(workspace), "TMP": str(workspace),
           "UV_CACHE_DIR": str(workspace / "cache"), "UV_PYTHON_DOWNLOADS": "never"}

    def run(argv, input=""):
        result = subprocess.run(argv, input=input, capture_output=True, text=True,
                                encoding="utf-8", cwd=workspace, env=env, timeout=60)
        assert result.returncode == 0, (argv, result.stdout, result.stderr)
        return result.stdout

    run([shutil.which("uv"), "build", str(ROOT), "--wheel", "--out-dir", str(workspace)])
    venv.EnvBuilder(with_pip=False).create(workspace / "installed")
    python = workspace / "installed" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([shutil.which("uv"), "pip", "install", "--python", str(python), "--no-deps",
         str(next(workspace.glob("*.whl")))])
    project = workspace / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Preserve project instructions.\n", encoding="utf-8")
    baseline = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["scripts"]
    installed = json.loads(run([str(python), "-I", "-B", "-c",
        "import json; from importlib.metadata import distribution; "
        "print(json.dumps({e.name:e.value for e in distribution('seldon').entry_points "
        "if e.group == 'console_scripts'}))"]))
    assert installed == scripts
    initialize = json.dumps(dict(jsonrpc="2.0", id=1, method="initialize", params=dict(
        protocolVersion="2025-06-18", capabilities={}, clientInfo=dict(name="cold-golden", version="1"))))

    def handshake(argv):
        response = json.loads(run(argv, initialize + "\n"))
        assert response["id"] == 1 and response["result"]["serverInfo"], response
        assert response["result"]["protocolVersion"] == "2025-06-18"

    for name in installed:
        # Match generated console wrappers while forcing Python isolation, with no
        # application imports before the requested entry point is loaded.
        argv = [str(python), "-I", "-B", "-c",
            "import sys; from importlib.metadata import distribution; name = sys.argv.pop(1); "
            "sys.exit(next(e for e in distribution('seldon').entry_points "
            "if e.group == 'console_scripts' and e.name == name).load()())", name]
        if name == "seldon-mcp":
            handshake([*argv, "--workspace", str(project)])
        else:
            assert "usage:" in run([*argv, "--help"])
    for module in ("seldon_cli", "plan_cli"):
        assert "usage:" in run([str(python), "-I", "-B", "-m", "codex_harness.core." + module, "--help"])
    for module in ("seldon_mcp", "mcp_planner"):
        handshake([str(python), "-I", "-B", "-m", "codex_harness.core." + module,
                   "--workspace", str(project)])
    cli = [str(python), "-I", "-B", "-m", "codex_harness.core.seldon_cli", "init", str(project)]
    run([*cli, "--host", "codex", "--approval-mode", "prompt", "--yes"])
    config = tomllib.loads((project / ".codex/config.toml").read_text("utf-8"))["mcp_servers"]["seldon"]
    assert Path(config["command"]).resolve() == python.resolve()
    assert config["default_tools_approval_mode"] == "prompt"
    handshake([config["command"], *config["args"]])
    assert (project / "SELDON.md").is_file() and (project / ".codex-harness/.seldon-init").is_file()
    run([*cli, "--check"])
    run([*cli, "--remove", "--yes"])
    assert not (project / "SELDON.md").exists() and not (project / ".codex-harness/.seldon-init").exists()
    retained = tomllib.loads((project / ".codex/config.toml").read_text("utf-8"))["mcp_servers"]["seldon"]
    assert not {"command", "args", "env"} & retained.keys()
    assert retained["default_tools_approval_mode"] == "prompt"
    assert all((project / p).read_bytes() == data for p, data in baseline.items())
    print(f"golden cold startup: {len(installed)} installed entries; init/check/handshake/remove; seconds={time.monotonic() - started:.2f}")



def scripted_judgment(call, judgment=None, meaning=None, verdict="positive", gaps=None, response=None,
                      *, rationale="Scripted fixture assessment; no actual provider or human authentication",
                      **request):
    """One test-only provider for any 1j question, with explicit semantics supplied by its caller.

    Pass a prepared request or public prepare arguments. Never infer approval, equivalence,
    reuse, continuation or acceptance from an empty response; the real 1j owner decides
    binding, pending state and exact-fix disposition. Raw replies exercise that same owner.
    """
    if judgment is None:
        judgment = call("harness_prepare_judgment", **request)
    example = dict(judgment["responseExample"], verdict=verdict, rationale=rationale,
                   meaning=meaning, gaps=gaps or [])
    reply = call("harness_submit_judgment", judgment_id=judgment["id"],
                response=response if response is not None else json.dumps(example), source="golden:scripted-semantic-provider")
    current = call("harness_judgment", judgment_id=judgment["id"],
                   **({"candidate": judgment["candidate"]} if judgment.get("candidate") else {}))
    assert current["binding"] == judgment["binding"]
    return dict(reply, current=current)


def scripted_review_reply(launch, findings, *, verdict, rationale, gaps=()):
    """Script only a provider reply to the actual bound review packet."""
    request = json.loads(launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split(
        "\nReturn ONLY JSON", 1)[0])
    # R9 regression: prior fixtures invented a valid depth, hiding absent packet guidance.
    reply = request["responseExample"]
    reply.update(verdict=verdict, rationale=rationale, gaps=list(gaps))
    reply["meaning"]["findings"] = findings
    return json.dumps(reply)


def scripted_worker(packet, root, files):
    """Script worker-authored files in response to the delivered packet, never proof results."""
    instructions = packet.split("\n\n")[1]
    assert "SELDON-STUB" not in instructions and "exits 3" not in instructions, instructions
    assert "unit tests" not in instructions, instructions
    for name, source in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        if source is None:
            (root / name).unlink(missing_ok=True)
        else:
            (root / name).write_text(source, encoding="utf-8")


def settle_decision(call, outcome, decision="approve"):
    # These old delivery fixtures explicitly intend approval and preserve scope when
    # adding proof adapters. The separate intent journey scripts ambiguity/narrowing.
    meanings = {"vision-expansion": {}, "user-decision": {"decision": decision},
                "vision-intent": {"impact": "preserved"}}
    for _ in range(3):
        if outcome.get("state") != "pending-judgment":
            return outcome
        judgment = outcome["judgment"]
        reply = scripted_judgment(call, judgment, meanings[judgment["purpose"]])
        outcome = reply["decision"]
    assert outcome["state"] != "pending-judgment", outcome
    return outcome


def judge_executions(workspace, patcher, *, contracts_gap=None, pending_execution=False):
    """One fake semantic provider for real dispatch observations, shared by all journeys."""
    from codex_harness.core import dispatch
    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.planner_candidate import assess_execution
    from tests.python.core.mcp_planner_support import workspace_wire

    observe = dispatch._semantic_validation

    def judged(root, plan, record, phase):
        proof = observe(root, plan, record, phase)
        if pending_execution or proof["state"] == "incomplete":
            return proof
        with_server = PlannerServer(workspace)
        try:
            def call(tool, **args):
                reply = workspace_wire(with_server, tool, expected_workspace=str(workspace), **args)
                assert not reply["isError"], reply
                return reply["structuredContent"]

            with_server._store()
            with with_server.store._transaction():
                with_server.store._event("execution-observed", proof)
                event = with_server.store.connection.execute("SELECT max(id) FROM events").fetchone()[0]
            prepared = call("harness_prepare_judgment", question="Assess meaningfulness, correct RED and stub/mock gaps",
                subject_ids=with_server.store.spine().ids(), source_event_ids=[event], candidate=proof["candidate"])
            gaps = contracts_gap() if contracts_gap else []
            meaning = dict(check="execution", evidenceBinding=proof["evidenceBinding"], phase=proof["phase"], meaningful=True,
                correctFailureReason=True, noStubMockGaps=True,
                qualitativeChecks={o["check"]: True for p in [proof, proof.get("red") or proof] for r in p["results"]
                                   for o in r.get("observations", []) if o["passed"] is None})
            reply = scripted_judgment(call, prepared, meaning, "negative" if gaps else "positive", gaps)
            return assess_execution(proof, reply["current"])
        finally:
            with_server.close()

    patcher.setattr(dispatch, "_semantic_validation", judged)


def judge_candidate(call, observation):
    """Replace legacy static candidate verdicts with the public bound 1j request."""
    identity_regression = observation.pop("identity_regression", False)
    observation = {k: v for k, v in observation.items() if k != "judgment_id"}
    prepared = call("harness_accept_assess", judgment=observation)
    packet = prepared["sourceRefs"][0]["payload"]
    if identity_regression:
        # R14: handwritten 'execution' replies hid the accepted-but-ignored
        # contracts label. Public prepare/submit/read must teach both identities.
        unknown = call("harness_accept_assess", judgment=dict(repo=observation["repo"], check="contracts", judgment_id=prepared["id"]), allow_error=True)
        assert unknown["isError"] and packet["check"] in str(unknown) and packet["evidenceBinding"] in str(unknown), unknown
        for change in ({"check": "contracts"}, {"evidenceBinding": "wrong-observation-digest"}):
            wrong = dict(check=packet["check"], evidenceBinding=packet["evidenceBinding"], phase=packet["phase"])
            wrong.update(change)
            rejected = scripted_judgment(call, prepared, wrong)
            assert rejected["disposition"] == "exact-fix" and not rejected["current"]["positiveCredit"], rejected
            assert packet["check"] in rejected["error"] and packet["evidenceBinding"] in rejected["error"], rejected
        missing = call("harness_prepare_judgment", question="Assess candidate", subject_ids=["V1"],
                       evidence_ids=[packet["evidenceBinding"]], allow_error=True)
        assert missing["isError"] and packet["evidenceBinding"] in str(missing)
        assert "evidence-record" in str(missing) and "harness_accept_assess" in str(missing), missing
    if observation["check"] != "execution":
        # CC's real web gate reached this distinct semantic request. Execution-only
        # fixtures missed both its preparation refusal and cross-phase credit.
        rejected = scripted_judgment(call, prepared, dict(prepared["responseExample"]["meaning"], phase="wrong-phase"))
        assert rejected["disposition"] == "exact-fix" and not rejected["current"]["positiveCredit"], rejected
        assert packet["check"] in rejected["error"] and packet["phase"] in rejected["error"], rejected
    scripted_judgment(call, prepared, dict(check=packet["check"], evidenceBinding=packet["evidenceBinding"], phase=packet["phase"],
        meaningful=True, correctFailureReason=True, noStubMockGaps=True,
        qualitativeChecks={o["check"]: True for p in [packet["proof"], packet["proof"].get("red") or packet["proof"]] for r in p["results"]
                           for o in r.get("observations", []) if o["passed"] is None}))
    return call("harness_accept_assess", judgment=dict(repo=observation["repo"], check=observation["check"],
                                                     judgment_id=prepared["id"]))


def planning_intent_golden(workspace):
    """Protect intake→Lock→own words→reload and complete-field narrowing.

    Catch count-based ceremony, keyword consent and hidden intent changes outside
    the former field subset. Stage 1a proves trace links, not semantic authority;
    1j proves judgment storage, not canonical intake/consent consumers.
    """
    from copy import deepcopy
    from tests.python.core.mcp_planner_support import workspace_wire
    from tests.python.core.semantic_wiring_support import node
    from codex_harness.core.mcp_planner import PlannerServer

    workspace.mkdir(parents=True)
    server = PlannerServer(workspace)

    def call(tool_name, **args):
        reply = workspace_wire(server, tool_name, expected_workspace=str(workspace), **args)
        assert not reply["isError"], (tool_name, reply)
        return reply.get("structuredContent", {})

    def reload():
        nonlocal server
        server.close()
        server = PlannerServer(workspace)
        server._store()

    def words(card, text):
        return call("harness_record_chat_confirmation", gate=card["gate"], request_id=card["request_id"],
                    expected_revision=card["plan_revision"], quoted_message=text,
                    quote_sha256=hashlib.sha256(text.encode()).hexdigest(), actor_label="scripted owner")

    try:
        call("harness_plan_start", idea="Tune both existing sensor channels", name="sensor", plan_mode="semantic-v1")
        existing = node("EX", "feature", ["V1"])
        existing["text"] = "Read both sensor channels"
        call("harness_upsert_node", node=existing)
        ref = dict(id="EX", revision=server.store.spine().revision("EX"))
        evidence = node("BASE", "result", ["EX"], evidence=dict(itemRef=ref, candidate="retained-baseline",
            source=dict(reference="fixture:baseline", realProvider=False, historical=True),
            observation=dict(reference="fixture:two-channel-readback", outcome="passed"), state="integrated"))
        call("harness_upsert_node", node=evidence)
        template = call("harness_schema", tool="harness_intake")["draftTemplate"]["node"]
        template["fields"].update(sizeEstimate="large", recipe="slim: direct trace and one review",
            requestKind="refactor", ambiguity=dict(level="high", reasons=["Calibration not measured"]),
            dependencyBreadth=dict(producerIds=["sensor", "analyser"], crossWaveContracts=["readback"]))
        route = call("harness_intake", node=template, expected_spine_revision=server.store._revision())["routing"]
        assert route["state"] == "pending-judgment" and route["size"] is None
        scripted_judgment(call, route["judgment"], dict(size="tiny", process="Direct requirement/test trace, one bounded review"))
        route = call("harness_intake", node=template, expected_spine_revision=server.store._revision())["routing"]
        assert route["size"] == "tiny" and "Direct" in route["process"] and route["overrideAvailable"]
        template["fields"]["sizeOverride"] = dict(size="small", words="Treat this as small so we review the migration", sourceRef="fixture:owner-message-1")
        override = call("harness_intake", node=template, expected_spine_revision=server.store._revision())["routing"]
        scripted_judgment(call, override["judgment"], dict(size="tiny", process="Collapsed trace with migration review", overrideAccepted=True))
        reload()
        route = call("harness_intake", node=template, expected_spine_revision=server.store._revision())["routing"]
        assert route["size"] == "small" and route["userOverride"]["sourceRef"] == "fixture:owner-message-1"
        facts = route["judgment"]["sourceRefs"][0]["payload"]["facts"]
        assert {n["id"] for n in facts["baseline"]["nodes"]} >= {"EX", "BASE"}
        vision = server.store.spine().node("V1")
        vision["fields"].update(
            quality_bar="Preserve readback while calibrating", done_feels_like="Residual noise meets the agreed metric",
            qualityCriteria=dict(ux="Readable channel status", performance="Readback under 20ms", maintainability="One calibration owner",
                architecture="Existing channel interface", observability="Retain metric per iteration", failures="Keep last valid calibration"),
            expansion=dict(mode="macro and micro", reason="One useful upgrade suffices for this small dream", upgrades=[
                dict(id="UP1", text="Retain the best calibration", kind="micro", impact="Recover after a bad iteration", disposition="accept")]),
            research=dict(reason="Optimum is unknown", choices=["Measured sweep", "Adaptive search"], sourceRef="fixture:research-choice"),
            openLoop=dict(metric="Residual noise dB", provenance="fixture:owner-metric", stopConditions=["Reach -30dB", "Owner stops the search"]),
            brownfield=dict(behaviorRefs=["EX"], evidenceRefs=["BASE"], reason="Map existing readback before changing calibration"),
            operatingEnvelope=dict(channels=[1, 2]))
        call("harness_upsert_node", node=vision)
        call("harness_upsert_node", node=node("R_UP", "requirement", ["EX"], expansionRefs=["UP1"]))
        card = call("harness_prepare_chat_confirmation", gate="align+vision-lock")
        pending = words(card, "Yes")
        assert pending["state"] == "pending-judgment" and pending["approval_id"] is None
        after_expansion = scripted_judgment(call, pending["judgment"], {})["decision"]
        assert after_expansion["judgment"]["purpose"] == "user-decision"
        scripted_judgment(call, after_expansion["judgment"], {}, verdict="inconclusive")
        reload()
        assert not call("harness_plan_status")["approval"]["granted"]
        quote = after_expansion["judgment"]["sourceRefs"][0]["payload"]["facts"]
        assert quote["words"] == "Yes" and quote["request_id"] == card["request_id"]
        pending = words(card, "Proceed with both channels and keep the best calibration")
        approved = scripted_judgment(call, pending["judgment"], dict(decision="approve"))["decision"]
        assert approved["state"] == "approved" and not approved["human_identity_verified"]
        reload()
        assert call("harness_plan_status")["approval"]["granted"]
        locked = server.store.locked_acceptance_spine().node("V1")
        assert locked["fields"]["openLoop"]["provenance"] == "fixture:owner-metric"
        from codex_harness.core.planner_vision import expansion_status
        assert expansion_status(locked, downstream_nodes=server.store.list_nodes())["untracedAcceptedUpgradeRefs"] == []
        narrowed = deepcopy(locked)
        narrowed["fields"]["operatingEnvelope"]["channels"] = [1]
        changed = call("harness_upsert_node", node=narrowed)
        assert changed["judgment"]["status"] == "pending-judgment"
        assert not call("harness_plan_status")["approval"]["granted"]
        scripted_judgment(call, changed["judgment"], dict(impact="narrowed"))
        card = call("harness_prepare_chat_confirmation", gate="align+vision-lock")
        assert card["deviation"]["impact"] == "narrowed" and "Proposed charter deviation" in card["card"]
        assert any("operatingEnvelope" in d["field"] for d in card["diff"])
        final = settle_decision(call, words(card, "I approve the deviation: drop channel two"))
        assert final["state"] == "approved"
        reload()
        assert server.store.locked_acceptance_spine().node("V1")["fields"]["operatingEnvelope"]["channels"] == [1]
        assert call("harness_plan_status")["approval"]["granted"]
        print("golden planning: tiny process; explicit size override; one accepted upgrade; ambiguous consent pending; visible narrowing approved")
    finally:
        server.close()


def fixture_ownership_golden(product):
    """Protect external fixture ownership through real CSV -> SQLite -> HTTP.

    Earlier setup overwrote snapshot code or supplied neutral-extension source;
    suffix and argv membership checks then rejected legitimate importer data.
    """
    from copy import deepcopy
    from codex_harness.core.planner_candidate import observation_files

    product.mkdir()
    (product / "index.html").write_text("<h1>Shelf items</h1>", encoding="utf-8")
    (product / "app.py").write_text("raise RuntimeError('candidate source must remain unchanged')\n", encoding="utf-8")
    (product / "importer.py").write_text(
        "import csv,sqlite3,sys\nfrom pathlib import Path\n"
        "assert Path(sys.argv[4]).read_text(encoding='utf-8') == '<h1>Shelf items</h1>'\n"
        "rows=list(csv.DictReader(Path(sys.argv[1]).open()))\n"
        "with sqlite3.connect(sys.argv[3]) as db:\n"
        " db.execute('create table items (name text,quantity integer)')\n"
        " db.executemany('insert into items values (?,?)',[(r['name'],int(r['quantity'])) for r in rows])\n"
        "print('imported',len(rows))\n", encoding="utf-8")
    (product / "server.py").write_text(
        "import json,os,sqlite3,sys\nfrom http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer\n"
        "class Handler(SimpleHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  if self.path!='/items': return super().do_GET()\n"
        "  with sqlite3.connect(sys.argv[1]) as db: rows=db.execute('select name,quantity from items').fetchall()\n"
        "  body=json.dumps(rows).encode(); self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)\n"
        "ThreadingHTTPServer(('127.0.0.1',int(os.environ['SELDON_PORT'])),Handler).serve_forever()\n", encoding="utf-8")
    (product / "receipt_module").mkdir()
    (product / "receipt_module/__init__.py").write_text("", encoding="utf-8")
    (product / "receipt_module/__main__.py").write_text("from . import loader\n", encoding="utf-8")
    (product / "receipt_module/loader.py").write_text("import importer\n", encoding="utf-8")
    spec = dict(targets={"web": dict(kind="web", entry="index.html", server=dict(
        entry="server.py", runtime=sys.executable, argv=[{"input": "{data}/pantry.sqlite3"}], readyPath="/items", readyWithin=5))},
        scenarios=dict(schema="seldon/scenarios-v1", scenarios=[dict(id="fixtures", requirementRefs=["R1"], contractRefs=[],
            setup=dict(dirs=["{data}/fixtures"], files=[dict(path="{data}/fixtures/items.csv", utf8="name,quantity\nTea,2\n"),
                dict(path="{data}/blob.bin", base64="AAEC")]),
            actions=[dict(id="import", cwd="receipt_module", process=dict(entry="importer.py", runtime=sys.executable, argv=[{"input": "{data}/fixtures/items.csv"}, "--db", {"input": "{data}/pantry.sqlite3"}, {"path": "index.html", "relative": True}])),
                dict(id="read", http=dict(target="web", method="GET", path="/items", statusEquals=200, jsonEquals=[["Tea", 2]])),
                dict(id="not-served", http=dict(target="web", method="GET", path="/DATA/fixtures/items.csv", statusEquals=404))],
            observe=[dict(action="import", exitEquals=0, stdoutEquals="imported 1\n"), dict(action="read"), dict(action="not-served"),
                dict(path="{data}/blob.bin", fileBytesEquals="\u0000\u0001\u0002")], verdict="all")]))

    # Public canonical writer -> persisted scenario -> actual preview consumer.
    # CC's web gate exposed a writer failure that direct runner fixtures missed.
    from codex_harness.core.mcp_planner import PlannerServer
    from tests.python.core.mcp_planner_support import workspace_wire
    from tests.python.core.semantic_wiring_support import node
    plan_root = product.parent / "fixture-plan"
    plan_root.mkdir()
    server = PlannerServer(plan_root)
    try:
        def call(tool, **args):
            result = workspace_wire(server, tool, expected_workspace=str(plan_root), **args)
            assert not result["isError"], result
            return result["structuredContent"]
        call("harness_plan_start", idea="Import CSV and display shelf items over HTTP", name="external-data", plan_mode="semantic-v1")
        vision = server.store.spine().node("V1")
        vision["fields"].update(targets=spec["targets"], scenarios=spec["scenarios"])
        call("harness_upsert_nodes", nodes=[node("F1", "feature", ["V1"]), node("R1", "requirement", ["F1"]), vision])
        stored = server.store.spine().node("V1")["fields"]
        spec = dict(targets=stored["targets"], scenarios=stored["scenarios"])
    finally:
        server.close()

    def preview(document):
        (product / "SCENARIOS.json").write_text(json.dumps(document), encoding="utf-8")
        before = observation_files(product)
        result = subprocess.run([sys.executable, "-B", "-m", "codex_harness.core.semantic_runner", "--preview", "SCENARIOS.json", "fixtures"],
            cwd=product, capture_output=True, text=True, timeout=45, check=True)
        assert observation_files(product) == before  # Every candidate byte, not only app.py.
        return json.loads(result.stdout)

    # Separate storage alone let fixture bytes become the Python entry. Earlier
    # coverage checked writes, never the code origin consumed by the launch owner.
    invalid = deepcopy(spec)
    invalid["targets"]["fixture"] = dict(entry="{data}/app.py", argv=[])
    scenario = invalid["scenarios"]["scenarios"][0]
    scenario["setup"] = dict(files=[dict(path="{data}/app.py", utf8="print('fixture-code')\n")])
    scenario["actions"] = [dict(id="exec", cli=dict(target="fixture", operation="run", argv=[]))]
    scenario["observe"] = [dict(action="exec", stdoutEquals="fixture-code\n")]
    refusal = preview(invalid)
    assert refusal["state"] != "PASS" and "entry" in str(refusal) and "candidate" in str(refusal), refusal

    # A missing candidate module must never fall back to installed pytest code.
    invalid["targets"]["fixture"] = dict(module="pytest", argv=["--version"])
    refusal = preview(invalid)
    assert refusal["state"] != "PASS" and "module" in str(refusal) and "candidate" in str(refusal), refusal

    for position in ("cwd", "PYTHONPATH", "directory", "process-entry"):
        invalid = deepcopy(spec)
        scenario = invalid["scenarios"]["scenarios"][0]
        if position == "cwd":
            scenario["actions"][0]["cwd"] = "{data}"
        elif position == "PYTHONPATH":
            scenario["actions"][0]["process"]["env"] = dict(PYTHONPATH="{data}")
        elif position == "process-entry":
            scenario["actions"][0]["process"]["entry"] = "{data}/app.py"
        else:
            invalid["targets"]["web"]["server"] = dict(argv=[sys.executable, "-m", "http.server", "--directory", "{data}"], readyPath="/", readyWithin=5)
        refusal = preview(invalid)
        assert refusal["state"] != "PASS" and "candidate" in str(refusal), refusal
        assert ("cwd" if position == "cwd" else "env" if position == "PYTHONPATH" else "entry") in str(refusal), refusal
        assert not refusal.get("cleanup", {}).get("childLaunched"), refusal

    result = preview(spec)
    assert result["state"] == "PASS", result
    csv = Path(result["actions"]["import"]["command"][2])
    data = csv.parent.parent
    snapshot = Path(result["actions"]["read"]["server"]["cwd"])
    assert Path(result["actions"]["import"]["cwd"]) == snapshot / "receipt_module"
    assert data.name == "DATA" and data.parent == snapshot.parent and not data.is_relative_to(snapshot)
    assert not data.is_relative_to(product) and not data.exists() and not snapshot.exists()
    assert result["actions"]["read"]["http"][0]["response"]["json"] == [["Tea", 2]]
    assert result["cleanup"]["quiescenceVerified"] and result["cleanup"]["activeProcessesAfterTeardown"] == 0
    assert (product / ".codex-harness/evidence/preview").is_dir() and not (product / "fixtures").exists()
    bound_module = deepcopy(spec)
    bound_module["targets"]["module-importer"] = dict(module="receipt_module", argv=[])
    action = bound_module["scenarios"]["scenarios"][0]["actions"][0]
    action["cli"] = dict(target="module-importer", operation="import", argv=action.pop("process")["argv"])
    assert preview(bound_module)["state"] == "PASS"
    # Types do not establish ownership: even code-looking bytes are inert DATA.
    inert = deepcopy(spec)
    inert["scenarios"]["scenarios"][0]["setup"]["files"].append(dict(path="{data}/notes.py", utf8="inert fixture bytes"))
    inert["scenarios"]["scenarios"][0]["observe"].append(dict(path="{data}/notes.py", fileBytesEquals="inert fixture bytes"))
    assert preview(inert)["state"] == "PASS"
    for name in ("app.py", "fixtures/app.txt", ".codex/config.toml", "{data}/.codex/config.toml", "{data}/../app.py", "{data}/"):
        invalid = deepcopy(spec)
        invalid["scenarios"]["scenarios"][0]["setup"] = dict(files=[dict(path=name, utf8="print('fixture-code')\n")])
        refusal = preview(invalid)
        assert refusal["state"] == "ERROR" and name in refusal["reason"], refusal
        assert "DATA" in refusal["reason"] or "reserved" in refusal["reason"] or "sandbox" in refusal["reason"], refusal
        assert not refusal["cleanup"]["childLaunched"], refusal
    # Keep the confirmed real-junction regression, before any output write.
    outside = product.parent / "junction-target"
    outside.mkdir()
    linked = product / ".codex-harness"
    assert linked.resolve().is_relative_to(product.resolve()) and not linked.is_junction() and not linked.is_symlink()
    shutil.rmtree(linked)
    subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(linked), str(outside)], check=True, capture_output=True)
    try:
        refusal = preview(spec)
        assert refusal["state"] == "ERROR" and ".codex-harness" in refusal["reason"] and "link" in refusal["reason"], refusal
        assert list(outside.iterdir()) == [] and not refusal["cleanup"]["childLaunched"], refusal
    finally:
        linked.rmdir()


def execution_judgment_golden(workspace):
    """Protect compiled worker tests -> real observations -> stored, bound 1j verdicts.

    Catch packet-requested marker RED, source/domain refusal, lost brownfield GREEN,
    wrong-reason RED and missing-judgment FAIL. Earlier coverage hand-wrote the verifier
    without consuming a generated packet. Worker files and judge replies are scripted;
    compiled execution commands, exits/output, candidate binding and cleanup are real.
    """
    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.graph import load_plan
    from codex_harness.core.planner_candidate import _semantic_validation, assess_execution, _evidence_digest
    from codex_harness.core.runners import execution_witnesses
    from codex_harness.core.worker_packet import build_packet
    from tests.python.core.mcp_planner_support import workspace_wire
    from tests.python.core.semantic_wiring_support import node

    workspace.mkdir(parents=True)
    server = PlannerServer(workspace)

    def call(tool, **args):
        reply = workspace_wire(server, tool, expected_workspace=str(workspace), **args)
        assert reply['isError'] is False, (tool, reply)
        return reply['structuredContent']

    def git(*args):
        return subprocess.run(['git', '-C', str(product), *args], check=True,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)

    def retain(proof):
        # This is the observation writer, never a fake process or provider result.
        receipt = workspace / (proof['evidenceBinding'] + '.json')
        receipt.write_text(json.dumps(proof), encoding='utf-8')
        with server.store._transaction():
            server.store._event('execution-observed', json.loads(receipt.read_text('utf-8')))
            event_id = server.store.connection.execute('SELECT max(id) FROM events').fetchone()[0]
        return call('harness_prepare_judgment', question=(
            'Assess meaningfulness, correct failure reason and stub/mock gaps in these real observations'),
            subject_ids=['R_NEW', 'R_BASE', 'T_NEW', 'T_BASE'],
            source_event_ids=[event_id], candidate=proof['candidate'])

    def judge(prepared, proof, verdict, gaps=()):
        meaning = dict(check='execution', evidenceBinding=proof['evidenceBinding'], phase=proof['phase'],
                       meaningful=True, correctFailureReason=True, noStubMockGaps=True)
        return scripted_judgment(call, prepared, meaning, verdict, list(gaps))["current"]

    try:
        call('harness_plan_start', idea='Preserve baseline readback and add both channels',
             name='execution-judgment', plan_mode='semantic-v1')
        call('harness_upsert_nodes', nodes=[
            node('F', 'feature', ['V1']),
            node('R_BASE', 'requirement', ['F'], behavior='Preserve baseline readback'),
            node('R_NEW', 'requirement', ['F'], behavior='Read both channels'),
            node('T_BASE', 'test', ['R_BASE'], module='baseline.sensor', level='semantic',
                 execution=dict(entry='baseline.sensor', runtime=sys.executable, argv=[], testRoots=['baseline.sensor'],
                                rationale='Preserve existing baseline readback')),
            node('T_NEW', 'test', ['R_NEW'], module='verify.unfamiliar', level='semantic',
                 execution=dict(entry='verify.unfamiliar', runtime=sys.executable, argv=['channels.dat'], testRoots=['verify.unfamiliar', 'helperlib'],
                                rationale='Observe both channels from the candidate data path')),
            node('C', 'bead', ['T_BASE', 'T_NEW'], ownedFiles=['verify.unfamiliar', 'helperlib/__init__.py'], dependsOn=[],
                 lessons=[], semanticPhase='contracts'),
            node('B', 'bead', ['T_BASE', 'T_NEW'], ownedFiles=['channels.dat', 'helperlib/__init__.py'], dependsOn=['C'],
                 lessons=[], semanticPhase='build'),
        ])
        template = workspace / 'template'
        template.mkdir(exist_ok=True)
        product = workspace / 'candidate'
        product.mkdir()
        (product / 'baseline.sensor').write_text(
            "print('SELDON-STUB is a diagnostic string, baseline remains GREEN')\n")
        (product / 'helperlib').mkdir()
        (product / 'helperlib/__init__.py').write_text(
            "from pathlib import Path\ndef read_channels():\n    return Path('channels.dat').read_text()\n")
        (product / 'payload.binary').write_bytes(b'x' * 262145)
        for index in range(130):
            (product / f'asset-{index}.opaque').write_text(str(index))
        call('harness_compile')
        manifest = server.store._manifest(server.store.spine())
        compiled = workspace / 'compiled'
        compiled.mkdir()
        (compiled / 'sample.json').write_text(json.dumps(manifest), encoding='utf-8')
        plan = load_plan(compiled)
        for name in plan.requirement_files:
            (product / name).write_bytes((template / name).read_bytes())
        git('init', '-q')
        git('add', '.')
        git('-c', 'user.name=golden', '-c', 'user.email=golden@local', 'commit', '-qm', 'baseline')
        def worker(bead, files):
            packet = build_packet(plan, dict(manifest['beads'][bead], id=bead), product, None)
            scripted_worker(packet, product, files)

        worker('C', {'verify.unfamiliar': "raise RuntimeError('verifier startup failed')\n"})
        broken = _semantic_validation(product, plan, {}, 'contracts')
        assert broken['observedState'] == 'FAIL' and broken['state'] == 'pending-judgment'
        request = retain(broken)
        gap = judge(request, broken, 'negative', ['Verifier fails during startup before reading channels'])
        rejected = assess_execution(broken, gap)
        assert rejected['state'] == 'FAIL' and not rejected['admitted'] and rejected['gaps']
        worker('C', {'verify.unfamiliar':
            "# Behavior: both channels; catches missing readback; baseline covers only startup.\n"
            "from pathlib import Path\nfrom helperlib import read_channels\nimport sys\n"
            "assert Path('payload.binary').stat().st_size == 262145\n"
            "assert len(list(Path('.').glob('asset-*.opaque'))) == 130\n"
            "actual = read_channels() if Path(sys.argv[1]).is_file() else 'absent'\n"
            "print('observed channels:', actual)\n"
            "if actual != 'ready:42,84':\n"
            "    print('missing behavior: both channel values', file=sys.stderr)\n"
            "    sys.exit(2)\n"})
        git('add', '.')
        git('-c', 'user.name=golden', '-c', 'user.email=golden@local', 'commit', '-qm', 'worker test')
        red = _semantic_validation(product, plan, {}, 'contracts')
        rows = {r['id']: r for r in red['results']}
        assert rows['T_BASE']['state'] == 'PASS'  # Diagnostic prose never decides a semantic gap.
        assert rows['T_NEW']['actions']['execute']['exit'] == 2
        # P1: argv may name a product file owned by a later Build. Earlier
        # Contracts pre-created it, masking false incomplete and copied GREEN.
        assert 'observed channels: absent' in rows['T_NEW']['actions']['execute']['stdout']
        assert 'missing behavior:' in rows['T_NEW']['actions']['execute']['stderr']
        assert all(r['cleanup']['quiescenceVerified'] for r in red['results'])
        # Persisted receipt loss must not become process-quiescence credit from a
        # positive semantic reply. Existing real executions retained every receipt.
        uncertain = json.loads(json.dumps(red))
        uncertain['results'][0].pop('cleanup')
        uncertain['evidenceBinding'] = _evidence_digest(uncertain)
        assert not assess_execution(uncertain, judge(retain(uncertain), uncertain, 'positive'))['admitted']
        request = retain(red)
        assert not assess_execution(red, call('harness_judgment', judgment_id=request['id'],
                                             candidate=red['candidate']))['admitted']
        red = assess_execution(red, judge(request, red, 'positive'))
        assert red['state'] == 'RED' and red['admitted']
        worker('B', {'channels.dat': 'ready:42,84'})
        git('add', '.')
        git('-c', 'user.name=golden', '-c', 'user.email=golden@local', 'commit', '-qm', 'worker implementation')
        green = _semantic_validation(product, plan, {'attempt': {'baseCommit': red['candidateCommit']}}, 'build')
        assert green['red']['observedState'] == 'FAIL'
        assert green['observedState'] == 'PASS' and green['state'] == 'pending-judgment'
        assert not green['passed'] and green['judgmentStatus'] == 'pending-judgment'
        assert all(r['state'] == 'PASS' for r in green['results'])
        witnesses = execution_witnesses(dict(semantic=green))
        assert {w['id'] for w in witnesses} == {'T_BASE', 'T_NEW'}
        assert all(w['sha256'] and not w['artifactPaths'] for w in witnesses)
        request = retain(green)
        unsupported = judge(request, green, 'unsupported')
        assert not unsupported['positiveCredit'] and not unsupported['concreteGap']
        assert assess_execution(green, unsupported)['state'] == 'pending-judgment'
        judge(request, green, 'positive')
        server.close()
        server = PlannerServer(workspace)
        current = call('harness_judgment', judgment_id=request['id'], candidate=green['candidate'])
        accepted = assess_execution(green, current)
        assert accepted['passed'] and accepted['admitted'] and accepted['state'] == 'PASS'
        # A strengthened assertion helper invalidates old RED, even though the
        # module/argv are unchanged. The current base already passes this bundle;
        # excerpt-only identity could reuse old RED and manufacture completion.
        helper_base = git('rev-parse', 'HEAD').stdout.strip()
        worker('B', {'helperlib/__init__.py':
            "from pathlib import Path\ndef read_channels():\n    value = Path('channels.dat').read_text()\n"
            "    assert '\\n' not in value, 'unexpected channel newline'\n    return value\n"})
        git('add', 'helperlib/__init__.py')
        git('-c', 'user.name=golden', '-c', 'user.email=golden@local', 'commit', '-qm', 'worker assertion helper')
        no_red = _semantic_validation(product, plan, {'redObservations': [red], 'attempt': {'baseCommit': helper_base}}, 'build')
        refused = assess_execution(no_red, judge(retain(no_red), no_red, 'positive'))
        assert no_red['testSources']['contentHash'] != red['testSources']['contentHash']
        assert no_red['red']['observedState'] == 'PASS'
        assert refused['state'] == 'FAIL' and not refused['passed']
        changed = server.store.spine().node('R_NEW')
        changed['text'] = 'Read a third channel too'
        call('harness_upsert_node', node=changed)
        stale = call('harness_judgment', judgment_id=request['id'], candidate=green['candidate'])
        assert not assess_execution(green, stale)['admitted']
    finally:
        server.close()


def judgment_contract_golden(workspace):
    """Protect pending/verdict durability through public submission, reload and consumption.

    Catch lost pending state, stale subject/evidence/candidate credit and unbounded malformed
    corrections. Stage 1a only reloads trace observations; it never consumes semantic verdicts.
    All semantic replies are explicitly scripted, without provider or human-consent credit.
    """
    from copy import deepcopy
    from tests.python.core.mcp_planner_support import workspace_wire
    from tests.python.core.semantic_wiring_support import node
    from codex_harness.core.mcp_planner import PlannerServer

    workspace.mkdir(parents=True)
    server = PlannerServer(workspace)

    def call(tool, *, error=False, **args):
        reply = workspace_wire(server, tool, expected_workspace=str(workspace), **args)
        assert reply["isError"] is error, (tool, reply)
        return reply.get("structuredContent", {})

    def reload():
        nonlocal server
        server.close()
        server = PlannerServer(workspace)

    def read(record, **args):
        return call("harness_judgment", judgment_id=record["id"], **args)

    def submit(record, verdict, gaps=None, **changes):
        response = dict(judgmentId=record["id"], binding=record["binding"], verdict=verdict,
                        rationale="Scripted semantic assessment of retained sensor facts",
                        meaning={"assessment": "retained readback"}, gaps=gaps or [])
        response.update(changes)
        return scripted_judgment(call, record, response["meaning"], response=json.dumps(response))

    try:
        call("harness_plan_start", idea="Preserve sensor readback", name="judgments", plan_mode="semantic-v1")
        call("harness_upsert_nodes", nodes=[node("F1", "feature", ["V1"]),
                                           node("R1", "requirement", ["F1"])])
        spine = server.store.spine()
        evidence = node("E1", "result", ["R1"], evidence={
            "itemRef": {"id": "R1", "revision": spine.revision("R1")},
            "candidate": "sensor-candidate-1",
            "source": {"reference": "fixture:measurement", "realProvider": False, "historical": True},
            "observation": {"reference": "fixture:readback", "outcome": "passed"}, "state": "integrated"})
        call("harness_upsert_node", node=evidence)
        event_id = server.store.connection.execute("SELECT max(id) FROM events").fetchone()[0]
        request = dict(question="Does the retained measurement establish readback?", subject_ids=["R1"],
                       evidence_ids=["E1"], source_event_ids=[event_id])
        pending = call("harness_prepare_judgment", **request)
        # The same producer teaches sibling shapes, not just reviewer replies.
        # Earlier golden replies supplied these fields without reading any packet example.
        for purpose in ("plan-review", "finding-equivalence", "finding-disposition", "review-reuse",
                        "plan-convergence", "vision-intent", "planning-size", "user-decision"):
            packet = call("harness_prepare_judgment", **dict(request, question=purpose))
            example = packet["responseExample"]
            reply = scripted_judgment(call, packet, response=json.dumps(example))
            assert reply["current"]["fixesIssued"] == 0 and not reply["current"]["positiveCredit"]
            assert reply["current"]["meaning"] == example["meaning"]
        reload()
        restored = read(pending, candidate="sensor-candidate-1")
        assert restored["status"] == "pending-judgment"
        assert not restored["positiveCredit"] and not restored["concreteGap"]
        assert restored["sourceRefs"] == pending["sourceRefs"]
        assert restored["evidenceRefs"][0]["node"]["fields"]["evidence"]["state"] == "integrated"
        submit(pending, "unsupported")
        reload()
        unsupported = read(pending, candidate="sensor-candidate-1")
        assert unsupported["status"] == "pending-judgment" and unsupported["verdict"] == "unsupported"
        assert unsupported["disposition"] == "judge" and unsupported["owner"] is None
        submit(pending, "inconclusive")
        assert not read(pending, candidate="sensor-candidate-1")["concreteGap"]
        submit(pending, "positive")
        reload()
        credited = read(pending, candidate="sensor-candidate-1")
        assert credited["positiveCredit"] and not credited["concreteGap"]
        assert credited["meaning"] == {"assessment": "retained readback"}
        assert not credited["sourceVerified"] and credited["owner"] is None
        assert not read(pending)["positiveCredit"]
        assert not read(pending, candidate="sensor-candidate-2")["positiveCredit"]
        # Stage 2 shares this provider, not a separate review/reuse/loop judge store.
        # A reply of the wrong shape must still be repaired by canonical 1j below.
        # Earlier fixtures only supplied execution and consent meanings.
        for question, meaning in (
            ("Does the revised plan address the readback gap?",
             dict(reviewVerdict="changes-requested", rationale="Second channel is still absent")),
            ("Are the findings equivalent and may this revision reuse the old review?",
             dict(equivalent=False, reuse=False, memberDispositions={"channel-gap": "open"})),
            ("What should the loop do after this observed failure?",
             dict(action="repair", reason="Readback has an observed missing channel")),
            ("Does this candidate meet the accepted dream and quality bar?",
             dict(complete=False, noStubMockGaps=True, qualityBarMet=False)),
        ):
            result = scripted_judgment(call, question=question, subject_ids=["R1"],
                evidence_ids=["E1"], source_event_ids=[event_id], meaning=meaning,
                verdict="negative", gaps=["Second-channel readback is absent"])
            reload()
            retained = read(result["current"], candidate="sensor-candidate-1")
            assert retained["concreteGap"] and not retained["positiveCredit"]
            assert retained["meaning"] == meaning and retained["question"] == question
        # An unrelated addition must not discard a bound semantic decision.
        call("harness_upsert_node", node=node("OTHER", "feature", ["V1"]))
        assert read(pending, candidate="sensor-candidate-1")["positiveCredit"]
        negative = call("harness_prepare_judgment", **request)
        submit(negative, "negative", ["Sensor readback lacks a second-channel observation"])
        reload()
        gap = read(negative, candidate="sensor-candidate-1")
        assert gap["concreteGap"] and not gap["positiveCredit"]
        assert gap["gaps"] == ["Sensor readback lacks a second-channel observation"]
        changed = server.store.spine().node("R1")
        changed["text"] = "Read both sensor channels"
        call("harness_upsert_node", node=changed)
        reload()
        assert read(pending, candidate="sensor-candidate-1")["status"] == "pending-judgment"
        assert not read(negative, candidate="sensor-candidate-1")["concreteGap"]
        # Evidence has its own revision binding, independent of the subject.
        fresh = call("harness_prepare_judgment", **request)
        submit(fresh, "positive")
        changed_evidence = deepcopy(server.store.spine().node("E1"))
        changed_evidence["fields"]["evidence"]["note"] = "Additional measurement annotation"
        call("harness_upsert_node", node=changed_evidence)
        assert not read(fresh, candidate="sensor-candidate-1")["positiveCredit"]
        # A corrected second fix may succeed; exhausted fixes cannot accept a fourth reply.
        malformed = call("harness_prepare_judgment", question="Judge the current readback contract", subject_ids=["R1"])
        for expected_fixes, expected_disposition in ((1, "exact-fix"), (2, "exact-fix"), (2, "steer")):
            submit(malformed, "positive", binding="wrong-captured-binding")
            reload()
            retained = read(malformed)
            assert retained["fixesIssued"] == expected_fixes
            assert retained["disposition"] == expected_disposition
            assert "binding" in retained["error"] and retained["owner"] is None
            assert not retained["positiveCredit"] and not retained["concreteGap"]
        call("harness_submit_judgment", judgment_id=malformed["id"], response="{}",
             source="scripted-judge", error=True)
        correctable = call("harness_prepare_judgment", question="Judge the revised sensor contract", subject_ids=["R1"])
        scripted_judgment(call, correctable, {}, response="{")
        submit(correctable, "negative")  # Missing concrete gap: second exact fix.
        submit(correctable, "positive")
        reload()
        assert read(correctable)["positiveCredit"] and read(correctable)["fixesIssued"] == 2
        assert server.store.spine().node("E1")["fields"]["evidence"]["state"] == "stale"
        assert call("harness_plan_status")["approval"]["granted"] is False
    finally:
        server.close()


def trace_contract_golden(workspace):
    """Protect public full/collapsed brownfield traces and exact amendment freshness.

    Catch dropped tests/baselines during compilation, broad evidence invalidation and
    destructive feature replacement, metadata edits laundering retained observations and
    stale revision references retaining current evidence credit; scenario-only delivery
    journeys miss typed trace persistence, revision binding and independent branches. Observations here are retained fixture evidence,
    never live-provider acceptance. No workers or providers execute in this journey.
    """
    from copy import deepcopy

    from tests.python.core.mcp_planner_support import workspace_wire
    from tests.python.core.semantic_wiring_support import node

    from codex_harness.core.graph import load_plan
    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.spine import Spine, SpineError

    for mode, full in (("semantic-v1", True), ("legacy", False)):
        root = workspace / mode
        root.mkdir(parents=True)
        (root / "template").mkdir()
        server = PlannerServer(root)

        def call(tool_name, *, error=False, **args):
            reply = workspace_wire(server, tool_name, expected_workspace=str(root), **args)
            assert reply["isError"] is error, (tool_name, reply)
            return reply.get("structuredContent", {})

        def ref(key):
            return {"id": key, "revision": server.store.spine().revision(key)}

        try:
            call("harness_plan_start", idea="Tune a sensor while retaining readback", name="sensor", plan_mode=mode)
            vision = server.store.spine().node("V1")
            if mode == "legacy":
                vision["fields"].update(validation=["opaque-simulator"], requirementFiles=[])
                (root / "template" / "tests").mkdir()
            nodes = [vision]
            for suffix in ("1", "2"):
                f, r, t, b, c = (prefix + suffix for prefix in ("F", "R", "T", "B", "C"))
                nodes.append(node(f, "feature", ["V1"]))
                parent = f
                if full:
                    story = "S" + suffix
                    nodes.append(node(story, "story", [f]))
                    parent = story
                nodes.append(node(r, "requirement", [parent]))
                parent = r
                if full:
                    architecture, design, contract = "A" + suffix, "D" + suffix, "K" + suffix
                    nodes.extend([node(architecture, "architecture", [r]), node(design, "design", [architecture]), node(contract, "contract", [design])])
                    parent = contract
                nodes.extend([
                    node(t, "test", [parent], module="sensor/readback", level="semantic", execution={"entry": "sensor/readback", "argv": ["--measure"], "testRoots": ["sensor"], "rationale": "Measure retained sensor behavior"}),
                    node(b, "bead", [t], ownedFiles=["sensor" + suffix + ".bin"], dependsOn=[], lessons=[], **({"semanticPhase": "build"} if mode == "semantic-v1" else {"proofModule": "sensor/readback"})),
                    node(c, "code", [b], path="sensor" + suffix + ".bin"),
                ])
            if full:
                nodes = [n for n in nodes if n["id"] != "B2"]
                next(n for n in nodes if n["id"] == "C2")["parents"] = ["T2"]
                next(n for n in nodes if n["id"] == "B1")["parents"] = ["T1", "T2"]
            call("harness_upsert_nodes", nodes=nodes)
            additions = []
            for suffix in ("1", "2"):
                result = "E" + suffix
                evidence = {"itemRef": ref("C" + suffix), "candidate": "baseline-candidate", "source": {"reference": "baseline:measurement:" + suffix, "realProvider": False, "historical": True}, "observation": {"reference": "measurement:" + suffix, "outcome": "passed", "rms": 0.2}, "state": "integrated"}
                additions.append(node(result, "result", ["C" + suffix], evidence=evidence))
            projected = Spine({**server.store.spine().to_dict(), "nodes": server.store.spine().to_dict()["nodes"] + additions})
            features = []
            for suffix in ("1", "2"):
                feature = server.store.spine().node("F" + suffix)
                feature["fields"]["baseline"] = {"source": "existing-repository", "behavior": "Read existing sensor channel " + suffix, "rationale": "Preserve verified channel readback", "codeRefs": [ref("C" + suffix)], "testRefs": [ref("T" + suffix)], "resultRefs": [{"id": "E" + suffix, "revision": projected.revision("E" + suffix)}]}
                feature["fields"]["evidenceRefs"] = [{"id": "E" + suffix, "revision": projected.revision("E" + suffix)}]
                features.append(feature)
            # Baseline annotation changes the upstream feature, so supply the retained
            # observations in the same canonical transaction rather than refreshing later.
            call("harness_upsert_nodes", nodes=features + additions)
            if full:
                call("harness_upsert_nodes", nodes=[node("RES", "research", ["V1"], rationale="Investigate sensor calibration"), node("MET", "metric", ["V1"], definition="Measured residual noise", unit="dB")])
                call("harness_upsert_nodes", nodes=[node("STOP", "stop-condition", ["MET"], metricRefs=[ref("MET")], condition="Residual noise reaches agreed threshold"), node("PENDING", "result", ["R1"], metricRefs=[ref("MET")], evidence={"itemRef": ref("R1"), "candidate": "next-measurement", "source": {"reference": "planned:measurement", "realProvider": False, "historical": False}, "observation": None, "state": "awaiting"})])
                call("harness_upsert_node", node=node("ITER", "iteration", ["MET"], metricRefs=[ref("MET")], resultRefs=[ref("E1")], stopConditionRefs=[ref("STOP")], researchRefs=[ref("RES")]))
                call("harness_upsert_node", node=node("BAT", "baton", ["ITER"], decisionRefs=[], assumptions=["Baseline sensor remains available"], openQuestions=["Which calibration meets the agreed metric?"], currentState="Baseline mapped; measurement awaits execution", nextSafeAction="Review the calibration proposal", rationale="Preserve current behavior while planning", iterationRefs=[ref("ITER")]))
            forged = deepcopy(additions[0])
            forged["id"] = "FORGED_LIVE"
            forged["fields"]["evidence"]["state"] = "validated live"
            call("harness_upsert_node", node=forged, error=True)
            extra_result = deepcopy(additions[0])
            extra_result["id"] = "E3"
            extra_result["fields"]["evidence"]["source"]["historical"] = False
            extra_result["fields"]["evidence"]["observation"]["reference"] = "fixture:fresh-channel-measurement"
            call("harness_upsert_node", node=extra_result)
            if full:
                call("harness_upsert_node", node=node("POINTER", "decision", ["V1"], batonRefs=[ref("BAT")], rationale="Resume from the canonical sensor baton"))
            initial = call("harness_compile", include_detail=True)
            sample = root / "sample.json"
            sample.write_text(json.dumps(initial), encoding="utf-8")
            consumed = load_plan(sample)
            assert consumed.trace == initial["trace"]
            assert initial["evidenceStates"]["C1"]["state"] == "integrated"
            assert initial["evidenceStates"]["C2"]["state"] == "integrated"
            if full:
                assert initial["evidenceStates"]["MET"]["state"] == "awaiting"
                assert initial["evidenceStates"]["PENDING"]["state"] == "awaiting"
            retained = server.store.spine().to_dict()
            server.close()
            server = PlannerServer(root)
            reloaded = call("harness_compile", include_detail=True)
            assert reloaded == initial
            assert reloaded["trace"] == retained
            assert set(reloaded["traceRevisions"]) == {n["id"] for n in retained["nodes"]}
            assert len(reloaded["beads"]) == (1 if full else 2)
            assert all(n["fields"]["level"] == "semantic" for n in retained["nodes"] if n["kind"] == "test")
            # Record an upward discovery without granting consent or silently editing R1.
            old_requirement = ref("R1")
            discovery = node("DISC", "discovery", ["C1"], source="baseline:measurement:1")
            decision = node("DEC", "decision", ["DISC"], rationale="Propose improved calibration")
            call("harness_upsert_nodes", nodes=[discovery, decision])
            amendment = node("AM", "amendment", ["DISC"], sourceRef=ref("DISC"), targetRefs=[old_requirement], proposedChange="Calibrate the first sensor channel", rationale="Observed drift in first channel", decisionRefs=[ref("DEC")])
            call("harness_upsert_node", node=amendment)
            assert ref("R1") == old_requirement
            before = server.store.spine()
            requirement = before.node("R1")
            requirement["text"] = "Calibrate first-channel readback while preserving the second channel"
            metadata_edit = before.node("E3")
            metadata_edit["fields"]["evidence"]["note"] = "Retained observation, extra annotation only"
            call("harness_upsert_nodes", nodes=[requirement, metadata_edit])
            assert server.store.spine().node("E3")["fields"]["evidence"]["state"] == "stale"
            mismatched = node("OLD_ITEM", "result", ["R1"], evidence={"itemRef": old_requirement, "candidate": "fixture-current-candidate", "source": {"reference": "fixture:stale-input", "realProvider": False, "historical": False}, "observation": {"reference": "fixture:old-requirement-observation", "outcome": "passed"}, "state": "integrated"})
            call("harness_upsert_node", node=mismatched)
            assert server.store.spine().node("OLD_ITEM")["fields"]["evidence"]["state"] == "stale"
            changed = call("harness_compile", include_detail=True)
            assert changed["evidenceStates"]["OLD_ITEM"]["state"] == "stale"
            assert next(n for n in changed["trace"]["nodes"] if n["id"] == "OLD_ITEM")["fields"]["evidence"]["state"] == "stale"
            spine = server.store.spine()
            assert spine.node("E1")["fields"]["evidence"]["state"] == "stale"
            assert spine.node("E2") == before.node("E2")
            assert spine.node("F2") == before.node("F2")
            assert spine.node("AM") == before.node("AM")
            for suffix in ("1", "2"):
                expected = deepcopy(before.node("E" + suffix)["fields"]["evidence"])
                actual = spine.node("E" + suffix)["fields"]["evidence"]
                assert {k: v for k, v in actual.items() if k != "state"} == {k: v for k, v in expected.items() if k != "state"}
            assert changed["evidenceStates"]["C1"]["state"] == "stale"
            assert changed["evidenceStates"]["C2"]["state"] == "integrated"
            assert spine.trace_up("E1")[0][-1] == "V1"
            feature = spine.node("F1")
            feature["fields"].pop("baseline")
            call("harness_upsert_node", node=feature, error=True)
            assert server.store.spine().to_dict() == spine.to_dict()
            imported = spine.to_dict()
            imported["nodes"] = [n for n in imported["nodes"] if n["id"] == "V1" or n["id"].endswith("2")]
            path = root / "narrowed.json"
            path.write_text(json.dumps(imported), encoding="utf-8")
            with pytest.raises(SpineError) as refusal:
                server.store.import_spine(path)
            assert refusal.value.code == "spine-evidence-preservation-required"
            assert server.store.spine().to_dict() == spine.to_dict()
        finally:
            server.close()


HTTP_DEADLINE = """import os as _os, threading as _threading, time as _time
_http_end = _time.time() + 15
def _http_watchdog():
    while _time.time() < _http_end:
        _time.sleep(.25)
    _os.write(2, b'HTTP verifier exceeded 15s wall-clock deadline, including startup/readback/cleanup\\n')
    _os._exit(124)
_threading.Thread(target=_http_watchdog, daemon=True).start()
"""


def http_golden(workspace, semantic=False):
    """Protect subprocess HTTP planning through publication, real RED/GREEN and C.

    Catch ShelfLife's import-only audit deadlock and unknown concurrency helpers;
    the semantic CLI/static-page journeys never consume a prospective audit.
    Only providers are doubled; HTTP, dispatch and acceptance execute normally.
    """
    from tests.python.core.acceptance_recovery_support import protocol
    from tests.python.core.acceptance_support import finish_public_acceptance
    from tests.python.core.mcp_chat_confirmations_support import record
    from tests.python.core.mcp_planner_support import rpc, workspace_wire
    from tests.python.core.planning_support import source_only_expansion
    from tests.python.core.review_support import finish_review
    from tests.python.core.semantic_wiring_support import node

    from codex_harness.core import dispatch, review_packets, review_provider, roundtable
    from codex_harness.core.mcp_planner import PlannerServer
    from codex_harness.core.plan_store import PHASES

    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("Use gpt-6.1-sol at medium effort for reviews.\n")
    server = PlannerServer(workspace)
    decisions = []

    def call(tool_name, *, error=False, **args):
        reply = workspace_wire(server, tool_name, expected_workspace=str(workspace), **args)
        if tool_name == "harness_accept_review":
            reply = finish_review(server, reply)
        if reply["isError"] and not error:
            print("\n".join(c.get("text", "") for c in reply.get("content", [])))
        assert reply["isError"] is error, (tool_name, reply)
        if tool_name in {"harness_upsert_node", "harness_upsert_nodes"} and store._meta()["locked_vision_json"]:
            from codex_harness.core.approvals import intent_assessment
            judgment = intent_assessment(store, store.spine().node("V1"), prepare=False)
            if judgment.get("id") and not judgment["positiveCredit"]:
                scripted_judgment(call, judgment, dict(impact="preserved"))
        return reply.get("structuredContent", {})

    def scope_how(rationale):
        from codex_harness.core.approvals import scope_assessment
        call("harness_compile", include_detail=True)
        judgment = scope_assessment(store, prepare=True)
        if judgment.get("id") and not judgment["positiveCredit"]:
            scripted_judgment(call, judgment, dict(impact="HOW"), rationale=rationale)

    def yes(card):
        decisions.append(card["gate"])
        reply = record(server, card, "Yes")
        assert not reply["isError"], reply
        assert settle_decision(call, reply["structuredContent"])["state"] == "approved"

    product = """import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class API(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(response_body().encode())
    def log_message(self, *args):
        pass
def response_body() -> str:
    return b'shelf'.decode()
server = ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), API)
print(server.server_port, flush=True)
server.serve_forever()
"""
    proof = HTTP_DEADLINE + """import queue, subprocess, sys, threading, unittest
from pathlib import Path
from urllib.request import urlopen
class HTTP(unittest.TestCase):
    def test_status_and_body(self):
        with subprocess.Popen([sys.executable, '-B', 'server.py', '0'],
                              cwd=Path(__file__).resolve().parents[1],
                              stdout=subprocess.PIPE, text=True) as server:
            try:
                ports = queue.Queue()
                threading.Thread(target=lambda: ports.put(server.stdout.readline()), daemon=True).start()
                try:
                    port = int(ports.get(timeout=5))
                except queue.Empty:
                    raise TimeoutError("HTTP server did not publish a port within 5s")
                with urlopen(f'http://127.0.0.1:{port}/items', timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b'shelf')
            finally:
                server.kill()
                server.wait(timeout=5)
"""
    helper = HTTP_DEADLINE + """import queue, subprocess, sys, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen
def simultaneous():
    with subprocess.Popen([sys.executable, '-B', 'server.py', '0'],
                          cwd=Path(__file__).resolve().parents[1],
                          stdout=subprocess.PIPE, text=True) as server:
        try:
            ports = queue.Queue()
            threading.Thread(target=lambda: ports.put(server.stdout.readline()), daemon=True).start()
            try:
                port = int(ports.get(timeout=5))
            except queue.Empty:
                raise TimeoutError("HTTP server did not publish a port within 5s")
            def get(_):
                with urlopen(f'http://127.0.0.1:{port}/items', timeout=5) as response:
                    return response.status, response.read()
            with ThreadPoolExecutor(max_workers=4) as pool:
                return list(pool.map(get, range(4)))
        finally:
            server.kill()
            server.wait(timeout=5)
"""
    opaque = """import unittest
from helpers import simultaneous
from test_status import HTTP as ReadinessHTTP
class ConcurrentHTTP(unittest.TestCase):
    def test_simultaneous_requests(self):
        self.assertEqual(simultaneous(), [(200, b'shelf')] * 4)
"""
    try:
        rpc(server, "initialize", dict(protocolVersion="2025-06-18", capabilities={}))
        call(
            "harness_plan_start",
            idea="Read shelf items over HTTP",
            name="http",
            plan_mode="semantic-v1" if semantic else "legacy",
        )
        store = server.store
        source_only_expansion(store)
        call(
            "harness_understanding_card",
            vision="Read shelf items over HTTP",
            users="Local operator",
            first_user_moment="GET items returns shelf",
            scope_in="Concurrent HTTP reads",
            scope_out="Remote hosting",
            assumptions=[],
        )
        # Legacy understanding saves a node; request the shared A subject explicitly.
        yes(call("harness_prepare_chat_confirmation", gate="align+vision-lock"))
        vision = store.spine().node("V1")
        if semantic:
            missing = call("harness_plan_status", full=True)["acceptance"]
            assert not missing["candidatePrerequisite"]["complete"] and not missing["accepted"]
            assert "retained candidate unavailable" in missing["invalidMessage"] and "harness_worker_start" in missing["next"]
            # Round 3 submitted only these importer scenarios; HTTP/UI were never
            # submitted. Replay their missing coverage plus a proposed HTTP shape.
            # Catch the Requirements deadlock at the public writer/compiler boundary;
            # legacy HTTP and CLI-only journeys never exercised that boundary.
            captured = {
                "schema": "seldon/scenarios-v1",
                "scenarios": [
                    {
                        "id": "S_IMPORT_MIXED",
                        "requirementRefs": ["R4"],
                        "contractRefs": ["G1"],
                        "setup": {
                            "dirs": [],
                            "files": [
                                {
                                    "path": "{data}/mixed.csv",
                                    "utf8": (
                                        "name,quantity,unit,expires\n"
                                        "Rice,2,kg,2030-01-10\n"
                                        "Beans,0,,\n"
                                        ",1,,\n"
                                        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                                        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx,1,,\n"
                                        "Bad negative,-1,,\n"
                                        "Bad fraction,1.5,,\n"
                                        "Bad date,1,,2030-02-30\n"
                                    ),
                                }
                            ],
                        },
                        "actions": [
                            {
                                "id": "import",
                                "cli": {
                                    "target": "importer",
                                    "operation": "import-mixed",
                                    "argv": [
                                        {"input": "{data}/mixed.csv"},
                                        "--db",
                                        {"input": "{data}/pantry.sqlite3"},
                                    ],
                                },
                            }
                        ],
                        "observe": [
                            {
                                "action": "import",
                                "exitEquals": 0,
                                "stdoutEquals": "imported 2, skipped 5\n",
                                "stderrEquals": "",
                            }
                        ],
                        "verdict": "all",
                    },
                    {
                        "id": "S_IMPORT_UNREADABLE",
                        "requirementRefs": ["R4"],
                        "contractRefs": ["G1"],
                        "setup": {"dirs": []},
                        "actions": [
                            {
                                "id": "unreadable",
                                "cli": {
                                    "target": "importer",
                                    "operation": "import-unreadable",
                                    "argv": [
                                        {"input": "{data}/missing.csv"},
                                        "--db",
                                        {"input": "{data}/pantry.sqlite3"},
                                    ],
                                },
                            }
                        ],
                        "observe": [
                            {
                                "action": "unreadable",
                                "exitEquals": 2,
                                "stdoutEquals": "",
                                "stderrNonempty": True,
                            }
                        ],
                        "verdict": "all",
                    },
                ],
            }
            vision["fields"].update(
                targets={"importer": {"entry": "import_items.py", "argv": []}},
                scenarios=captured,
            )
            requirements = [node(f"R{i}", "requirement", ["EXPORT_STORY"]) for i in range(1, 6)]
            owned = ["server.py", "import_items.py", "verify_http.py"]
            nodes = [
                vision,
                node("F", "feature", ["V1"]),
                dict(node("EXPORT_STORY", "story", ["F"]), text="export locally"),
                *requirements,
                dict(node("G1", "design", [r["id"] for r in requirements]), text="export locally"),
                node(
                    "T_HTTP",
                    "test",
                    ["G1"],
                    module="verify_http.py",
                    level="semantic",
                    execution={
                        "entry": "verify_http.py", "runtime": sys.executable, "argv": ["data/pantry.sqlite3"],
                        "testRoots": ["verify_http.py"],
                        "rationale": "Planned importer/SQLite/HTTP readback verifier supplied and run later in this journey",
                    },
                ),
                node(
                    "C",
                    "bead",
                    ["G1"],
                    ownedFiles=owned,
                    dependsOn=[],
                    lessons=[],
                    semanticPhase="contracts",
                ),
                node(
                    "I",
                    "bead",
                    ["T_HTTP"],
                    ownedFiles=owned,
                    dependsOn=["C"],
                    lessons=[],
                    semanticPhase="build",
                ),
                node(
                    "B",
                    "bead",
                    ["G1"],
                    ownedFiles=owned,
                    dependsOn=["I", "C"],
                    lessons=[],
                    semanticPhase="build",
                ),
            ]
            call("harness_upsert_nodes", nodes=nodes)
            # DV2: refuse a multi-build plan before any worker can run, then
            # replan through the public writer; earlier fixtures claimed multi-build.
            refused = workspace_wire(server, "harness_compile", expected_workspace=str(workspace))
            assert refused["isError"] and "semantic-v1 runs one build bead until Stage 3a (DV2): merge build beads into one" in str(refused)
            bead = store.spine().node("B")
            bead["fields"]["dependsOn"] = ["C"]
            call("harness_upsert_node", node=bead)
            call("harness_remove_node", id="I")
            # A contracts-only plan needs a new build, not an impossible merge.
            call("harness_remove_node", id="B")
            missing_build = workspace_wire(server, "harness_compile", expected_workspace=str(workspace))
            assert missing_build["isError"] and "add one build bead that depends on the contracts bead" in str(missing_build)
            assert "merge build beads" not in str(missing_build)
            call("harness_upsert_node", node=bead)
            # Counting builds alone missed contracts-after-build early completion.
            contracts = store.spine().node("C")
            bead["fields"]["dependsOn"], contracts["fields"]["dependsOn"] = [], ["B"]
            call("harness_upsert_nodes", nodes=[bead, contracts])
            wrong_order = workspace_wire(server, "harness_compile", expected_workspace=str(workspace))
            assert wrong_order["isError"] and "contracts must supply RED first" in str(wrong_order)
            bead["fields"]["dependsOn"], contracts["fields"]["dependsOn"] = ["C"], []
            call("harness_upsert_nodes", nodes=[bead, contracts])
            assert not list((workspace / ".codex-harness" / "worker-jobs").glob("*/started.json"))
            call("harness_advance", to_phase="plan")
            call("harness_compile", include_detail=True)
            assert not store.status()["coverage_gaps"], store.status()
            planned_scenarios = store.acceptance_scenarios()
            assert {
                s["requirementRefs"][0]
                for s in planned_scenarios
                if s.get("evidenceTier") == "scenario: unvalidated"
            } == {"R1", "R2", "R3", "R5"}
            assert all(s["executable"] for s in planned_scenarios if s["id"].startswith("S_IMPORT"))
            # Accept common HTTP request/response intent without pretending to run it.
            vision["fields"]["scenarios"]["scenarios"].append(
                dict(
                    id="S_HTTP",
                    requirementRefs=["R1", "R2", "R3", "R5"],
                    contractRefs=["G1"],
                    setup={},
                    actions=[{"id": "get", "http": {"method": "GET", "path": "/items"}}],
                    observe=[{"action": "get", "statusEquals": 200, "bodyEquals": "shelf"}],
                    verdict="all",
                )
            )
            call("harness_upsert_node", node=vision)
            call("harness_compile", include_detail=True)
            held = next(s for s in store.acceptance_scenarios() if s["id"] == "S_HTTP")
            assert held["evidenceTier"] == "scenario: unvalidated" and not held["executable"]
            from codex_harness.core.semantic_runner import run as run_scenarios

            pending = store._manifest(store.spine())["semantic"]
            (workspace / "import_items.py").write_text("# not executed\n")
            blocked = run_scenarios(
                pending["scenarios"], workspace, pending["targets"], "validation"
            )
            assert blocked[-1]["state"] == "pending-judgment" and blocked[-1]["unperformed"], blocked
            assert "observation adapter" in blocked[-1]["reason"], blocked
            assert not blocked[-1].get("actions"), blocked  # Unknown intent never invents an execution.
            (workspace / "import_items.py").unlink()
            # Workers can supply an ordinary CLI verifier for the HTTP lifecycle.
            # Resolve the unvalidated intent before B; it grants no RED/GREEN credit.
            mixed = vision["fields"]["scenarios"]["scenarios"][0]
            mixed["requirementRefs"] = [f"R{i}" for i in range(1, 6)]
            mixed["actions"].append(
                {
                    "id": "http",
                    "cli": {
                        "target": "http",
                        "operation": "read",
                        "argv": [{"path": "data/pantry.sqlite3"}],
                    },
                }
            )
            mixed["observe"].append(
                {"action": "http", "exitEquals": 0, "stdoutEquals": "Rice,Beans\n"}
            )
            vision["fields"]["targets"]["http"] = {"entry": "verify_http.py", "argv": []}
            vision["fields"]["scenarios"]["scenarios"].pop()
            call("harness_upsert_node", node=vision)
            template = workspace / "template"
            template.mkdir(exist_ok=True)
            product = product.replace(
                "self.wfile.write(response_body().encode())",
                "import sqlite3\n        from contextlib import closing\n"
                "        with closing(sqlite3.connect(sys.argv[2])) as db:\n"
                "            rows = db.execute('select name from items')\n"
                "            self.wfile.write(','.join(r[0] for r in rows).encode())",
            )
            (template / "server.py").write_text(product, encoding="utf-8")
            # A real template dependency is outside bead ownership and verifier roots.
            (template / "table_name.txt").write_text("items", encoding="utf-8")
            # Protect C20 at completion→delivery: dependencies formerly included
            # provider configuration; the old blanket dot guard also lost product dot-files.
            (template / ".codex").mkdir()
            (template / ".codex/config.toml").write_text('# Template config must never be installed\n', encoding="utf-8")
            (template / ".gitignore").write_text("*.local-product\n", encoding="utf-8")
            product = product.replace("db.execute('select name from items')", "db.execute('select name from ' + __import__('pathlib').Path('table_name.txt').read_text())")
            importer = """import csv, sys
from contextlib import closing
try:
    rows = list(csv.DictReader(open(sys.argv[1], encoding='utf-8')))
except OSError:
    print('unreadable', file=sys.stderr)
    sys.exit(2)
def valid(row):
    from datetime import date
    try:
        if row['expires']: date.fromisoformat(row['expires'])
        return 1 <= len(row['name']) <= 80 and int(row['quantity']) >= 0
    except ValueError: return False
import sqlite3
accepted = [row for row in rows if valid(row)]
with closing(sqlite3.connect(sys.argv[3])) as db, db:
    db.execute('create table if not exists items (name text)')
    db.executemany('insert into items values (?)', [(r['name'],) for r in accepted])
n = len(accepted)
print(f'imported {n}, skipped {len(rows)-n}')
"""
            verifier = HTTP_DEADLINE + """import queue, sqlite3, subprocess, sys, threading
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
root = Path(__file__).resolve().parent
# T_HTTP runs independently of scenario setup. Import real input into a private DB;
# catch empty HTTP success, SQLite startup errors and optional-field row loss.
with TemporaryDirectory(dir=root) as scratch:
    csv = Path(scratch) / 'items.csv'
    db = Path(scratch) / 'items.sqlite3'
    csv.write_text('name,quantity,unit,expires\\nRice,2,kg,2030-01-10\\nBeans,0,,\\n')
    imported = subprocess.run([sys.executable, '-B', str(root / 'import_items.py'),
        str(csv), '--db', str(db)], capture_output=True, text=True, timeout=5)
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout == 'imported 2, skipped 0\\n', imported.stdout
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute('select name from items order by rowid').fetchall() == [('Rice',), ('Beans',)]
    server = subprocess.Popen([sys.executable, '-B', str(root / 'server.py'), '0', str(db)],
        stdout=subprocess.PIPE, text=True)
    try:
        ports = queue.Queue()
        threading.Thread(target=lambda: ports.put(server.stdout.readline()), daemon=True).start()
        try:
            port = int(ports.get(timeout=5))
        except queue.Empty:
            raise TimeoutError('HTTP startup deadline: 5s')
        with urlopen(f'http://127.0.0.1:{port}/items', timeout=5) as response:
            assert response.status == 200, response.status
            body = response.read().decode()
            assert body == 'Rice,Beans', f'Expected imported rows over HTTP, got {body!r}'
            print(body)
    finally:
        server.kill()
        server.wait(timeout=5)
"""
        else:
            vision["fields"].update(
                validation=["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
                requirementFiles=["tests"],
            )
            call(
                "harness_upsert_nodes",
                nodes=[
                    vision,
                    node("F", "feature", ["V1"]),
                    node("R", "requirement", ["F"]),
                    node("R_STATUS", "requirement", ["F"], text="Read readiness over HTTP"),
                    node("A", "bead", ["R_STATUS"], ownedFiles=["status.py"], dependsOn=[],
                         lessons=[], proofModule="test_status.py"),
                    node("T_STATUS", "test", ["R_STATUS"], level="semantic", module="test_status.py"),
                    node(
                        "B",
                        "bead",
                        ["R"],
                        ownedFiles=["server.py"],
                        dependsOn=[],
                        lessons=[],
                        proofModule="test_http.py",
                    ),
                    node("T", "test", ["R", "R_STATUS"], level="e2e", module="test_http_concurrent.py"),
                    node("first-moment", "acceptance", ["F"], nodeIds=["first-moment"],
                         given="Both HTTP services start from clean data", steps=[], expect={},
                         waveBoundaryIntegrationTestId="T", waveBoundaryIntegrationModule="test_http_concurrent.py"),
                ],
            )
            template = workspace / "template"
            (template / "tests").mkdir(parents=True)
            for name, content in {
                "server.py": product.replace("b'shelf'", "b'stub'"),
                "status.py": product.replace("b'shelf'", "b'stub'"),
                "tests/test_status.py": proof.replace("'server.py'", "'status.py'").replace("b'shelf'", "b'ready'"),
                "tests/test_http.py": proof,
                "tests/helpers.py": helper,
                "tests/test_http_concurrent.py": opaque,
            }.items():
                (template / name).write_text(content, encoding="utf-8")
            for phase in PHASES[PHASES.index(store.phase()) + 1 : PHASES.index("roundtable")]:
                call("harness_advance", to_phase=phase)
        if not semantic:
            # Report an actual applied wave refinement, not a fabricated event.
            # Prior consent journeys only used upsert_nodes and missed this writer.
            def outcome(wid, dependencies):
                return dict(id=wid, outcome="Both accepted HTTP services remain available", demo="Run the HTTP checks",
                    requirementIds=["R", "R_STATUS"], featureIds=["F"], storyIds=[], designIds=[], riskIds=[],
                    exitScenarioIds=["first-moment"], dependsOnWaves=dependencies, incomingContractIds=[], outgoingContractIds=[])
            obligations = [dict(id="O_" + key, kind=kind,
                sourceRefs=[dict(id=key, revision=store.spine().revision(key))], waveIds=["first", "later"],
                criterion="Preserve the accepted HTTP behavior", requiredEvidence=[evidence], requiredForFullVision=True)
                for key, kind, evidence in (("R", "requirement", "source-test"),
                    ("R_STATUS", "requirement", "source-test"), ("first-moment", "scenario", "clean-state-product"),
                    ("V1", "review", "independent-review"))]
            map_node = node("WAVES", "decision", ["V1"], schema="dot-wave-map-v1", order=["first", "later"],
                waves=[outcome("first", []), outcome("later", ["first"])], contracts=[],
                obligations=obligations, globalReviewObligationId="O_V1")
            detail_nodes = [node("WD1", "design", ["WAVES"], schema="dot-wave-detail-v1", waveId="first",
                detail="detailed", beadIds=["A", "B"], sketches=[], observedInterfaces=[], verificationObligationIds=[]),
                node("WD2", "design", ["WAVES"], schema="dot-wave-detail-v1", waveId="later", detail="sketch", beadIds=[],
                    sketches=[dict(id="SK_HTTP", title="Refine HTTP adapter ownership after observing the service",
                        ownershipArea="HTTP adapter", requirementIds=["R"], expectedContractIds=[], risks=[],
                        pendingProducerIds=["B"], detailAfterWaveId="first")], observedInterfaces=[], verificationObligationIds=[])]
            call("harness_wave_map", map_node=map_node, detail_nodes=detail_nodes,
                 expected_spine_revision=store._revision())
        choice = json.loads(
            (ROOT / "tests/fixtures/efficiency/jsonkeys-reviewers.json").read_text("utf-8")
        )["choice"]
        if semantic:
            # R7 starts with one build; review will re-partition its tests into C.
            bead = store.spine().node("B")
            bead["fields"]["dependsOn"] = []
            call("harness_upsert_node", node=bead)
            call("harness_remove_node", id="C")
            # Existing incomplete implementation gives the later test worker a
            # real assertion RED, rather than a missing-program startup error.
            (template / "import_items.py").write_text(importer.replace(
                "if valid(row)", "if valid(row) and row['unit'] and row['expires']"), encoding="utf-8")
        choice["fields"].update(catalogId=None, catalogDigest=None)
        for reviewer in choice["fields"]["reviewers"]:
            reviewer["choiceId"] = None
        choice["fields"]["recommendation"]["choiceIds"] = []
        call("harness_reviewer_choice", node=choice, expected_spine_revision=store._revision())
        manifest = call("harness_compile", include_detail=True)
        options = dict(
            expected_revision=manifest["planRevision"],
            max_parallel=1 if semantic else 2,
            timeout=180 if semantic else 15,
            workers=[
                dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                for b in (("B",) if semantic else ("A", "B"))
            ],
        )
        with pytest.MonkeyPatch.context() as patch:
            # These helpers also run before the stdio journey installs its absolute
            # import path. Relative PYTHONPATH breaks runner children in temp cwd.
            patch.setenv("PYTHONPATH", os.pathsep.join([str(ROOT / "src"), str(ROOT)]))
            for owner in (roundtable, review_provider):
                patch.setattr(owner, "_executable", lambda *_: sys.executable)
            review_history_ids = set()
            provider_launches = []
            def positive_review(launch, *_):
                provider_launches.append(launch)
                if "\n## Bound Judgment\n" not in launch.stdin_text:
                    return protocol(launch.cli, text=json.dumps(dict(ready=True, canVerify=True, blockers=[], questions=[])))
                request = json.loads(launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split("\nReturn ONLY JSON", 1)[0])
                history = request["sourceRefs"][0]["payload"]["facts"]["packet"]
                assert all(key in history for key in review_history_ids)
                return tuple(v.replace("source-only", "gpt-6.1-sol").replace("Some(High)", "Some(Medium)")
                    if isinstance(v, str) else v for v in protocol(launch.cli, text=scripted_review_reply(launch,
                        [], verdict="positive", rationale="The planned HTTP import, persistence and readback cover the accepted local dream")))
            for owner in (roundtable, review_provider):
                patch.setattr(owner, "_run", positive_review)
            # The former call ceiling hid a separate readiness launch owner with no B check.
            # Ordinary review tests never exercised this public worker-packet consumer.
            selected_reviewers = [{k: r[k] if k != "effort" else r[k]["value"]
                                   for k in ("name", "provider", "model", "effort")}
                                  for r in choice["fields"]["reviewers"]]
            refused = workspace_wire(server, "harness_readiness", expected_workspace=str(workspace),
                                     reviewers=selected_reviewers, beads=["B"])
            assert refused["isError"] and "plan-b-consent-required" in str(refused)
            yes(
                call(
                    "harness_prepare_chat_confirmation",
                    gate="plan-execution",
                    execution=options,
                    reviewer_node_id=choice["id"],
                )
            )
            if semantic:
                # Recording observed catalog metadata for the same exact reviewers
                # must not broaden consent or introduce another B decision.
                from tests.python.core.plan_store_support import mock_catalog
                catalog = mock_catalog()
                catalog["providers"][0]["choices"][0]["effort"]["value"] = "medium"
                catalog = call("harness_reviewer_catalog", catalog=catalog,
                               expected_spine_revision=store._revision())["catalog"]
                choice["fields"].update(catalogId=catalog["id"], catalogDigest=catalog["digest"])
                for reviewer in choice["fields"]["reviewers"]:
                    reviewer["choiceId"] = catalog["providers"][0]["choices"][0]["choiceId"]
                call("harness_reviewer_choice", node=choice, expected_spine_revision=store._revision())
                scope_how("Catalog provenance now identifies the same reviewers; no product obligation changes")
                manifest = call("harness_compile", include_detail=True)
                options["expected_revision"] = manifest["planRevision"]
            if not semantic:
                # Mixed consent must refuse the entire selection before spending a start.
                # Earlier refusal tests used all-unapproved rosters and missed orphaned
                # required-slot attempts after an approved first reviewer ran.
                call("harness_adopt_review_policy", expectedManifestRevision=manifest["planRevision"],
                     slots=[dict(name=r["name"], provider=r["provider"], required=True) for r in selected_reviewers])
                mixed = [selected_reviewers[0], dict(selected_reviewers[1], name="unapproved-observer")]
                def refuse_mixed(tool, **arguments):
                    before = len(provider_launches)
                    reply = workspace_wire(server, tool, expected_workspace=str(workspace), reviewers=mixed, **arguments)
                    assert reply["isError"] and reply["refusalCode"] == "plan-b-consent-required", reply
                    text = "\n".join(c.get("text", "") for c in reply["content"])
                    assert text.count("Next: ") == 1 and "harness_prepare_chat_confirmation gate=plan-execution" in text
                    return len(provider_launches) - before
                mixed_launches = {"readiness": refuse_mixed("harness_readiness", beads=["B"])}
            readiness = call("harness_readiness", reviewers=selected_reviewers, beads=["B"])
            assert readiness["invalid"] == 0
            if not semantic:
                interrupted = call("harness_audit", mode="prospective")
                server.audits.close(store)  # Supported owner cleanup before dispatch.
                released = call("harness_audit_status", id=interrupted["attemptId"])
                assert released["state"] == "abandoned" and not released["proofCreditGranted"]
                audit = call("harness_audit", mode="prospective")
                assert audit["attemptId"] != interrupted["attemptId"]
                server.audits.dispatch_after_flush(store)
                server.audits.thread.join(timeout=10)
                server.audits.drain(store)
                audited = call("harness_audit_status", id=audit["attemptId"])
                result = audited["result"]
                assert result["ok"] and audited["proofCreditGranted"], audited
                assert result["evidenceTier"] == "audit: pending judgment", result
                assert result["judgmentStatus"] == "pending-judgment"
                assert not result["collection"] and not audited["didRunValidation"]
                # Source patterns cannot decide meaningfulness or reject an opaque helper.
                assert not result["findings"], result
            call("harness_advance", to_phase="roundtable")
            assert store.status()["pending_gate"] != "passing-audit-required"
            if not semantic:
                mixed_launches["roundtable"] = refuse_mixed("harness_roundtable", timeout=5)
                exists = store.connection.execute("SELECT 1 FROM sqlite_master WHERE name='review_attempts'").fetchone()
                attempts = [dict(row) for row in store.connection.execute("SELECT * FROM review_attempts")] if exists else []
                assert mixed_launches == {"readiness": 0, "roundtable": 0} and not attempts, (mixed_launches, attempts)
            if semantic:
                # Protect complete source members and invocation/revision-bound authority.
                # Earlier coverage merged opposing findings and never retried an unchanged packet.
                exhausted_calls, original_packets = {}, {}
                def malformed_review(launch, *_):
                    request = json.loads(launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split("\nReturn ONLY JSON", 1)[0])
                    key = request["id"]
                    facts = request["sourceRefs"][0]["payload"]["facts"]
                    original_packets[facts["reviewer"]["name"]] = facts["packetSha256"]
                    exhausted_calls[key] = exhausted_calls.get(key, 0) + 1
                    malformed = request["responseExample"]
                    malformed["meaning"]["depth"] = ""
                    return protocol(launch.cli, text=json.dumps(malformed))
                with pytest.MonkeyPatch.context() as bad:
                    for owner in (roundtable, review_provider):
                        bad.setattr(owner, "_run", malformed_review)
                    exhausted = call("harness_roundtable", timeout=5, error=True)
                assert sorted(exhausted_calls.values()) == [3, 3]
                assert all("meaning.depth" in call("harness_judgment", judgment_id=key)["error"] for key in exhausted_calls)
                assert exhausted["findings"] == 0 and not exhausted["successfulReviews"]
                exhausted_ids = set(exhausted_calls)
                # Terminal exact-fix exhaustion must teach fresh invocation, not
                # advertise a submission the owner necessarily rejects. Older flows
                # inspected the error count but never consumed its next-step card.
                terminal = call("harness_plan_status", full=True)
                requests = {r["id"]: r for r in terminal["pendingSemanticJudgments"]}
                assert all(not requests[key]["submitReady"] and requests[key]["action"] == "fresh-invocation" for key in exhausted_ids)
                guidance = terminal["continuation"]["recommendation"]
                assert guidance["kind"] == "review_required"
                assert "fresh invocation" in guidance["message"] and "submit" not in guidance["message"]
                conflict = dict(node_id="R2", severity="major", finding="HTTP readback evidence needs judgment",
                    suggestion="Prove persistence of both imported rows", classification="defect-in-plan",
                    scope_reason="Required readback could be missing")
                opposing = dict(conflict, suggestion="Explain whether extra readback coverage exceeds the brief",
                    classification="beyond-brief", scope_reason="Additional coverage might exceed the brief")
                def conflicting_review(launch, *_):
                    request = json.loads(launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split("\nReturn ONLY JSON", 1)[0])
                    name = request["sourceRefs"][0]["payload"]["facts"]["reviewer"]["name"]
                    assert request["id"] not in exhausted_ids
                    assert request["sourceRefs"][0]["payload"]["facts"]["packetSha256"] == original_packets[name]
                    concrete = name == choice["fields"]["reviewers"][0]["name"]
                    findings = [conflict, opposing] if concrete else [dict(conflict,
                        finding="Evidence access unavailable", suggestion="Obtain readback observations")]
                    return protocol(launch.cli, text=scripted_review_reply(launch, findings,
                        verdict="negative" if concrete else "inconclusive",
                        rationale="Readback is unproven until its real observations are assessed" if concrete
                            else "Unavailable observations cannot establish a concrete gap",
                        gaps=["HTTP readback evidence needs judgment"] if concrete else []))
                with pytest.MonkeyPatch.context() as bad:
                    for owner in (roundtable, review_provider):
                        bad.setattr(owner, "_run", conflicting_review)
                    rejected = call("harness_roundtable", timeout=5)
                assert not rejected["successfulReviews"]
                # Only invocation metadata changes on the otherwise identical initial dossier.
                assert all(r["shardDetails"][0]["reviewInvocationId"] != exhausted["reviewers"][i]["shardDetails"][0]["reviewInvocationId"]
                           for i,r in enumerate(rejected["reviewers"]))
                held = workspace_wire(server, "harness_advance", expected_workspace=str(workspace), to_phase="publish")
                assert held["isError"]
                equivalence = rejected["clusters"]["judgment"]
                members = equivalence["sourceRefs"][0]["payload"]["facts"]["members"]
                assert len([row for row in members if row.get("judgmentGap")]) == 1
                members = [row for row in members if row["finding"] == conflict["finding"] and not row.get("judgmentGap")]
                assert len(members) == 2 and {row["suggestion"] for row in members} == {conflict["suggestion"], opposing["suggestion"]}
                assert {row["scope_reason"] for row in members} == {conflict["scope_reason"], opposing["scope_reason"]}
                assert equivalence["status"] == "pending-judgment"
                scripted_judgment(call, equivalence, dict(groups=[[row["id"] for row in members]]),
                    rationale="The two independently retained members concern readback; neither rationale is discarded")
                first = [dict(id=members[0]["id"], status="resolved", resolution="Readback verifier covers the first member")]
                pending = call("harness_resolve_findings", findings=first)
                first_closure_id = pending["judgment"]["id"]
                scripted_judgment(call, pending["judgment"], dict(memberDispositions={members[0]["id"]: "resolved"}))
                call("harness_resolve_findings", findings=first)
                remainder = call("harness_review", id=rejected["roundId"])["findingMembers"]
                assert {row["id"] for row in remainder if row["id"] in {m["id"] for m in members} and row["status"] == "open"} == {members[1]["id"]}
                projected = roundtable.findings(store)
                assert any(row["id"] == members[1]["id"] and row["status"] == "open" for row in projected)
                # Every concrete negative gap survives independently of presentation;
                # an inconclusive major item remains pending, never a false blocker.
                gap_rows = [r for r in roundtable.findings(store, expand=True) if r.get("judgmentGap")]
                assert len(gap_rows) == 1 and all(r["state"] == "open" for r in gap_rows)
                review_history_ids.update(row["id"] for row in roundtable.findings(store, expand=True))
                assert all(r["id"] in {row["id"] for row in remainder} for r in gap_rows)
                continuation = call("harness_plan_status", full=True)["continuation"]["recommendation"]
                assert continuation["kind"] == "reconcile_findings" and members[1]["id"] in continuation["findingIds"]
                proposals = [dict(id=row["id"], status="resolved",
                    resolution="The real importer/SQLite/HTTP verifier supplies readback evidence")
                    for row in roundtable.findings(store, expand=True) if row["state"] != "closed" or row["id"] == members[0]["id"]]
                pending = call("harness_resolve_findings", findings=proposals)
                scripted_judgment(call, pending["judgment"], dict(memberDispositions={row["id"]: "resolved" for row in proposals}))
                assert call("harness_resolve_findings", findings=proposals)["dispositions"] == len(proposals)
                assert call("harness_judgment", judgment_id=pending["judgment"]["id"])["positiveCredit"]
                retained = call("harness_review", id=rejected["roundId"])["findingMembers"]
                assert {first_closure_id, pending["judgment"]["id"]} <= set(next(r for r in retained if r["id"] == members[0]["id"])["judgmentIds"])
                uncertain = [r for r in roundtable.findings(store, expand=True) if r["finding"] == "Evidence access unavailable"]
                assert len(uncertain) == 1 and uncertain[0]["state"] == "closed"
                assert not call("harness_plan_status", full=True)["reviewResolution"]["blockingOpenFindings"]
                # ShelfLife R6: review repairs changed the revision and demanded B
                # twice. Frozen-plan journeys never consumed a repaired test/command.
                preliminary = store.spine().node("T_HTTP")
                preliminary["text"] += " Assert imported rows survive SQLite and HTTP server restart."
                preliminary["fields"]["execution"]["argv"] = []
                call("harness_upsert_node", node=preliminary)
                # R7: one approved build becomes test-only contracts -> build,
                # preserving scope, allowed tuple, concurrency and deadline.
                contracts["fields"].update(ownedFiles=["verify_http.py"], dependsOn=[])
                contracts["parents"] = ["T_HTTP"]
                bead["fields"].update(ownedFiles=["server.py", "import_items.py", "verify_http.py"], dependsOn=["C"])
                call("harness_upsert_nodes", nodes=[contracts, bead])
                options["workers"] = [dict(options["workers"][0], bead=b) for b in ("C", "B")]
                # Repair the scripted worker's real verifier body too: read the
                # same persisted rows after stopping and restarting the server.
                prefix, lifecycle = verifier.split("    server = subprocess.Popen", 1)
                lifecycle = ("    server = subprocess.Popen" + lifecycle).replace("            print(body)\n", "")
                verifier = prefix + "    for restart in range(2):\n" + "\n".join(
                    "    " + line for line in lifecycle.splitlines()) + "\n    print(body)\n"
                (template / "verify_http.py").write_text(verifier + "\n# Published verifier baseline\n", encoding="utf-8")
                scope_how("Tests-first contracts/build ownership and restart-verifier repairs implement the same accepted local product")
                options["expected_revision"] = call("harness_compile", include_detail=True)["planRevision"]
            if not semantic:
                reviewer = choice["fields"]["reviewers"][0]
                # Protect dossier replacement: accepted A obligations survive B's
                # overflow; publication must await their disposition, never retired B.
                # Prior independent-shard checks never replaced a partly judged dossier.
                original_shards = roundtable.packet_shards
                replaced, abandoned = {}, []
                reshard_gap = "Resharding must retain the imported-row evidence gap"
                def split_dossier(owner, provider, *, token_budget=None, **kwargs):
                    if token_budget is not None:
                        return original_shards(owner, provider, token_budget=token_budget, **kwargs)
                    full_packet = original_shards(owner, provider, **kwargs)[0]
                    focused_packet = original_shards(owner, provider, token_budget=full_packet["estimatedTokens"] - 1, **kwargs)[0]
                    return [full_packet, focused_packet]
                def reshard_review(launch, *_):
                    request = json.loads(launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split("\nReturn ONLY JSON", 1)[0])
                    facts = request["sourceRefs"][0]["payload"]["facts"]
                    if facts["reviewer"]["name"] == reviewer["name"]:
                        if not replaced:
                            replaced.update(invocation=facts["invocationId"], judgment=request["id"])
                            return protocol(launch.cli, text=scripted_review_reply(launch, [dict(node_id="plan", severity="major",
                                finding="Retain the readback finding across dossier replacement", suggestion="Retain the HTTP observations",
                                classification="defect-in-plan", scope_reason="Required readback remains unproven")],
                                verdict="negative", rationale="A's concrete readback gap needs disposition", gaps=[reshard_gap]))
                        if facts["invocationId"] == replaced["invocation"]:
                            abandoned.append(request["id"])
                            return (1, "", "prompt is too long: context window exceeded")
                        assert reshard_gap in launch.stdin_text
                    return protocol(launch.cli, text=scripted_review_reply(launch, [], verdict="positive",
                        rationale="Replacement dossier reviewed; retained obligations require their own disposition"))
                with pytest.MonkeyPatch.context() as reshard:
                    for owner in (roundtable, review_packets):
                        reshard.setattr(owner, "packet_shards", split_dossier)
                    for owner in (roundtable, review_provider):
                        reshard.setattr(owner, "_run", reshard_review)
                    replacement = call("harness_roundtable", timeout=5)
                assert len(replacement["successfulReviews"]) == 2, replacement
                carried = [r for r in roundtable.findings(store, expand=True) if r.get("judgmentId") == replaced["judgment"]]
                assert {r["finding"] for r in carried} == {reshard_gap, "Retain the readback finding across dossier replacement"}, carried
                assert all(r["state"] == "open" for r in carried)
                assert abandoned and not set(abandoned) & {r["id"] for r in call("harness_plan_status")["pendingSemanticJudgments"]}
                assert all(not call("harness_judgment", judgment_id=key)["current"] for key in abandoned)
                if replacement["convergence"]:
                    scripted_judgment(call, replacement["convergence"], dict(action="ready"))
                held = workspace_wire(server, "harness_advance", expected_workspace=str(workspace), to_phase="publish")
                assert held["isError"] and held.get("refusalCode") == "pending-judgment"
                carried_proposals = [dict(id=r["id"], status="resolved", resolution="Bound readback observations address A's retained obligation") for r in carried]
                closure = call("harness_resolve_findings", findings=carried_proposals)
                scripted_judgment(call, closure["judgment"], dict(memberDispositions={r["id"]: "resolved" for r in carried}))
                call("harness_resolve_findings", findings=carried_proposals)
                assert store.review_prerequisite(store.manifest_revision())["complete"]
                assert not call("harness_plan_status", full=True)["reviewResolution"]["blockingOpenFindings"]
                review_history_ids.update(r["id"] for r in carried)
                reviewed = replacement
            else:
                reviewed = call("harness_roundtable", timeout=5)
            assert len(reviewed["successfulReviews"]) == 2, reviewed
            assert all(call("harness_judgment", judgment_id=part["judgmentId"])["fixesIssued"] == 0
                       for row in reviewed["reviewers"] for part in row["shardDetails"])
            if semantic:
                visible_repair = workspace_wire(server, "harness_plan_status", expected_workspace=str(workspace))
                assert {"B", "C", "T_HTTP"} <= set(visible_repair["structuredContent"]["continuedPlanChanges"])
                assert "Continued under existing B; updated:" in visible_repair["content"][0]["text"]
                assert len([r for r in store.connection.execute("SELECT payload_json FROM events WHERE kind='confirmation-requested'")
                            if json.loads(r[0])["gate"] == "plan-execution"]) == 1
            # Preserve recorded scope after deleting quota totals: raw SQLite integers
            # previously rendered as unknown; review results alone missed this status consumer.
            visible = workspace_wire(server, "harness_plan_status", expected_workspace=str(workspace), full=True)
            assert not visible["isError"]
            assert "recorded review scope: packet only (recorded)" in visible["content"][0]["text"]
            if not semantic:
                policy = visible["structuredContent"]["reviewPolicy"]
                assert policy["eligible"] and all(not row["incompleteAttempt"] for row in policy["slots"]), policy
            if semantic:
                scripted_judgment(call, reviewed["convergence"], dict(action="ready"))
                # DV10: stale closures + fresh positive reviews used to publish.
                # Earlier stale-binding checks never attempted that publication.
                retained = [r for r in roundtable.findings(store, expand=True) if r["id"] in review_history_ids]
                assert retained and all(r["state"] == "pending" for r in retained)
                held = workspace_wire(server, "harness_advance", expected_workspace=str(workspace), to_phase="publish")
                assert held["isError"] and held.get("refusalCode") == "pending-judgment", held
                assert "What happened: Judgment is pending." in str(held)
                for row in retained:
                    assert row["id"] in str(held)
                status = call("harness_plan_status", full=True)
                assert status["phase"] == "roundtable"
                continuation = status["continuation"]["recommendation"]
                assert continuation["question"] == "finding-disposition"
                assert set(continuation["findingIds"]) == {r["id"] for r in retained}
                assert continuation["mcpTool"] == "harness_resolve_findings"
                pending = call("harness_resolve_findings", findings=proposals)
                scripted_judgment(call, pending["judgment"], dict(memberDispositions={row["id"]: "resolved" for row in proposals}))
                assert call("harness_resolve_findings", findings=proposals)["dispositions"] == len(proposals)
                rejected_ids = {part["judgmentId"] for row in rejected["reviewers"] for part in row["shardDetails"]}
                assert not rejected_ids & {part["judgmentId"] for row in reviewed["reviewers"] for part in row["shardDetails"]}
                convergence = reviewed["convergence"]
                # Canonical full + compact shard evidence deliberately shares all
                # spine refs. Earlier coverage used only one shard per reviewer,
                # so purpose/whole-spine dedup hid the other required request.
                reviewer = choice["fields"]["reviewers"][0]
                full = roundtable.packet_shards(store, reviewer["provider"])[0]
                focused = roundtable.packet_shards(store, reviewer["provider"], token_budget=full["estimatedTokens"] - 1)[0]
                assert full["hash"] != focused["hash"]
                shards = [full, focused]
                roundtable.bind_review_dossiers(store, {reviewer["provider"]: shards}, [reviewer])
                shard_requests = [store.review_judgment(reviewer, shard, prepare=False) for shard in shards]
                meaning = dict(findings=[], depth="Assess both bound evidence shards independently; observations unavailable")
                for request in shard_requests:
                    scripted_judgment(call, request, meaning, verdict="inconclusive")
                visible = {r["id"]: r for r in call("harness_plan_status")["pendingSemanticJudgments"]}
                # Explicit replacement retires exhausted requests; they cannot
                # take continuation from the distinct live shard identities.
                assert not exhausted_ids & visible.keys()
                assert all(visible[r["id"]]["submitReady"] for r in shard_requests)
                assert visible[shard_requests[0]["id"]]["requestKey"] != visible[shard_requests[1]["id"]]["requestKey"]
                meaning["depth"] = "Completed independent assessment of the bound import and HTTP evidence shard"
                scripted_judgment(call, shard_requests[0], meaning)
                partial = call("harness_plan_status", full=True)
                assert shard_requests[1]["id"] in partial["continuation"]["recommendation"]["judgmentIds"]
                assert not partial["reviewResolution"]["review"]["current"]
                held = workspace_wire(server, "harness_advance", expected_workspace=str(workspace), to_phase="publish")
                assert held["isError"] and "plan-publication-evidence-required" in str(held)
                scripted_judgment(call, shard_requests[1], meaning)
                assert call("harness_plan_status", full=True)["reviewResolution"]["review"]["current"]
                amended = store.spine().node("R2")
                amended["text"] += " HTTP readback must preserve both imported rows."
                call("harness_upsert_node", node=amended)
                call("harness_compile", include_detail=True)
                # Same-ID scope edits previously bypassed B without any meaning
                # check; frozen-ID consent tests never changed requirement content.
                before_launches = len(provider_launches)
                held = workspace_wire(server, "harness_readiness", expected_workspace=str(workspace),
                                      reviewers=selected_reviewers, beads=["B"])
                assert held["isError"] and held["refusalCode"] == "pending-judgment", held
                scope_judge = held["structuredContent"]["scopeJudgment"]
                assert scope_judge["current"] and scope_judge["question"] == "scope-intent"
                assert "scope.nodes.R2.text" in {d["field"] for d in held["structuredContent"]["diff"]}
                scripted_judgment(call, scope_judge, {}, verdict="inconclusive")
                still_waiting = workspace_wire(server, "harness_readiness", expected_workspace=str(workspace),
                                              reviewers=selected_reviewers, beads=["B"])
                assert still_waiting["refusalCode"] == "pending-judgment"
                assert still_waiting["structuredContent"]["scopeJudgment"]["id"] == scope_judge["id"]
                assert len(provider_launches) == before_launches
                scripted_judgment(call, scope_judge, dict(impact="HOW"),
                    rationale="Both imported rows were already required by the accepted readback scenario; this wording repairs the trace")
                assert "R2" in call("harness_plan_status")["continuedPlanChanges"]
                assert len([r for r in store.connection.execute("SELECT payload_json FROM events WHERE kind='confirmation-requested'")
                            if json.loads(r[0])["gate"] == "plan-execution"]) == 1
                manifest = call("harness_compile", include_detail=True)
                options["expected_revision"] = manifest["planRevision"]
                assert not store.successful_reviews(manifest["planRevision"])
                assert not call("harness_judgment", judgment_id=convergence["id"])["current"]
                pending = call("harness_resolve_findings", findings=proposals)
                scripted_judgment(call, pending["judgment"], dict(memberDispositions={row["id"]: "resolved" for row in proposals}))
                closed = call("harness_resolve_findings", findings=proposals)
                assert len(closed["reuseJudgments"]) == 2
                # Requests are distinct by reviewer/member, never hidden by purpose.
                requests = closed["reuseJudgments"]
                pending_ids = {row["id"] for row in call("harness_plan_status")["pendingSemanticJudgments"]}
                assert {r["id"] for r in requests} <= pending_ids
                first_reuse = scripted_judgment(call, requests[0], dict(reuse=True))
                scripted_judgment(call, first_reuse["convergence"], dict(action="ready"))
                partial = call("harness_plan_status", full=True)
                assert requests[1]["id"] in {r["id"] for r in partial["pendingSemanticJudgments"]}
                assert partial["continuation"]["recommendation"]["kind"] == "judgment_required"
                assert partial["reviewResolution"]["review"]["current"] is False
                result = scripted_judgment(call, requests[1], dict(reuse=True),
                    rationale="The actual delta clarifies existing readback; the real verifier covers both rows")
                current_convergence = result["convergence"]
                assert current_convergence["current"] and current_convergence["id"] != convergence["id"]
                assert len(store.successful_reviews(manifest["planRevision"])) == 2
                pending_ids = {row["id"] for row in call("harness_plan_status")["pendingSemanticJudgments"]}
                assert current_convergence["id"] in pending_ids
                assert call("harness_plan_status", full=True)["continuation"]["recommendation"]["kind"] == "judgment_required"
                scripted_judgment(call, current_convergence, dict(action="ready"))
                assert call("harness_plan_status")["continuedPlanChanges"]
            call("harness_advance", to_phase="publish")
            if semantic:
                call("harness_publish")
            else:
                call(
                    "harness_publish",
                    proof_level="prospective",
                    reason="No reference implementation",
                )
            call("harness_advance", to_phase="execute")
            published = store.published_manifest(
                store.status()["publication"]["published_revision"]
            )
            (workspace / "sample.json").write_text(json.dumps(published), encoding="utf-8")
            if not semantic:
                options["expected_revision"] = store.status()["published_revision"]

            if semantic:
                # C must consume the admitted job's real root delivery. Standalone
                # dispatch proves scenarios, but never supplies that delivery.
                contract_sources = {"verify_http.py": verifier}
                (workspace / "scripted-provider.json").write_text(
                    json.dumps(
                        {
                            "contracts": contract_sources,
                            "build": {
                                "server.py": product,
                                "import_items.py": importer,
                                "verify_http.py": verifier,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                actual_popen = subprocess.Popen

                def launch(argv, **kwargs):
                    if "codex_harness.core.planner_jobs" in argv:
                        argv = [
                            sys.executable,
                            "-B",
                            str(Path(__file__).resolve()),
                            "controller",
                            str(workspace),
                            "shelflife",
                            argv[-1],
                        ]
                    return actual_popen(argv, **kwargs)

                with pytest.MonkeyPatch.context() as process_patch:
                    process_patch.setattr(subprocess, "Popen", launch)
                    job = call("harness_worker_start", **options)
                    deadline = time.time() + options["timeout"]
                    while job["status"] not in {"completed", "failed"} and time.time() < deadline:
                        job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=1)
                    # AUDIT1: a live writer pauses with a partial terminal envelope.
                    # Immediate replies never exposed silence truncating valid evidence.
                    expired = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
                    assert expired["beads"]["C"]["status"] == "contracts-red", job
                    live = json.loads((workspace / ".codex-harness/live-provider.json").read_text())
                    assert live["outputSeconds"] >= 3 and live["cpuSeconds"] >= 3
                    assert live["flushSeconds"] >= 125, live
                    contract_attempt = Path(expired["beads"]["C"]["evidenceDir"]) / "attempt"
                    assert "thinking" in (contract_attempt / "stderr.txt").read_text()
                    complete = json.loads((contract_attempt / "result.json").read_text())
                    assert complete["permissionGuard"]["telemetryComplete"] and complete["exitCode"] == 0
                    assert (contract_attempt / "events.jsonl").read_text().endswith('{"type": "turn.completed"}\n')
                    truncated_attempt = Path(expired["beads"]["B"]["evidenceDir"]) / "attempt"
                    stopped = json.loads((truncated_attempt / "result.json").read_text())
                    assert job["status"] == "failed" and stopped["exitCode"] == 0 and stopped["failure"] is None
                    assert stopped["outputComplete"] and stopped["statusBeforePermissionHold"] == "exited"
                    assert stopped["cleanup"]["quiescenceVerified"] and stopped["cleanup"]["activeProcessesAfterTeardown"] == 0
                    guard = stopped["permissionGuard"]
                    assert guard["requiresAuditHold"] and guard["auditReason"] == "malformed"
                    assert guard["disposition"] == "unknown" and guard["didObservePermissionDenial"] is None
                    assert guard["observationError"] == "Invalid or truncated provider event"
                    assert expired["beads"]["B"]["status"] == dispatch.STATUS_AUDIT_HELD
                    assert "held: denial audit inconclusive" in job["terminal"]["dispatchStatus"]
                    assert not expired.get("delivery") and not expired.get("didRunIntegratedValidation")
                    recovered = call("harness_recovery", mode="resolve", job_id=job["jobId"],
                        choice="readmit", reason="Supply the missing terminal event from a fresh provider within the same B")
                    assert recovered["choice"] == "readmit"
                    job = call("harness_worker_start", **options)
                    assert job["jobId"] == recovered["successor"]
                    deadline = time.time() + options["timeout"]
                    while job["status"] not in {"completed", "failed"} and time.time() < deadline:
                        job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=1)
                    # r7: an omitted declared test crashed fallback RED in git restore.
                    # Earlier journeys always supplied the whole worker bundle.
                    incomplete = "incomplete: declared verify_http.py missing from candidate"
                    failed = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
                    assert job["status"] == "failed" and failed["beads"]["B"]["status"] == incomplete, job
                    assert failed["beads"]["B"]["integrated"] is None
                    assert failed["semanticContracts"]["state"] == "RED" and "semanticValidation" not in failed
                    assert "error" not in job["terminal"]
                    recovered = call("harness_recovery", mode="resolve", job_id=job["jobId"],
                        choice="readmit", reason="Supply the exact declared worker input")
                    assert recovered["choice"] == "readmit" and recovered["proof"]["faultOutcomes"] == {"B": incomplete}
                    assert recovered["proof"]["controllerAndChildren"]["quiescenceVerified"]
                    job = call("harness_worker_start", **options)
                    assert job["jobId"] == recovered["successor"]
                    deadline = time.time() + options["timeout"]
                    while job["status"] not in {"completed", "failed"} and time.time() < deadline:
                        job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=1)
                assert job["status"] == "completed", job
                run = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
                assert "Previous worker outcome; repair before returning: " + incomplete in run["beads"]["B"]["prompt"]
                assert run["semanticContracts"]["state"] == "RED" and run["semanticValidation"]["state"] == "PASS"
                assert all(f["sha256"] for f in run["semanticValidation"]["testSources"]["files"])
                assert run["delivery"]["root"] == str(workspace.resolve()), run
            else:
                # Independent real failures A, B, then A+B must not pool their
                # allowances. Earlier serial C/B faults never exercised this union.
                actual_popen = subprocess.Popen
                def launch(argv, **kwargs):
                    if "codex_harness.core.planner_jobs" in argv:
                        argv = [sys.executable, "-B", str(Path(__file__).resolve()),
                                "controller", str(workspace), "parallel-http", argv[-1]]
                    return actual_popen(argv, **kwargs)
                products = {"A": {"status.py": product.replace("b'shelf'", "b'ready'")},
                            "B": {"server.py": product}}
                def start_fault(faults):
                    (workspace / "scripted-provider.json").write_text(
                        json.dumps(dict(products=products, fail=list(faults), deadlinePair=tuple(faults) == ("A",),
                                        cancelBead=tuple(faults) == ("B",))), encoding="utf-8")
                    job = call("harness_worker_start", **options)
                    deadline = time.monotonic() + 180
                    released = cancelled = False
                    while job["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
                        job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=1)
                        if not job.get("run"):
                            continue
                        observed = json.loads(Path(job["run"]["recordPath"]).read_text())
                        if tuple(faults) == ("A",) and not released:
                            if not all(observed["beads"][b].get("evidenceDir") for b in ("A", "B")):
                                continue
                            a = Path(observed["beads"]["A"]["evidenceDir"]) / "attempt/result.json"
                            b = Path(observed["beads"]["B"]["evidenceDir"]) / "attempt"
                            if a.exists() and (workspace / ".codex-harness/sibling-started").exists():
                                ended = json.loads(a.read_text())
                                assert ended["failure"]["type"] == "TimeoutExpired"
                                assert ended["cleanup"]["quiescenceVerified"] and ended["cleanup"]["activeProcessesAfterTeardown"] == 0
                                assert ended["cleanup"]["activeProcessesBeforeTeardown"] >= 3
                                assert (workspace / ".codex-harness/deadline-child").exists()
                                assert json.loads((b / "request.json").read_text())["processObservedAt"] < ended["finishedAt"]
                                before = (b / "stderr.txt").stat().st_size
                                until = time.monotonic() + 2
                                while (b / "stderr.txt").stat().st_size == before and time.monotonic() < until:
                                    time.sleep(.05)
                                assert not (b / "result.json").exists() and (b / "stderr.txt").stat().st_size > before
                                (workspace / ".codex-harness/sibling-release").touch()
                                released = True
                        if tuple(faults) == ("B",) and not cancelled and (workspace / ".codex-harness/cancel-provider").exists():
                            from codex_harness.core.attempt_control import request_cancel
                            request_cancel(Path(job["run"]["recordPath"]).parents[1], "B")
                            cancelled = True
                    if tuple(faults) == ("A",):
                        assert released and observed["beads"]["B"]["integrated"]["validation"]["passed"]
                    if tuple(faults) == ("B",):
                        assert cancelled
                        control = json.loads((Path(observed["beads"]["B"]["evidenceDir"]) / "attempt/result.json").read_text())
                        assert control["cancellation"]["status"] == "cancelled" and control["cleanup"]["activeProcessesAfterTeardown"] == 0
                    return job
                with pytest.MonkeyPatch.context() as process_patch:
                    process_patch.setattr(subprocess, "Popen", launch)
                    for faults, expected in ((("A",), "readmit"), (("B",), "readmit"),
                                             (("A", "B"), "readmit"), (("A", "B"), "steer")):
                        job = start_fault(faults)
                        assert job["status"] == "failed", job
                        recovered = call("harness_recovery", mode="resolve", job_id=job["jobId"],
                            choice="readmit", reason="Recover the observed HTTP worker faults")
                        assert set(recovered["proof"]["faultBeads"]) == set(faults)
                        assert recovered["choice"] == expected, recovered
                    if tuple(faults) == ("A", "B"):
                        offending = job["run"]["beads"]["B"]
                        assert offending["status"] == "rejected: changes outside owned files"
                        assert offending["guard"]["changesOutsideOwnedFiles"] == ["unexpected.txt"]
                        assert offending["candidateRoot"] == json.loads(Path(job["run"]["recordPath"]).read_text())["beads"]["B"]["attempt"]["cwd"]
                    assert recovered["successor"] is None
                    scripted_judgment(call, recovered["judgment"], dict(action="readmit",
                        direction="Keep both accepted HTTP services; use a fresh isolated attempt"))
                    directed = call("harness_recovery", mode="resolve", job_id=job["jobId"],
                        choice="readmit", reason="Apply the recorded same-scope direction")
                    assert directed["steeringJudgmentId"] == recovered["judgment"]["id"]
                    job = start_fault(())
                assert job["status"] == "completed", job
                run = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
            # Main now declares the concurrent HTTP integration proof; STALL1's
            # real scripted workers must preserve that stronger result as well.
            assert run["status"] == dispatch.STATUS_VALIDATED, run
            if semantic:
                # Catch loss of native job/controller/snapshot/attempt authority when quota
                # code is deleted. Earlier fake_workers bypassed all those launch checks.
                for bead in run["beads"].values():
                    attempt = Path(bead["evidenceDir"]) / "attempt"
                    original = json.loads((attempt / "request.json").read_text())
                    result = json.loads((attempt / "result.json").read_text())
                    assert original["processId"] == result["processId"] > 0
                    # Portable fallback used to skip on_launch and omit cleanup;
                    # consumer-only doubles missed lost provenance and AttributeError.
                    assert result["cleanup"]["payloadPid"] == original["processId"]
                    assert result["cleanup"]["quiescenceVerified"] and result["cleanup"]["activeProcessesAfterTeardown"] == 0
                    assert original["processProvenanceDigest"] == result["processProvenanceDigest"]
                    assert (attempt / "process-provenance.json").is_file()
                    assert result["exitCode"] == 0 and result["cleanup"]["controllerProcessExited"]
                    assert bead["localProcessProvenance"]["state"] == "consistent"
            if not semantic:
                assert not run["baseline"]["validation"]["B"]["passed"], run
            if semantic:
                assert run["beads"]["C"]["status"] == "contracts-red", run
                assert set(run["beads"]) == {"C", "B"}
                assert run["semanticValidation"]["red"]["evidenceBinding"] == run["semanticContracts"]["evidenceBinding"]
                assert next(r for r in run["requirementValidation"] if r["bead"] == "B")[
                    "passed"
                ], run
            else:
                assert {r["bead"] for r in run["requirementValidation"] if r.get("passed")} == {"A", "B", "integration"}, run
            if not semantic:
                derived = call("harness_accept_scenarios", derive=True)
                scenarios = derived["scenarios"]
                for scenario in scenarios:
                    pattern = (
                        "test_http.py"
                        if scenario["id"] == "first-moment"
                        else "test_http_concurrent.py"
                    )
                    scenario.update(
                        steps=[
                            '"{python}" -B -m unittest discover -s "{repo}/tests" -p ' + pattern
                        ],
                        expect={"exit": 0, "stderr": "OK"},
                    )
                call("harness_accept_scenarios", scenarios=scenarios)
                # A forced stop must not pass just because exit 19 was expected.
                # Ordinary exit-0 acceptance never exercised this credit boundary.
                from codex_harness.core.acceptance import run_scenarios
                stopped_marker = Path(run["repo"]) / ".acceptance-interrupted"
                interruption = json.loads(json.dumps(scenarios))
                interruption[0].update(steps=[subprocess.list2cmdline([sys.executable, "-B", "-c",
                    "import sys,time; from pathlib import Path; Path(sys.argv[1]).write_text('started'); time.sleep(600)",
                    "{repo}/.acceptance-interrupted"])], expect={"exit": 19})
                call("harness_accept_scenarios", scenarios=interruption)
                stopped = run_scenarios(run["repo"], store.acceptance_scenarios(), should_stop=stopped_marker.exists)
                assert not stopped[0]["passed"], stopped
                step = stopped[0]["steps"][0]
                assert step["cleanup"]["stopReason"] == "acceptance-stopped" and step["cleanup"]["activeProcessesAfterTeardown"] == 0
                stopped_marker.unlink()
                call("harness_accept_scenarios", scenarios=scenarios)
            if semantic:
                # r11: a passing product could not be re-observed after verifier
                # edits; frozen-plan journeys rebuilt instead of retaining bytes.
                from codex_harness.core.planner_candidate import file_hashes
                product_hashes = file_hashes(Path(run["repo"]), ["server.py", "import_items.py"])
                attempts = sorted((workspace / ".codex-harness/worker-jobs").rglob("attempt/request.json"))
                original_root = Path(run["beads"]["B"]["integrated"]["root"])
                original_proof = run["semanticValidation"]["evidenceBinding"]
                baseline_verifier = (template / "verify_http.py").read_text(encoding="utf-8")
                baseline_server = (template / "server.py").read_text(encoding="utf-8")
                built_product = run["semanticValidation"]["productBaseline"]
                original_test = store.spine().node("T_HTTP")

                def publish_verifier(rationale):
                    previous_publication = store._meta()["published_revision"]
                    scope_how(rationale)
                    options["expected_revision"] = call("harness_compile", include_detail=True)["planRevision"]
                    assert options["expected_revision"] != previous_publication
                    call("harness_advance", to_phase="roundtable")
                    reviewed = call("harness_roundtable", timeout=5)
                    members = [r for r in roundtable.findings(store, expand=True) if r["state"] != "closed"]
                    if members:
                        dispositions = [dict(id=r["id"], status="resolved", resolution="The unchanged product and current verifier preserve the reviewed readback obligation") for r in members]
                        pending = call("harness_resolve_findings", findings=dispositions)
                        scripted_judgment(call, pending["judgment"], dict(memberDispositions={r["id"]: "resolved" for r in members}))
                        applied = call("harness_resolve_findings", findings=dispositions)
                        for reuse in applied.get("reuseJudgments", []):
                            reused = scripted_judgment(call, reuse, dict(reuse=True))
                            if reused.get("convergence"):
                                convergence = call("harness_judgment", judgment_id=reused["convergence"]["id"])
                                if convergence["current"] and not convergence["positiveCredit"]:
                                    scripted_judgment(call, convergence, dict(action="ready"))
                    if reviewed.get("convergence"):
                        convergence = call("harness_judgment", judgment_id=reviewed["convergence"]["id"])
                        if convergence["current"] and not convergence["positiveCredit"]:
                            scripted_judgment(call, convergence, dict(action="ready"))
                    call("harness_advance", to_phase="publish")
                    call("harness_publish")
                    call("harness_advance", to_phase="execute")

                # HIGH review: a later root expansion must never exempt built
                # product bytes; frozen declarations missed this two-refresh attack.
                # Rename/removal also needs the prior verifier root through C.
                for filename, source, roots in (
                    ("verify_http.py", baseline_verifier + "\nprint('retained-verifier-v2', file=sys.stderr)\n", ["verify_http.py", "server.py"]),
                    ("verify_renamed.py", baseline_verifier, ["verify_renamed.py"]),
                ):
                    files = {"verify_http.py": source if filename == "verify_http.py" else None, filename: source}
                    scripted_worker(run["beads"]["B"]["prompt"], template, files)
                    stale = workspace_wire(server, "harness_accept_assess", expected_workspace=str(workspace),
                        judgment=dict(repo=run["repo"], check="execution"))
                    assert stale["isError"] and "verification inputs changed" in str(stale)
                    edited_test = json.loads(json.dumps(original_test))
                    edited_test["text"] += " Observe current verifier " + filename + (" with product readback" if len(roots) > 1 else "")
                    edited_test["fields"]["execution"]["testRoots"] = roots
                    edited_test["fields"]["execution"]["entry"] = filename
                    call("harness_upsert_node", node=edited_test)
                    if filename != "verify_http.py":
                        renamed = store.spine().node("V1")
                        renamed["fields"]["targets"]["http"]["entry"] = filename
                        call("harness_upsert_node", node=renamed)
                        for key in ("C", "B"):
                            ownership = store.spine().node(key)
                            ownership["fields"]["ownedFiles"] = [filename if n == "verify_http.py" else n for n in ownership["fields"]["ownedFiles"]]
                            call("harness_upsert_node", node=ownership)
                    publish_verifier("Verifier declarations and filenames change HOW we observe the same accepted product; product obligations are unchanged")
                    if filename == "verify_http.py":
                        # Pre-upgrade jobs have dispatch identity but no retained-candidate record.
                        (workspace / ".codex-harness/retained-candidate.json").unlink()
                        remedy = call("harness_plan_status", full=True)["acceptance"]["candidatePrerequisite"]
                        assert remedy["recoveryArguments"] == dict(mode="inspect", job_id=job["jobId"])
                        inspected = call("harness_recovery", **remedy["recoveryArguments"])
                        assert inspected["revalidation"]["available"], inspected
                    refreshed = call("harness_recovery", mode="resolve", job_id=job["jobId"], choice="revalidate",
                        reason="Observe the unchanged product with the current verifier, without another build")
                    assert refreshed["validation"]["state"] == "pending-judgment", refreshed
                    observed_status = call("harness_plan_status", full=True)
                    assert observed_status["acceptance"]["candidatePrerequisite"]["complete"], observed_status
                    run = json.loads(Path(job["run"]["recordPath"]).read_text())
                    root = Path(run["beads"]["B"]["integrated"]["root"])
                    assert file_hashes(root, product_hashes) == file_hashes(original_root, product_hashes) == product_hashes
                    proof = run["semanticValidation"]
                    assert proof["productBaseline"] == built_product and built_product["server.py"] == product_hashes["server.py"]
                    assert proof["planRevision"] == options["expected_revision"]
                    assert proof["evidenceBinding"] != original_proof
                    source_row = next(f for f in proof["testSources"]["files"] if f["path"] == filename)
                    assert source_row["sha256"] == hashlib.sha256((template / filename).read_bytes()).hexdigest()
                    stderr = next(r for r in proof["results"] if r["id"] == "T_HTTP")["actions"]["execute"]["stderr"]
                    assert ("retained-verifier-v2" in stderr) is ("retained-verifier-v2" in source)
                    assert all(r["cleanup"]["quiescenceVerified"] and r["cleanup"]["activeProcessesAfterTeardown"] == 0 for r in proof["results"])
                    if len(roots) > 1:
                        for changed in ("server.py",):
                            scripted_worker(run["beads"]["B"]["prompt"], template,
                                {changed: baseline_server + "\n# Product bytes changed after root expansion\n" if changed == "server.py" else "New product file\n"})
                            fault = store.spine().node("T_HTTP")
                            fault["text"] += " Inspect changed " + changed
                            call("harness_upsert_node", node=fault)
                            publish_verifier("Exercise recovery guards without changing accepted product behavior; verifier classification grants no product-write authority")
                            refused = call("harness_recovery", mode="resolve", job_id=job["jobId"], choice="revalidate",
                                reason="Reject changed or added product bytes even after verifier-root expansion")
                            assert refused["validation"]["state"] == "ERROR" and "product inputs changed" in str(refused["validation"]), refused
                            assert file_hashes(root, product_hashes) == product_hashes and not (root / "unowned-product.txt").exists()
                            scripted_worker(run["beads"]["B"]["prompt"], template,
                                {changed: baseline_server if changed == "server.py" else None})
                # An externally added product file must also be refused by the
                # canonical retained-candidate guard, before any observation launches.
                scripted_worker(run["beads"]["B"]["prompt"], root, {"unowned-product.txt": "New product file\n"})
                outside = call("harness_recovery", mode="inspect", job_id=job["jobId"])
                assert not outside["revalidation"]["available"] and "retained candidate changed" in outside["revalidation"]["reason"]
                scripted_worker(run["beads"]["B"]["prompt"], root, {"unowned-product.txt": None})
                judge_candidate(call, dict(repo=str(root), check="execution"))
                refreshed = call("harness_recovery", mode="resolve", job_id=job["jobId"], choice="revalidate",
                    reason="Consume the current observation's bound execution judgment")
                assert refreshed["validation"]["state"] == "PASS", refreshed
                run = json.loads(Path(job["run"]["recordPath"]).read_text())
                assert file_hashes(Path(run["repo"]), product_hashes) == product_hashes
                assert (workspace / "verify_renamed.py").is_file() and not (workspace / "verify_http.py").exists()
                assert sorted((workspace / ".codex-harness/worker-jobs").rglob("attempt/request.json")) == attempts
                assert decisions == ["align+vision-lock", "plan-execution"]
            call("harness_advance", to_phase="accept")
            if not semantic:
                # A real capture queues acknowledgement before scenarios may launch.
                # A changed product must refuse here, not run and reject credit later.
                admitted = call("harness_accept_run", repo=run["repo"], subject="product")
                owner = server.acceptance
                owner.changed.clear()
                owner.dispatch_after_flush(store)
                assert owner.changed.wait(timeout=15), "Product capture did not reach acknowledgement"
                product_path = Path(run["repo"]) / "server.py"
                original = product_path.read_text(encoding="utf-8")
                product_path.write_text("from pathlib import Path; Path('.stale-scenario-ran').write_text('ran')\n" + original,
                                        encoding="utf-8")
                owner.drain(store)
                owner.thread.join(timeout=30)
                assert not owner.thread.is_alive(), "Stale acceptance did not terminate"
                owner.drain(store)
                refused = call("harness_accept_status", id=admitted["attemptId"], error=True)
                assert not (Path(run["repo"]) / ".stale-scenario-ran").exists(), "Stale scenarios executed"
                assert refused["result"] is None and "acceptance-revision-changed" in refused["error"]
                assert "Rerun acceptance" in refused["error"]
                product_path.write_text(original, encoding="utf-8")
            accepted = finish_public_acceptance(
                server,
                workspace_wire(
                    server,
                    "harness_accept_run",
                    expected_workspace=str(workspace),
                    repo=run["repo"],
                    subject="product",
                ),
            )
            assert accepted["structuredContent"]["result"]["passed"], accepted
            if semantic:
                # C must reject a changed/missing delivered dependency even when
                # acceptance against the intact integration passed. Owned-file-only
                # delivery tests missed this productBaseline entry.
                dependency = workspace / "table_name.txt"
                assert dependency.read_text() == "items"
                for changed in ("other_table", None):
                    dependency.write_text(changed) if changed is not None else dependency.unlink()
                    refused = workspace_wire(server, "harness_accept", expected_workspace=str(workspace),
                        statement="The product meets the vision", by="proxy:scripted-owner")
                    assert refused["isError"] and "table_name.txt" in str(refused) and "acceptance-delivery-invalid" in str(refused)
                    if changed is not None:
                        from codex_harness.core import planner_jobs
                        from codex_harness.core.planner_candidate import deliver
                        with pytest.raises(ValueError, match="existing table_name.txt changed"):
                            deliver(workspace, planner_jobs._request(Path(job["evidenceDir"])), run)
                    dependency.write_text("items", encoding="utf-8")
                # r12: after a new publication and revalidation of the same job,
                # a historical acceptance binding used to crash every status read.
                # Frozen-publication journeys never reached that reader seam.
                owner_record = workspace / ".codex-harness/retained-candidate.json"
                owner = json.loads(owner_record.read_text())
                assert ".codex/config.toml" in owner["productBaseline"]
                assert ".codex/config.toml" not in owner["productHashes"] and not (workspace / ".codex").exists()
                assert (workspace / ".gitignore").read_bytes() == (template / ".gitignore").read_bytes()
                assert owner["productHashes"][".gitignore"] == hashlib.sha256((workspace / ".gitignore").read_bytes()).hexdigest()
                assert owner["jobId"] == job["jobId"] and owner["runId"] == run["runId"]
                assert owner["publicationRevision"] == store._meta()["published_revision"]
                assert owner["candidateRoot"] == str(root) and owner["productBaseline"] == built_product
                owner_record.rename(owner_record.with_suffix(".held"))
                try:
                    pending = call("harness_plan_status", full=True)["acceptance"]
                    assert not pending["accepted"] and not pending["candidatePrerequisite"]["complete"]
                    assert "retained candidate unavailable" in pending["invalidMessage"] and "harness_recovery" in pending["next"]
                    refused = workspace_wire(server, "harness_accept_assess", expected_workspace=str(workspace),
                        judgment=dict(repo=run["repo"], check="execution"))
                    assert refused["isError"] and "retained candidate unavailable" in str(refused)
                    refused = workspace_wire(server, "harness_accept", expected_workspace=str(workspace),
                        statement="The retained product meets the vision", by="proxy:scripted-owner")
                    assert refused["isError"] and "retained candidate unavailable" in str(refused)
                finally:
                    owner_record.with_suffix(".held").rename(owner_record)
                owner_record.write_text(json.dumps(dict(owner, runId="unbound")), encoding="utf-8")
                try:
                    pending = call("harness_plan_status", full=True)["acceptance"]
                    assert not pending["candidatePrerequisite"]["complete"] and not pending["accepted"]
                    assert "candidate record is inconsistent" in pending["invalidMessage"]
                    assert "harness_recovery" in pending["next"] and job["jobId"] in pending["next"]
                finally:
                    owner_record.write_text(json.dumps(owner), encoding="utf-8")
                assert call("harness_plan_status", full=True)["acceptance"]["candidatePrerequisite"]["complete"]
            def acceptance_gap(launch, *_):
                request = json.loads(launch.stdin_text.split("\n## Bound Judgment\n", 1)[1].split("\nReturn ONLY JSON", 1)[0])
                reviewer = request["sourceRefs"][0]["payload"]["facts"]["reviewer"]
                # A negative verdict with no presentation findings still creates
                # concrete obligations. The next positive packet must carry them.
                items = []
                return tuple(v.replace("source-only", reviewer["model"]).replace("Some(High)", "Some(Medium)") if isinstance(v,str) else v
                             for v in protocol(launch.cli, text=scripted_review_reply(launch, items, verdict="negative",
                                 rationale="The readback finding needs candidate-bound assessment", gaps=["Readback assessment is pending"])))
            with pytest.MonkeyPatch.context() as gap:
                for owner in (roundtable, review_provider):
                    gap.setattr(owner, "_run", acceptance_gap)
                acceptance_members = call("harness_accept_review", repo=run["repo"])["findings"]
            review_history_ids.update(row["id"] for row in acceptance_members)
            assert len(acceptance_members) == 2 and all(row["state"] == "open" for row in acceptance_members)
            dispositions = [dict(id=row["id"], status="resolved", resolution="Current readback addresses this retained obligation")
                            for row in acceptance_members]
            if not semantic:
                prepared = call("harness_resolve_findings", findings=dispositions)
                scripted_judgment(call, prepared["judgment"], dict(memberDispositions={row["id"]:"resolved" for row in acceptance_members}))
                product = Path(run["repo"])
                source = (product / "server.py").read_text(encoding="utf-8")
                changed = source.replace("self.end_headers()",
                    "self.send_header('Content-Type', 'text/plain')\n        self.end_headers()")
                assert changed != source
                scripted_worker(run["beads"]["B"]["prompt"], product, {"server.py": changed})
                stale = call("harness_judgment", judgment_id=prepared["judgment"]["id"])
                assert not stale["current"] and not stale["positiveCredit"]
                # Round 2: the run is the disposition's producer. Earlier coverage
                # reran it manually while guidance advertised immediately stale judgments.
                status = call("harness_plan_status", full=True)
                action = status["continuation"]["recommendation"]
                assert action["mcpTool"] == "harness_accept_run", action
                assert "harness_accept_run" in status["acceptance"]["next"]
                assert status["acceptance"]["accepted"] is False
                assert all(row["state"] == "pending" for row in store.acceptance_findings())
                scripted_worker(run["beads"]["B"]["prompt"], product, {"server.py": source})
                assert call("harness_resolve_findings", findings=dispositions)["dispositions"] == len(dispositions)
                assert call("harness_judgment", judgment_id=prepared["judgment"]["id"])["positiveCredit"]
                assert all(r["state"] == "closed" for r in store.acceptance_findings() if r["id"] in {d["id"] for d in dispositions})
                # Applying a closure never changes immutable facts. A later candidate
                # edit must remove effective closure everywhere, including ordinary reads.
                scripted_worker(run["beads"]["B"]["prompt"], product, {"server.py": changed})
                assert all(r["state"] == "pending" for r in store.acceptance_findings())
                assert all(r["status"] != "resolved" for r in store.acceptance_findings())
                # Same regression at C: execute the changed candidate and obtain
                # fresh positive reviews, then try acceptance before disposition.
                action = call("harness_plan_status", full=True)["continuation"]["recommendation"]
                assert action["mcpTool"] == "harness_accept_run"
                admitted = workspace_wire(server, action["mcpTool"], expected_workspace=str(workspace), **action["arguments"])
                waiting = call("harness_plan_status", full=True)["continuation"]["recommendation"]
                assert waiting["mcpTool"] == "harness_accept_status"
                assert waiting["arguments"]["id"] == admitted["structuredContent"]["attemptId"]
                rerun = finish_public_acceptance(server, admitted)
                assert rerun["structuredContent"]["result"]["passed"], rerun
            # Migration refreshes execution only. No review/approval lineage is
            # restored; both legacy inputs must proceed through fresh judgments.
            from codex_harness.core import acceptance
            original_approval = call("harness_plan_status")["acceptance"]["approval"]
            old_run = acceptance._latest_run(store)
            legacy = dict(old_run, revisionAlgorithm="git-diff-v1",
                          revision=acceptance._integrated_revision_v1(run["repo"]))
            # Historical persisted input only; judgments and refreshed runs still
            # come from their canonical producers, with no mocked acceptance.
            with store._transaction():
                if not semantic:
                    legacy_scenarios = acceptance._raw_scenarios(store)
                    for item in legacy_scenarios:
                        del item["nodeRevisions"]
                        store.connection.execute("UPDATE acceptance_scenarios SET scenario_json=? WHERE id=?",
                                                 (json.dumps(item), item["id"]))
                    legacy["scenarioRevision"] = acceptance._hash(legacy_scenarios)
                store.connection.execute("UPDATE acceptance_runs SET result_json=? WHERE id=?",
                                         (json.dumps(legacy), legacy["id"]))
            status = call("harness_plan_status", full=True)
            action = status["continuation"]["recommendation"]
            assert action["mcpTool"] == "harness_accept_rebind", action
            refreshed = call(action["mcpTool"], **action["arguments"])
            refreshed_id = refreshed["run"]["id"]
            assert refreshed["run"]["passed"] and refreshed_id != legacy["id"]
            assert "approval" not in refreshed and "reviews" not in refreshed
            status = call("harness_plan_status", full=True)
            assert status["acceptance"]["accepted"] is False
            assert status["acceptance"]["approval"] == original_approval
            assert status["continuation"]["recommendation"]["question"] == "finding-disposition"
            if not semantic:
                # Upgrade fixture: retain a base-produced positive C with no review
                # binding. Prior replays created only newly bound C, missing revival
                # of historical consent after template drift and fresh same-run reviews.
                from codex_harness.core.approvals import assess
                from codex_harness.core.reviewer_evidence import review_binding
                historical = assess(store, "user-decision", dict(subject=dict(kind="C", run=refreshed["run"]),
                    reviewBinding=review_binding(store, run_id=refreshed_id), words="Yes", actor="user"))
                scripted_judgment(call, historical, dict(decision="approve"))
                historical = store._judgment_record(historical["id"])
                for ref in historical["sourceRefs"]:
                    ref["payload"]["facts"].pop("reviewBinding", None)
                historical["binding"] = hashlib.sha256(json.dumps({key: historical[key] for key in
                    ("id", "question", "subjectRefs", "evidenceRefs", "sourceRefs", "candidate")},
                    sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                legacy_c = dict(id="legacy-unbound-C", runId=refreshed_id, by="user", statement="Yes",
                                isProxy=False, judgmentId=historical["id"], human_identity_verified=False)
                with store._transaction():
                    store._event("judgment-recorded", historical)
                    store.connection.execute("INSERT INTO acceptance_approvals VALUES (?, ?)",
                                             (legacy_c["id"], json.dumps(legacy_c)))
            # Before fresh review, changed template bytes grant no credit. Earlier
            # replays restored lineage instead of exercising current judgments.
            template_file = next(path for path in (workspace / "template").rglob("*.py") if path.is_file())
            original_template = template_file.read_bytes()
            template_file.write_text(template_file.read_text(encoding="utf-8") + "\n# Changed migration review input\n", encoding="utf-8")
            scope_how("The migration-review template comment changes no executable assertion or product obligation")
            required = call("harness_plan_status", full=True)["reviewResolution"]["findingDispositionPrerequisite"]["members"]
            dispositions = [dict(id=row["id"], status="resolved", resolution="Current execution and review address this retained obligation") for row in required]
            assert store.acceptance_review_guard(refreshed_id) == "acceptance-review-required"
            call("harness_accept_review", repo=run["repo"])
            held = workspace_wire(server, "harness_accept", expected_workspace=str(workspace), by="user", statement="Yes")
            assert held["isError"] and held["refusalCode"] == "pending-judgment", held
            for row in required:
                assert row["id"] in str(held)
            prepared = call("harness_resolve_findings", findings=dispositions)
            facts = prepared["judgment"]["sourceRefs"][0]["payload"]["facts"]
            assert prepared["judgment"]["current"] and facts["reviewBinding"]["runId"] == refreshed_id
            scripted_judgment(call, prepared["judgment"], dict(memberDispositions={row["id"]: "resolved" for row in dispositions}))
            assert call("harness_resolve_findings", findings=dispositions)["dispositions"] == len(dispositions)
            status = call("harness_plan_status", full=True)
            assert status["acceptance"]["accepted"] is False
            assert status["acceptance"]["invalidReason"] == "acceptance-approval-required", status["acceptance"]
            assert "harness_accept" in status["acceptance"]["next"]
            if not semantic:
                assert status["acceptance"]["approval"] == legacy_c
                assert not call("harness_judgment", judgment_id=historical["id"])["positiveCredit"]
            decisions.append("accept")
            fresh_c = settle_decision(call, call("harness_accept", by="user", statement="Yes"))
            assert fresh_c["runId"] == refreshed_id and fresh_c["by"] == "user"
            assert call("harness_judgment", judgment_id=fresh_c["judgmentId"])["positiveCredit"]
            assert store.status()["acceptance"]["accepted"]
            assert decisions == ["align+vision-lock", "plan-execution", "accept"]
            # After completed migration/C, further template drift must stale both
            # review credit and C's canonical judgment; finding holds cannot mask it.
            retained_template = template_file.read_text(encoding="utf-8")
            template_file.write_text(retained_template + "\n# Changed after current C\n", encoding="utf-8")
            scope_how("The additional template comment preserves every approved behavior and acceptance assertion")
            assert store.acceptance_review_guard(refreshed_id) == "acceptance-review-required"
            assert not call("harness_judgment", judgment_id=fresh_c["judgmentId"])["current"]
            assert call("harness_plan_status")["acceptance"]["accepted"] is False
            if not semantic:
                from codex_harness.core.acceptance import run_wave_exits
                from codex_harness.core.spine import SpineError
                run_dir = Path(job["run"]["recordPath"]).parent.parent
                with pytest.raises(SpineError, match="Publish the current scope"):
                    run_wave_exits(store, run_dir, "first")
                template_file.write_bytes(original_template)
                scripted_worker(run["beads"]["B"]["prompt"], Path(run["repo"]), {"server.py": source})
                exits = run_wave_exits(store, run_dir, "first")
                assert exits["passed"] and all(r["passed"] for r in exits["results"])
                context = store.prepare_wave_refinement("later", run_dir)
                interface = next(f"{r['blob']}:{symbol['symbol']}" for r in context["interfaces"]
                                 for symbol in r["symbols"] if symbol["symbol"] == "response_body")
                response = dict(schema="dot-wave-refinement-response-v1", contextDigest=context["contextDigest"],
                    refinements=[dict(sketchId="SK_HTTP", nodes=[
                        node("T_NEXT", "test", ["R"], level="semantic", module="test_http.py"),
                        node("NEXT", "bead", ["T_NEXT"], ownedFiles=["http_adapter.py"], dependsOn=["B"],
                             lessons=[], proofModule="test_http.py")], interfaceRefs=[interface],
                        reason="Split adapter ownership after observing the actual producer interface",
                        replacedAssumptions=["Use the observed byte response contract"])], contradictions=[], questions=[])
                proposal = store.record_wave_refinement(store._revision(), context["contextDigest"], json.dumps(response).encode())
                assert proposal["state"] == "complete", proposal
                applied = store.apply_wave_refinement(proposal["proposalId"], store._revision())
                assert {"NEXT", "T_NEXT", "WD2"} == set(applied["editedNodeIds"])
                scope_how("The new adapter bead/test and refined wave detail re-partition the same HTTP behavior using its observed interface")
                visible = workspace_wire(server, "harness_plan_status", expected_workspace=str(workspace))
                assert {"NEXT", "T_NEXT", "WD2", "later"} <= set(visible["structuredContent"]["continuedPlanChanges"])
                assert "Continued under existing B; updated:" in visible["content"][0]["text"]
                assert len([r for r in store.connection.execute("SELECT payload_json FROM events WHERE kind='confirmation-requested'")
                            if json.loads(r[0])["gate"] == "plan-execution"]) == 1
                # Node-only consent missed harness_revise_test: real assertion
                # weakening kept every node unchanged, while admission consumed
                # different protected bytes. Existing node edits missed this seam.
                from codex_harness.core.planner_execution import execution_scope, require_execution
                from codex_harness.core.plan_store import PlanStoreError
                proof_path = template / "tests/test_http.py"
                original_source = proof_path.read_text(encoding="utf-8")
                weaker = original_source.replace("self.assertEqual(response.read(), b'shelf')", "self.assertTrue(response.read())")
                assert weaker != original_source
                roster = [dict(options["workers"][0], bead=k) for k in store.spine().ids("bead")]
                def candidate_scope():
                    revision = call("harness_compile", include_detail=True)["planRevision"]
                    return execution_scope(store, revision, roster, max_parallel=1,
                                           timeout=options["timeout"], _candidate=True)
                prior_scope = candidate_scope()
                for source, impact in [(weaker, "WHAT"),
                    (weaker.replace("def test_status_and_body", "def test_http_response"), "HOW")]:
                    nodes_before = store.spine().to_dict()
                    before_source = proof_path.read_bytes().decode("utf-8")
                    revision = call("harness_compile", include_detail=True)["planRevision"]
                    call("harness_revise_test", node_id="T_NEXT", path="tests/test_http.py",
                         expected_manifest_revision=revision,
                         expected_file_sha256=hashlib.sha256(proof_path.read_bytes()).hexdigest(),
                         source=source, reason="Scripted planner revises the linked HTTP acceptance check")
                    assert nodes_before == store.spine().to_dict()
                    scope = candidate_scope()
                    with pytest.raises(ValueError, match="worker-execution-inputs-stale"):
                        require_execution(store, prior_scope)
                    with pytest.raises(PlanStoreError, match="pending-judgment") as held:
                        require_execution(store, scope)
                    question = held.value.approval["scopeJudgment"]
                    facts = question["sourceRefs"][0]["payload"]["facts"]
                    assert facts["before"]["files"]["tests/test_http.py"]["content"] == before_source
                    assert facts["after"]["files"]["tests/test_http.py"]["content"] == source
                    scripted_judgment(call, question, dict(impact=impact), rationale=(
                        "Nonempty output replaces the accepted exact shelf response; acceptance is weakened" if impact == "WHAT"
                        else "Only the test method name changes; the approved assertions and data flow are identical"))
                    if impact == "WHAT":
                        with pytest.raises(PlanStoreError, match="plan-b-consent-required"):
                            require_execution(store, scope)
                        approved_options = dict(options, workers=roster,
                            expected_revision=call("harness_compile", include_detail=True)["planRevision"])
                        card = call("harness_prepare_chat_confirmation", gate="plan-execution", execution=approved_options)
                        assert "tests/test_http.py" in str(card["diff"]) and "self.assertTrue(response.read())" in str(card["diff"])
                        assert call("harness_prepare_chat_confirmation", gate="plan-execution", execution=approved_options)["request_id"] == card["request_id"]
                        yes(card)
                    else:
                        assert {"T_NEXT", "tests/test_http.py"} <= set(call("harness_plan_status")["continuedPlanChanges"])
                    require_execution(store, scope)
                    assert len([r for r in store.connection.execute("SELECT payload_json FROM events WHERE kind='confirmation-requested'")
                                if json.loads(r[0])["gate"] == "plan-execution"]) == 2
                    prior_scope = scope
                assert decisions == ["align+vision-lock", "plan-execution", "accept", "plan-execution"]
            else:
                # Rank-based consent omitted design instructions delivered in
                # SCENARIOS.json. Frozen requirements and story-only edits missed
                # both third-party disclosure and ordinary technical refactors.
                from codex_harness.core.approvals import scope_assessment
                previous = scope_assessment(store)
                for node_id, text, impact, cards in [
                    ("G1", "export locally; split the exporter into two internal modules", "HOW", 1),
                    ("G1", "upload exports to a third party", "WHAT", 2),
                    ("EXPORT_STORY", "upload exports to a third party", "WHAT", 3),
                ]:
                    assert call("harness_judgment", judgment_id=previous["id"])["current"]
                    unchanged = {k: store.spine().node(k) for k in store.spine().ids() if k != node_id}
                    edited = store.spine().node(node_id)
                    edited["text"] = text
                    before_launches = len(provider_launches)
                    call("harness_upsert_node", node=edited)
                    assert unchanged == {k: store.spine().node(k) for k in unchanged}
                    options["expected_revision"] = call("harness_compile", include_detail=True)["planRevision"]
                    assert not call("harness_judgment", judgment_id=previous["id"])["current"]
                    held = workspace_wire(server, "harness_readiness", expected_workspace=str(workspace),
                                          reviewers=selected_reviewers, beads=["B"])
                    assert held["refusalCode"] == "pending-judgment" and len(provider_launches) == before_launches
                    previous = held["structuredContent"]["scopeJudgment"]
                    scripted_judgment(call, previous, dict(impact=impact), rationale=(
                        "Only internal module boundaries change; local export obligations remain identical" if impact == "HOW"
                        else "Local export now discloses data to a third party despite unchanged generic feature/requirement text"))
                    options["expected_revision"] = call("harness_compile", include_detail=True)["planRevision"]
                    if impact == "WHAT":
                        held = workspace_wire(server, "harness_readiness", expected_workspace=str(workspace),
                                              reviewers=selected_reviewers, beads=["B"])
                        assert held["refusalCode"] == "plan-b-consent-required" and len(provider_launches) == before_launches
                        card = call("harness_prepare_chat_confirmation", gate="plan-execution", execution=options)
                        assert f"scope.nodes.{node_id}.text" in {d["field"] for d in card["diff"]}
                        assert call("harness_prepare_chat_confirmation", gate="plan-execution", execution=options)["request_id"] == card["request_id"]
                        yes(card)
                    else:
                        assert node_id in call("harness_plan_status")["continuedPlanChanges"]
                    call("harness_readiness", reviewers=selected_reviewers, beads=["B"])
                    assert len(provider_launches) == before_launches + len(selected_reviewers)
                    assert all(text in launch.stdin_text for launch in provider_launches[before_launches:])
                    assert len([r for r in store.connection.execute("SELECT payload_json FROM events WHERE kind='confirmation-requested'")
                                if json.loads(r[0])["gate"] == "plan-execution"]) == cards
                assert decisions == ["align+vision-lock", "plan-execution", "accept", "plan-execution", "plan-execution"]
                assert not store.status()["acceptance"]["accepted"]  # New third-party work is unbuilt.
            print(
                "golden ShelfLife: malformed two-fix/steer; negative minor/conflict pending; actual-delta reuse; importer/SQLite/HTTP RED->GREEN; fresh C accepted; template drift held"
                if semantic
                else "golden http: original A/B/C; file WHAT approved; HOW reported; HTTP + concurrency RED->GREEN; template drift held"
            )
    finally:
        server.close()



def served_product():
    """Worker reply: a real server and UI write, with disk-backed API readback."""
    return """import os, json
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, HTTPServer
class App(SimpleHTTPRequestHandler):
    def do_DELETE(self):
        Path('state.json').unlink(missing_ok=True)
        self.send_response(204); self.end_headers()
    def do_GET(self):
        dbpath = Path('scenario.sqlite3').resolve()
        import sqlite3
        with sqlite3.connect(dbpath) as db: db.execute('create table if not exists preview (value text)')
        if self.path in ('/empty', '/reset', '/cached'):
            self.send_response({'/empty': 204, '/reset': 205, '/cached': 304}[self.path]); self.end_headers(); return
        if self.path == '/disconnect':
            self.connection.close(); return
        if self.path in ('/truncated200', '/truncated409'):
            self.send_response(200 if self.path == '/truncated200' else 409); self.send_header('Content-Length', '64'); self.end_headers()
            self.wfile.write(b'{'); self.wfile.flush(); self.close_connection = True; return
        if self.path in ('/overconsume', '/broken'):
            self.send_response(409 if self.path == '/overconsume' else 500); self.end_headers(); self.wfile.write(b'{}'); return
        if self.path == '/preview-db':
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(dict(path=str(dbpath), exists=dbpath.is_file())).encode()); return
        if self.path in ('/api/state', '/seed'):
            file = Path('seed.json' if self.path == '/seed' else 'state.json')
            data = file.read_bytes() if file.exists() else (b'{"note":"Missing seed"}' if self.path == '/seed' else b'{"saved":false}')
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)
        else:
            super().do_GET()
    def do_POST(self):
        if self.path == '/submit':
            from urllib.parse import parse_qs
            Path('submission.json').write_text(json.dumps(parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())))
            self.send_response(302); self.send_header('Location', '/index.html#posted'); self.end_headers(); return
        assert self.path in ('/api/state', '/seed')
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        Path('seed.json' if self.path == '/seed' else 'state.json').write_text(json.dumps(dict(saved=True, note=payload['note'])))
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
HTTPServer(('127.0.0.1', int(os.environ['SELDON_PORT'])), App).serve_forever()
"""


def provider_host(mode, workspace, surface, job_id=None):
    """Install external-provider doubles in each ordinary process owner.

    Preserve the detached controller, Windows Job monitor and ordinary dispatch;
    only replace provider execution with deterministic file-writing workers/reviewers.
    """
    from tests.python.core.acceptance_recovery_support import protocol

    from codex_harness.core import (
        audit_process,
        dispatch,
        mcp_planner,
        planner_jobs,
        planner_recovery,
        review_provider, roundtable,
    )

    workspace = Path(workspace)
    provider_patch = pytest.MonkeyPatch()
    judge_executions(workspace, provider_patch, pending_execution=surface == "web" or surface == "cli" and (workspace / "contracts-attempted").exists(), contracts_gap=lambda: (
        ["Worker verifier crashes before exercising the requested command behavior"]
        if surface == "cli" and not (workspace / "valid-contracts-attempted").exists() else []))
    filename = "index.html" if surface == "web" else "hashslip.py"
    actual_popen = subprocess.Popen
    actual_audit = audit_process.run_audit_process
    actual_review = roundtable._run
    actual_run = dispatch.run_plan
    actual_write = dispatch._write_record

    def publish(path, record):
        actual_write(path, record)
        if (surface == "web" and path.name == "retained-candidate.json" and record["bead"] == "B"):
            # Hold the real producer after publication so MCP must consume this
            # transition, rather than usually missing it between status polls.
            end = time.monotonic() + 10
            while not (workspace / "validation-observed").exists() and time.monotonic() < end:
                time.sleep(0.05)

    dispatch._write_record = publish

    def command(role, project, job=None):
        return [sys.executable, "-B", str(Path(__file__).resolve()), role, project, surface] + (
            [job] if job else []
        )

    def launch(argv, **options):
        if "codex_harness.core.planner_jobs" in argv:
            argv = command("controller", argv[argv.index("--workspace") + 1], argv[-1])
        return actual_popen(argv, **options)

    def controller_payload(argv, **options):
        return actual_audit(command("worker", str(workspace), job_id), **options)

    def write_shelflife(root, phase):
        payload = json.loads((workspace / "scripted-provider.json").read_text(encoding="utf-8"))
        scripted_worker(packet(root, "C" if phase == "contracts" else "B"), root, payload[phase])

    def packet(root, bead):
        record = json.loads((root.parent / "evidence/run.json").read_text(encoding="utf-8"))
        request = Path(record["beads"][bead]["evidenceDir"]) / "attempt/request.json"
        return json.loads(request.read_text(encoding="utf-8"))["prompt"]

    def contracts(root):
        if surface == "shelflife":
            return write_shelflife(root, "contracts")
        first = surface == "cli" and not (workspace / "contracts-attempted").exists()
        if first:
            (workspace / "contracts-attempted").write_text("attempted", encoding="utf-8")
        else:
            (workspace / "valid-contracts-attempted").write_text("attempted", encoding="utf-8")
        scripted_worker(packet(root, "C"), root, {filename:
            "<!doctype html><title>HashSlip</title><h1>HashSlip</h1>"
            if surface == "web"
            else "raise RuntimeError('receipt startup failed')"
            if first
            else "print('Receipt pending')\n"})

    def build(root):
        if surface == "shelflife":
            return write_shelflife(root, "build")
        if surface == "cli":
            scripted_worker(packet(root, "B"), root, {"checks/verify_hashslip.py":
                "# Exact binary receipt; catches missing CLI/data loss; scenarios alone had no worker test.\n"
                "import subprocess, sys\nfrom pathlib import Path\nfrom receipt_helper import payload, check_receipt\n"
                "command = Path(__file__).resolve().parents[1] / 'hashslip.py'\n"
                "data = payload()\n"
                "import hashslip  # Tests-first: the product module is absent on baseline.\n"
                "run = subprocess.run([sys.executable, '-B', str(command)], input=data, capture_output=True, timeout=5)\n"
                "check_receipt(data, run)\n",
                "checks/receipt_helper.py":
                "import hashlib, json\nfrom pathlib import Path\n"
                "def payload():\n    assert sorted(p.name for p in Path(__file__).parent.glob('*.json')) == ['input.json'], 'retired fixture still present'\n"
                "    return bytes(json.loads((Path(__file__).parent / 'input.json').read_text()))\n"
                "def check_receipt(data, run):\n    assert run.returncode == 0 and not run.stderr, run.stderr\n"
                "    assert json.loads(run.stdout) == dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), label=None)\n",
                "checks/input.json": "[0, 255, 65]\n", "checks/retired.json": None})
            if not (workspace / "contracts-attempted").exists():
                (workspace / "contracts-attempted").write_text("attempted", encoding="utf-8")
                return scripted_worker(packet(root, "B"), root, {filename: "raise RuntimeError('receipt startup failed')\n"})
            (workspace / "valid-contracts-attempted").write_text("attempted", encoding="utf-8")
        scripted_worker(packet(root, "B"), root, {filename:
            (ROOT / "tests/fixtures/efficiency" / filename).read_text(encoding="utf-8")})
        if surface == "web":
            with (root / filename).open("a", encoding="utf-8") as out:
                # A normal larger document must not overflow adapter stdout when
                # request evidence captures its body; the earlier tiny page hid it.
                out.write("<!--" + "page content " * 2048 + "-->")
                out.write("<style>h1{background:linear-gradient(white,#fff)}</style>"
                    "<input id='note' aria-label='Note'><button id='save' onclick=\"fetch('/api/state',{method:'POST',body:JSON.stringify({note:document.querySelector('#note').value})}).then(()=>this.textContent='Saved')\">Save</button>"
                    "<button id='overconsume' onclick=\"fetch('/overconsume').then(()=>this.textContent='Rejected')\">Overconsume</button>"
                    "<button id='happy' onclick=\"fetch('/overconsume').then(()=>this.textContent='Happy')\">Happy path</button>"
                    "<button id='broken' onclick=\"fetch('/broken').then(()=>this.textContent='Broken')\">Break</button>"
                    "<button id='delete' onclick=\"fetch('/api/state',{method:'DELETE'}).then(()=>this.textContent='Deleted')\">Delete</button>"
                    "<button id='empty' onclick=\"location.href='/empty'\">Empty navigation</button>"
                    "<button id='cached' onclick=\"fetch('/cached').then(()=>this.textContent='Cached')\">Read cache</button>"
                    "<button id='disconnect' onclick=\"fetch('/disconnect').catch(()=>this.textContent='Disconnected')\">Disconnect</button>"
                    "<button id='truncated200' onclick=\"fetch('/truncated200').then(r=>r.text()).catch(()=>this.textContent='Truncated 200')\">Incomplete success</button>"
                    "<button id='truncated409' onclick=\"fetch('/truncated409').then(r=>r.text()).catch(()=>this.textContent='Truncated 409')\">Incomplete conflict</button>"
                    "<button id='read-seed' onclick=\"fetch('/seed').then(r=>r.json()).then(d=>document.querySelector('#seeded').textContent=d.note)\">Read seeded note</button><p id='seeded'></p>"
                    "<form method='post' action='/submit'><input type='hidden' name='note' value='Posted'><button id='post-redirect'>Submit form</button></form>"
                    "<button id='failed-happy' onclick=\"fetch('/broken').then(()=>this.textContent='Failed happy')\">Another happy path</button>")
            scripted_worker(packet(root, "B"), root, {"serve.py": served_product(),
                "serve_wrapper.py": "import subprocess,sys; subprocess.Popen([sys.executable,'-B','serve.py']).wait()\n",
                "seed_check.py": "import csv,json,sys; from pathlib import Path; assert list(csv.DictReader(Path(sys.argv[1]).open())) == [{'name':'Tea','quantity':'2'}]; print(json.loads(Path('seed.json').read_text())['note'])\n"})

    if surface in {"shelflife", "parallel-http"}:
        from codex_harness.core.provider_launch import ProviderNormalLaunch

        class ScriptedWorker:
            def provider_normal_command(self, **arguments):
                if (surface == "parallel-http" and "Your job: implement bead B." in arguments["prompt"]
                        and json.loads((workspace / "scripted-provider.json").read_text())["deadlinePair"]):
                    until = time.monotonic() + 30
                    while not (workspace / ".codex-harness/deadline-child").exists() and time.monotonic() < until:
                        time.sleep(.05)
                    assert (workspace / ".codex-harness/deadline-child").exists()
                    time.sleep(4)  # B's independently approved deadline begins after A's.
                return ProviderNormalLaunch(cli="codex", command=tuple(command("reply", str(workspace))),
                    protections=("Scripted local provider; no actual provider acceptance",), stdin_text=arguments["prompt"])

        dispatch.get_adapter = lambda _: ScriptedWorker()

    def workers(plan, **options):
        if surface == "parallel-http":
            return actual_run(plan, **options)
        workers = {"B": build} if surface == "cli" else {"C": contracts, "B": build}
        return actual_run(plan, **options, **({} if surface == "shelflife" else dict(fake_workers=workers)))

    def review(launch, *args, **options):
        if "## Read-only candidate evidence" in launch.stdin_text:
            tier = "same-model" if surface == "cli" else "cross-provider"
            assert f"## Review tier: {tier}" in launch.stdin_text
            assert "catalog: unverified" in launch.stdin_text
            if mode == "server" and surface == "cli" and not (workspace / ".codex-harness" / "review-crash-started").exists():
                # A real provider wait survives a lost client until its process owner
                # is killed. Recovery must not reuse that abandoned review's evidence.
                (workspace / ".codex-harness" / "review-crash-started").write_text("started", encoding="utf-8")
                actual_audit([sys.executable, "-B", "-c", "import time; time.sleep(600)"],
                             cwd=workspace, timeout=900)
        text = "[]"
        if (
            mode == "server"
            and surface == "cli"
            and not (workspace / "review-output-sent").exists()
        ):
            (workspace / "review-output-sent").write_text("sent")
            payload = json.loads((ROOT / "tests/fixtures/efficiency/run26.json").read_text("utf-8"))
            text = json.dumps([payload["headingFinding"]])
        response = tuple(
            value.replace(
                "source-only", "gpt-6.1-sol" if launch.cli == "codex" else "claude-sonnet-5-5"
            ).replace("Some(High)", "Some(Medium)" if surface == "web" else "Some(Medium)")
            if isinstance(value, str)
            else value
            for value in protocol(launch.cli, text=scripted_review_reply(launch, json.loads(text),
                verdict="positive", rationale="Independent plan assessment: required behaviors are covered; trace note is advisory"))
        )

        # One real review launch protects the shared owner; other unchanged
        # semantic replies reuse their existing double instead of repeating it.
        observed_review = workspace / ".codex-harness/review-job-observed"
        if observed_review.exists():
            return response
        from dataclasses import replace
        body = "import sys; sys.stdout.buffer.write(" + repr(response[1].encode("utf-8")) + "); sys.stderr.buffer.write(" + repr(response[2].encode("utf-8")) + ")"
        actual = actual_review(replace(launch, command=(sys.executable, "-B", "-c", body)), *args, **options)
        observed_review.touch()
        return actual

    subprocess.Popen = launch
    roundtable._run = review_provider._run = review
    dispatch.run_plan = workers
    if mode == "reply":
        prompt = sys.stdin.read()
        phase = "contracts" if prompt.startswith("semantic-v1 contracts;") else "build"
        payload = json.loads((workspace / "scripted-provider.json").read_text(encoding="utf-8"))
        if surface == "parallel-http":
            bead = next(b for b in ("A", "B") if f"Your job: implement bead {b}." in prompt)
            if payload["deadlinePair"]:
                if bead == "A":
                    import signal
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    child = actual_popen([sys.executable, "-B", "-c",
                        "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(600)"])
                    (workspace / ".codex-harness/deadline-child").write_text(str(child.pid))
                    while True:
                        print("thinking despite deadline", file=sys.stderr, flush=True)
                        time.sleep(.1)
                (workspace / ".codex-harness/sibling-started").touch()
                while not (workspace / ".codex-harness/sibling-release").exists():
                    print("sibling active", file=sys.stderr, flush=True)
                    time.sleep(.1)
            if payload["cancelBead"] and bead == "B":
                (workspace / ".codex-harness/cancel-provider").touch()
                while True:
                    print("awaiting cancellation", file=sys.stderr, flush=True)
                    time.sleep(.1)
            if bead in payload["fail"] and bead != "B":
                raise SystemExit(7)
            sources = payload["products"][bead]
            if bead in payload["fail"]:
                sources["unexpected.txt"] = "Real product-folder ownership violation\n"
        else:
            sources = payload[phase]
        live = workspace / ".codex-harness/live-provider.json"
        slow_flush = surface == "shelflife" and phase == "contracts" and not live.exists()
        if slow_flush:
            # Output-only and CPU-only reasoning precede the delayed final flush;
            # neither activity observation is a stream-completion witness.
            start = time.monotonic()
            while time.monotonic() - start < 3:
                print("thinking", file=sys.stderr, flush=True)
                time.sleep(.2)
            output_seconds = time.monotonic() - start
            start = time.monotonic()
            while time.monotonic() - start < 3:
                hashlib.sha256(b"reasoning" * 1024).digest()
            live.write_text(json.dumps(dict(outputSeconds=output_seconds,
                                           cpuSeconds=time.monotonic() - start)))
        if surface == "shelflife" and phase == "build":
            if not (workspace / ".codex-harness/truncated-provider").exists():
                (workspace / ".codex-harness/truncated-provider").touch()
                print('{"type": "turn.started"}\n{"type": "turn.com', end="", flush=True)
                return  # Exit normally without ever supplying the missing final bytes.
            if "incomplete: declared verify_http.py missing from candidate" not in prompt:
                sources["verify_http.py"] = None
        scripted_worker(prompt, Path.cwd(), sources)
        # r11: the actual provider child consumes dispatch's supported output env.
        # Earlier worker doubles never wrote tool output outside the product.
        artifact = Path(os.environ["PLAYWRIGHT_MCP_OUTPUT_DIR"]) / "browser-console.log"
        artifact.write_text("Scripted browser tool output\n", encoding="utf-8")
        assert not artifact.is_relative_to(Path.cwd())
        _, output, stderr = protocol("codex", text="Scripted worker completed the requested files")
        if slow_flush:
            # Real stdout file, partial JSON and no CPU/output activity beyond the
            # former 120-second window. Restoring any shorter idle kill must fail
            # this same journey, rather than escape an accelerated three-second twin.
            sys.stdout.write(output[:-5])
            sys.stdout.flush()
            flush_started = time.monotonic()
            time.sleep(125)
            sys.stdout.write(output[-5:] + "\n")
            sys.stdout.flush()
            observed = json.loads(live.read_text())
            live.write_text(json.dumps(dict(observed, flushSeconds=time.monotonic() - flush_started)))
        else:
            print(output)
        print(stderr.replace("source-only", "gpt-6.1-sol").replace("Some(High)", "Some(Medium)"), file=sys.stderr)
        return
    if mode == "server":
        original_dispatch = mcp_planner.PlannerServer._dispatch

        def noisy_dispatch(self, name, arguments, **options):
            if name == "harness_plan_start":
                print("tool diagnostic: café 中文")
                os.write(1, "raw diagnostic: 中文\n".encode())
            return original_dispatch(self, name, arguments, **options)

        mcp_planner.PlannerServer._dispatch = noisy_dispatch
        sys.argv = [sys.argv[0], "--workspace", str(workspace)]
        runpy.run_module("codex_harness.core.seldon_mcp", run_name="__main__")
    elif mode == "controller":
        audit_process.run_audit_process = controller_payload
        raise SystemExit(planner_recovery.monitor(workspace, job_id))
    else:
        result = planner_jobs.execute_worker_job(workspace, job_id)
        raise SystemExit(1 if result["status"] == "failed" else 0)


@pytest.fixture
def http_journeys(tmp_path):
    """Overlap independent HTTP journeys; require their assertions and owned cleanup.

    Separate interpreters isolate provider monkeypatches, workspaces and ports.
    A failed child or unfinished process tree must fail the enclosing CLI case.
    """
    from concurrent.futures import ThreadPoolExecutor
    from codex_harness.core.audit_process import run_audit_process

    stop = threading.Event()

    def run_http(name, semantic, ready):
        started = time.monotonic()
        workspace = tmp_path.parent / (tmp_path.name + "-" + name)
        try:
            completed = run_audit_process(
                [sys.executable, "-B", str(Path(__file__).resolve()), "http-golden",
                 str(workspace), str(int(semantic))],
                cwd=ROOT, timeout=600, stop_when=stop.is_set,
                on_launch=lambda _: ready.set())
            if completed.returncode:
                workspace.mkdir(parents=True, exist_ok=True)
                failure_log = workspace / "journey-failure.log"
                output = "=== stdout ===\n" + completed.stdout + "\n=== stderr ===\n" + completed.stderr
                failure_log.write_text(output, encoding="utf-8")
                tail = "\n".join(output.splitlines()[-60:])
                assert completed.returncode == 0, f"{name}: full output: {failure_log}\nLast 60 lines:\n{tail}"
            assert completed.audit_control["quiescenceVerified"]
            assert completed.audit_control["activeProcessesAfterTeardown"] == 0
            print(completed.stdout, file=sys.stderr, end="", flush=True)
            print(f"golden {name}: seconds={time.monotonic() - started:.2f}", file=sys.stderr, flush=True)
        finally:
            ready.set()  # Also wake setup if launch failed; future.result retains that failure.

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = []
        try:
            for name, semantic in (("http-journey", False), ("shelflife-scenarios", True)):
                ready = threading.Event()
                future = pool.submit(run_http, name, semantic, ready)
                pending.append(future)
                assert ready.wait(60), f"HTTP journey did not start: {name}"
                if future.done():
                    future.result()
            yield
            for future in pending:
                future.result()
        finally:
            stop.set()


@pytest.mark.parametrize("surface", ["cli", "web"])
def test_golden_path(tmp_path, request, surface):
    """Protect idea→root product→accepted with three plain user decisions.

    Catch quota removal dropping launch authority, late reviewer questions and reapproval after recovery,
    and changed limits launching without a diff; plan-review-only tests miss delivery.
    Replay run26/UX6 plans, reviewer tuples, schema refusals and browser actions.
    Catch incomplete preflight, invented TRACE nodes, illegal phase guidance and a late
    visual judgment staling scenarios; earlier authored-perfect journeys missed these seams.
    Replay the JSONKeys farm B refusal with installed Codex+Claude but two authorized Sol
    reviewers; prove the same-provider tier survives B, acceptance packet and final C.
    No model catalog is supplied: discovery-unavailable must still reach C with three
    decisions and catalog: unverified in B and the real acceptance packet. Catalog-backed
    fixtures missed the farm deadlock before B; exact proposed tuples must reach launches.
    Scripted provider results are suite evidence only, never actual-provider acceptance.
    Replay UX-5's captured Unicode upsert and survive an uncaught malformed tool
    request plus tool stdout noise; direct handle_line tests miss pipe death.
    Replay ShelfLife: lose an exited owner's receipt, then hang a real descendant
    mid-Build. Both attempts must be quarantined and readmitted under the same B;
    ordinary successful fixtures missed UNKNOWN holding delivery forever.
    Hold the Build observation publication before finalization: pending evidence
    and status must agree; ordinary polling usually missed that transient window.
    Keep unavailable normal Chromium pending with its launch diagnostic; earlier
    fallback hid startup failure and supplied crash-prone single-process evidence.
    Catch unlimited third worker retries and interrupted acceptance/review ownership;
    earlier fault twins retried twice but never restarted acceptance/review owners.
    The HTTP journey also protects independent and joint bead recovery counts and
    stale product capture: serial-only faults missed pooled counts; terminal-only
    freshness checks missed scenarios executing against changed product bytes.
    Replay ShelfLife's dependency-free build with worker tests+code through C;
    its new assertion helper and fixture must reach the RED baseline as one bundle.
    Refuse multiple builds under DV2, then replan to contracts→single build→C.
    ShelfLife R6: same-subject test/command and catalog repairs retain B through
    review, execution and C; frozen-plan journeys missed revision-bound churn.
    An unapproved worker model instead yields one reusable subject-diff card.
    ShelfLife R12: verifier-only publications retain one candidate through status
    and C; a missing owner keeps historical acceptance reads pending, never crashing.
    """
    if surface == "cli":
        fixture_ownership_golden(tmp_path / "fixture-ownership")
        request.getfixturevalue("http_journeys")
        cold_start_golden(tmp_path.parent / (tmp_path.name + "-cold-start"))
        planning_intent_golden(tmp_path.parent / (tmp_path.name + "-planning-intent"))
        judgment_contract_golden(tmp_path.parent / (tmp_path.name + "-judgment-contract"))
        execution_judgment_golden(tmp_path.parent / (tmp_path.name + "-execution-judgment"))
        trace_contract_golden(tmp_path.parent / (tmp_path.name + "-trace-contract"))
    if surface == "web":
        from codex_harness.core.semantic_runner import run as observe
        timeout_product = tmp_path / "timeout-product"
        timeout_product.mkdir()
        api = dict(target="service", method="GET", path="/", statusEquals=200)
        timeout_spec = dict(schema="seldon/scenarios-v1", scenarios=[dict(id="timeout", requirementRefs=["R1"],
            contractRefs=[], setup={}, actions=[dict(id="api", http=api)], observe=[dict(action="api")], verdict="all")])
        (timeout_product / "wait.py").write_text("import time; time.sleep(60)\n", encoding="utf-8")
        before = time.monotonic()
        timed = observe(timeout_spec, timeout_product, {"service": dict(server=dict(
            entry="wait.py", runtime=sys.executable, argv=[], readyPath="/", readyWithin=.25))}, "validation",
            web_config=dict(chromium="missing-browser", playwright="missing-playwright"))[0]
        assert timed["state"] == "ERROR" and "readiness timeout" in timed["reason"], timed
        assert time.monotonic() - before < 15
        assert timed["cleanup"]["quiescenceVerified"] and timed["cleanup"]["activeProcessesAfterTeardown"] == 0
        assert timed["actions"]["api"]["server"]["stopped"]
        # CHROME1: a real executable that cannot speak Chromium's protocol must
        # produce bounded, unverified infrastructure evidence, never a product FAIL.
        # The healthy browser journeys never exercised normal launch rejection.
        from tests.python.core.semantic_runner_support import WEB_PAGE, WEB_TARGET, web_document
        (timeout_product / "index.html").write_text(WEB_PAGE, encoding="utf-8")
        unavailable = observe(web_document(split=True), timeout_product, WEB_TARGET, "validation",
                              web_config=dict(chromium=sys.executable))
        assert len(unavailable) == 4 and all(r["state"] == "pending-judgment" for r in unavailable), unavailable
        assert all(r["unperformed"] and r["evidenceTier"] == "browser: UNVERIFIED" for r in unavailable), unavailable
        assert all("browser unavailable" in r["reason"] and r["launchDiagnostic"] for r in unavailable), unavailable
        assert all(r["cleanup"]["quiescenceVerified"] and r["cleanup"]["activeProcessesAfterTeardown"] == 0 for r in unavailable)
        request.getfixturevalue("web_browser_ready")

    started = time.time()
    (tmp_path / "AGENTS.md").write_text(
        "Use gpt-6.1-sol at medium effort for all helper assignments, including reviews.\n",
        encoding="utf-8",
    )
    replies = queue.Queue()
    calls = decisions = 0
    counts = dict(happy=0, probes=0, polls=0)
    probing = False
    revision = None
    tools = tmp_path / "tools"
    tools.mkdir()
    codex = tools / ("codex.exe" if os.name == "nt" else "codex")
    captured_run = json.loads(
        (
            ROOT / "tests/fixtures/efficiency" / ("ux6.json" if surface == "web" else "run26.json")
        ).read_text("utf-8")
    )
    if surface == "web":
        # Regression: served UI/API plans were unsupported. Offline journeys never
        # exercised server launch, HTTP persistence, readiness or teardown.
        vision = captured_run["nodes"][0]["fields"]
        vision["targets"]["served"] = dict(kind="web", entry="index.html",
            server=dict(entry="serve_wrapper.py", runtime=sys.executable, argv=[], readyPath="/", readyWithin=5))
        journey = vision["scenarios"]["scenarios"][0]["actions"][0]["web"]
        journey["target"] = "served"
        # Review P2: preview must preserve cross-target API/CLI prerequisites and
        # their persistence checks; the former same-target fixture missed filtering.
        vision["targets"]["seed-service"] = dict(vision["targets"]["served"])
        # r9: a deliberate negative path was counted as a console error. Existing
        # served journeys made only successful requests and never created preview DBs.
        scenario = vision["scenarios"]["scenarios"][0]
        scenario["setup"] = dict(dirs=["{data}/fixtures"], files=[dict(path="{data}/fixtures/items.csv", utf8="name,quantity\nTea,2\n")])
        scenario["actions"].insert(0, dict(id="seed", http=dict(target="seed-service", method="POST", path="/seed", json=dict(note="Seeded"), statusEquals=200)))
        scenario["actions"].insert(1, dict(id="seed-check", process=dict(entry="seed_check.py", runtime=sys.executable, argv=[{"input": "{data}/fixtures/items.csv"}])))
        scenario["observe"].extend([dict(action="seed"), dict(action="seed-check", exitEquals=0, stdoutEquals="Seeded\n"), dict(path="seed.json", fileJsonEquals=dict(saved=True, note="Seeded"))])
        journey["steps"][1:1] = [dict(op="click", locator=dict(selector="#read-seed")), dict(op="assertVisible", locator=dict(text="Seeded"))]
        # R3: a native form redirect completes without an exposed response body.
        # Earlier empty-body/truncation journeys never exercised browser redirects.
        journey["steps"][3:3] = [dict(id="formpost", op="click", locator=dict(selector="#post-redirect"), expectedErrors=[dict(method="POST", path="/submit", statusEquals=302)]), dict(op="assertFragment", fragment="#posted")]
        scenario["observe"].append(dict(path="submission.json", fileJsonEquals=dict(note=["Posted"])))
        scenario["actions"].append(dict(id="api", http=dict(target="served", method="GET", path="/api/state", statusEquals=200, jsonEquals=dict(saved=True, note="Tea"))))
        scenario["observe"].append(dict(action="api"))
        scenario["observe"].append(dict(path="state.json", fileJsonEquals=dict(saved=True, note="Tea")))
        journey["steps"].extend([
            dict(op="fill", locator=dict(selector="#note"), value="Tea"),
            dict(op="click", locator=dict(selector="#save")),
            dict(op="assertVisible", locator=dict(text="Saved")),
            dict(op="request", request=dict(method="GET", path="/api/state", statusEquals=200, jsonEquals=dict(saved=True, note="Tea"))),
            dict(id="overconsume", op="click", locator=dict(selector="#overconsume"), expectedErrors=[dict(method="GET", path="/overconsume", statusEquals=409)]),
            dict(op="assertVisible", locator=dict(text="Rejected")),
            dict(op="request", request=dict(method="GET", path="/preview-db", statusEquals=200)),
            # r11: null-body HTTP crashed before any judgment; status-only coverage
            # also lost successful declarations and their actual abort diagnostics.
            dict(id="delete", op="click", locator=dict(selector="#delete"), expectedErrors=[dict(method="DELETE", path="/api/state", statusEquals=204)]),
            dict(op="assertVisible", locator=dict(text="Deleted")),
            dict(op="request", request=dict(method="DELETE", path="/api/state", statusEquals=204)),
            dict(op="request", request=dict(method="HEAD", path="/index.html", statusEquals=200)),
            dict(op="request", request=dict(method="GET", path="/reset", statusEquals=205)),
            dict(op="request", request=dict(method="GET", path="/cached", statusEquals=304)),
            dict(id="empty", op="click", locator=dict(selector="#empty"), expectedErrors=[dict(method="GET", path="/empty", statusEquals=204)]),
            dict(op="assertVisible", locator=dict(text="Deleted")),
            dict(id="cached", op="click", locator=dict(selector="#cached"), expectedErrors=[dict(method="GET", path="/cached", statusEquals=304)]),
            dict(op="assertVisible", locator=dict(text="Cached")),
        ])
        # Read back persistence after deletion, rather than asking the product to
        # change its DELETE contract to fit the observer.
        scenario["actions"][-1]["http"]["jsonEquals"] = dict(saved=False)
        scenario["observe"][-1] = dict(path="state.json", fileAbsent=True)
        for n in captured_run["nodes"]:
            if n["kind"] == "bead":
                n["fields"]["ownedFiles"].extend(["serve.py", "serve_wrapper.py", "seed_check.py"])
    shutil.copy2(sys.executable, codex)
    shutil.copy2(sys.executable, tools / ("claude.exe" if os.name == "nt" else "claude"))
    # Keep OS/git/node tools while excluding the host's provider installations/bundles.
    paths = [str(tools), str(Path(shutil.which("git")).parent)]
    if os.name == "nt":
        paths += [
            os.path.join(os.environ["SystemRoot"], "System32"),
            os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0"),
        ]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
        "PATH": os.pathsep.join(paths),
        "LOCALAPPDATA": str(tools),
        "PYTHONIOENCODING": "cp1252",
    }
    if surface == "web":
        from codex_harness.core.semantic_runner import web_toolchain

        env["SELDON_WEB_CHROMIUM"] = web_toolchain(None, False)["chromium"]["path"]
    with (tmp_path / "server.log").open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "server", str(tmp_path), surface],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            env=env,
        )

        def read(stream, sink):
            for line in stream:
                sink.put(json.loads(line))
            sink.put(None)

        reader = threading.Thread(target=read, args=(process.stdout, replies), daemon=True)
        reader.start()

        rpc_id = 0

        def rpc(method, params, *, timeout=90, notification=False):
            nonlocal rpc_id
            rpc_id += 1
            expected_id = rpc_id
            end = time.time() + timeout

            def send():
                try:
                    message = dict(jsonrpc="2.0", method=method, params=params)
                    if not notification:
                        message["id"] = expected_id
                    process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                    process.stdin.flush()
                    if notification:
                        replies.put(expected_id)
                except (OSError, ValueError) as error:
                    replies.put(error)

            # A server that stops reading must not trap the test in a pipe write.
            threading.Thread(target=send, daemon=True).start()
            while time.time() < end:
                try:
                    reply = replies.get(timeout=min(0.25, max(0.001, end - time.time())))
                except queue.Empty:
                    continue
                if notification and reply == expected_id:
                    return None
                assert not isinstance(reply, Exception), f"Golden MCP transport failed: {reply}"
                assert reply is not None, (tmp_path / "server.log").read_text()
                if "id" in reply:
                    assert reply["id"] == expected_id, reply
                    assert "error" not in reply, reply
                    return reply["result"]
            pytest.fail(f"Golden MCP deadline after {timeout}s: {params.get('name', method)}; "
                        f"pid={process.pid}; inspect {tmp_path / 'server.log'}")

        def call(tool_name, *, allow_error=False, _probe=False, **arguments):
            nonlocal calls, revision
            calls += 1
            category = (
                "polls"
                if tool_name in {"harness_worker_status", "harness_accept_status"}
                else "probes"
                if probing or _probe or allow_error
                else "happy"
            )
            counts[category] += 1
            call_started = time.time()
            result = rpc(
                "tools/call",
                dict(name=tool_name, arguments=dict(expected_workspace=str(tmp_path), **arguments)),
            )
            if time.time() - call_started > 5:
                print(f"golden wait: {tool_name} seconds={time.time() - call_started:.2f}", flush=True)
            if result.get("isError") and not allow_error:
                print(json.dumps(result))
            if allow_error:
                assert result.get("isError"), result
                return result
            assert not result.get("isError"), (tool_name, json.dumps(result, ensure_ascii=True))
            content = "\n".join(c.get("text", "") for c in result.get("content", []))
            data = result.get("structuredContent", {})
            if tool_name == "harness_publish":
                assert "Next: harness_advance to_phase=execute" in content, content
            if tool_name == "harness_worker_status" and data.get("status") == "failed":
                assert "Next: harness_recovery mode=inspect" in content, content
            if tool_name == "harness_worker_status" and data.get("run", {}).get("delivery"):
                assert "Next: harness_advance to_phase=accept" in content, content
            revision = result.get("plannerConnection", {}).get("planRevision", revision)
            return result.get("structuredContent", {})

        def crash_and_reconnect():
            nonlocal process, reader, replies
            process.kill()
            process.wait(timeout=5)
            assert process.returncode != 0
            reader.join(timeout=5)
            assert not reader.is_alive()
            process.stdin.close()
            process.stdout.close()
            replies = queue.Queue()
            process = subprocess.Popen(
                [sys.executable, "-B", str(Path(__file__).resolve()), "server", str(tmp_path), surface],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True,
                encoding="utf-8", env=env)
            reader = threading.Thread(target=read, args=(process.stdout, replies), daemon=True)
            reader.start()
            rpc("initialize", dict(protocolVersion="2024-11-05", capabilities={},
                                   clientInfo=dict(name="golden-reconnect", version="1")))
            rpc("notifications/initialized", {}, notification=True)

        def yes(card):
            nonlocal decisions
            decisions += 1
            outcome = call(
                "harness_record_chat_confirmation",
                gate=card["gate"],
                request_id=card["request_id"],
                expected_revision=card["plan_revision"],
                quoted_message="Yes",
                quote_sha256=hashlib.sha256(b"Yes").hexdigest(),
                actor_label="scripted user",
            )
            assert settle_decision(lambda tool, **args: call(tool, _probe=True, **args), outcome)["state"] == "approved"

        try:
            rpc(
                "initialize",
                dict(
                    protocolVersion="2025-06-18",
                    capabilities={},
                    clientInfo=dict(name="golden", version="1"),
                ),
            )
            rpc("notifications/initialized", {}, notification=True, timeout=5)
            call("harness_plan_status")
            call(
                "harness_plan_start",
                idea=captured_run["nodes"][0]["text"],
                name=captured_run["nodes"][0]["title"],
                plan_mode="semantic-v1",
            )
            if surface == "cli":
                calls += 1
                counts["probes"] += 1
                failed = rpc(
                    "tools/call",
                    dict(
                        name=["harness_upsert_node"],
                        arguments=dict(expected_workspace=str(tmp_path)),
                    ),
                )
                assert failed["isError"] and "unconfirmed" in failed["content"][0]["text"]
                assert rpc("ping", {}) == {}
                probing = True
                captured = json.loads(
                    (ROOT / "tests/fixtures/ux5-turn7-upsert.json").read_text("utf-8")
                )
                before = call("harness_show_node", id="V1")
                # Regression: r5 guessed nested shapes/writer across five refusals;
                # captured, already-valid plans never consumed public schema examples.
                from copy import deepcopy
                schema_nodes = {}
                for kind in ("vision", "test", "bead"):
                    guide = call("harness_schema", tool="harness_upsert_node", kind=kind)
                    for prerequisite in guide["prerequisiteNodes"]:
                        call("harness_upsert_node", node=prerequisite)
                    call("harness_upsert_node", **guide["example"])
                    example = guide["example"]["node"]
                    schema_nodes[kind] = example
                    saved = call("harness_show_node", id=example["id"])
                    assert saved["fields"] == example["fields"]
                batch = call("harness_schema", tool="harness_upsert_nodes")
                call("harness_upsert_nodes", **batch["example"])
                malformed_node = call("harness_upsert_node", node=[], allow_error=True)
                assert all(word in malformed_node["content"][0]["text"] for word in ("arguments.node", "allowed keys", "harness_upsert_node"))
                wrong = call("harness_accept_scenarios", scenarios=[], allow_error=True)
                assert "upsert_node" in wrong["content"][0]["text"]
                unknown = call("harness_schema", tool="semanticScenarios", allow_error=True)
                assert "harness_upsert_node" in unknown["content"][0]["text"]
                for field, value, path, allowed in (
                    ("scenarios", [], "vision.fields.scenarios", "allowed keys"),
                    ("setup", {"database": "guess"}, "vision.fields.scenarios.scenarios[0].setup", "snapshots"),
                    ("verdict", "any", "vision.fields.scenarios.scenarios[0]", "verdict"),
                    ("execution", {"argv": ["python"], "rationale": "check"}, "T1.fields.execution", "testRoots"),
                ):
                    malformed = deepcopy(schema_nodes["test" if field == "execution" else "vision"])
                    target = malformed["fields"] if field in {"scenarios", "execution"} else malformed["fields"]["scenarios"]["scenarios"][0]
                    target[field] = value
                    retained = call("harness_show_node", id=malformed["id"])["fields"]
                    refusal = call("harness_upsert_node", node=malformed, allow_error=True)
                    message = refusal["content"][0]["text"]
                    assert all(word in message for word in (path, allowed, "harness_upsert_node")), message
                    assert call("harness_show_node", id=malformed["id"])["fields"] == retained
                call("harness_upsert_node", node={k: before[k] for k in ("id", "kind", "title", "text", "parents", "fields")})
                for node_id in ("B1", "T1", "R1", "F1"):
                    call("harness_remove_node", id=node_id)
                call("harness_upsert_node", node=captured["node"])
                unvalidated = call("harness_plan_status")["acceptance"]["scenarios"]
                assert any(
                    s.get("evidenceTier") == "scenario: unvalidated"
                    and "value" in s["validationMessage"]
                    and not s["executable"]
                    for s in unvalidated
                ), unvalidated
                captured_f3 = json.loads(
                    (ROOT / "tests/fixtures/f3-turn11-vision.json").read_text("utf-8")
                )
                call("harness_upsert_node", node=captured_f3)
                assert call("harness_show_node", id="V1")["fields"] == captured_f3["fields"]
                call(
                    "harness_upsert_node",
                    node={
                        k: before[k] for k in ("id", "kind", "title", "text", "parents", "fields")
                    },
                )
                probing = False
            nodes = captured_run["nodes"]
            if surface == "cli":
                nodes = [n for n in nodes if n["id"] != "C"]
                nodes.append(dict(id="T_RECEIPT", kind="test", title="Binary receipt", text="Verify exact bytes through the real CLI",
                    parents=["G1"], fields=dict(module="checks/verify_hashslip.py", level="semantic",
                        execution={**schema_nodes["test"]["fields"]["execution"],
                            "entry": "checks/verify_hashslip.py", "argv": [], "testRoots": ["checks"]})))
                bead = next(n for n in nodes if n["id"] == "B")
                bead["parents"] = ["T_RECEIPT"]
                bead["fields"].update(dependsOn=[], ownedFiles=["hashslip.py", "checks/verify_hashslip.py", "checks/receipt_helper.py", "checks/input.json", "checks/retired.json"])
                # The worker deletes this tracked baseline fixture. Additive copying
                # left RED enumerating different inputs from integrated GREEN.
                (tmp_path / "template/checks").mkdir(parents=True, exist_ok=True)
                (tmp_path / "template/checks/retired.json").write_text("[1]\n", encoding="utf-8")
            scenarios = nodes[0]["fields"]["scenarios"]
            written = call("harness_upsert_nodes", nodes=nodes)
            a = call("harness_understanding_card", **captured_run["understanding"])
            yes(a)
            filename = "index.html" if surface == "web" else "hashslip.py"
            call("harness_advance", to_phase="plan")
            preflight = call(
                "harness_preflight",
                providers=["codex", "claude"],
                execution={},
                reviewer_node=next(
                    r["arguments"]["node"]
                    for r in captured_run["refusals"]
                    if r["tool"] == "harness_reviewer_choice"
                ),
            )
            issues = "; ".join(preflight["planningPrerequisites"])
            assert all(
                part in issues
                for part in (
                    "execution.expected_revision",
                    "execution.workers",
                    "reviewer-effort-invalid",
                )
            ), issues
            assert "reviewer-catalog" not in issues, issues
            assert preflight["catalog"] == "unverified"
            detected = preflight["providers"]
            assert all(p["executable"]["status"] == "available" for p in detected)
            farm = json.loads(
                (ROOT / "tests/fixtures/efficiency/jsonkeys-reviewers.json").read_text("utf-8")
            )
            choice = farm["choice"] if surface == "cli" else captured_run["choice"]
            choice["fields"].update(catalogId=None, catalogDigest=None)
            for reviewer in choice["fields"]["reviewers"]:
                reviewer["choiceId"] = None
            choice["fields"]["recommendation"]["choiceIds"] = []
            call("harness_reviewer_choice", node=choice, expected_spine_revision=revision)
            compiled = call("harness_compile", include_detail=True)
            options = dict(
                expected_revision=compiled["planRevision"],
                max_parallel=1,
                timeout=300,
                workers=[
                    dict(bead=b, provider="codex", model="gpt-6.1-sol", effort="medium")
                    for b in (("B",) if surface == "cli" else ("C", "B"))
                ],
            )
            proposal = dict(gate="plan-execution", execution=options, reviewer_node_id=choice["id"])
            if surface == "cli":
                # Farm turns 2-6: installed Claude must not override this two-Sol selection.
                captured = next(t["refusals"][-1] for t in reversed(farm["turns"]) if t["refusals"])
                proposal = {
                    k: v for k, v in captured["arguments"].items() if k != "expected_workspace"
                }
                proposal.update(execution=options, reviewer_node_id=choice["id"])
            b = call("harness_prepare_chat_confirmation", **proposal)
            repeated = call(
                "harness_prepare_chat_confirmation",
                gate="plan-execution",
                execution=options,
                reviewer_node_id=choice["id"],
                _probe=True,
            )
            assert repeated["request_id"] == b["request_id"]
            reviewers = b["subject"]["reviewers"]
            assert reviewers == [
                {
                    k: r[k] if k != "effort" else r[k]["value"]
                    for k in ("name", "provider", "model", "effort")
                }
                for r in choice["fields"]["reviewers"]
            ]
            tier = "same-model" if surface == "cli" else "cross-provider"
            assert b["subject"]["reviewTier"] == tier and f"Review tier: {tier}" in b["card"]
            assert "Cross-subscription is preferred, never required" in b["card"]
            assert "callCap" not in b["proposedAction"]["execution"]
            assert not ({"reviewAllowance", "reviewPolicy", "scheduledReviewStarts", "repairReserve"} & b["subject"].keys())
            assert "reviewAllowance" not in b["card"] and "callCap" not in b["card"]
            assert all(r["name"] in b["card"] and r["model"] in b["card"] for r in reviewers)
            assert "up to 10 reviews" not in b["card"]
            assert b["catalog"] == "unverified"
            assert "catalog: unverified" in b["card"]
            yes(b)
            call("harness_advance", to_phase="roundtable")
            plan_review = call(
                "harness_roundtable",
                timeout=5,
            )
            assert [
                {k: r[k] for k in ("name", "provider", "model", "effort")}
                for r in plan_review["reviewers"]
            ] == reviewers
            if surface == "cli":
                review = call("harness_review", id=plan_review["roundId"], _probe=True)
                row = review["findingMembers"][0]
                assert row["node_id"] == "plan" and row["finding"].startswith("[TRACE]")
                captured_closure = next(
                    r["arguments"]["findings"][0]
                    for r in captured_run["refusals"]
                    if r["tool"] == "harness_resolve_findings"
                )
                closure = captured_closure["closure"]
                closure.update(
                    expectedManifestRevision=options["expected_revision"],
                    memberIds=[row["id"]],
                    nodeRefs=[dict(nodeId="G1", revision=written["revisions"]["G1"])],
                )
                disposition = call(
                    "harness_resolve_findings",
                    findings=[
                        dict(
                            id=row["id"],
                            status="resolved",
                            resolution=captured_closure["resolution"],
                            closure=closure,
                        )
                    ],
                )
                assert disposition["state"] == "pending-judgment" and disposition["owner"] is None
                scripted_judgment(lambda tool, **args: call(tool, _probe=True, **args), disposition["judgment"],
                    dict(memberDispositions={row["id"]: "resolved"}))
                disposition = call("harness_resolve_findings", findings=[dict(id=row["id"],
                    status="resolved", resolution=captured_closure["resolution"], closure=closure)], _probe=True)
                assert disposition["dispositions"] == 1
            call("harness_advance", to_phase="publish")
            call("harness_publish")
            call("harness_advance", to_phase="execute")
            if surface == "cli":
                # Preserve main's higher-deadline refusal: tuple-change checks alone
                # miss accidental admission beyond the user's approved time limit.
                changed = call("harness_worker_start", **{**options, "timeout": 301},
                               allow_error=True, _probe=True)
                diff = changed["structuredContent"]["chatConfirmation"]["diff"]
                assert [(d["field"], d["before"], d["after"]) for d in diff] == [
                    ("execution.timeout", 300, 301)
                ]
                changed_options = {**options, "workers": [dict(options["workers"][0], model="gpt-6.1-terra")]}
                changed = call("harness_worker_start", **changed_options, allow_error=True)
                diff = changed["structuredContent"]["chatConfirmation"]["diff"]
                assert [(d["field"], d["before"], d["after"]) for d in diff] == [
                    ("execution.workers", [dict(provider="codex", model="gpt-6.1-sol", effort="medium")],
                     [dict(provider="codex", model="gpt-6.1-sol", effort="medium"),
                      dict(provider="codex", model="gpt-6.1-terra", effort="medium")])
                ]
                assert (
                    sum(c["text"].count("Your decision: B.") for c in changed["content"])
                    == 1
                )
                repeated_change = call("harness_worker_start", **changed_options, allow_error=True, _probe=True)
                assert repeated_change["structuredContent"]["chatConfirmation"]["request_id"] == changed["structuredContent"]["chatConfirmation"]["request_id"]
            job = call("harness_worker_start", **options)
            deadline = time.time() + 300
            while job["status"] not in ("completed", "failed", "pending-judgment") and time.time() < deadline:
                job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=1)
            if surface == "web":
                saved = json.loads(Path(job["run"]["recordPath"]).read_text("utf-8"))
                assert job["status"] == saved["status"] == "pending-judgment"
                assert saved["beads"]["C"]["status"] == "awaiting judgment"
                assert saved["beads"]["B"]["attempt"] is None
                assert saved["semanticContracts"]["observedState"] == "ERROR"
                assert any("can't open file" in a.get("reason", "") for r in saved["semanticContracts"]["results"] for a in r.get("actions", {}).values())
                assert "awaiting judgment" in saved["beads"]["B"]["status"]
                roots = saved["beads"]["C"]["integrated"]["root"]
                judge_candidate(lambda tool, **args: call(tool, _probe=True, **args),
                                dict(repo=roots, check="execution"))
                while (job["status"] not in {"failed", "completed"}
                       and (not job["run"].get("semanticValidation") or job["status"] != "pending-judgment")
                       and time.time() < deadline):
                    job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=1)
                assert job["status"] == "pending-judgment", job
                saved = json.loads(Path(job["run"]["recordPath"]).read_text("utf-8"))
                assert saved["status"] == "pending-judgment", saved["status"]
                (tmp_path / "validation-observed").write_text("observed", encoding="utf-8")
                assert saved["semanticContracts"]["state"] == "RED"
                assert saved["semanticValidation"]["state"] == "pending-judgment"
                roots = saved["beads"]["B"]["integrated"]["root"]
                assert any(a.get("checks", {}).get("textContrastAA", False) is None
                           for r in saved["semanticValidation"]["results"] for a in r["actions"].values())
                for check in ("textContrastAA", "execution"):
                    judge_candidate(lambda tool, **args: call(tool, _probe=True, **args),
                                    dict(repo=roots, check=check))
                while job["status"] not in {"completed", "failed"} and time.time() < deadline:
                    job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=1)
            if surface == "cli":
                failed = json.loads(Path(job["run"]["recordPath"]).read_text())
                assert failed["beads"]["B"]["integrated"]["semantic"]["state"] == "FAIL"
                assert failed["beads"]["B"]["integrated"]["semantic"]["gaps"]
                probing = True
                original = job["jobId"]
                proof = call("harness_recovery", mode="inspect", job_id=original)
                while proof["verdict"] != "VERIFIED-QUIESCENT" and time.time() < deadline:
                    time.sleep(0.1)
                    proof = call("harness_recovery", mode="inspect", job_id=original)
                assert proof["verdict"] == "VERIFIED-QUIESCENT", proof
                evidence = Path(job["evidenceDir"])
                (evidence / "quiescence.json").rename(evidence / "retained-quiescence.json")
                resolved = call(
                    "harness_recovery",
                    mode="resolve",
                    job_id=original,
                    choice="readmit",
                    reason="Repair the observed Contracts startup failure",
                )
                assert "card" not in resolved
                assert resolved["proof"]["verdict"] == "ABANDONED"
                assert resolved["quarantine"] is True
                job = call("harness_worker_start", **options)
                assert job["jobId"] == resolved["successor"] != original
                while job["status"] not in ("completed", "failed", "pending-judgment") and time.time() < deadline:
                    job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=1)
                # The web failure's continuation seam, without needing Chromium:
                # scripted dispatch has a retained owner but no native dispatch-run record.
                assert job["status"] == "pending-judgment", job
                assert not (Path(job["evidenceDir"]) / "dispatch-run.json").exists()
                judge_candidate(lambda tool, **args: call(tool, _probe=True, **args),
                    dict(repo=job["run"]["beads"]["B"]["candidateRoot"], check="execution", identity_regression=True))
                while job["status"] not in {"completed", "failed"} and time.time() < deadline:
                    job = call("harness_worker_status", job_id=job["jobId"], wait_seconds=1)
                probing = False
            assert job["status"] == "completed", job
            if surface == "web":
                assert job["run"]["semanticContracts"]["state"] == "RED", job
            else:
                retained = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
                assert set(retained["beads"]) == {"B"}
                red = retained["semanticValidation"]["red"]
                assert red["observedState"] == "FAIL", red
                assert any(r["id"] == "T_RECEIPT" and r["state"] == "FAIL" for r in red["results"])
                observed = next(r for r in red["results"] if r["id"] == "T_RECEIPT")["actions"]["execute"]["stderr"]
                assert "ModuleNotFoundError: No module named 'hashslip'" in observed
                assert "retired fixture still present" not in observed
                assert not (tmp_path / "checks/retired.json").exists()
                assert all(r["cleanup"]["quiescenceVerified"] for r in red["results"])
                assert retained["semanticValidation"]["testSources"] == red["testSources"]
                assert retained["semanticValidation"]["judgmentId"]
                # S6 must invoke the absolute candidate entry from another owned
                # directory; root-only receipt checks missed the cwd regression.
                invocation = next(r for r in retained["semanticValidation"]["results"] if r["id"] == "S6")["actions"]["run"]
                entry, caller = Path(invocation["command"][1]), Path(invocation["cwd"])
                assert entry.is_absolute() and caller == entry.parent / "checks"
            assert job["run"]["semanticValidation"]["state"] == "PASS", job
            if surface == "web":
                # Protect mode provenance and preview guidance through canonical dispatch;
                # CLI journeys never render, and packet fixtures do not consume dispatch.
                retained = json.loads(Path(job["run"]["recordPath"]).read_text(encoding="utf-8"))
                proof = retained["semanticValidation"]["results"]
                actions = [a for r in proof for a in r["actions"].values() if a.get("browserMode")]
                assert actions and all(a["browserMode"] == "normal" for a in actions)
                served = [a for a in actions if a.get("server")]
                assert len(served) == 1, actions
                api = proof[0]["actions"]["api"]
                assert api["http"][0]["response"]["json"] == {"saved": False}
                assert api["server"]["stopped"] and not api.get("browserMode")
                assert served[0]["server"]["ready"] and served[0]["server"]["stopped"]
                assert served[0]["http"][0]["response"]["json"] == {"saved": True, "note": "Tea"}
                assert served[0]["checks"]["consoleErrors"] == 0
                assert [(r["step"], r["status"], Path(r["body"]["path"]).read_text() if r["body"] else None) for r in served[0]["responses"] if r["expected"]] == [("formpost", 302, None), ("overconsume", 409, "{}"), ("delete", 204, ""), ("empty", 204, ""), ("cached", 304, "")]
                assert any(r["status"] == 302 and r.get("bodyDiagnostic") for r in served[0]["responses"]), served[0]
                assert any(r["url"].split("#", 1)[0].endswith("/index.html") and r["body"] and Path(r["body"]["path"]).stat().st_size > 16384 for r in served[0]["responses"])
                assert sum(r["method"] == "DELETE" for r in served[0]["responses"]) == 1
                assert all(Path(r["body"]["path"]).read_bytes() == b"" for r in served[0]["responses"] if r["status"] == 204)
                assert any(r["step"] == "empty" and r.get("transportDiagnostic") for r in served[0]["responses"]), served[0]
                assert [(r["response"]["status"], r["response"]["body"]) for r in served[0]["http"][2:]] == [(204, ""), (200, ""), (205, ""), (304, "")]
                assert served[0]["http"][1]["response"]["json"]["exists"] is True
                assert any(a["url"].startswith("file://") for a in actions)
                assert all(r["cleanup"]["quiescenceVerified"] and r["cleanup"]["activeProcessesAfterTeardown"] == 0 for r in proof)
                import socket
                with socket.socket() as probe:
                    assert probe.connect_ex(("127.0.0.1", served[0]["server"]["port"])) != 0
                # Execute the packet's actual shell command: a quoted Windows executable
                # without PowerShell's & looks plausible but never opens the preview.
                prompt = retained["beads"]["B"]["prompt"]
                commands = [c for c in prompt.split("`") if "--preview " in c]
                assert any("--preview ." in c for c in commands), prompt
                command = next(c for c in commands if "--preview SCENARIOS.json 'served'" in c)
                # Static previews never started a server: its log must not become
                # an unowned worker change, and its protected binding lives here.
                worker_root = Path(retained["beads"]["B"]["attempt"]["cwd"])
                # Round 2: happy/negative steps share endpoints and error statuses.
                # Journey-wide declarations hid happy-path failures; the old probe
                # covered only a different, undeclared endpoint's 500.
                from copy import deepcopy
                negative = deepcopy(vision["scenarios"])
                negative["scenarios"] = negative["scenarios"][:1]
                negative["scenarios"][0]["actions"] = [next(a for a in negative["scenarios"][0]["actions"] if a["id"] == "a1")]
                negative["scenarios"][0]["observe"] = negative["scenarios"][0]["observe"][:1]
                negative["scenarios"][0]["actions"][0]["web"]["steps"] = [s for s in journey["steps"] if s["op"] == "open" or s.get("id") == "overconsume"]
                negative["scenarios"][0]["actions"][0]["web"]["steps"].extend([
                    dict(id="happy409", op="click", locator=dict(selector="#happy")),
                    dict(op="assertVisible", locator=dict(text="Happy")),
                    dict(id="negative500", op="click", locator=dict(selector="#broken"), expectedErrors=[dict(method="GET", path="/broken", statusEquals=500)]),
                    dict(op="assertVisible", locator=dict(text="Broken")),
                    dict(id="happy500", op="click", locator=dict(selector="#failed-happy")),
                    dict(op="assertVisible", locator=dict(text="Failed happy")),
                    dict(id="disconnect", op="click", locator=dict(selector="#disconnect")),
                    dict(op="assertVisible", locator=dict(text="Disconnected")),
                    # P1: headers are not completion, including a declared error.
                    # Existing HTTP failures never truncated a response body.
                    dict(id="truncated200", op="click", locator=dict(selector="#truncated200")),
                    dict(op="assertVisible", locator=dict(text="Truncated 200")),
                    dict(id="truncated409", op="click", locator=dict(selector="#truncated409"), expectedErrors=[dict(method="GET", path="/truncated409", statusEquals=409)]),
                    dict(op="assertVisible", locator=dict(text="Truncated 409"))])
                failed = observe(negative, worker_root, vision["targets"], "validation")[0]
                assert failed["state"] == "FAIL" and failed["cleanup"]["quiescenceVerified"], failed
                assert failed["actions"]["a1"]["checks"]["consoleErrors"] == 5, failed
                assert [(r["step"], r["status"], r["expected"]) for a in failed["actions"].values() for r in a["responses"] if r["status"] and r["status"] >= 400] == [
                    ("overconsume", 409, True), ("happy409", 409, False), ("negative500", 500, True), ("happy500", 500, False), ("truncated409", 409, True)]
                assert {r["status"] for r in failed["actions"]["a1"]["responses"] if (r.get("step") or "").startswith("truncated") and (r.get("bodyDiagnostic") or r.get("transportDiagnostic"))} == {200, 409}, failed
                assert any(r["step"] == "disconnect" and r["status"] is None and r["transportDiagnostic"] for r in failed["actions"]["a1"]["responses"])
                before_preview = subprocess.check_output(["git", "status", "--porcelain"], cwd=worker_root)
                from codex_harness.core.planner_candidate import observation_files
                before_files = observation_files(worker_root)
                # A finished adapter must exit despite a referenced event-loop
                # handle. Previously the preview had no such handle, hiding an
                # implicit lifetime that could wait until the outer watchdog.
                keepalive = tools / "keep-node-open.cjs"
                keepalive.write_text("setInterval(() => {}, 1000);\n", encoding="utf-8")
                preview_env = dict(os.environ, NODE_OPTIONS=(os.environ.get("NODE_OPTIONS", "")
                    + " --require " + json.dumps(keepalive.as_posix())).strip())
                preview_started = time.time()
                preview = subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                    cwd=worker_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=preview_env)
                try:
                    # P2: headless preview must finish and reap its launcher/child
                    # server without an outer Job or any desktop window control.
                    output, errors = preview.communicate(timeout=35)
                    assert preview.returncode == 0, errors
                    completion = json.loads(output)
                    assert completion["state"] == "pending-judgment", completion
                    assert set(completion["actions"]) == {a["id"] for a in scenario["actions"]}, completion
                    assert completion["actions"]["seed"]["http"][0]["request"]["json"] == dict(note="Seeded")
                    assert completion["actions"]["seed-check"]["stdout"].replace("\r\n", "\n") == "Seeded\n"
                    assert all(o["passed"] is not False for o in completion["observations"]), completion
                    observed = completion["actions"]["a1"]
                    assert len(observed["steps"]) == len(journey["steps"]), completion
                    assert observed["checks"]["consoleErrors"] == 0
                    # Named previews previously opened a desktop page and erased
                    # screenshots; check retained phone pixels and actual flow.
                    import struct
                    for artifact in observed["artifacts"]:
                        png = Path(artifact["path"])
                        assert png.is_relative_to(worker_root / ".codex-harness/evidence/preview")
                        data = png.read_bytes()
                        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
                        assert struct.unpack('>I', data[16:20])[0] == 375
                    assert "Deleted" in observed["rendered"]["text"]
                    assert observed["url"].startswith("http://127.0.0.1:") and observed["server"]["ready"], observed
                    assert "HeadlessChrome/" in observed["userAgent"], observed
                    cleanup = completion["cleanup"]
                finally:
                    if preview.poll() is None:
                        preview.communicate(timeout=135)  # The preview's own bounded owner ends it.
                assert observed["browserMode"] == "normal"
                assert cleanup["backend"] == "windows-job" and cleanup["membershipVerifiedBeforePayload"]
                assert cleanup["quiescenceVerified"]
                assert cleanup["activeProcessesAfterTeardown"] == 0
                assert Path(observed["server"]["cwd"]) != worker_root
                assert not Path(observed["server"]["cwd"]).exists()
                with socket.socket() as probe:
                    assert probe.connect_ex(("127.0.0.1", observed["server"]["port"])) != 0
                assert subprocess.check_output(["git", "status", "--porcelain"], cwd=worker_root) == before_preview
                assert observation_files(worker_root) == before_files
                print(f"golden preview: seconds={time.time() - preview_started:.2f}", flush=True)
                print(
                    f"golden web: scenarios={len(proof)}; journeys={len(actions)}; "
                    f"browserModes={sorted({a['browserMode'] for a in actions})}"
                )
            assert (tmp_path / filename).is_file()
            if surface == "cli":
                assert subprocess.check_output(
                    [sys.executable, "-B", str(tmp_path / filename), "--help"], cwd=tmp_path,
                    timeout=10,
                ).startswith(b"usage:")
            before_judgment = call("harness_plan_status", _probe=True)
            # Scripted semantic request on CLI; web retains actual rendered evidence.
            judgment = dict(captured_run["judgments"][-1], repo=str(tmp_path)) if surface == "web" else dict(repo=str(tmp_path), check="visualQuality")
            quality = judge_candidate(lambda tool, **args: call(tool, _probe=True, **args), judgment)
            from codex_harness.core.acceptance_runs import acceptance_inputs
            from codex_harness.core.plan_store import open_plan_store
            with open_plan_store(tmp_path) as store:
                consumed = acceptance_inputs(store, tmp_path, subject="product")["judgments"]["visualQuality"]
            assert consumed["id"] == quality["id"] and consumed["positiveCredit"], consumed
            assert call("harness_plan_status", _probe=True)["plan_revision"] == before_judgment["plan_revision"]
            call("harness_advance", to_phase="accept")
            deadline = time.time() + 300
            # Complete the bound verdict before capturing acceptance inputs. Replacing
            # a static passed=True mid-capture is now a new authoritative judgment,
            # so the old capture must lose credit; it cannot be treated as unchanged.
            acceptance = call("harness_accept_run", repo=str(tmp_path), subject="product")
            if surface == "cli":
                interrupted_id = acceptance["attemptId"]
                assert acceptance["state"] == "queued" and acceptance["result"] is None
                crash_and_reconnect()
                interrupted = call("harness_accept_status", id=interrupted_id, allow_error=True)["structuredContent"]
                assert interrupted["state"] == "unknown-interrupted" and interrupted["result"] is None
                released = call("harness_recovery", mode="resolve", job_id="acceptance:" + interrupted_id,
                    choice="abandon", reason="Transport loss grants no result or consent")
                assert released["ownershipReleased"] and released["quarantine"]
                assert released["quiescenceVerified"] is False
                old = call("harness_accept_status", id=interrupted_id, allow_error=True)["structuredContent"]
                assert old["state"] == "abandoned" and old["result"] is None
                assert not call("harness_plan_status", _probe=True)["acceptance"]["accepted"]
                acceptance = call("harness_accept_run", repo=str(tmp_path), subject="product", _probe=True)
                assert acceptance["attemptId"] != interrupted_id
            while acceptance.get("result") is None and time.time() < deadline:
                acceptance = call(
                    "harness_accept_status", id=acceptance["attemptId"], wait_seconds=1
                )
            assert acceptance.get("result") is not None, f"Acceptance poll deadline: {acceptance}"
            assert acceptance["result"]["passed"], acceptance
            review = call("harness_accept_review", repo=str(tmp_path))
            if surface == "cli":
                interrupted_id = review["attemptId"]
                end = time.time() + 15
                while not (tmp_path / ".codex-harness" / "review-crash-started").exists() and time.time() < end:
                    time.sleep(0.05)
                assert (tmp_path / ".codex-harness" / "review-crash-started").exists()
                crash_and_reconnect()
                interrupted = call("harness_accept_status", id=interrupted_id, allow_error=True)["structuredContent"]
                assert interrupted["state"] == "unknown-interrupted"
                released = call("harness_recovery", mode="resolve", job_id="review:" + interrupted_id,
                    choice="abandon", reason="Lost review owner; quarantine evidence without inventing quiescence")
                assert released["ownershipReleased"] and released["quiescenceVerified"] is False
                old = call("harness_accept_status", id=interrupted_id, allow_error=True)["structuredContent"]
                assert old["state"] == "abandoned" and old["result"] is None
                review = call("harness_accept_review", repo=str(tmp_path), _probe=True)
                assert review["attemptId"] != interrupted_id
            attempt_id = review["attemptId"]
            deadline = time.time() + 300
            while review["state"] in {"queued", "running"} and time.time() < deadline:
                review = call("harness_accept_status", id=attempt_id, wait_seconds=1)
                assert review["attemptId"] == attempt_id, review
            assert review["state"] == "completed", review
            assert review["result"]["id"] == attempt_id, review
            assert [
                {k: r[k] for k in ("name", "provider", "model", "effort")}
                for r in review["result"]["reviewers"]
            ] == reviewers
            decisions += 1
            pending_c = call("harness_accept", by="user", statement="yes")
            assert not call("harness_plan_status", _probe=True)["acceptance"]["accepted"]
            settle_decision(lambda tool, **args: call(tool, _probe=True, **args), pending_c)
            final_status = call("harness_plan_status", _probe=True)
            assert final_status["acceptance"]["accepted"]
            assert {s["id"] for s in final_status["acceptance"]["scenarios"]} == {
                s["id"] for s in scenarios["scenarios"]
            }
            assert all(s["lastResult"] == "pass" for s in final_status["acceptance"]["scenarios"])
            # The real saved producer reaches HTTP and the shipped browser view.
            # Catch revision/blocker/next-step divergence and broken asset wiring;
            # planning-only tests and synthetic UI fixtures miss this integration.
            from tests.python.core.cockpit_server_support import get, running

            before = {
                p: p.read_bytes() for p in (tmp_path / ".codex-harness").rglob("*") if p.is_file()
            }
            run_path = Path(job["run"]["recordPath"])
            runs_root = (
                run_path.parents[2] if run_path.parent.name == "evidence" else run_path.parents[1]
            )
            with running(tmp_path, workspace=tmp_path, runs_root=runs_root) as connection:
                status_code, _, body = get(connection, "/api/status")
                assert status_code == 200
                observed = json.loads(body)["plan"]
                for key in ("plan_revision", "pending_gate", "open_findings", "next", "acceptance"):
                    assert observed[key] == final_status[key], key
                if surface == "web":
                    from codex_harness.core.audit_process import run_audit_process

                    checked = run_audit_process(
                        [
                            "node",
                            str(ROOT / "tests/browser/cockpit/checks.js"),
                            f"http://127.0.0.1:{connection.port}",
                            observed["plan_revision"],
                            observed["next"],
                        ],
                        cwd=tmp_path,
                        timeout=45,
                    )
                    assert checked.returncode == 0, (checked.stdout, checked.stderr)
                    assert checked.audit_control["quiescenceVerified"]
            after = {
                p: p.read_bytes() for p in (tmp_path / ".codex-harness").rglob("*") if p.is_file()
            }
            assert before == after
            assert decisions == 3
            assert counts["happy"] <= 25, counts
            assert sum(counts.values()) == calls
            print(
                f"golden {surface}: decisions={decisions}; planner MCP calls={calls}; "
                f"happy={counts['happy']}; probes={counts['probes']}; polls={counts['polls']}; "
                f"seconds={time.time() - started:.2f}"
            )
        finally:
            failing = sys.exc_info()[0] is not None
            try:
                if "job" in locals() and job["status"] not in {"completed", "failed"}:
                    cleanup = rpc("tools/call", dict(name="harness_recovery", arguments=dict(
                        expected_workspace=str(tmp_path), mode="resolve", job_id=job["jobId"],
                        choice="abandon", reason="End failed golden fixture through its existing Job owner")), timeout=10)
                    assert not cleanup["isError"], cleanup
                if failing:
                    process.kill()  # Unblock any outstanding pipe writer before closing stdin.
                else:
                    closed = threading.Event()
                    def close_input():
                        try:
                            process.stdin.close()
                        finally:
                            closed.set()
                    threading.Thread(target=close_input, daemon=True).start()
                    end = time.time() + 5
                    while not closed.wait(.25):
                        assert time.time() < end, "Golden stdin close exceeded 5s wall-clock deadline"
                process.wait(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            if not failing:
                assert process.returncode == 0, (tmp_path / "server.log").read_text("utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "http-golden":
        http_golden(Path(sys.argv[2]), semantic=bool(int(sys.argv[3])))
    else:
        provider_host(*sys.argv[1:])
