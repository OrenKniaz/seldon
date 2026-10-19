"""Cockpit routing preserves the existing read-only status producer."""

import json
import subprocess
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

import pytest

from codex_harness.core import status_server
from codex_harness.core.plan_store import PlanStore


@contextmanager
def running(tmp_path, workspace=None):
    with status_server.create_server(
        workspace=workspace or tmp_path / "missing-workspace",
        runs_root=tmp_path / "missing-runs",
        port=0,
    ) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            yield connection
        finally:
            connection.close()
            server.shutdown()
            thread.join(timeout=5)


def get(connection, path):
    connection.request("GET", path)
    response = connection.getresponse()
    return response.status, dict(response.getheaders()), response.read()


def test_new_root_and_explicit_assets(tmp_path):
    with running(tmp_path) as connection:
        for route in ("/", "/?mode=live", "/?mode=history&project=linkshelf"):
            status, headers, body = get(connection, route)
            assert status == 200
            assert b'id="mission-header"' in body
            assert b'id="spine-card"' in body
            assert b"/cockpit.js" in body
            assert b"/cockpit.css" in body
            assert headers["Cache-Control"] == "no-store"
        for name, mime in (("cockpit.js", "text/javascript"), ("cockpit.css", "text/css")):
            status, headers, body = get(connection, "/" + name)
            assert status == 200
            assert headers["Content-Type"].startswith(mime)
            assert body == Path(status_server.__file__).with_name(name).read_bytes()
            assert headers["X-Content-Type-Options"] == "nosniff"


def test_legacy_and_board_are_byte_preserved(tmp_path):
    with running(tmp_path) as connection:
        for route, name in (("/legacy", "workspace.html"), ("/board", "board.html")):
            status, _, body = get(connection, route)
            assert status == 200
            assert body == Path(status_server.__file__).with_name(name).read_bytes()
        assert get(connection, "/board?unrecognized=1")[0] == 404
        assert get(connection, "/api/status?unrecognized=1")[0] == 404


@pytest.mark.parametrize("name", ["cockpit.css", "cockpit.js"])
def test_cockpit_asset_routes_exist(tmp_path, name):
    with running(tmp_path) as connection:
        assert get(connection, "/" + name)[0] == 200


@pytest.mark.parametrize(
    "route",
    ["/status_server.py", "/../pyproject.toml", "/%2e%2e/README.md", "/fixtures/private.json"],
)
def test_only_named_assets_are_served(tmp_path, route):
    with running(tmp_path) as connection:
        assert get(connection, route)[0] == 404


def test_status_preserves_null_and_hostile_strings_without_mutation(tmp_path, monkeypatch):
    payload = {
        "plan": {"name": '<img src=x onerror="alert(1)">', "spine": []},
        "cockpit": {"answers": None, "capabilities": [None], "waves": None},
    }
    before = json.dumps(payload)
    monkeypatch.setattr(status_server, "snapshot", lambda **_: payload)
    with running(tmp_path) as connection:
        status, _, body = get(connection, "/api/status")
        assert status == 200
        assert json.loads(body) == payload
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CUSTOM"):
            connection.request(method, "/api/status")
            response = connection.getresponse()
            assert response.status == 405
            response.read()
    assert json.dumps(payload) == before
    assert not list(tmp_path.iterdir())


def test_canonical_missing_snapshot_does_not_create_sources(tmp_path):
    with running(tmp_path) as connection:
        status, _, body = get(connection, "/api/status")
        assert status == 200
        assert json.loads(body)["plan"] == "unavailable"
    assert not list(tmp_path.iterdir())


def test_real_plan_store_to_http_is_read_only(tmp_path):
    workspace = tmp_path / "canonical"
    workspace.mkdir()
    with PlanStore(workspace) as store:
        store.ask("Which first-use promise should we keep?", [])
        expected = store.status()
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    with running(tmp_path, workspace=workspace) as connection:
        status, _, body = get(connection, "/api/status")
        assert status == 200
        plan = json.loads(body)["plan"]
        assert plan["phase"] == expected["phase"]
        assert len(plan["clarifications"]) == expected["open_clarifications"]
        assert "Which first-use promise should we keep?" in json.dumps(plan["clarifications"])
        assert get(connection, "/")[0] == 200
        assert get(connection, "/cockpit.js")[0] == 200
    after = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("name", ["linkshelf.json", "linkshelf-planning.json", "csv2md.json"])
def test_public_history_routes_preserve_fixture_bytes(tmp_path, name):
    source = Path(__file__).parents[2] / "browser" / "cockpit" / "fixtures" / "public" / name
    with running(tmp_path) as connection:
        status, _, body = get(connection, "/fixtures/" + name)
        assert status == 200
        assert body == source.read_bytes()


@pytest.mark.parametrize("projection", ["explicit-null", "absent"])
def test_adapter_excludes_mismatched_run_and_honors_unknowns(projection):
    payload = {
        "plan": {
            "phase": "execute",
            "plan_revision": "P2",
            "published_revision": "P2",
            "spine": [
                {"id": "V2", "kind": "vision", "title": "Current plan"},
                {"id": "F2", "kind": "feature", "title": "Current capability", "parents": ["V2"]},
                {"id": "W2", "kind": "bead", "title": "Current work", "parents": ["F2"]},
            ],
        },
        "currentRunId": "old-P1",
        "runs": [
            {
                "runId": "old-P1",
                "planRevision": "P1",
                "beads": [
                    {"id": "OLD-ORPHAN", "title": "Old blocked orphan", "status": "blocked"},
                    {"id": "W2", "status": "done"},
                ],
            }
        ],
        "currentAgents": [{"provider": "old-provider", "model": "old-model"}],
    }
    if projection == "explicit-null":
        payload["cockpit"] = {"answers": {"blocking": None}, "crew": None}
    result = _adapter(payload)
    assert [w["id"] for w in result["work"]] == ["W2"]
    assert result["work"][0]["category"] == "unknown"
    assert result["blocking"] is None
    assert result["crew"] == []
    assert result["stale"] is True
    assert "old-P1" in result["earlierRuns"]


def test_adapter_run_population_without_spine_has_run_scope_only():
    payload = {
        "plan": {
            "plan_revision": "P2",
            "published_revision": "P2",
            "publication": {"up_to_date": True},
        },
        "currentRunId": "run-P2",
        "runs": [
            {
                "runId": "run-P2",
                "planRevision": "P2",
                "beads": [{"id": "RUN-ONLY", "status": "running"}],
            }
        ],
    }
    result = _adapter(payload)
    assert result["scope"] == "run"
    assert [w["id"] for w in result["work"]] == ["RUN-ONLY"]


def test_adapter_matching_run_keeps_canonical_work_binding():
    payload = {
        "plan": {
            "plan_revision": "P2",
            "published_revision": "P2",
            "publication": {"up_to_date": True},
            "spine": [
                {"id": "V2", "kind": "vision", "title": "Vision"},
                {"id": "W2", "kind": "bead", "title": "Work", "parents": ["V2"]},
            ],
        },
        "currentRunId": "run-P2",
        "runs": [
            {"runId": "run-P2", "planRevision": "P2", "beads": [{"id": "W2", "status": "blocked"}]}
        ],
        "currentAgents": [{"provider": "recorded-provider", "model": "recorded-model"}],
    }
    result = _adapter(payload)
    assert result["work"][0]["category"] == "blocked"
    assert result["blocking"][0]["id"] == "W2"
    assert result["crew"][0]["provider"] == "recorded-provider"
    payload["cockpit"] = {"answers": {"blocking": None}, "crew": None}
    result = _adapter(payload)
    assert result["blocking"] is None
    assert result["crew"] == []


@pytest.mark.parametrize("freshness", ["absent", None, False, "true"])
def test_adapter_missing_freshness_does_not_confirm_matching_old_run(freshness):
    payload = {
        "plan": {
            "plan_revision": "editable-P2",
            "published_revision": "published-P1",
            "spine": [{"id": "W", "kind": "bead", "title": "Current work"}],
        },
        "currentRunId": "old-run",
        "runs": [
            {
                "runId": "old-run",
                "planRevision": "published-P1",
                "beads": [{"id": "W", "status": "requirement-validated"}],
            }
        ],
        "currentAgents": [{"provider": "old-provider", "model": "old-model"}],
    }
    if freshness != "absent":
        payload["plan"]["publication"] = {"up_to_date": freshness}
    result = _adapter(payload)
    assert result["work"][0]["category"] == "unknown"
    assert result["blocking"] is None
    assert result["crew"] == []
    assert result["stale"] is True
    assert result["earlierRuns"] == ["old-run"]


def _linkshelf_history():
    public = Path(__file__).parents[2] / "browser/cockpit/fixtures/public"
    payload = json.loads((public / "linkshelf.json").read_bytes())
    planning = json.loads((public / "linkshelf-planning.json").read_bytes())
    payload["plan"] = {
        **planning["meta"][0],
        "spine": [
            {**n, "parents": json.loads(n["parents_json"]), "fields": json.loads(n["fields_json"])}
            for n in planning["nodes"]
        ],
    }
    return payload


def test_adapter_real_linkshelf_validated_reuse_is_done_work_only():
    result = _adapter(_linkshelf_history(), "history")
    assert len(result["work"]) == 5
    assert all(w["category"] == "done" for w in result["work"])
    assert result["capabilities"] == [
        {"id": "F1", "total": 2, "done": 2},
        {"id": "F2", "total": 3, "done": 3},
        {"id": "F3", "total": 1, "done": 1},
        {"id": "F4", "total": 3, "done": 3},
        {"id": "F5", "total": 1, "done": 1},
    ]


@pytest.mark.parametrize("validation", [None, "failed", "missing"])
def test_adapter_reuse_without_passed_validation_stays_unknown(validation):
    payload = _linkshelf_history()
    bead = next(b for b in payload["runs"][0]["beads"] if b["id"] == "B1")
    if validation == "missing":
        del bead["integratedValidation"]
    else:
        bead["integratedValidation"] = validation
    result = _adapter(payload, "history")
    assert next(w for w in result["work"] if w["id"] == "B1")["category"] == "unknown"


def test_adapter_reuse_without_validated_run_stays_unknown():
    payload = _linkshelf_history()
    payload["runs"][0]["status"] = "running"
    result = _adapter(payload, "history")
    assert next(w for w in result["work"] if w["id"] == "B1")["category"] == "unknown"


@pytest.mark.parametrize(
    "fault", ["sentinel", "numeric", "historical", "unavailable", "foreign-run", "null-binding"]
)
def test_adapter_current_eligibility_rejects_conflicting_identity_and_binding(fault):
    payload = {
        "plan": {
            "plan_revision": "spine-P2",
            "published_revision": "manifest-P2",
            "publication": {"up_to_date": True},
            "spine": [{"id": "W", "kind": "bead", "title": "Canonical work"}],
        },
        "currentRunId": "run-P2",
        "runs": [
            {
                "runId": "run-P2",
                "planRevision": "manifest-P2",
                "beads": [{"id": "W", "status": "done"}],
            }
        ],
        "currentAgents": [{"provider": "old-crew"}],
    }
    if fault in ("sentinel", "numeric"):
        revision = "unavailable" if fault == "sentinel" else 7
        payload["plan"]["published_revision"] = revision
        payload["runs"][0]["planRevision"] = revision
    else:
        binding = (
            None
            if fault == "null-binding"
            else {
                "status": "current" if fault == "foreign-run" else fault,
                "runId": "foreign-run" if fault == "foreign-run" else "run-P2",
            }
        )
        payload["cockpit"] = {"source": {"runBinding": binding}}
    result = _adapter(payload)
    assert result["work"] == [{"id": "W", "category": "unknown"}]
    assert result["blocking"] is None
    assert result["crew"] == []
    assert result["stale"] is True
    assert result["earlierRuns"] == ["run-P2"]
    # The same eligibility decision also gates projected current facts.
    payload.setdefault("cockpit", {}).update(
        {
            "answers": {"blocking": [], "where": {"waveId": "wave-1"}},
            "crew": [{"provider": "projected-old-crew"}],
            "waves": {"status": "live", "items": [{"id": "wave-1", "isDraft": False}]},
        }
    )
    result = _adapter(payload)
    assert result["blocking"] is None
    assert result["crew"] == []
    assert result["waveStatus"] == "unavailable"


def _planning_answers(stale_run):
    payload = {
        "plan": {
            "phase": "align",
            "plan_revision": "P2",
            "publication": {"up_to_date": False},
            "spine": [{"id": "W", "kind": "bead", "title": "Canonical work"}],
        },
        "cockpit": {
            "source": {"planRevision": "P2"},
            "answers": {
                "blocking": [
                    {
                        "id": "PLAN",
                        "plain": "Resolve current planning finding",
                        "refs": ["review-findings:PLAN"],
                    }
                ],
                "needsYou": [
                    {"id": "Q", "question": "Which product promise?", "refs": ["clarifications:Q"]}
                ],
            },
        },
    }
    if stale_run:
        payload.update(
            {
                "currentRunId": "old-run",
                "runs": [
                    {
                        "runId": "old-run",
                        "planRevision": "P1",
                        "beads": [{"id": "W", "status": "done"}],
                    }
                ],
                "currentAgents": [{"provider": "old-crew"}],
            }
        )
        payload["cockpit"]["answers"]["blocking"].append({"id": "OLD", "refs": ["run:old-run"]})
        payload["cockpit"]["answers"]["needsYou"].append({"id": "OLD-Q", "refs": ["run:old-run"]})
    return payload


@pytest.mark.parametrize("stale_run", [False, True])
def test_adapter_current_plan_answers_survive_without_current_runtime(stale_run):
    result = _adapter(_planning_answers(stale_run))
    assert [b["id"] for b in result["blocking"]] == ["PLAN"]
    assert [n["id"] for n in result["needs"]] == ["Q"]
    assert result["crew"] == []
    assert result["work"] == [{"id": "W", "category": "unknown"}]


@pytest.mark.parametrize("stale_run", [False, True])
def test_adapter_conflicting_plan_source_answers_stay_unknown(stale_run):
    payload = _planning_answers(stale_run)
    payload["cockpit"]["source"]["planRevision"] = "foreign-P1"
    result = _adapter(payload)
    assert result["blocking"] is None
    assert result["needs"] is None


@pytest.mark.parametrize("stale_run", [False, True])
def test_adapter_explicit_null_plan_answers_stay_unknown(stale_run):
    payload = _planning_answers(stale_run)
    payload["cockpit"]["answers"] = {"blocking": None, "needsYou": None}
    result = _adapter(payload)
    assert result["blocking"] is None
    assert result["needs"] is None


def _plan_facts_payload():
    # Compact contract shape from DOT's actual canonical draft/scenario snapshots.
    proof = {
        "passed": 4,
        "total": 4,
        "runId": "acceptance-A",
        "subject": "product",
        "buildRevision": "build-A",
        "evidenceRefs": [
            "acceptance_runs:acceptance-A",
            "acceptance_scenarios:X1",
            "acceptance_scenarios:X2",
            "acceptance_scenarios:first-moment",
            "acceptance_scenarios:scope-1",
        ],
    }
    return {
        "plan": {
            "plan_revision": "P2",
            "published_revision": None,
            "publication": {"up_to_date": False},
            "spine": [
                {
                    "id": "V1",
                    "kind": "vision",
                    "title": "Canonical vision",
                    "fields": {"vision": "Canonical promise"},
                },
                {"id": "F1", "kind": "feature", "title": "Canonical F1", "parents": ["V1"]},
                {"id": "F2", "kind": "feature", "title": "Canonical F2", "parents": ["V1"]},
            ],
        },
        "cockpit": {
            "source": {
                "planRevision": "P2",
                "publicationRevision": None,
                "runBinding": {"status": "unavailable", "runId": None},
            },
            "vision": {
                "title": "Projected title",
                "promise": "Projected promise",
                "proof": {"productScenarios": proof, "requirementValidation": None},
            },
            "capabilities": [
                {
                    "id": "F1",
                    "proof": {
                        "productScenarios": {**proof, "passed": 1, "total": 1},
                        "requirementValidation": None,
                    },
                },
                {"id": "F2", "proof": {"productScenarios": None, "requirementValidation": None}},
            ],
            "waves": {
                "status": "draft",
                "sourceRevision": "P2",
                "items": [
                    {"id": "W1", "title": "Outcome 1", "isDraft": True, "phase": "done"},
                    {"id": "W2", "title": "Outcome 2", "isDraft": True},
                ],
            },
        },
    }


def test_adapter_plan_draft_and_scenarios_survive_without_dispatch_run():
    result = _adapter(_plan_facts_payload())
    assert result["waveStatus"] == "draft"
    assert result["waveIds"] == ["W1", "W2"]
    assert result["currentWave"] is None
    assert result["currentRun"] is None
    assert result["globalProof"]["passed"] == result["globalProof"]["total"] == 4
    assert result["capProofs"]["F1"]["passed"] == result["capProofs"]["F1"]["total"] == 1
    assert result["capProofs"]["F2"] is None
    assert all(p is None for p in result["requirementProofs"].values())
    assert result["crew"] == []


@pytest.mark.parametrize("source_revision", ["P2", "foreign-P1"])
def test_adapter_projection_cannot_replace_canonical_topology_or_promise(source_revision):
    payload = _plan_facts_payload()
    payload["cockpit"]["source"]["planRevision"] = source_revision
    payload["cockpit"]["capabilities"][0].update({"title": "Foreign title", "parents": ["FOREIGN"]})
    payload["cockpit"]["capabilities"].append({"id": "FOREIGN", "title": "Foreign capability"})
    result = _adapter(payload)
    assert result["title"] == "Canonical vision"
    assert result["promise"] == "Canonical promise"
    assert result["capTitles"] == {"F1": "Canonical F1", "F2": "Canonical F2"}


@pytest.mark.parametrize(
    "fault",
    [
        "missing-source",
        "null-source",
        "stale-plan",
        "oracle",
        "null",
        "missing-build",
        "missing-refs",
    ],
)
def test_adapter_plan_scenario_unknowns_never_earn_a_numerator(fault):
    payload = _plan_facts_payload()
    source = payload["cockpit"]["source"]
    proof = payload["cockpit"]["vision"]["proof"]
    if fault == "missing-source":
        del source["planRevision"]
    elif fault == "null-source":
        source["planRevision"] = None
    elif fault == "stale-plan":
        source["planRevision"] = "old-P1"
    elif fault == "null":
        proof["productScenarios"] = None
    elif fault == "oracle":
        proof["productScenarios"]["subject"] = "oracle"
    elif fault == "missing-build":
        del proof["productScenarios"]["buildRevision"]
    else:
        proof["productScenarios"]["evidenceRefs"] = []
    payload["cockpit"]["answers"] = {
        "blocking": [{"id": "PLAN", "refs": ["review-findings:PLAN"]}],
        "needsYou": [{"id": "Q", "question": "Plan question", "refs": ["clarifications:Q"]}],
    }
    result = _adapter(payload)
    assert result["globalProof"] is None
    if fault in ("missing-source", "null-source", "stale-plan"):
        assert result["waveStatus"] == "unavailable"
        assert result["capProofs"]["F1"] is None
        assert result["blocking"] is None
        assert result["needs"] is None


def test_adapter_readiness_acceptance_and_nulls_remain_separately_recorded():
    payload = _plan_facts_payload()
    payload["plan"]["acceptance"] = {
        "readyForReview": True,
        "accepted": False,
        "humanOriginVerified": False,
    }
    payload["cockpit"]["vision"]["acceptance"] = {"accepted": False}
    result = _adapter(payload)
    assert result["acceptance"]["readyForReview"] is True
    assert result["acceptance"]["accepted"] is False
    payload["cockpit"]["vision"]["acceptance"] = {"readyForReview": None, "accepted": None}
    result = _adapter(payload)
    assert result["acceptance"]["readyForReview"] is None
    assert result["acceptance"]["accepted"] is None
    payload["cockpit"]["vision"]["acceptance"] = None
    assert _adapter(payload)["acceptance"] == {}


def _adapter(payload, mode="live"):
    """Execute the shipped adapter itself, without a duplicate test implementation."""
    script = r"""
const fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const prefix=source.slice(0,source.indexOf('function acceptance()'));
const adapter=vm.runInNewContext(prefix+'\nreturn {normalize,unit};})()',{
  URLSearchParams,location:{search:'?mode='+process.argv[2]},document:{querySelector:()=>null}
});
const m=adapter.normalize(JSON.parse(fs.readFileSync(0,'utf8')));
console.log(JSON.stringify({work:m.work.map(w=>({id:w.id,category:w.category})),
  blocking:m.blocking,needs:m.needs,crew:m.crew,stale:m.stale,scope:m.workScope??null,waveStatus:m.waveStatus,
  rightNow:m.rightNow,recordedReviews:m.recordedReviews,
  earlierRuns:(m.earlierRuns||[]).map(r=>r.runId),title:m.title,promise:m.promise,
  waveIds:m.waves.map(w=>w.id),currentWave:m.currentWave??null,currentRun:m.current?.runId??null,
  globalProof:adapter.unit(m.c.vision?.proof?.productScenarios,'productScenarios',m),
  capProofs:Object.fromEntries(m.caps.map(c=>[c.id,adapter.unit(c.proof?.productScenarios,'productScenarios',m)])),
  requirementProofs:Object.fromEntries(m.caps.map(c=>[c.id,adapter.unit(c.proof?.requirementValidation,'requirementValidation',m)])),
  capTitles:Object.fromEntries(m.caps.map(c=>[c.id,c.title])),acceptance:m.acceptance,
  capabilities:m.caps.map(c=>({id:c.id,total:m.workFor(c).length,
    done:m.workFor(c).filter(w=>w.category==='done').length}))}));
"""
    result = subprocess.run(
        ["node", "-e", script, str(Path(status_server.__file__).with_name("cockpit.js")), mode],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_actual_recorded_review_producer_reaches_shipped_consumer(tmp_path):
    from tests.python.core.test_cockpit_projection import (
        confirmation_snapshots,
        recorded_review_snapshots,
    )

    _, samples = recorded_review_snapshots(tmp_path)
    payload = samples["ready"]
    result = _adapter(payload)
    assert result["rightNow"]["text"] == "1 recorded review points still need a response."
    assert result["recordedReviews"] == payload["cockpit"]["recordedReviews"]
    assert result["crew"] == []  # Recorded reviews do not become live worker identities.
    payload["cockpit"]["recordedReviews"]["sourceRevision"] = "foreign"
    payload["cockpit"]["answers"]["rightNow"]["sourceRevision"] = "foreign"
    result = _adapter(payload)
    assert result["recordedReviews"] is None
    assert result["rightNow"]["code"] == "recorded-status"
    assert result["rightNow"]["text"] == "Plan status is recorded; current activity is unknown."
    confirmations = confirmation_snapshots(tmp_path)
    actual = _adapter(confirmations["recorded"])
    assert actual["needs"][0]["recordedUnderstanding"]["title"] == "Daily tracker understanding"
    assert actual["needs"][0]["recordedUnderstanding"]["chatConnection"] == "unknown"
    assert not _adapter(confirmations["cancelled"])["needs"][0].get("recordedUnderstanding")
