"""Real developer fixtures protect data execution; legacy audits inspect test code."""

import json
import sys
from pathlib import Path

import pytest

from codex_harness.core.semantic_runner import run


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


def test_action_timeout_identifies_stalled_cli_and_preserves_cleanup(tmp_path):
    """Expose the stalled launch for recovery; existing timeouts lose action/argv/cwd/stdin."""
    (tmp_path / "tally.py").write_text("import time\ntime.sleep(30)\n")
    document = scenario()
    item = document["scenarios"][0]
    item["setup"] = {"dirs": ["data/caller"]}
    item["observe"] = [{"action": "add", "exitEquals": 0}]
    item["actions"][0]["cwd"] = "data/caller"
    item["actions"][0]["cli"]["stdin"] = {"utf8": "hello"}
    result = run(document, tmp_path, TARGETS, "validation", action_seconds=1)[0]
    assert result["state"] == "ERROR" and result["reason"] == "Product action timeout"
    stalled = result["action"]
    assert stalled["id"] == "add" and stalled["seconds"] == 1
    assert stalled["command"][:2] == [sys.executable, "-B"]
    assert Path(stalled["command"][2]).name == "tally.py"
    assert stalled["command"][3:] == [
        "--db",
        str(Path(stalled["cwd"]).parents[1] / "data/ledger.json"),
        "add",
        "food",
        "0",
    ]
    assert Path(stalled["cwd"]).parts[-2:] == ("data", "caller")
    assert stalled["stdinBytes"] == 5
    assert result["cleanup"]["quiescenceVerified"]
    assert result["cleanup"]["activeProcessesAfterTeardown"] == 0


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
    assert (
        result[0]["cleanup"]["childLaunched"] is False
    )  # Preflight rejects before held payload release.
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
    assert result[0]["cleanup"]["childLaunched"] is False


@pytest.mark.parametrize(
    "expected,state", [({"a": True, "b": [2]}, "PASS"), ({"a": 1, "b": [2]}, "FAIL")]
)
def test_json_equality_preserves_types_not_layout(tmp_path, expected, state):
    """Catch true==1 and layout-sensitive JSON; plain exit/file-preservation tests miss decoding."""
    (tmp_path / "tally.py").write_text('print(\' { "b" : [2], "a" : true } \')\n')
    document = scenario()
    document["scenarios"][0]["observe"] = [{"action": "add", "stdoutJsonEquals": expected}]
    assert run(document, tmp_path, TARGETS, "validation")[0]["state"] == state


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
    assert result[0]["cleanup"]["childLaunched"] is False
    assert not marker.exists()


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


WEB_TARGET = {"page": {"kind": "web", "entry": "index.html"}}
WEB_PAGE = """<!doctype html><html><meta charset="utf-8"><title>Fixture</title>
<style>body{margin:16px;color:#111;background:white;font:16px Arial}
a,button{color:#111;background:white} :focus{outline:2px solid black}
@media(prefers-reduced-motion:no-preference){h1{animation:fade .1s}}
@keyframes fade{from{opacity:.95}to{opacity:1}}</style>
<h1>Welcome</h1><p>Useful content</p><a href="#details">Details</a>
<button onclick="document.querySelector('#extra').hidden=false">Reveal</button>
<section id="details"><h2>Details section</h2><p id="extra" hidden>Revealed content</p></section>
</html>"""


def web_document():
    from codex_harness.core.semantic_schema import WEB

    actions = [
        {
            "id": f"web-{width}-{motion}",
            "web": {
                "target": "page",
                "operation": "visit",
                "viewport": {"width": width, "height": height},
                "reducedMotion": motion,
                "steps": [
                    {"op": "open"},
                    {"op": "assertVisible", "locator": {"text": "Welcome"}},
                    {"op": "key", "key": "Tab"},
                    {"op": "assertFocus", "locator": {"role": "link", "name": "Details"}},
                    {"op": "key", "key": "Enter"},
                    {"op": "assertFragment", "fragment": "#details"},
                    {"op": "click", "locator": {"selector": "button"}},
                    {"op": "assertVisible", "locator": {"text": "Revealed content"}},
                    {"op": "key", "key": "Escape"},
                ],
            },
        }
        for width, height in [(375, 812), (1440, 900)]
        for motion in ["no-preference", "reduce"]
    ]
    return {
        "schema": "seldon/scenarios-v1",
        "scenarios": [
            {
                "id": "web",
                "requirementRefs": ["R1@1"],
                "contractRefs": ["page@1"],
                "setup": {},
                "actions": actions,
                "observe": [
                    {
                        "action": a["id"],
                        **{
                            k: 0 if k in ("consoleErrors", "externalRequests") else True
                            for k in WEB
                        },
                    }
                    for a in actions
                ],
                "verdict": "all",
            }
        ],
    }


def browser_readiness(tmp_path_factory):
    """Retain one real readiness attempt and native privilege failures without browser proof."""
    root = tmp_path_factory.mktemp("web-browser-readiness")
    (root / "index.html").write_text(WEB_PAGE, encoding="utf-8")
    result = run(web_document(), root, WEB_TARGET, "validation", evidence_dir=root.parent / "pngs")[
        0
    ]
    receipt = root.parent / "web-browser-readiness.json"
    receipt.write_text(json.dumps(result, indent=2), encoding="utf-8")
    reason = result.get("reason", "")
    if (
        result["state"] == "ERROR"
        and "GPU process exited unexpectedly: exit_code=-1073741790" in reason
    ):
        assert result["cleanup"]["quiescenceVerified"]
        assert result["cleanup"]["activeProcessesAfterTeardown"] == 0
        pytest.skip(f"Chromium sandbox child access denied (0xC0000022); actual ERROR: {receipt}")
    assert result["state"] == "PASS", result
    return result


def test_web_real_page_all_gates_input_and_retained_evidence(web_browser_ready):
    """Protect real DOM→input→gates/PNG integration; CLI tests never render a page."""
    import hashlib

    result = web_browser_ready
    assert result["state"] == "PASS", result
    assert result["cleanup"]["membershipVerifiedBeforePayload"]
    assert result["cleanup"]["quiescenceVerified"]
    for action in result["actions"].values():
        assert action["toolchain"]["playwright"] == "1.59.1"
        assert len(action["toolchain"]["identity"]) == 4
        assert len(action["artifacts"]) == 2
        for artifact in action["artifacts"]:
            file = Path(artifact["path"])
            assert (
                file.is_file()
                and hashlib.sha256(file.read_bytes()).hexdigest() == artifact["sha256"]
            )


@pytest.mark.parametrize(
    "broken,check",
    [
        ('<div style="width:2000px">Wide</div>', "noHorizontalOverflow"),
        ('<p style="color:#aaa">Low contrast</p>', "textContrastAA"),
        ('<script>fetch("https://example.invalid/x").catch(()=>{})</script>', "externalRequests"),
        ('<script>new WebSocket("wss://example.invalid/socket")</script>', "externalRequests"),
        ('<script>console.error("Broken")</script>', "consoleErrors"),
        ('<script>throw Error("Broken script")</script>', "consoleErrors"),
        ("<h1>Second main heading</h1>", "headingStructure"),
        ("<h4>Skipped heading</h4>", "headingStructure"),
        (
            "<style>h1{animation:move 10s infinite}"
            "@keyframes move{to{transform:translateX(2px)}}</style>",
            "reducedMotionRespected",
        ),
        (
            "<script>document.querySelector('h1').animate([{opacity:1},{opacity:.9}],"
            "{duration:10000,iterations:Infinity})</script>",
            "reducedMotionRespected",
        ),
        (
            "<style>button{transition:transform 8s}</style><script>"
            "document.querySelector('button').addEventListener('click',e=>"
            "e.target.style.transform='translateX(2px)')</script>",
            "reducedMotionRespected",
        ),
        (
            "<script>document.querySelector('button').addEventListener('click',()=>"
            "document.querySelector('#extra').style.color='#aaa')</script>",
            "textContrastAA",
        ),
        ("missing", "assertVisible"),
        (
            '<p style="background:rgba(0,0,0,.9);color:rgba(255,255,255,.1)">Alpha</p>',
            "textContrastAA",
        ),
    ],
)
def test_web_broken_rendering_fails_owning_check(tmp_path, broken, check, web_browser_ready):
    """Protect browser gate attribution for real defects; good-page/CLI probes miss failures."""
    page = WEB_PAGE.replace("Welcome", "Missing") if broken == "missing" else WEB_PAGE + broken
    (tmp_path / "index.html").write_text(page, encoding="utf-8")
    result = run(web_document(), tmp_path, WEB_TARGET, "validation")[0]
    assert result["state"] == "FAIL", result
    assert any(o["check"] == check and not o["passed"] for o in result["observations"]), result
    assert result["cleanup"]["quiescenceVerified"]


def test_web_observed_stub_red_unperformed_interaction_and_validation_rejection(
    tmp_path, web_browser_ready
):
    """Protect attributable browser RED and marker refusal; CLI stderr/exit-3 cannot prove DOM."""
    (tmp_path / "index.html").write_text("<h1>SELDON-STUB visit</h1>", encoding="utf-8")
    result = run(web_document(), tmp_path, WEB_TARGET, "contracts")[0]
    assert result["state"] == "FAIL", result
    assert len(result["stubActions"]) == 4
    assert all(
        [s["check"] for s in a["steps"]] == ["open", "assertVisible"]
        for a in result["actions"].values()
    )
    assert run(web_document(), tmp_path, WEB_TARGET, "validation")[0]["state"] == "ERROR"
    (tmp_path / "index.html").write_text(WEB_PAGE, encoding="utf-8")
    result = run(web_document(), tmp_path, WEB_TARGET, "contracts")[0]
    assert result["state"] == "ERROR" and "Web RED requires" in result["reason"], result


@pytest.mark.parametrize(
    "source,reason",
    [
        ('<canvas width="10" height="10"></canvas>', "Uninspectable"),
        (
            "<script>requestAnimationFrame(function tick(){requestAnimationFrame(tick)})</script>",
            "Uninspectable",
        ),
    ],
)
def test_web_unobservable_rendering_is_error(tmp_path, source, reason, web_browser_ready):
    """Reject unknown motion; measurable gate failures miss uninspectable rendering."""
    (tmp_path / "index.html").write_text(WEB_PAGE + source, encoding="utf-8")
    result = run(web_document(), tmp_path, WEB_TARGET, "validation")[0]
    assert result["state"] == "ERROR" and reason in result["reason"], result
    assert result["cleanup"]["quiescenceVerified"]


def test_web_unknown_contrast_advises_without_masking_measured_failure(tmp_path, web_browser_ready):
    """Keep decorative/unknown contrast deliverable but catch AA defects alongside it;
    existing gate tests have no generated content or advisory-to-verdict integration.
    """
    fixture = (
        WEB_PAGE
        + """<style>
    .decoration::after{content:'+';color:white}
    .hidden::before{content:'Hidden label';color:white}
    .label::before{content:'Readable label';color:#111}
    #unknown::after{content:'Generated label';background:linear-gradient(white,black)}
    #covered{background:white;color:#111}
    </style><span class="decoration"></span><span class="hidden" aria-hidden="true"></span>
    <span class="label"></span><span id="unknown"></span>
    <div style="background:linear-gradient(white,black)">
      <p id="covered">Opaque background resolves through transparent child <em>text</em></p>
    </div><p id="body-copy" style="color:#111">Body copy</p>"""
    )
    for color, state in [("#111", "PASS"), ("#aaa", "FAIL")]:
        (tmp_path / "index.html").write_text(
            fixture.replace(
                'id="body-copy" style="color:#111"', f'id="body-copy" style="color:{color}"'
            ),
            encoding="utf-8",
        )
        result = run(web_document(), tmp_path, WEB_TARGET, "validation")[0]
        assert result["state"] == state, result
        for action in result["actions"].values():
            assert action["advisories"], action
            assert all(note["severity"] == "ADVISORY" for note in action["advisories"])
            elements = {note["element"] for note in action["advisories"]}
            # Unmeasurable text is advised; decorative, hidden and opaque-backed text is not.
            assert "span#unknown::after" in elements
            assert not elements & {"span.decoration::after", "span.hidden::before", "p#covered"}
            assert action["checks"]["textContrastAA"] == (state == "PASS")
        assert result["cleanup"]["quiescenceVerified"]


def test_web_missing_tool_is_prelaunch_error(tmp_path, monkeypatch):
    """Reject missing tools even with a short remaining deadline; CLI budgets miss web clamping."""
    (tmp_path / "index.html").write_text(WEB_PAGE, encoding="utf-8")
    monkeypatch.setenv("SELDON_WEB_NODE", str(tmp_path / "absent-node.exe"))
    result = run(web_document(), tmp_path, WEB_TARGET, "contracts", run_seconds=1)[0]
    assert result["state"] == "ERROR" and "Web tool missing: node" in result["reason"], result
    assert result["cleanup"]["childLaunched"] is False


def test_web_hung_renderer_timeout_reaps_browser_children(tmp_path, web_browser_ready):
    """Protect real hung-renderer teardown; CLI timeout never starts Chromium descendants."""
    (tmp_path / "index.html").write_text(
        WEB_PAGE + "<script>while(true){}</script>", encoding="utf-8"
    )
    result = run(
        web_document(),
        tmp_path,
        WEB_TARGET,
        "contracts",
        action_seconds=2,
        scenario_seconds=5,
        run_seconds=8,
    )[0]
    assert result["state"] == "ERROR" and "timeout" in result["reason"].lower(), result
    assert result["cleanup"]["membershipVerifiedBeforePayload"]
    assert result["cleanup"]["activeProcessesBeforeTeardown"] > 0
    assert result["cleanup"]["quiescenceVerified"]
    assert result["cleanup"]["activeProcessesAfterTeardown"] == 0


@pytest.mark.parametrize(
    "invalid", ["eval", "url", "flags", "active-setup", "coverage", "gate", "ambiguous-action"]
)
def test_web_closed_data_refuses_executable_or_incomplete_journeys(tmp_path, invalid):
    """Reject executable/incomplete web data before launch; CLI admission has no web vocabulary."""
    (tmp_path / "index.html").write_text(WEB_PAGE, encoding="utf-8")
    document = web_document()
    item = document["scenarios"][0]
    journey = item["actions"][0]["web"]
    if invalid == "eval":
        journey["steps"].append({"op": "eval", "script": "alert(1)"})
    elif invalid == "url":
        journey["steps"][0]["url"] = "https://example.invalid"
    elif invalid == "flags":
        journey["launchFlags"] = ["--no-sandbox"]
    elif invalid == "active-setup":
        item["setup"] = {"files": [{"path": "data/payload.txt", "utf8": "alert(1)"}]}
    elif invalid == "coverage":
        item["actions"].pop()
        item["observe"].pop()
    elif invalid == "gate":
        del item["observe"][0]["textContrastAA"]
    else:
        item["actions"][0]["cli"] = {"target": "page", "operation": "visit", "argv": []}
    result = run(document, tmp_path, WEB_TARGET, "validation")[0]
    assert result["state"] == "ERROR", result
    assert result["cleanup"]["childLaunched"] is False
