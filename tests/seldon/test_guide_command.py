"""Fresh-checkout walkthrough from a documented PowerShell command to PRODUCT."""

import json
import shutil

import pytest

from seldon import features, gitwork, plan
from test_journey import finish, prepare


GUIDE = """# Read the saved value

From the checkout root in PowerShell, run `./docs/try-it/read.ps1`.
It should print `value-001`. The optional browser check requires Node 20
at the approved local path `./tools/node20/node.exe`.
"""

READ = """$settings = 'unit-001/value.txt'
Get-Content -LiteralPath $settings -ErrorAction Stop
"""

CHECK = """$ErrorActionPreference = 'Stop'
$value = & ./docs/try-it/read.ps1
if ($value -ne 'value-001') { throw "Unexpected value: $value" }
$rows = @(@{
    operation = 'Read the saved value'
    documentation = 'docs/try-it.md#read-the-saved-value'
    command = './docs/try-it/read.ps1'
    observed = $true
    after = $value
})
$approved = './tools/node20/node.exe'
if (-not (Test-Path -LiteralPath $approved)) {
    $rows += @{
        operation = 'Optional browser check'
        observed = $false
        reason = "Approved Node 20 is unavailable at $approved"
    }
}
$rows | ConvertTo-Json -AsArray | Set-Content -Encoding utf8NoBOM (
    Join-Path $env:SELDON_EVIDENCE_DIR 'operation-lifecycle.json')
"""


@pytest.mark.fast
@pytest.mark.parametrize("folded", [False, True], ids=["separate-guide", "sole-owner"])
def test_documented_powershell_command_reaches_guide_judgment(tmp_path, scalar_align, monkeypatch, folded):
    """Catch an opaque worker directory and missing runtime coaching at the guide boundary.

    The existing server journey checks separate browser evidence. This journey runs the
    same shell script the reader is told to run, with a second observation left unverified.
    """
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is required to exercise the documented command")
    monkeypatch.setattr(features, "planning_setup", lambda: {"sizeFirst": folded, "split": "auto"})
    slot = tmp_path / "guide"
    run, provider = prepare(slot)
    native = slot / "native.py"
    source = native.read_text(encoding="utf-8")
    start = source.index("    command = ", source.index("elif name == 'docs':"))
    end = source.index("\nelse:", start)
    source = source[:start] + "    pass" + source[end:]
    setup = f"""\
if 'docs/try-it.md' in facts['Write scope']:
    Path('docs/try-it').mkdir(parents=True, exist_ok=True)
    Path('docs/try-it.md').write_text({GUIDE!r}, encoding='utf-8')
    Path('docs/try-it/read.ps1').write_text({READ!r}, encoding='utf-8')
    Path('docs/try-it/check.ps1').write_text({CHECK!r}, encoding='utf-8')
    answer['checkPaths'] += ['docs/try-it/check.ps1', 'docs/try-it/read.ps1']
    answer['checks'].append({{'purpose': 'Read the documented value',
        'command': [{pwsh!r}, '-NoProfile', '-File', 'docs/try-it/check.ps1'],
        'expectedEvidence': 'Read value-001 and report the missing approved runtime'}})
"""
    before = source.index("if 'Existing checks' in facts")
    native.write_text(source[:before] + setup + source[before:], encoding="utf-8")
    original = provider.invoke
    seen = []

    def invoke(question, started):
        facts = question["facts"]
        if folded and question["kind"] == "JUDGE" and "Walkthrough checks" in facts:
            check, = facts["Walkthrough checks"]
            observation, = (row for row in facts["Observations"]
                            if row["check"] == check and row["purpose"] == "final")
            execution = observation
            checkout = observation["workingDirectory"]
            guide = facts["Product files"][plan.GUIDE_PATH]
        elif not folded and question["kind"] == "PRODUCT" and question["targets"] == ["tryItGuide"]:
            observation, = facts["Final observations"]
            execution = observation
            checkout = observation["workingDirectory"]
            guide = facts["Guide file"]["text"]
        else:
            return original(question, started)
        assert "clean" in facts["Walkthrough checkout"].lower()
        assert "`./docs/try-it/read.ps1`" in guide
        assert execution["cleanCheckout"] and execution["executionStarted"] and execution["cleanupVerified"]
        assert observation["exitCode"] == 0, observation["stderr"]
        ran, pending = observation["operationLifecycle"]
        assert ran["observed"] and ran["after"] == "value-001"
        assert ran["command"] == "./docs/try-it/read.ps1"
        assert pending["observed"] is False and "Node 20" in pending["reason"]
        seen.append((observation, checkout))
        finding = {"verdict": "uncertain", "neededFacts": [pending["reason"]], "why": pending["reason"]}
        if folded:
            answer = question["examples"][0]["answer"]
            answer["findings"]["tryItGuide"] = finding
        else:
            answer = finding
        return json.dumps({"answer": answer}), None

    provider.invoke = invoke
    try:
        cursor, _, _ = finish(run)
        (observation, checkout), = seen
        assert observation["commit"] == cursor.candidate
        assert checkout != str(run.project)
        assert gitwork.git(checkout, "rev-parse", "HEAD") == cursor.candidate
        context = "unit-001" if folded else "docs"
        brief = json.loads((slot / f"context-{context}.json").read_text(encoding="utf-8"))
        guidance = brief["Try-it guide"] if folded else brief["Unit"]
        for detail in ("same commands", "shell", "exact runtime", "observed=false"):
            assert detail in guidance
        assert run.store.records("outcome")[-1][2]["verification_complete"] is False
        assert plan.guide_checks(plan.execution(run.store)).keys() == {observation["check"]}
    finally:
        run.close()
