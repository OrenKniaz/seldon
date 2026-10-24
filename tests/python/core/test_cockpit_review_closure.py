"""Canonical closure declarations stay distinct from proof in the shipped member DOM."""

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
def recorded_closures(tmp_path):
    workspace = tmp_path / "closure-fixture"
    workspace.mkdir()
    shutil.copytree(FIXTURE.parent / "template", workspace / "template")
    reviewers = [
        dict(name="Source Alpha", provider="codex"),
        dict(name="Source Beta", provider="claude"),
    ]
    with PlanStore(workspace) as store:
        store.import_spine(FIXTURE)
        store.compile_template(workspace / "template")
        run_roundtable(
            store,
            reviewers=reviewers,
            runner=lambda *_: (
                0,
                json.dumps(
                    [
                        dict(
                            node_id="R3",
                            severity="minor",
                            finding="Historical point",
                            suggestion="Inspect",
                        )
                    ]
                ),
            ),
            executable_for=lambda provider: provider,
        )
        node = store.spine().node("R1")
        node["title"] += " current"
        store.upsert_node(node)
        store.compile_template(workspace / "template")
        store.record_plan_review_policy(
            [dict(name=r["name"], provider=r["provider"], required=True) for r in reviewers],
            store.manifest_revision(),
        )

        def response(launch, *_):
            rows = [
                dict(
                    node_id="R1", severity="major", finding="Shared repair", suggestion="Repair it"
                )
            ]
            rows.append(
                dict(
                    node_id="R2" if launch.cli == "codex" else "R3",
                    severity="minor",
                    finding="Deferred concern" if launch.cli == "codex" else "Open concern",
                    suggestion="Retain obligation",
                )
            )
            return 0, json.dumps(rows)

        result = run_roundtable(
            store, reviewers=reviewers, runner=response, executable_for=lambda provider: provider
        )
        current = [
            row for row in findings(store, expand=True) if row["round_id"] == result["roundId"]
        ]
        for node_id, disposition, reason, basis in [
            ("R1", "resolved", "Fixed", "repair"),
            ("R2", "deferred", "Later", "defer"),
        ]:
            members = [row for row in current if row["node_id"] == node_id]
            closure = dict(
                schema="seldon/finding-closure-declaration-v1",
                expectedManifestRevision=store.manifest_revision(),
                memberIds=[row["id"] for row in members],
                basis=basis,
                nodeRefs=[dict(nodeId=node_id, revision=store.spine().revision(node_id))],
                reviewIds=[],
                auditIds=[],
                obligationNodeIds=[node_id],
            )
            store.resolve_findings(
                [dict(id=members[0]["id"], status=disposition, resolution=reason, closure=closure)]
            )
    return workspace


def member_dom(payload):
    """Execute actual normalize + recordedReviews; the shim supplies DOM primitives only."""
    script = r"""
const fs=require('fs'),vm=require('vm');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.open=false;}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 setAttribute(name,value){this[name]=value;}
 addEventListener(){}
 showModal(){this.open=true;}
 close(){this.open=false;}
}
const inspector=new Element('dialog'),preview=new Element('aside');
const context={URLSearchParams,location:{search:''},Node:Element,
 document:{querySelector:key=>key==='#inspector'?inspector:preview,
 createElement:tag=>new Element(tag),
 createTextNode:text=>Object.assign(new Element('#text'),{textContent:text})},
 input:JSON.parse(fs.readFileSync(0,'utf8'))};
const source=fs.readFileSync(process.argv[1],'utf8');
vm.runInNewContext(source.slice(0,source.indexOf('function workScope()'))+
 '\nmodel=normalize(input);recordedReviews();})()',context);
function text(node){
 return [node.textContent,...node.children.map(text)].filter(Boolean).join('\n');
}
const cards=[];
function collect(node){
 if(node.className==='review-member')cards.push(text(node));
 for(const child of node.children)collect(child);
}
collect(inspector);console.log(JSON.stringify({cards,text:text(inspector)}));
"""
    completed = subprocess.run(
        ["node", "-e", script, str(Path(status_api.__file__).with_name("cockpit.js"))],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_canonical_declaration_projects_unverified_obligation_into_actual_member_dom(
    recorded_closures,
):
    """Catch short 'Fixed' reasons hiding unfinished proof; old DOM checks test disposition only."""
    before = tree(recorded_closures)
    payload = status_api.snapshot(workspace=recorded_closures)
    assert all(
        row["closure"] == "unverified" for row in payload["plan"]["reviewPolicy"]["closureFindings"]
    )
    dom = member_dom(payload)
    repaired = [card for card in dom["cards"] if "Shared repair" in card]
    assert len(repaired) == 2
    for card in repaired:
        assert "Declared disposition: resolved" in card
        assert "Reason: Fixed" in card
        assert "Closure verification: unverified" in card
        assert "Declaration binding: current" in card
        assert "Remaining obligation: retained" in card
        assert "Obligation verification: unverified" in card
        assert "targeted-assessment-producer-unavailable" in card
        assert "Recorded obligation references: R1" in card
    deferred = next(card for card in dom["cards"] if "Deferred concern" in card)
    assert "Declared disposition: deferred" in deferred and "Reason: Later" in deferred
    assert "Remaining obligation: retained" in deferred
    assert "Recorded obligation references: R2" in deferred
    assert "Source-only / simulated review" in dom["text"]
    assert "No provider execution recorded" in dom["text"]
    assert "Historical review" in dom["text"] and "Current plan" in dom["text"]
    historical = [card for card in dom["cards"] if "Historical point" in card]
    assert len(historical) == 2
    assert all("Closure verification: unknown" in card for card in historical)
    opened = next(card for card in dom["cards"] if "Open concern" in card)
    assert "Declared disposition: open" in opened
    assert "Closure verification: unverified" in opened
    assert "Remaining obligation: retained" in opened
    assert "Recorded obligation references: unknown" in opened
    assert "Declaration binding: not current or absent" in opened
    assert tree(recorded_closures) == before


@pytest.mark.parametrize(
    "fault", ["missing", "member", "node", "revision", "disposition", "binding", "conflict"]
)
def test_unbound_closure_facts_stay_unknown_at_visible_consumer(
    recorded_closures, monkeypatch, fault
):
    """Catch borrowed or ambiguous proof; canonical happy-path facts all have exact bindings."""
    original = PlanStore.plan_review_policy_state

    def altered_read_projection(store, revision):
        policy = deepcopy(original(store, revision))
        # Explicit read-input fault only: canonical records and approvals are never edited.
        rows = policy.get("closureFindings", [])
        targets = [row for row in rows if row["nodeId"] == "R1"]
        for row in targets:
            if fault == "member":
                row["id"] = "unrelated-" + row["id"]
            elif fault == "node":
                row["nodeId"] = "R3"
            elif fault == "revision":
                row["recordedRevision"] = "wrong-revision"
            elif fault == "disposition":
                row["disposition"] = "rejected"
            elif fault == "binding":
                row["declarationBindingCurrent"] = False
            elif fault == "conflict":
                rows.append(dict(row, closure="conflicting-value"))
        if fault == "missing":
            policy["closureFindings"] = []
        return policy

    before = tree(recorded_closures)
    monkeypatch.setattr(PlanStore, "plan_review_policy_state", altered_read_projection)
    dom = member_dom(status_api.snapshot(workspace=recorded_closures))
    repaired = [card for card in dom["cards"] if "Shared repair" in card]
    assert len(repaired) == 2
    for card in repaired:
        assert "Reason: Fixed" in card
        assert "Closure verification: unknown" in card
        assert "Declaration binding: unknown" in card
        assert "Remaining obligation: unknown" in card
    assert tree(recorded_closures) == before


def test_real_plan_revision_change_does_not_reuse_closure_verification(recorded_closures):
    """Catch stale canonical declarations, which malformed-input checks do not reach."""
    with PlanStore(recorded_closures) as store:
        node = store.spine().node("R1")
        node["title"] += " changed again"
        store.upsert_node(node)
        store.compile_template(recorded_closures / "template")
    before = tree(recorded_closures)
    dom = member_dom(status_api.snapshot(workspace=recorded_closures))
    repaired = [card for card in dom["cards"] if "Shared repair" in card]
    assert len(repaired) == 2
    for card in repaired:
        assert "Closure verification: unknown" in card
        assert "Remaining obligation: unknown" in card
    assert tree(recorded_closures) == before
