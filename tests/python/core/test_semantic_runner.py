"""Real developer fixtures protect data execution; legacy audits inspect test code."""

import json
import subprocess
import sys
from pathlib import Path

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


@pytest.mark.parametrize(
    "ending,expected,state",
    [
        ("\n", "1.00\n", "PASS"),
        ("", "1.00\n", "FAIL"),
        ("\r", "1.00\n", "FAIL"),
        ("\n", "1.00 \n", "FAIL"),
        ("\n", "1.00\r\n", "FAIL"),
    ],
    ids=["crlf-print", "missing-newline", "bare-cr", "spacing", "expected-unchanged"],
)
def test_process_text_equality_normalizes_only_actual_crlf(tmp_path, ending, expected, state):
    """Protect portable print equality without stripping; existing CLI tests omit newlines."""
    (tmp_path / "tally.py").write_text(
        "import sys\nsys.stdout.reconfigure(newline='\\r\\n')\n"
        "sys.stderr.reconfigure(newline='\\r\\n')\n"
        f"print('1.00', end={ending!r})\nprint('diagnostic', file=sys.stderr)\n"
    )
    document = scenario()
    document["scenarios"][0]["observe"] = [
        {"action": "add", "stdoutEquals": expected, "stderrNonempty": True}
    ]
    result = run(document, tmp_path, TARGETS, "validation")[0]
    assert result["state"] == state, result
    assert result["actions"]["add"]["stdout"] == "1.00" + ending.replace("\n", "\r\n")
    assert result["actions"]["add"]["stderr"] == "diagnostic\r\n"
    assert document["scenarios"][0]["observe"][0]["stdoutEquals"] == expected
    assert result["cleanup"]["quiescenceVerified"]


def test_crlf_file_rewrite_stays_byte_mismatch(tmp_path):
    """Catch newline normalization leaking into files; prior preservation test changes spaces."""
    (tmp_path / "tally.py").write_text(
        GRAMMAR + "from pathlib import Path\nPath(a.db).write_bytes(b'[]\\r\\n')\n"
    )
    document = scenario()
    s = document["scenarios"][0]
    s["setup"]["files"][0]["utf8"] = "[]\n"
    s["observe"] = [
        {"path": "data/ledger.json", "fileBytesEquals": "[]\n", "bytesUnchangedFrom": "before"}
    ]
    result = run(document, tmp_path, TARGETS, "validation")[0]
    assert result["state"] == "FAIL", result
    assert [o["passed"] for o in result["observations"]] == [False, False]
    assert result["observations"][0]["actual"] == "[]\r\n"
    assert result["cleanup"]["quiescenceVerified"]


def test_process_json_string_newlines_remain_exact(tmp_path):
    """Catch normalization before JSON decoding; existing JSON checks cover types, not text."""
    (tmp_path / "tally.py").write_text("import json\nprint(json.dumps('line\\r\\n'))\n")
    document = scenario()
    document["scenarios"][0]["observe"] = [{"action": "add", "stdoutJsonEquals": "line\n"}]
    result = run(document, tmp_path, TARGETS, "validation")[0]
    assert result["state"] == "FAIL", result
    assert result["observations"][0]["actual"] == "line\r\n"


@pytest.mark.parametrize("check,expected", [("fileJsonEquals", []), ("fileBytesEquals", "[]")])
def test_missing_product_output_fails_then_persisted_output_passes(
    tmp_path, monkeypatch, check, expected
):
    """Protect output RED→GREEN; existing file tests always materialize the observed file."""
    # Worker cwd changes: keep this test independent of the known relative-path import bug.
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[3] / "src"))
    document = scenario()
    s = document["scenarios"][0]
    s["setup"] = {}
    s["observe"] = [{"path": "data/ledger.json", check: expected}]
    entry = tmp_path / "tally.py"
    entry.write_text(STUB)
    red = run(document, tmp_path, TARGETS, "contracts")[0]
    assert red["state"] == "FAIL", red
    assert red["stubActions"] == ["add"]
    assert red["observations"] == [
        dict(check=check, passed=False, actual=None, reason="product file missing")
    ]
    assert red["cleanup"]["quiescenceVerified"]
    entry.write_text(GRAMMAR)
    assert run(document, tmp_path, TARGETS, "validation")[0]["state"] == "FAIL"
    entry.write_text(
        GRAMMAR + "from pathlib import Path\n"
        "db=Path(a.db); db.parent.mkdir(parents=True); db.write_text('[]')\n"
    )
    green = run(document, tmp_path, TARGETS, "validation")[0]
    assert green["state"] == "PASS", green
    assert green["cleanup"]["quiescenceVerified"]


def test_missing_setup_snapshot_remains_error(tmp_path, monkeypatch):
    """Protect setup errors; post-action output coverage cannot detect a missing preimage."""
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[3] / "src"))
    (tmp_path / "tally.py").write_text(STUB)
    document = scenario()
    document["scenarios"][0]["setup"]["files"] = []
    result = run(document, tmp_path, TARGETS, "contracts")[0]
    assert result["state"] == "ERROR", result
    assert "ledger.json" in result["reason"]


def test_tally_stub_fail_then_implemented_fixture_cli_pass(tmp_path):
    """Catch false stub RED/PASS; previous AST proofs never consumed scenario data via CLI."""
    product = tmp_path
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
    "keys,value",
    [
        (("actions", 1), {"id": "later", "http": {}}),
        (("actions", 1), {"id": "later", "file": {}}),
        (("setup", "files", 0, "path"), "../outside"),
        (("actions", 0, "cli", "argv"), [{"path": "../outside"}]),
        (("actions", 0, "cli", "target"), "missing"),
        (("observe", 1, "bytesUnchangedFrom"), "missing"),
        (("observe", 0, "exitEquals"), True),
        (("observe", 0, "eval"), True),
        pytest.param("data/cli.py", None, id="entry-collision"),
        pytest.param("data/helper.py", None, id="import-collision"),
        pytest.param("data/helper.py", "missing", id="new-import-collision"),
        pytest.param("data/helper.py.", "missing", id="dot-alias-collision"),
        pytest.param("data/helper.py ", "missing", id="space-alias-collision"),
    ],
)
def test_preflight_refuses_invalid_data_before_any_action(tmp_path, keys, value):
    """Catch setup supplying code; prior collisions missed creation of a missing imported module."""
    launched = tmp_path / "launched"
    (tmp_path / "tally.py").write_text(f"open({str(launched)!r},'w').write('unexpected')")
    document = scenario()
    s = document["scenarios"][0]
    s["actions"].append(dict(s["actions"][0], id="later"))
    targets = TARGETS
    if type(keys) is str:
        (tmp_path / "data").mkdir()
        entry = GRAMMAR + (
            "try:\n import helper\nexcept ModuleNotFoundError:\n"
            " print('SELDON-STUB '+a.operation,file=sys.stderr);sys.exit(3)\n"
        )
        (tmp_path / "data/cli.py").write_text(entry)
        if value != "missing":
            (tmp_path / "data/helper.py").write_text(STUB)
        targets = {"tally": {"entry": "data/cli.py", "argv": []}}
        if value == "missing":
            baseline = run(document, tmp_path, targets, "contracts")
            (tmp_path / "baseline.json").write_text(json.dumps(baseline))
            assert baseline[0]["state"] == "FAIL"
        source = f"open({str(launched)!r},'w').write('unexpected');print('true')"
        s["setup"]["files"].append({"path": keys, "utf8": source})
        s["observe"] = [{"action": "add", "stdoutJsonEquals": True}]
    else:
        parent = s
        for key in keys[:-1]:
            parent = parent[key]
        parent[keys[-1]] = value
    result = run(document, tmp_path, targets, "validation")
    (tmp_path / "observation.json").write_text(json.dumps(result))
    assert result[0]["state"] == "ERROR", result
    if keys == ("actions", 1):
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
