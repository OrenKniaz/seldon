"""Shared temporary projects and browser readiness for retained E2E journeys."""

import os
import tempfile
from pathlib import Path

import pytest


def pytest_configure(config):
    root = Path(os.environ.get("TEMP", tempfile.gettempdir()))
    root.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(root)
    tempfile.tempdir = str(root)
    if not config.option.basetemp:
        config.option.basetemp = str(root / "p")


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("w")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    items.sort(key=lambda item: item.path.name != "test_golden_path.py")
    config._suite_count = len(items)


@pytest.fixture(scope="session")
def web_browser_ready(tmp_path_factory):
    """Share one real browser readiness result, through the production launcher."""
    from tests.python.core.semantic_runner_support import browser_readiness

    return browser_readiness(tmp_path_factory)
