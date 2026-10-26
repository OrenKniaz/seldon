"""Real developer fixtures protect data execution; legacy audits inspect test code."""

import json
import subprocess
import sys

import pytest

from codex_harness.core.semantic_runner import MODULE, run


def scenario():
    return json.loads("""{"schema":"seldon/scenarios-v1","scenarios":[{
      "id":"invalid-zero","requirementRefs":["R2@1"],"contractRefs":["cli@1"],
      "setup":{"files":[{"path":"data/ledger.json","utf8":"[]"}],
        "snapshots":[{"id":"before","path":"data/ledger.json"}]},
      "actions":[{"id":"add","cli":{"target":"tally","operation":"add",
        "argv":["--db",{"path":"data/ledger.json"},"add","food","0"]}}],
      "observe":[{"action":"add","exitEquals":2,"stdoutEquals":"","stderrNonempty":true},
        {"path":"data/ledger.json","bytesUnchangedFrom":"before","fileJsonEquals":[]}],
      "verdict":"all"}]}""")


TARGETS = {"tally": {"entry": "tally.py", "argv": []}}
GRAMMAR = """import argparse, sys
p=argparse.ArgumentParser(); p.add_argument('--db')
p.add_argument('operation', choices=['add']); p.add_argument('category')
p.add_argument('amount'); a=p.parse_args()
"""
STUB = GRAMMAR + "print('SELDON-STUB '+a.operation, file=sys.stderr); sys.exit(3)\n"
PRODUCT = GRAMMAR + "print('amount must be positive', file=sys.stderr); sys.exit(2)\n"


def test_tally_stub_fail_then_implemented_fixture_cli_pass(tmp_path):
    """Catch false stub RED/PASS; previous AST proofs never consumed scenario data via CLI."""
    product = tmp_path / "product"
    product.mkdir()
    entry = product / "tally.py"
    entry.write_text(STUB)
    red = run(scenario(), product, TARGETS, "contracts")
    (tmp_path / "red.json").write_text(json.dumps(red))
    assert red[0]["state"] == "FAIL", red
    assert red[0]["stubActions"] == ["add"]
    assert red[0]["cleanup"]["quiescenceVerified"] is True
    entry.write_text(PRODUCT)
    spec, targets = tmp_path / "spec.json", tmp_path / "targets.json"
    spec.write_text(json.dumps(scenario()))
    targets.write_text(json.dumps(TARGETS))
    cli = subprocess.run(
        [sys.executable, "-B", "-m", MODULE, str(spec), str(product), str(targets), "validation"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cli.returncode == 0, cli.stderr
    (tmp_path / "green.json").write_text(cli.stdout)
    green = json.loads(cli.stdout)[0]
    assert green["state"] == "PASS" and green["stubActions"] == [], green


@pytest.mark.parametrize(
    "source,reason",
    [
        ("raise RuntimeError('crashed')", "marker"),
        ("import time; time.sleep(20)", "timeout"),
        ("print('x'*100000)", "output"),
        ("print('SELDON-STUB wrong', file=__import__('sys').stderr); exit(3)", "marker"),
    ],
)
def test_infrastructure_stops_remaining_scenarios(tmp_path, source, reason):
    """Catch infra earning RED or launching later work; passing stubs miss finite-stop failures."""
    (tmp_path / "tally.py").write_text(source)
    document = scenario()
    document["scenarios"].append(dict(document["scenarios"][0], id="unperformed"))
    results = run(document, tmp_path, TARGETS, "contracts", action_seconds=0.5)
    assert [r["state"] for r in results] == ["ERROR", "ERROR"], results
    assert reason in results[0]["reason"].lower(), results
    assert results[1]["reason"] == "unperformed after earlier ERROR"


@pytest.mark.parametrize(
    "defect", ["http", "file", "path", "argv", "target", "snapshot", "type", "code"]
)
def test_preflight_refuses_invalid_data_before_any_action(tmp_path, defect):
    """Catch malformed input launching an earlier action; valid fixtures miss preflight gaps."""
    launched = tmp_path / "launched"
    (tmp_path / "tally.py").write_text(f"open({str(launched)!r},'w').write('unexpected')")
    document = scenario()
    s = document["scenarios"][0]
    if defect in ("http", "file"):
        s["actions"].append({"id": "later", defect: {}})
    elif defect == "path":
        s["setup"]["files"][0]["path"] = "../outside"
    elif defect == "argv":
        s["actions"][0]["cli"]["argv"] = [{"path": "../outside"}]
    elif defect == "target":
        s["actions"][0]["cli"]["target"] = "missing"
    elif defect == "snapshot":
        s["observe"][1]["bytesUnchangedFrom"] = "missing"
    else:
        s["observe"][0]["exitEquals" if defect == "type" else "eval"] = True
    result = run(document, tmp_path, TARGETS, "validation")
    assert result[0]["state"] == "ERROR", result
    if defect in ("http", "file"):
        assert "unsupported in v1" in result[0]["reason"]
    assert not launched.exists()


@pytest.mark.parametrize(
    "expected,state", [({"a": True, "b": [2]}, "PASS"), ({"a": 1, "b": [2]}, "FAIL")]
)
def test_json_equality_preserves_types_not_layout(tmp_path, expected, state):
    """Catch true==1 and layout-sensitive JSON; plain exit/file-preservation tests miss decoding."""
    (tmp_path / "tally.py").write_text('print(\' { "b" : [2], "a" : true } \')\n')
    document = scenario()
    document["scenarios"][0]["observe"] = [{"action": "add", "stdoutJsonEquals": expected}]
    assert run(document, tmp_path, TARGETS, "validation")[0]["state"] == state


def test_passing_stub_is_not_forced_red_and_validation_refuses_marker(tmp_path):
    """Catch forced RED and final stub credit; mismatching expectations alone miss both."""
    (tmp_path / "tally.py").write_text(STUB)
    document = scenario()
    document["scenarios"][0]["observe"] = [{"action": "add", "exitEquals": 3}]
    assert run(document, tmp_path, TARGETS, "contracts")[0]["state"] == "PASS"
    assert run(document, tmp_path, TARGETS, "validation")[0]["state"] == "ERROR"


def test_byte_preservation_detects_json_equivalent_mutation(tmp_path):
    """Catch changed bytes hidden by JSON equality; read-only success misses rewrites."""
    mutation = "open(a.db,'w').write(' [ ] '); "
    source = PRODUCT.replace("print('amount", mutation + "print('amount")
    (tmp_path / "tally.py").write_text(source)
    result = run(scenario(), tmp_path, TARGETS, "validation")[0]
    assert result["state"] == "FAIL", result
    checks = {o["check"]: o["passed"] for o in result["observations"]}
    assert checks["bytesUnchangedFrom"] is False and checks["fileJsonEquals"] is True
