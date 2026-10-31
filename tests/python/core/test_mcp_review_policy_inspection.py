"""Canonical policy declaration -> guarded public read-only contract and Next.

Existing CLI policy tests cannot detect a native inspection gap or a native
handler opening a writable store. Source-only confirmation prepares the fixture;
no providers, human approval or policy adoption through inspection is claimed.
"""

import json

import pytest
from tests.python.core.test_plan_store import FIXTURE, _link_r6, _to

from codex_harness.core.plan_store import open_plan_store


@pytest.fixture
def project(tmp_path):
    with open_plan_store(tmp_path) as store:
        store.import_spine(FIXTURE)
        _link_r6(store)
        _to(store, "roundtable")
        yield store


def wire(server, tool, arguments):
    reply = server.handle_line(
        json.dumps(
            dict(
                jsonrpc="2.0",
                id=7,
                method="tools/call",
                params=dict(name=tool, arguments=arguments),
            )
        )
    )
    assert reply["id"] == 7
    return reply["result"]
