"""Synthetic provider-protocol regressions; no native provider execution credit."""

import pytest
from tests.python.core.test_roundtable import store as roundtable_store

from codex_harness.core import roundtable

store = roundtable_store


@pytest.fixture(autouse=True)
def forbid_native_launch(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Protocol tests must never launch a native provider")

    monkeypatch.setattr(roundtable, "_run", forbidden)
