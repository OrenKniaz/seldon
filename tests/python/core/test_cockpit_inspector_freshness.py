"""Canonical changes reach a retained inspector through the shipped GET/load/DOM path."""

import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from tests.python.core.test_cockpit_projection import tree
from tests.python.core.test_roundtable import FIXTURE

from codex_harness.core import status_api
from codex_harness.core.plan_store import PlanStore
from codex_harness.core.roundtable import findings, run_roundtable


@pytest.fixture
def snapshots(tmp_path):
    workspace = tmp_path / "inspector"
    workspace.mkdir()
    shutil.copytree(FIXTURE.parent / "template", workspace / "template")
    with PlanStore(workspace) as store:
        store.import_spine(FIXTURE)
        question = store.ask("Which timezone?", ["R1"])
        # Use the real question identity for the existing byId recommendation lookup.
        store.upsert_node(
            dict(
                id=question,
                kind="decision",
                title="Timezone",
                text="Local date",
                parents=["V1"],
                fields={"recommendation": "Use local timezone"},
            )
        )
        store.compile_template(workspace / "template")
        run_roundtable(
            store,
            reviewers=[dict(name="Source Reviewer", provider="codex")],
            executable_for=lambda provider: provider,
            runner=lambda *_: (
                0,
                json.dumps(
                    [
                        dict(
                            node_id="R1",
                            severity="minor",
                            finding="Date boundary",
                            suggestion="Specify it",
                        )
                    ]
                ),
            ),
        )
        before = status_api.snapshot(workspace=workspace)
        member = findings(store, expand=True)[0]
        store.resolve_findings([dict(id=member["id"], status="resolved", resolution="Fixed")])
        resolved = status_api.snapshot(workspace=workspace)
        node = store.spine().node(question)
        node["fields"]["recommendation"] = "Use UTC timezone"
        store.upsert_node(node)
        store.compile_template(workspace / "template")
        revised = status_api.snapshot(workspace=workspace)
        store.answer(question, "UTC")
        answered = status_api.snapshot(workspace=workspace)
    return workspace, dict(before=before, resolved=resolved, revised=revised, answered=answered)


def journey(samples, actions):
    """DOM/fetch primitives only; actual load, normalize, render and dialog callbacks run."""
    script = r"""
const fs=require('fs'),vm=require('vm'),input=JSON.parse(fs.readFileSync(0,'utf8'));
const roots=new Map(),requests=[],writes=[],records=[];
let queue=[],active=null,savedCopy=null;
class Element {
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};
  this.attrs={};this.dataset={};this.style={};this.ownText='';this.open=false;this.scrollTop=0;
  this.classList={add:()=>{}};}
 set textContent(v){this.ownText=String(v);this.children=[];}
 get textContent(){return this.ownText+this.children.map(c=>c.textContent).join('\n');}
 append(...items){this.children.push(...items.map(c=>c instanceof Element?c:
  Object.assign(new Element('#text'),{textContent:String(c)})));}
 replaceChildren(...items){this.ownText='';this.children=[];this.append(...items);}
 setAttribute(k,v){this.attrs[k]=v;if(k==='id')this.id=v;}
 removeAttribute(k){delete this.attrs[k];}
 addEventListener(k,fn){this.listeners[k]=fn;}
 querySelectorAll(selector){return descendants(this).filter(e=>matches(e,selector));}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 showModal(){this.open=true;}
 close(){this.open=false;}
 focus(){active=this;}
 cloneNode(){return new Element(this.tagName);}
}
function descendants(e){return e.children.flatMap(c=>[c,...descendants(c)]);}
function matches(e,s){return s.startsWith('#')?e.id===s.slice(1):s.startsWith('.')?
 String(e.className||'').split(' ').includes(s.slice(1)):s.startsWith('[')?
 Object.hasOwn(e.attrs,s.slice(1,-1)):e.tagName===s.toUpperCase();}
function select(s){for(const root of roots.values()){
 if(matches(root,s))return root;const found=root.querySelector(s);if(found)return found;}
 if(!roots.has(s))roots.set(s,new Element('div'));return roots.get(s);}
const document={querySelector:select,querySelectorAll:()=>[],createElement:t=>new Element(t),
 createElementNS:(_,t)=>new Element(t),createTextNode:t=>Object.assign(new Element('#text'),
 {textContent:t}),documentElement:new Element('html'),get activeElement(){return active;}};
const context={document,Node:Element,URL,URLSearchParams,location:{search:'',href:'http://local/'},
 history:{replaceState(){}},navigator:{clipboard:{async writeText(t){writes.push(t);}}},
 async fetch(url,options){requests.push({url,method:options.method});
  if(!queue.length)throw new Error('Unexpected request');const next=queue.shift();
  if(next.error)throw new Error(next.error);return {ok:true,json:async()=>next};}};
const source=fs.readFileSync(process.argv[1],'utf8');
vm.runInNewContext(source.slice(0,source.indexOf("\ndocument.querySelectorAll('[data-palette]')"))+
 '\nglobalThis.api={load,decisions,recordedDecisions,recordedReviews,show};})()',context);
const dialog=select('#inspector');let body=null;
const buttons=()=>dialog.querySelectorAll('button');
const button=label=>buttons().find(e=>e.textContent===label);
async function click(e,force=false){if(e&&(force||!e.disabled))await e.listeners.click?.();}
(async()=>{
 for(const action of input.actions){
  if(action.load){queue.push(input.samples[action.load]);await context.api.load();}
  if(action.open){context.api[action.open]();body=dialog.querySelector('.review-member')||
    dialog.querySelector('.decision')||dialog.children[2];
   dialog.scrollTop=77;body?.focus();for(const d of dialog.querySelectorAll('details'))d.open=true;
   savedCopy=button('Copy suggested reply');}
  if(action.copy)await click(savedCopy,action.copy==='force');
  if(action.refresh)await click(button('Show latest received details'));
  if(action.generic)context.api.show('Unrelated detail',new Element('p'));
  if(action.capture)records.push({name:action.capture,text:dialog.textContent,
   sameBody:descendants(dialog).includes(body),sameFocus:active===body,scroll:dialog.scrollTop,
   copyDisabled:savedCopy?.disabled===true,refreshDisabled:
    button('Show latest received details')?.disabled===true,writes:[...writes],
   disclosures:dialog.querySelectorAll('details').every(e=>e.open)});
 }
 console.log(JSON.stringify({records,requests,writes}));
})().catch(e=>{console.error(e);process.exit(1);});
"""
    completed = subprocess.run(
        ["node", "-e", script, str(Path(status_api.__file__).with_name("cockpit.js"))],
        input=json.dumps(dict(samples=samples, actions=actions)),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    result = json.loads(completed.stdout)
    assert all(r == dict(url="/api/status", method="GET") for r in result["requests"])
    return result


def test_review_poll_qualifies_changed_facts_without_replacing_reading_context(snapshots):
    """Catch stale review disposition at an unchanged spine hash; navigation tests reopen it."""
    workspace, samples = snapshots
    assert (
        samples["before"]["plan"]["plan_revision"] == samples["resolved"]["plan"]["plan_revision"]
    )
    before = tree(workspace)
    result = journey(
        samples,
        [
            dict(load="before", open="recordedReviews"),
            dict(load="before", capture="same"),
            dict(load="resolved", capture="changed"),
            dict(refresh=True, capture="refreshed"),
            dict(load="revised", capture="revision"),
        ],
    )
    same, changed, refreshed, revision = result["records"]
    assert "These details changed" in changed["text"]
    assert "Matches latest received snapshot" in same["text"]
    assert "Declared disposition: open" in changed["text"]
    assert "Declared disposition: resolved" in refreshed["text"]
    assert "Matches latest received snapshot" in refreshed["text"]
    assert "These details changed" in revision["text"]
    for row in (same, changed):
        assert row["sameBody"] and row["sameFocus"] and row["scroll"] == 77
        assert row["disclosures"]
    assert not refreshed["sameBody"]
    assert tree(workspace) == before


def test_changed_decision_disables_copy_and_guards_captured_callback(snapshots):
    """Catch stale recommendation copying, including direct old callbacks after refresh."""
    workspace, samples = snapshots
    before = tree(workspace)
    result = journey(
        samples,
        [
            dict(load="before", open="decisions", copy=True),
            dict(load="revised", copy="force", capture="stale"),
            dict(refresh=True, copy="force", capture="latest"),
            dict(load="answered", capture="answered"),
            dict(refresh=True, capture="removed"),
        ],
    )
    stale, latest, answered, removed = result["records"]
    assert stale["copyDisabled"] and stale["sameBody"] and stale["sameFocus"]
    assert "Use local timezone" in stale["text"] and "These details changed" in stale["text"]
    assert "Use UTC timezone" in latest["text"] and "Use local timezone" not in latest["text"]
    assert "These details changed" in answered["text"]
    assert "Which timezone?" not in removed["text"]
    assert result["writes"] == ["Use local timezone"]
    assert tree(workspace) == before


@pytest.mark.parametrize("fault", ["missing", "mismatched", "failed"])
def test_unconfirmable_poll_keeps_details_unknown_and_blocks_copy(snapshots, fault):
    """Catch false-current cached content on GET failure or missing binding, unlike happy polls."""
    workspace, samples = snapshots
    samples = deepcopy(samples)
    samples["unknown"] = deepcopy(samples["before"])
    if fault == "missing":
        samples["unknown"]["plan"].pop("plan_revision")
    elif fault == "mismatched":
        samples["unknown"]["cockpit"]["source"]["planRevision"] = "foreign"
    else:
        samples["unknown"] = {"error": "Source unavailable"}
    before = tree(workspace)
    row = journey(
        samples,
        [
            dict(load="before", open="decisions"),
            dict(load="unknown", copy="force", capture="unknown"),
        ],
    )["records"][0]
    assert "Cannot confirm these details" in row["text"]
    assert "Matches latest received snapshot" not in row["text"]
    assert row["sameBody"] and row["sameFocus"] and row["scroll"] == 77
    assert row["copyDisabled"] and row["refreshDisabled"] and not row["writes"]
    assert tree(workspace) == before


def test_recorded_decision_is_qualified_but_unrelated_dialog_is_not(snapshots):
    """Catch missing history-view binding without adding a global modal framework."""
    workspace, samples = snapshots
    before = tree(workspace)
    changed, unrelated = journey(
        samples,
        [
            dict(load="before", open="recordedDecisions"),
            dict(load="revised", capture="changed"),
            dict(generic=True),
            dict(load="before", capture="unrelated"),
        ],
    )["records"]
    assert "These details changed" in changed["text"]
    assert "Unrelated detail" in unrelated["text"]
    assert "These details changed" not in unrelated["text"]
    assert "Show latest received details" not in unrelated["text"]
    assert tree(workspace) == before


def test_empty_decisions_qualifies_same_revision_rendered_readiness_change(snapshots):
    """Catch stale empty-view readiness; previous tests changed decision items, not this text."""
    from tests.python.core.planning_support import source_only_confirmation, source_only_expansion

    workspace, _ = snapshots
    with PlanStore(workspace) as store:
        source_only_expansion(store)
        source_only_confirmation(store, "align", statement="Approve")
        store.advance("expand", by="planner")
    baseline = status_api.snapshot(workspace=workspace)
    assert baseline["cockpit"]["answers"]["needsYou"] == []
    # Explicit consumer doubles: canonical status does not emit readyForReview.
    # Keep actual canonical empty needs and revisions; vary only optional displayed readiness.
    samples = {name: deepcopy(baseline) for name in ("not_ready", "ready")}
    for name, ready in (("not_ready", False), ("ready", True)):
        samples[name]["cockpit"]["vision"]["acceptance"]["readyForReview"] = ready
    assert (
        samples["not_ready"]["plan"]["plan_revision"] == samples["ready"]["plan"]["plan_revision"]
    )
    before = tree(workspace)
    changed, refreshed = journey(
        samples,
        [
            dict(load="not_ready", open="decisions"),
            dict(load="ready", capture="changed"),
            dict(refresh=True, capture="refreshed"),
        ],
    )["records"]
    assert "These details changed" in changed["text"]
    assert "Acceptance not ready" in changed["text"]
    assert "Ready for your review" not in changed["text"]
    assert changed["sameBody"] and changed["sameFocus"] and changed["scroll"] == 77
    assert "Matches latest received snapshot" in refreshed["text"]
    assert "Ready for your review" in refreshed["text"]
    assert "Acceptance not ready" not in refreshed["text"]
    assert tree(workspace) == before
