"""Native audit presentation through its canonical producer and persisted receipt."""

import json
from copy import deepcopy

from tests.python.core import test_test_audit
from tests.python.core.test_mcp_audit import finished_audit

from codex_harness.core import test_audit
from codex_harness.core.mcp_planner import PlannerServer
from codex_harness.core.plan_store import PlanStore, open_plan_store_readonly

prospective = test_test_audit.prospective


def test_public_audit_groups_repeated_diagnostics_without_changing_engine_or_receipt(
    prospective, monkeypatch
):
    """Catch repetitive native cards or altered findings; engine/CLI tests miss this seam."""
    root = prospective["template"].parent
    data = prospective["spine"].to_dict()
    data["nodes"] = [node for node in data["nodes"] if node["id"] not in {"R", "T"}]
    for i in range(1, 6):
        data["nodes"].extend(
            [
                dict(
                    id=f"R{i}",
                    kind="requirement",
                    title=f"R{i}",
                    text=f"R{i}",
                    parents=["F"],
                    fields={},
                ),
                dict(
                    id=f"T{i}",
                    kind="test",
                    title=f"T{i}",
                    text=f"T{i}",
                    parents=[f"R{i}"],
                    fields={"module": "test_app.py", "level": "unit"},
                ),
            ]
        )
    next(node for node in data["nodes"] if node["id"] == "B")["parents"] = [
        f"R{i}" for i in range(1, 6)
    ]
    (prospective["template"] / "tests/test_app.py").write_text(
        "import unittest\nfrom app import value\nclass Proof(unittest.TestCase):\n"
        + "".join(f"    def test_{i}(self): self.assertEqual(42, 42)\n" for i in range(10)),
        encoding="utf-8",
    )
    spine_file = root / "source-only-spine.json"
    spine_file.write_text(json.dumps(data), encoding="utf-8")
    with PlanStore(root) as store:
        store.import_spine(spine_file)
        store.compile_template(prospective["template"])
    observed = []
    engine = test_audit.audit_prospective_tests

    def capture(**kwargs):
        result = engine(**kwargs)
        observed.append(deepcopy(result))
        return result

    monkeypatch.setattr(test_audit, "audit_prospective_tests", capture)
    server = PlannerServer(root)
    try:
        response = finished_audit(server, mode="prospective")
        raw = response["structuredContent"]
        text = response["content"][0]["text"]
        assert response["isError"] and raw["ok"] is False
        assert len(raw["findings"]) == 51
        assert all(raw[key] == value for key, value in observed[0].items())
        assert json.dumps(raw["findings"]).encode() == json.dumps(observed[0]["findings"]).encode()
        assert "51 findings grouped into 11 distinct diagnostics" in text
        assert text.count("has no supported owned-interface assertion") == 10
        assert "R1, R2, R3, R4, R5" in text
        assert raw["warning"] in text and "Integration not checked" in text
        assert "Next:" in text and "helper/control-flow" in text
        row = server.store.connection.execute(
            "SELECT result_json FROM audit_runs WHERE id=?", (raw["id"],)
        ).fetchone()
        assert json.loads(row[0]) == raw
        assert server.store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    finally:
        server.close()
    with open_plan_store_readonly(root) as store:
        saved = store.connection.execute(
            "SELECT result_json FROM audit_runs WHERE id=?", (raw["id"],)
        ).fetchone()
        assert json.loads(saved[0]) == raw
        assert not store.passing_audit(raw["manifestRevision"])


def test_materially_different_and_unknown_findings_keep_all_details():
    """Catch over-grouping of severity/output/artifacts; the repeated producer case lacks these."""
    from codex_harness.core.planner_audit_views import render_native_audit_card

    original = dict(
        code="future-code",
        severity="blocker",
        module="test.py",
        output="same\nfull untruncated detail",
        outputPreview="same",
        bead="R1",
        artifactPath="proof/a.txt",
        vendorDetail={"why": "future"},
    )
    rows = [
        original,
        dict(original, bead="R2"),
        dict(original, severity="warning"),
        dict(original, output="different"),
        dict(original, artifactPath="proof/b.txt"),
        dict(original, output="long diagnostic " * 1000 + "END-OF-FULL-DETAIL"),
    ]
    raw = dict(
        ok=False,
        findings=rows,
        expectedFailures=[
            dict(
                original,
                code="expected-stub-failure",
                severity="expected",
                output="expected detail",
            )
        ],
    )
    before = deepcopy(raw)
    text = render_native_audit_card(raw)
    assert "6 findings grouped into 5 distinct diagnostics" in text
    assert all(
        value in text
        for value in (
            "future-code",
            "blocker",
            "warning",
            "different",
            "proof/a.txt",
            "proof/b.txt",
            "vendorDetail",
            "future",
            "full untruncated detail",
            "expected-stub-failure",
            "Expected failures (retained separately)",
            "expected detail",
        )
    )
    assert "Full diagnostic retained in structured audit result findings #6" in text
    assert "END-OF-FULL-DETAIL" not in text
    assert raw["findings"][5]["output"].endswith("END-OF-FULL-DETAIL")
    assert raw == before


def test_prospective_pass_keeps_warning_and_integration_limit():
    """Catch a prospective pass rendered as product proof; failure cards cannot detect it."""
    from codex_harness.core.planner_audit_views import render_native_audit_card

    raw = dict(
        ok=True,
        proofLevel="prospective",
        baseline="template",
        findings=[],
        expectedFailures=[],
        warning=test_audit.PROSPECTIVE_WARNING,
        integrationChecked=False,
        integrationReason="No reference implementation.",
    )
    text = render_native_audit_card(raw)
    assert text.startswith("The prospective audit passed for this revision.")
    assert raw["warning"] in text
    assert "Integration not checked: No reference implementation." in text
