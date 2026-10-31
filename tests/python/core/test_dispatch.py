"""Stand-in-only dispatch integration tests; never launch a real provider."""

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from codex_harness.core.dispatch import (
    STATUS_MOCK_ONLY,
    _commit,
    _git,
    run_plan,
)
from codex_harness.core.project_memory import ProjectMemoryStore
from codex_harness.core.spine import requirement_files_revision

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "fixtures" / "dogfood-tipsplit"
D1 = "tip-split amounts are integer cents everywhere; never floats (D1)"


def _bind_protected(plan: Path) -> None:
    path = plan / "sample.json"
    manifest = json.loads(path.read_text())
    manifest["requirementFilesRevision"] = requirement_files_revision(
        plan / "template", manifest["requirementFiles"]
    )
    path.write_text(json.dumps(manifest))


@pytest.fixture(autouse=True)
def no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "codex_harness.core.dispatch.get_adapter",
        lambda *args: pytest.fail("tests must never launch a provider"),
    )


def _tiny_plan(tmp_path: Path, *, dependent: bool = False) -> Path:
    plan = tmp_path / "plan"
    template = plan / "template"
    (template / "tests").mkdir(parents=True)
    for bead_id in ("A", "B"):
        (template / f"{bead_id}.py").write_text("VALUE = 0\n")
        (template / "tests" / f"test_{bead_id}.py").write_text(
            f"import unittest\nfrom {bead_id} import VALUE\n"
            f"class Proof(unittest.TestCase):\n"
            f"    def test_value(self):\n        self.assertEqual(VALUE, 1)\n"
        )
    manifest = {
        "sample": "parallel-plan",
        "validation": ["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
        "requirementFiles": ["tests"],
        "beads": {
            bead_id: {
                "title": f"Bead {bead_id}",
                "ownedFiles": [f"{bead_id}.py"],
                "requirements": [f"R{bead_id}"],
                "proofModule": f"test_{bead_id}.py",
            }
            for bead_id in ("A", "B")
        },
    }
    if dependent:
        manifest["beads"]["B"]["dependsOn"] = ["A"]
    (plan / "sample.json").write_text(json.dumps(manifest))
    return plan


@pytest.mark.parametrize("worker_committed", [False, True])
def test_dispatch_stages_sparse_owned_files_and_deletions(tmp_path, worker_committed):
    """Integrate actual edits when owned scope includes absent and deleted paths.

    Catches Contracts staging a not-yet-created Build file (Git128) and restaging
    committed deletions; existing dispatch fixtures create every owned file.
    """
    plan = _tiny_plan(tmp_path)
    manifest_path = plan / "sample.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["beads"]["B"]
    manifest["beads"]["A"]["ownedFiles"] += ["later.css", "obsolete.css"]
    manifest_path.write_text(json.dumps(manifest))
    (plan / "template/obsolete.css").write_text("obsolete")

    def worker(root):
        (root / "A.py").write_text("VALUE = 1\n")
        (root / "obsolete.css").unlink()
        if worker_committed:
            _git(root, "add", "-A")
            _commit(root, "worker edit and deletion")

    record = run_plan(plan, work_root=tmp_path / "runs", fake_workers={"A": worker})
    assert record["status"] == STATUS_MOCK_ONLY, record
    repo = Path(record["repo"])
    assert _git(repo, "show", "HEAD:A.py") == "VALUE = 1"
    assert not (repo / "obsolete.css").exists() and not (repo / "later.css").exists()
    assert record["beads"]["A"]["integrated"]["validation"]["passed"]


def _tiny_audited_plan(tmp_path: Path, *, dependent=False) -> Path:
    """Give canonical source fixtures an actual owned-call red expectation.

    Direct constant assertions remain sufficient for ordinary dispatch tests,
    but prospective publication needs observed owned-function failure lineage.
    Workers still implement the same VALUE=1 contract; accessors are not mocks.
    """
    plan = _tiny_plan(tmp_path, dependent=dependent)
    manifest = json.loads((plan / "sample.json").read_text())
    for bead_id in ("A", "B"):
        module = f"value_{bead_id}"
        (plan / "template" / f"{module}.py").write_text(
            f"from {bead_id} import VALUE\n\ndef value() -> int:\n    return VALUE\n"
        )
        manifest["beads"][bead_id]["ownedFiles"].append(f"{module}.py")
        (plan / "template/tests" / f"test_{bead_id}.py").write_text(
            f"import unittest\nfrom {module} import value\n"
            "class Proof(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(value(), 1)\n"
        )
    (plan / "sample.json").write_text(json.dumps(manifest))
    return plan


@pytest.fixture
def lesson_dispatch(tmp_path, monkeypatch):
    """Actual canonical publication/attempt/child/proof/memory; no provider or proof double."""
    from tests.python.core import execution_support
    from tests.python.core.test_dispatch_cancel import source_only_packet_renderer

    from codex_harness.core import dispatch
    from codex_harness.core.provider_launch import ProviderNormalLaunch

    def run(mode="ok"):
        plan = _tiny_audited_plan(tmp_path)
        lesson = ("Retain the full agreed decision and its validated evidence. " * 40)[:1854]

        def node(id, kind, parents, fields=None, text=None):
            return dict(
                id=id,
                kind=kind,
                title=f"Bead {id}",
                text=text or id,
                parents=parents,
                fields=fields or {},
            )

        nodes = [
            node(
                "V1",
                "vision",
                [],
                dict(
                    validation=["-B", "-m", "unittest", "discover", "-s", "tests", "-p"],
                    requirementFiles=["tests"],
                ),
            ),
            node("F", "feature", ["V1"]),
            node("RA", "requirement", ["F"]),
            node("RB", "requirement", ["F"]),
            node("D0", "decision", ["V1"], text=lesson),
        ]
        for bead_id in ("A", "B"):
            nodes += [
                node(
                    bead_id,
                    "bead",
                    ["R" + bead_id],
                    dict(
                        ownedFiles=[bead_id + ".py", "value_" + bead_id + ".py"],
                        dependsOn=["A"] if bead_id == "B" else [],
                        proofModule="test_" + bead_id + ".py",
                        lessons=["D0"] if bead_id == "A" else [],
                    ),
                ),
                node(
                    "T" + bead_id,
                    "test",
                    ["R" + bead_id],
                    dict(
                        module="test_" + bead_id + ".py",
                        level="unit",
                    ),
                ),
            ]
        source = plan / "source-spine.json"
        source.write_text(json.dumps(dict(schema="codex-harness/spine-v1", name="m", nodes=nodes)))
        publish = execution_support.publish_source_store
        # Select genuine typed input for the existing producer; no admission guard is patched.
        monkeypatch.setattr(
            execution_support,
            "publish_source_store",
            lambda workspace, ignored: publish(workspace, spine_file=source),
        )
        context = execution_support.source_execution_context(tmp_path / "canonical", plan)

        class FiniteAdapter:
            def provider_normal_command(self, *, prompt, **kwargs):
                action = (
                    "Path('A.py').write_text('VALUE = 1\\n')"
                    if "Bead A" in prompt
                    else "Path('.harness').mkdir(exist_ok=True); "
                    "Path('.harness/blocker.json').write_text("
                    '\'{"reason":"source fixture B blocked"}\')'
                )
                script = (
                    "from pathlib import Path\n" + action + "\n"
                    'print(\'{"type":"turn.completed","isMocked":true}\')\n'
                    "# SOURCE-ONLY local process fixture\n"
                )
                return ProviderNormalLaunch(
                    cli="codex",
                    command=(sys.executable, "-c", script),
                    protections=("finite source-only child",),
                )

        monkeypatch.setattr(dispatch, "get_adapter", lambda name: FiniteAdapter())
        source_only_packet_renderer(monkeypatch)
        execution_support.source_process_observations(monkeypatch)
        options = dict(context["kwargs"])
        if mode == "unavailable":

            def unavailable(*args, **kwargs):
                raise PermissionError("controlled advisory memory write unavailable")

            monkeypatch.setattr(ProjectMemoryStore, "add", unavailable)
        if mode == "self-confirmation":
            options["confirmer"] = "worker:A:codex"
        if mode == "receipt-failure":
            write = dispatch._write_record

            def unavailable_receipt(path, value):
                if Path(path).name == "integrated.json":
                    raise OSError("controlled canonical receipt write failure")
                return write(path, value)

            monkeypatch.setattr(dispatch, "_write_record", unavailable_receipt)
        record = run_plan(context["plan"], work_root=context["runs"], **options)
        if mode == "reused":
            record = run_plan(
                context["plan"],
                work_root=context["runs"],
                resume_run_dir=context["runs"] / record["runId"],
                **options,
            )
            assert record["beads"]["A"]["status"] == "reused"
        return dict(
            context=context, record=record, lesson=lesson, workspace=context["directory"].parents[2]
        )

    return run


def test_dispatch_attempts_overlap_and_integrate_without_ownership_conflict(tmp_path: Path) -> None:
    plan = _tiny_plan(tmp_path)
    barrier = threading.Barrier(2, timeout=10)
    arrivals = []
    # Real packet consumption catches a dropped argument at dispatch; the
    # canonical store producer is covered in the owning worker-job test.
    intent_nodes = [
        dict(kind="vision", title="Parallel product", text="Both parts work.", fields={})
    ]

    def worker(bead_id: str):
        def edit(worktree: Path) -> None:
            arrivals.append(bead_id)
            barrier.wait()
            (worktree / f"{bead_id}.py").write_text("VALUE = 1\n")

        return edit

    record = run_plan(
        plan,
        work_root=tmp_path / "runs",
        max_parallel=2,
        setup={"githubLogin": "dispatch-user"},
        intent_nodes=intent_nodes,
        fake_workers={bead_id: worker(bead_id) for bead_id in ("A", "B")},
    )
    assert all(
        "GitHub must be dispatch-user." in bead["prompt"] for bead in record["beads"].values()
    )
    assert sorted(arrivals) == ["A", "B"]
    assert all(
        "## User intent\nParallel product\n\nBoth parts work." in b["prompt"]
        for b in record["beads"].values()
    )
    assert record["status"] == STATUS_MOCK_ONLY
    assert all(bead["integrated"]["validation"]["passed"] for bead in record["beads"].values())
    assert record["beads"]["A"]["attempt"]["baseCommit"] == record["baseline"]["commit"]
    assert record["beads"]["B"]["attempt"]["baseCommit"] == record["baseline"]["commit"]
    repo = Path(record["repo"])
    assert all((repo / f"{bead_id}.py").read_text() == "VALUE = 1\n" for bead_id in ("A", "B"))
    assert len(_git(repo, "rev-list", "--merges", "HEAD").splitlines()) == 2


@pytest.mark.parametrize("artifact", ["bytecode", "source"])
def test_generated_bytecode_integrates_but_untracked_source_refuses(
    tmp_path, monkeypatch, artifact
):
    """Catch post-worker import caches blocking integration; existing retries create no caches."""
    from codex_harness.core import dispatch

    plan = _tiny_plan(tmp_path)
    path = plan / "sample.json"
    manifest = json.loads(path.read_text())
    del manifest["beads"]["B"]
    path.write_text(json.dumps(manifest))
    original = dispatch._integration_candidate

    def integrate(record, bead, repo):
        worker = Path(record["attempt"]["cwd"])
        exclude = repo / ".git/info/exclude"
        # Appending must retain an existing local owner's bytes, including a missing LF.
        assert exclude.read_bytes().startswith(b"# retained local entry")
        common = _git(worker, "rev-parse", "--path-format=absolute", "--git-common-dir")
        assert Path(common).resolve() == (repo / ".git").resolve()
        if artifact == "bytecode":
            environment = dict(os.environ)
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            environment.pop("PYTHONPYCACHEPREFIX", None)
            subprocess.run(
                [sys.executable, "-c", "import A"], cwd=worker, env=environment, check=True
            )
            assert list((worker / "__pycache__").glob("*.pyc"))
            (worker / "loose.pyc").write_bytes(b"generated bytecode")
        else:
            (worker / "unexpected.py").write_text("VALUE = 9\n")
        return original(record, bead, repo)

    template = tmp_path / "git-template/info"
    template.mkdir(parents=True)
    (template / "exclude").write_bytes(b"# retained local entry")
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template.parent))
    monkeypatch.setattr(dispatch, "_integration_candidate", integrate)
    result = run_plan(
        plan,
        work_root=tmp_path / "runs",
        fake_workers={"A": lambda tree: (tree / "A.py").write_text("VALUE = 1\n")},
    )
    if artifact == "bytecode":
        assert result["status"] == STATUS_MOCK_ONLY
        assert _git(Path(result["repo"]), "status", "--porcelain") == ""
    else:
        assert "integration recovery worker changed" in result["beads"]["A"]["status"]


def test_new_dispatch_repo_refuses_foreign_git_metadata(tmp_path):
    """Catch editing another repo's common exclude; ordinary fixtures have no .git indirection."""
    plan = _tiny_plan(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init", "-q")
    exclude = foreign / ".git/info/exclude"
    exclude.write_bytes(b"foreign sentinel\n")
    (plan / "template/.git").write_text(f"gitdir: {foreign / '.git'}\n")
    with pytest.raises(ValueError, match="dispatch repository metadata"):
        run_plan(plan, work_root=tmp_path / "runs", mock_provider="reference")
    assert exclude.read_bytes() == b"foreign sentinel\n"


def test_dispatch_failed_bead_blocks_transitive_dependents_but_independent_continues(
    tmp_path: Path,
) -> None:
    plan = _tiny_plan(tmp_path, dependent=True)
    manifest = json.loads((plan / "sample.json").read_text())
    manifest["beads"]["C"] = {**manifest["beads"]["B"], "dependsOn": ["B"]}
    manifest["beads"]["D"] = {**manifest["beads"]["B"], "ownedFiles": ["D.py"]}
    manifest["beads"]["D"].pop("dependsOn")
    shutil.copy2(plan / "template" / "B.py", plan / "template" / "D.py")
    (plan / "sample.json").write_text(json.dumps(manifest))

    def independent(worktree: Path) -> None:
        (worktree / "D.py").write_text("VALUE = 1\n")

    # D proves its own file, avoiding a proof that silently depends on B.
    manifest["beads"]["D"]["proofModule"] = "test_D.py"
    (plan / "sample.json").write_text(json.dumps(manifest))
    (plan / "template" / "tests" / "test_D.py").write_text(
        (plan / "template" / "tests" / "test_B.py").read_text().replace("from B", "from D")
    )
    record = run_plan(
        plan,
        work_root=tmp_path / "runs",
        fake_workers={
            "A": lambda path: None,
            "D": independent,
            "B": lambda path: pytest.fail("B launched"),
            "C": lambda path: pytest.fail("C launched"),
        },
    )
    assert record["beads"]["A"]["status"] == "rejected: no source change"
    assert record["beads"]["B"]["status"] == "blocked: dependency A failed"
    assert record["beads"]["C"]["status"] == "blocked: dependency A failed"
    assert record["beads"]["D"]["status"] == STATUS_MOCK_ONLY
