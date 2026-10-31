"""Consume a core-only package with neither legacy code nor site packages present."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_core_only_import_lazy_memory_and_console_consumers(tmp_path):
    package = tmp_path / "src" / "codex_harness"
    shutil.copytree(
        ROOT / "src/codex_harness/core",
        package / "core",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = """
import importlib
import json
import pkgutil
import sys
from importlib.metadata import EntryPoint
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from codex_harness.core import dispatch, memory_promotion
assert memory_promotion.memory_prompt_for(sys.argv[2]) is None
assert not (Path(sys.argv[2]) / '.codex-harness').exists()
if sys.platform == 'win32':
    from codex_harness.core.project_memory import ProjectMemoryStore, Provenance
    store = ProjectMemoryStore(sys.argv[2])
    fact = store.add('Use integer cents.', provenance=Provenance(author='worker'))
    assert memory_promotion.memory_prompt_for(sys.argv[2]) is None
    store.confirm(fact.id, by='planner')
    assert 'Use integer cents.' in memory_promotion.memory_prompt_for(sys.argv[2])
import codex_harness.core
for module in pkgutil.iter_modules(codex_harness.core.__path__):
    importlib.import_module('codex_harness.core.' + module.name)
for name, value in json.loads(sys.argv[3]).items():
    entry = EntryPoint(name=name, value=value, group='console_scripts').load()
    sys.argv = [name, '--help']
    try:
        entry()
    except SystemExit as error:
        assert error.code == 0, (name, error.code)
    else:
        raise AssertionError('help must exit: ' + name)
assert all(name == 'codex_harness' or name.startswith('codex_harness.core')
           for name in sys.modules if name.startswith('codex_harness'))
assert (Path(codex_harness.core.__file__).parent / 'cockpit.html').is_file()
"""
    entries = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"].get("scripts", {})
    expected = {
        "p": "codex_harness.core.plan_cli:main",
        "plan_cli": "codex_harness.core.plan_cli:main",
        "run_plan": "codex_harness.core.dispatch_cli:main",
        "status_server": "codex_harness.core.status_server:main",
    }
    # Feed the required entries to the consumer even on the baseline: an actual
    # legacy import must fail before a missing packaging declaration can mask it.
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-S",
            "-c",
            script,
            str(tmp_path / "src"),
            str(workspace),
            json.dumps(expected),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert {name: entries.get(name) for name in expected} == expected
