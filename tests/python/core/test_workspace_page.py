"""Read-only workspace landmarks and the page's network boundary."""

import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from codex_harness.core import status_server

PAGE = Path(status_server.__file__).with_name("workspace.html")


def test_workspace_header_displays_canonical_proof_level():
    html = PAGE.read_text(encoding="utf-8")
    function = html.split("function header(data){", 1)[1].split("\nfunction renderDefinition", 1)[0]
    script = (
        """
const assert = require('node:assert/strict');
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {
    textContent:'', style:{}, childNodes:[],
    append(text){this.textContent += text;}, replaceChildren(){},
  });
  return elements.get(id);
};
const obj = value => value && typeof value === 'object';
const list = value => Array.isArray(value) ? value : [];
const display = value => String(value ?? 'unknown');
const empty = () => {};
const document = {title:''};
let openPhase = null;
"""
        + "function header(data){"
        + function
        + """
for (const label of ['Prospective plan · tests not oracle-verified', 'Oracle-verified plan']) {
  header({plan:{proofLevelLabel:label, phases:[], phase:'publish'}});
  assert.ok($('mission-eyebrow').textContent.endsWith(label));
}
header({plan:null});
assert.ok($('mission-eyebrow').textContent.endsWith('Tests not audited'));
"""
    )
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


def test_inspector_defaults_to_vision_and_preserves_explicit_selection():
    html = PAGE.read_text(encoding="utf-8")
    function = html.split("function selectedContext(){", 1)[1].split(
        "\nfunction renderInspector", 1
    )[0]
    script = (
        """
const assert = require('node:assert/strict');
let selected = null;
const vision = {id:'V1', kind:'vision', title:'Vision'};
const feature = {id:'F1', kind:'feature', title:'Feature'};
const latest = {plan:{vision},runs:[]};
const traceNodes = () => [vision, feature];
const list = value => Array.isArray(value) ? value : [];
"""
        + "function selectedContext(){"
        + function
        + """
assert.equal(selectedContext(), vision);
selected = 'node:F1';
assert.equal(selectedContext(), feature);
selected = 'node:missing';
assert.equal(selectedContext(), undefined);
selected = null;
latest.plan.vision = null;
assert.equal(selectedContext(), null);
"""
    )
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_workspace_landmarks_and_read_only_controls():
    html = PAGE.read_text(encoding="utf-8")
    parser = PageParser()
    parser.feed(html)
    ids = {attrs["id"] for _, attrs in parser.elements if "id" in attrs}
    assert {
        "rail",
        "mission-header",
        "status-strip",
        "phase-stepper",
        "phase-stepper-row",
        "current-phase",
        "planner-mail",
        "run-mail",
        "acceptance-card",
        "acceptance-status",
        "spine-card",
        "inspector",
        "inbox",
        "workspace-script",
        "copy-status",
        "last-updated",
        "verdict-card",
        "verdict-chips",
        "gantt",
        "efficiency-strip",
        "first-use-transcript",
        "known-gaps-card",
        "phase-popovers",
        "phase-popover",
        "diagram-expand",
        "diagram-overlay",
        "shortcut-sheet",
        *{f"tab-{name}" for name in ("definition", "work", "review", "agents", "evidence")},
    } <= ids
    assert (
        html.index('id="status-strip"')
        < html.index('id="phase-stepper-row"')
        < html.index('<div class="workspace-grid">')
    )
    assert html.index('id="acceptance-card"') < html.index('id="evidence-list"')
    assert "POST" not in html
    assert not any(tag == "form" for tag, _ in parser.elements)
    buttons = [attrs for tag, attrs in parser.elements if tag == "button"]
    assert all(attrs.get("type") == "button" and "formaction" not in attrs for attrs in buttons)
    assert html.count("fetch(") == 1
    assert "fetch('/api/status',{method:'GET'" in html
    scripts = [attrs for tag, attrs in parser.elements if tag == "script" and "src" in attrs]
    assert [attrs["src"] for attrs in scripts] == [
        "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"
    ]
    assert "The board displays evidence; it is not proof or acceptance." in html
    assert "local run · not live acceptance" in html
    assert "setInterval(poll,5000)" in html
    assert "failedPolls>=3" in html
    assert "navigator.clipboard.writeText" in html


def test_workspace_responsive_and_poll_state_hooks():
    html = PAGE.read_text(encoding="utf-8")
    assert "prefers-color-scheme:dark" in html
    assert "max-width:1279px" in html and "max-width:899px" in html
    assert "data-state-key" in html and "data-scroll-key" in html
    assert "if(payload===lastPayload)return" in html
    assert "window.scrollTo" in html and "inspectorY" in html
    assert ":focus-visible" in html
    assert "securityLevel:'strict'" in html
    assert "overlay.id='trace-overlay'" in html
    assert "input.id='workspace-search'" in html
    assert "prefers-reduced-motion:reduce" in html
    assert "themeVariables:" in html
    assert "traceActive=false;drawTrace()" in html
    assert "if(editing)return" in html
    assert "▲ " in html and "▼ " in html
    assert not re.search(r"\b(?:XMLHttpRequest|WebSocket|sendBeacon)\b", html)


def test_deferred_group_and_unverified_approval_wording():
    page = (
        Path(__file__).resolve().parents[3] / "src/codex_harness/core/workspace.html"
    ).read_text(encoding="utf-8")
    assert "['open','deferred','resolved','rejected']" in page
    assert "'findings-deferred'" in page
    assert "Accepted (recorded, origin not verified)" in page
    assert "chip.textContent=accepted?approvalLabel(plan.acceptance)" in page


def test_acceptance_card_reads_defined_scenario_results():
    html = PAGE.read_text(encoding="utf-8")
    assert "scenario.lastResult" in html and "scenario.stepResults" in html
    assert "Planned steps" in html


def test_workspace_current_run_groups_and_totals():
    html = PAGE.read_text(encoding="utf-8")
    assert "Earlier runs (" in html and "From earlier runs (" in html
    assert "group.dataset.stateKey='earlier-runs'" in html
    assert "older.dataset.stateKey='earlier-mail'" in html
    assert "list(runs).filter(run=>run.current)" in html
    assert "id===latest?.currentRunId" in html
    assert "mail?.openCount+' open'" in html
    assert "const agents=list(data.currentAgents)" in html
    assert "renderAgents(data.currentAgents)" in html
    assert "Totals for the current run · all runs: " in html
    assert "'superseded','status pill muted'" in html
