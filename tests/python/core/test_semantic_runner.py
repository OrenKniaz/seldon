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


@pytest.mark.parametrize(
    "stdin,expected",
    [
        (None, ""),
        ({"utf8": ""}, ""),
        ({"utf8": "שלום\r\nline\n"}, "שלום\r\nline\n".encode().hex()),
        ({"base64": "/wANCoA="}, "ff000d0a80"),
    ],
)
def test_stdin_bytes_and_eof_reach_real_cli(tmp_path, stdin, expected):
    """Catch encoding/newline drift or missing EOF; prior CLI scenarios only use argv/files."""
    (tmp_path / "tally.py").write_text(
        "import json,sys\nprint(json.dumps(sys.stdin.buffer.read().hex()))\n"
    )
    document = scenario()
    item = document["scenarios"][0]
    item["setup"] = {}
    if stdin is not None:
        item["actions"][0]["cli"]["stdin"] = stdin
    item["observe"] = [{"action": "add", "exitEquals": 0, "stdoutJsonEquals": expected}]
    before = json.dumps(document)
    result = run(document, tmp_path, TARGETS, "validation")[0]
    assert result["state"] == "PASS", result
    assert result["id"] == item["id"]
    assert json.dumps(document) == before
    assert result["cleanup"]["quiescenceVerified"]
    assert result["cleanup"]["activeProcessesAfterTeardown"] == 0


def test_stdin_write_drains_output_before_waiting_for_reader(tmp_path):
    """Catch duplex pipe deadlock; small byte probes never fill either Windows pipe."""
    (tmp_path / "tally.py").write_text(
        "import sys\nsys.stdout.write('x'*12000);sys.stdout.flush()\n"
        "data=sys.stdin.buffer.read();print(len(data), data.count(b'z'))\n"
    )
    document = scenario()
    item = document["scenarios"][0]
    item["actions"][0]["cli"]["stdin"] = {"utf8": "z" * 48000}
    item["observe"] = [
        {"action": "add", "exitEquals": 0, "stdoutEquals": "x" * 12000 + "48000 48000\n"}
    ]
    result = run(document, tmp_path, TARGETS, "validation")[0]
    assert result["state"] == "PASS", result
    assert result["cleanup"]["quiescenceVerified"]
    assert result["cleanup"]["activeProcessesAfterTeardown"] == 0


@pytest.mark.parametrize("relative", [False, None, 0, 1, "true", [], {}])
def test_invalid_relative_flag_is_rejected_before_launch(tmp_path, relative):
    """Catch truthiness accepting non-true flags; existing negatives lack relative argv."""
    (tmp_path / "tally.py").write_text("raise AssertionError('unexpected launch')")
    document = scenario()
    document["scenarios"][0]["actions"][0]["cli"]["argv"] = [
        {"path": "data/ledger.json", "relative": relative}
    ]
    result = run(document, tmp_path, TARGETS, "validation")[0]
    assert result["state"] == "ERROR" and "relative must be true" in result["reason"]
    assert not result.get("cleanup")


@pytest.mark.parametrize(
    "binding",
    [
        {"module": "missing"},
        {"module": "probe", "entry": "tally.py"},
        {},
        {"module": "../outside"},
        {"module": "C:/outside"},
        {"module": "probe..sub"},
        {"module": "probe/sub"},
        {"module": 1},
        {"module": "unittest"},
        {"module": "namespace"},
    ],
)
def test_invalid_module_bindings_are_errors_before_launch(tmp_path, binding):
    """Catch missing/unsafe module admission; existing binding negatives use entry only."""
    marker = tmp_path / "launched"
    (tmp_path / "tally.py").write_text(f"open({str(marker)!r},'w').write('unexpected')")
    for name in ("unittest", "namespace"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "__main__.py").write_text("print('unexpected')")
    result = run(scenario(), tmp_path, {"tally": dict(binding, argv=[])}, "validation")[0]
    (tmp_path / "module-observation.json").write_text(json.dumps(result))
    assert result["state"] == "ERROR", result
    assert not result.get("cleanup") and not marker.exists()
    if binding == {"module": "missing"}:
        assert "module" in result["reason"] and "__main__.py" in result["reason"]


GRAMMAR = """import argparse, sys
p=argparse.ArgumentParser(); p.add_argument('--db')
p.add_argument('operation', choices=['add']); p.add_argument('category')
p.add_argument('amount'); a=p.parse_args()
"""
STUB = GRAMMAR + "print('SELDON-STUB '+a.operation, file=sys.stderr); sys.exit(3)\n"
PRODUCT = GRAMMAR + "print('amount must be positive', file=sys.stderr); sys.exit(2)\n"


@pytest.mark.parametrize("broken,state", [(True, "FAIL"), (False, "PASS")])
def test_module_uses_owned_root_over_regular_caller_package(tmp_path, broken, state):
    """Catch cwd shadowing a validated package; namespace checks miss two regular packages."""
    for name in ("probe", "data/caller/probe"):
        package = tmp_path / name
        package.mkdir(parents=True)
        (package / "__init__.py").touch()
        source = (
            "raise RuntimeError('root package broken')\n" if broken and name == "probe" else PRODUCT
        )
        (package / "__main__.py").write_text(source, encoding="utf-8")
    document = scenario()
    item = document["scenarios"][0]
    item["setup"]["dirs"] = ["data/caller"]
    item["actions"][0]["cwd"] = "data/caller"
    result = run(document, tmp_path, {"tally": {"module": "probe", "argv": []}}, "validation")[0]
    (tmp_path / "caller-shadow-observation.json").write_text(json.dumps(result))
    assert result["state"] == state, result
    assert result["cleanup"]["quiescenceVerified"]


@pytest.mark.parametrize(
    "dirs", ["data", [None], ["../escape"], [".config"], ["data/helper.py"], ["data/alias."]]
)
def test_unsafe_setup_dirs_are_refused_before_product_action(tmp_path, dirs):
    """Catch dirs bypassing data-only preflight; existing escape tests only supply files/argv."""
    marker = tmp_path / "launched"
    (tmp_path / "tally.py").write_text(
        f"open({str(marker)!r},'w').write('unexpected')", encoding="utf-8"
    )
    document = scenario()
    document["scenarios"][0]["setup"]["dirs"] = dirs
    result = run(document, tmp_path, TARGETS, "validation")
    assert result[0]["state"] == "ERROR", result
    assert not result[0].get("cleanup")  # Preflight rejects before held payload release.
    assert not marker.exists()


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
    """Catch unread input masking attributable RED; previous stub proof supplied no pipe."""
    product = tmp_path
    entry = product / "tally.py"
    entry.write_text(STUB)
    document = scenario()
    document["scenarios"][0]["actions"][0]["cli"]["stdin"] = {"utf8": "x" * 48000}
    red = run(document, product, TARGETS, "contracts")
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
    """Catch blocked stdin escaping timeout/cleanup; previous timeout supplied no input."""
    (tmp_path / "tally.py").write_text(source)
    document = scenario()
    if reason == "timeout":
        document["scenarios"][0]["actions"][0]["cli"]["stdin"] = {"utf8": "x" * 20000}
    document["scenarios"].append(dict(document["scenarios"][0], id="unperformed"))
    results = run(document, tmp_path, TARGETS, "contracts", action_seconds=0.5)
    assert [r["state"] for r in results] == ["ERROR", "ERROR"], results
    assert reason in results[0]["reason"].lower(), results
    assert results[1]["reason"] == "unperformed after earlier ERROR"
    assert results[0]["cleanup"]["quiescenceVerified"]
    assert results[0]["cleanup"]["activeProcessesAfterTeardown"] == 0


@pytest.mark.parametrize(
    "keys,value",
    [
        (("actions", 1), {"id": "later", "http": {}}),
        (("actions", 1), {"id": "later", "file": {}}),
        (("setup", "files", 0, "path"), "../outside"),
        (("actions", 0, "cli", "argv"), [{"path": "../outside"}]),
        (("actions", 0, "cli", "argv"), [{"path": "../outside", "relative": True}]),
        (("actions", 0, "cli", "target"), "missing"),
        *[
            pytest.param(("actions", 0, "cli", "stdin"), value, id=f"stdin-{name}")
            for name, value in [
                ("null", None),
                ("scalar", "text"),
                ("array", []),
                ("neither", {}),
                ("both", {"utf8": "", "base64": ""}),
                ("unknown", {"utf8": "", "extra": ""}),
                ("nonstring", {"utf8": 1}),
                ("invalid-base64", {"base64": "!"}),
                ("base64-whitespace", {"base64": "/w==\n"}),
                ("base64-padding", {"base64": "/w"}),
            ]
        ],
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
    """Catch unsafe setup and malformed stdin prelaunch; prior negatives omit input encoding."""
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
    assert not result[0].get("cleanup")


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


@pytest.mark.parametrize(
    "fixture,cwd",
    [
        ({"base64": "!invalid"}, "data"),
        ({"base64": "/w==", "utf8": "x"}, "data"),
        ({}, "data"),
        ({"base64": 3}, "data"),
        ({"base64": "/w==", "path": "data/source.PY"}, "data"),
        ({"base64": "/w==", "path": "data/source.py. "}, "data"),
        ({"base64": "/w=="}, "../escape"),
        ({"base64": "/w=="}, "data/missing"),
        ({"base64": "/w=="}, "data/ledger.json"),
    ],
)
def test_byte_fixture_and_cwd_errors_prevent_product_launch(tmp_path, fixture, cwd):
    """Catch new vocabulary bypassing preflight/source guards; old negatives use utf8/root cwd."""
    marker = tmp_path / "launched"
    (tmp_path / "tally.py").write_text(
        f"open({str(marker)!r},'w').write('unexpected')", encoding="utf-8"
    )
    document = scenario()
    item = document["scenarios"][0]
    item["setup"]["files"] = [dict({"path": "data/ledger.json"}, **fixture)]
    item["actions"][0]["cwd"] = cwd
    result = run(document, tmp_path, TARGETS, "contracts")
    assert result[0]["state"] == "ERROR", result
    assert not result[0].get("cleanup")
    assert not marker.exists()


@pytest.mark.parametrize("ending,state", [(b"\r\n", "PASS"), (b"", "FAIL")])
def test_stderr_equals_normalizes_actual_crlf_without_inventing_newline(tmp_path, ending, state):
    """Catch raw stderr equality rejecting Windows text; stdout newline cases miss stderrEquals."""
    (tmp_path / "tally.py").write_text(
        f"import sys\nsys.stderr.buffer.write({b'bad input' + ending!r})\n", encoding="utf-8"
    )
    document = scenario()
    observation = {"action": "add", "stderrEquals": "bad input\n"}
    document["scenarios"][0]["observe"] = [observation]
    result = run(document, tmp_path, TARGETS, "validation")[0]
    assert result["state"] == state, result
    assert result["actions"]["add"]["stderr"] == "bad input" + ending.decode()
    assert observation["stderrEquals"] == "bad input\n"
    assert result["cleanup"]["quiescenceVerified"]


@pytest.mark.parametrize("change", ["overflow", "junction"])
def test_unobservable_directory_snapshot_is_error_not_contracts_red(tmp_path, change):
    """Catch incomplete tree credit for overflow/junctions; old snapshots only read files."""
    source = (
        "for i in range(129): Path('data',str(i)).touch()"
        if change == "overflow"
        else "import subprocess\nPath('target').mkdir()\n"
        "subprocess.run(['cmd','/c','mklink','/J',str(Path('data/alias').resolve()),"
        "str(Path('target').resolve())],check=True)"
    )
    (tmp_path / "tally.py").write_text(
        "from pathlib import Path\nimport sys\n" + source + "\n"
        "print('SELDON-STUB add',file=sys.stderr);sys.exit(3)\n",
        encoding="utf-8",
    )
    document = scenario()
    document["scenarios"][0]["setup"]["snapshots"] = [dict(id="before", path="data")]
    document["scenarios"][0]["observe"] = [dict(path="data", bytesUnchangedFrom="before")]
    result = run(document, tmp_path, TARGETS, "contracts")[0]
    assert result["state"] == "ERROR" and "snapshot" in result["reason"], result
    assert result["cleanup"]["quiescenceVerified"]
