"""Short external projects and one measured fast-gate selection."""

import os
import tempfile
from pathlib import Path

import pytest

FAST_FILES = {
    "test_suite_budget.py",
    "test_module_size.py",
    "test_managed_paths.py",
    "test_plan_store_reads.py",
    "test_plan_store.py",
    "test_planner_confirmations.py",
    "test_planner_chat_confirmations.py",
    "test_planner_batch.py",
    "test_planner_refusal_events.py",
    "test_planning_review_budget.py",
    "test_semantic_runner.py",
    "test_worker_packet.py",
    "test_graph.py",
    "test_mail.py",
    "test_record_io.py",
    "test_provider_launch.py",
}


def pytest_configure(config):
    config.addinivalue_line("markers", "fast: caller invariants, bounded to ten minutes")
    root = (
        Path(os.environ.get("TEMP", "C:/Users/oren/t/d"))
        if os.name == "nt"
        else Path(tempfile.gettempdir())
    )
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
    for item in items:
        if item.path.name in FAST_FILES and not item.name.startswith("test_web_"):
            item.add_marker(pytest.mark.fast)
    config._diet_counts = {
        "suite": len(items),
        "fast": sum(item.get_closest_marker("fast") is not None for item in items),
    }


@pytest.fixture(scope="session")
def web_browser_ready(tmp_path_factory):
    """Share one real browser readiness result, through the production launcher."""
    from tests.python.core.test_semantic_runner import browser_readiness

    return browser_readiness(tmp_path_factory)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    if item.get_closest_marker("fast") is not None:
        config = item.config
        config._diet_fast_seconds = getattr(config, "_diet_fast_seconds", 0) + report.duration


def pytest_sessionfinish(session):
    seconds = getattr(session.config, "_diet_fast_seconds", 0)
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    if reporter:
        reporter.write_line(
            f"Fast marked checks: {seconds:.2f}s including setup/call/teardown; ceiling 600s"
        )
    if seconds > 600:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
