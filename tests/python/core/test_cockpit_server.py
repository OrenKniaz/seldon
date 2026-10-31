"""Cockpit routing preserves the existing read-only status producer."""

import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection

import pytest

from codex_harness.core import status_server
from codex_harness.core.plan_store import PlanStore


@contextmanager
def running(tmp_path, workspace=None, runs_root=None):
    with status_server.create_server(
        workspace=workspace or tmp_path / "missing-workspace",
        runs_root=runs_root or tmp_path / "missing-runs",
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


@pytest.mark.parametrize(
    "route",
    [
        "/status_server.py",
        "/../pyproject.toml",
        "/%2e%2e/README.md",
        "/fixtures/private.json",
        "/board",
        "/legacy",
        "/cockpit-overview.js",
    ],
)
def test_only_named_assets_are_served(tmp_path, route):
    with running(tmp_path) as connection:
        assert get(connection, route)[0] == 404


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
        for key in ("phase", "plan_revision", "pending_gate", "open_findings", "coverage_gaps"):
            assert plan[key] == expected[key], key
        assert len(plan["clarifications"]) == expected["open_clarifications"]
        assert "Which first-use promise should we keep?" in json.dumps(plan["clarifications"])
        assert get(connection, "/")[0] == 200
        assert get(connection, "/cockpit.js")[0] == 200
    after = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    assert after == before
