"""Read-only observations with injected discovery only; no fake installed tools."""

import pytest

from codex_harness.core.preflight import preflight_report, render_preflight


def test_preflight_reports_present_missing_unavailable_and_unknown_without_calls(
    tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("Preflight must not launch processes or read file contents")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("pathlib.Path.open", forbidden)
    seen = []

    def discovery(provider):
        seen.append(provider)
        if provider == "copilot":
            raise PermissionError("fixture discovery unavailable")
        return "mock:codex" if provider == "codex" else None

    workspace = tmp_path / "absent" / "project"
    report = preflight_report(workspace, executable_for=discovery)
    assert seen == ["codex", "claude", "copilot"]
    assert [row["executable"]["status"] for row in report["providers"]] == [
        "available",
        "missing",
        "unavailable",
    ]
    assert report["workspace"]["status"] == report["planDatabase"]["status"] == "missing"
    assert report["executionReadiness"] == "not-proven"
    assert report["didRunProvider"] is False and report["isMocked"] is True
    assert all(
        row["authentication"] == row["effectiveProtections"] == "unknown"
        for row in report["providers"]
    )
    assert report["unknown"] == {
        "planValidity": "unknown",
        "network": "unknown",
        "workerSuccess": "not-proven",
    }
    assert not workspace.parent.exists()
    text = render_preflight(report)
    assert "execution readiness NOT PROVEN" in text
    assert "authentication UNKNOWN" in text and "effective protections UNKNOWN" in text
    assert "Requested policy only" in text and "permission" in text


@pytest.mark.parametrize("providers", [[], "codex", ["unsupported"], ["codex", 123]])
def test_preflight_invalid_selection_refuses_before_discovery(tmp_path, providers):
    def discovery(provider):
        pytest.fail("Invalid provider input must refuse before discovery")

    with pytest.raises(ValueError):
        preflight_report(tmp_path / "missing", providers, executable_for=discovery)
    assert not (tmp_path / "missing").exists()
