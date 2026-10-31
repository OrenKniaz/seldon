"""Candidate-bound claim reuse with stand-ins; providers must never launch."""

import json
import shutil
from pathlib import Path

import pytest

from codex_harness.core import dispatch
from codex_harness.core.spine import compile_manifest, load_spine

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit"


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(dispatch, "get_adapter", lambda *args: pytest.fail("real provider launch"))


def _plan(tmp_path, *, stubbed=True):
    plan = tmp_path / "plan"
    shutil.copytree(FIXTURE, plan)
    spine = load_spine(plan / "spine.json")
    if stubbed:
        # Unit execution imports no A code: patched wrappers import money only
        # when the integration proof calls them for real.
        source = plan / "reference/tipsplit/cli.py"
        source.write_text(
            source.read_text().replace(
                "from tipsplit.money import parse_amount, split",
                "def parse_amount(value):\n"
                "    from tipsplit.money import parse_amount as real\n"
                "    return real(value)\n\n"
                "def split(total, people, tip):\n"
                "    from tipsplit.money import split as real\n"
                "    return real(total, people, tip)",
            ),
            encoding="utf-8",
        )
    _manifest(plan, spine, stubbed=stubbed)
    return plan, spine


def _manifest(plan, spine, *, stubbed=True):
    manifest = compile_manifest(spine, plan / "template")
    # Explicit exercised-dependency rule: test_cli patches money calls, so
    # proofDependsOn=[] binds no A code. Absent this field the transitive graph
    # rule is conservative and binds A, regardless of our knowledge of the stub.
    if stubbed:
        manifest["beads"]["B"]["proofDependsOn"] = []
    (plan / "sample.json").write_text(json.dumps(manifest), encoding="utf-8")


def _resume(plan, first, **kwargs):
    return dispatch.run_plan(
        plan,
        work_root=Path(first["repo"]).parent.parent,
        resume_run_dir=Path(first["repo"]).parent,
        **kwargs,
    )
