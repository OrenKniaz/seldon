"""Git, immutable verification inputs and evidence records."""

from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from codex_harness.core import graph
from codex_harness.core.record_io import write_record_json
from codex_harness.core.runners import resolve_runner, validate_proof
from codex_harness.core.spine import (
    compile_manifest,
    load_spine,
    requirement_file_sources,
    requirement_files_revision,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _GitError(subprocess.CalledProcessError):
    def __str__(self) -> str:
        return f"{super().__str__()} stderr: {self.stderr}"


def _git_run(cwd: Path, *args: str, ok=(0,), **kwargs):
    command = ["git", "-c", f"safe.directory={cwd.resolve().as_posix()}", "-C", str(cwd), *args]
    kwargs.setdefault("stdin", None if "input" in kwargs else subprocess.DEVNULL)
    if kwargs.get("text"):
        kwargs.update(encoding="utf-8", errors="replace")
    result = subprocess.run(command, capture_output=True, **kwargs)
    if result.returncode not in ok:
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        failure = dict(command=command, returncode=result.returncode, stderr=stderr[:4096])
        evidence = cwd.parent / "evidence"
        if evidence.is_dir():
            with (evidence / "git-failures.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(failure) + "\n")
        raise _GitError(result.returncode, command, result.stdout, failure["stderr"])
    return result


def _git(cwd: Path, *args: str) -> str:
    return _git_run(cwd, *args, text=True).stdout.strip()


def _commit(cwd: Path, message: str) -> str:
    identity = ["-c", "user.name=Harness Sample", "-c", "user.email=sample@harness.invalid"]
    _git(cwd, *identity, "commit", "-q", "--allow-empty", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


def _requirement_ids(repo: Path, names: list[str]) -> dict[str, str]:
    """Git object ids of the requirement files at HEAD (line-ending neutral)."""

    return {name: _git(repo, "rev-parse", f"HEAD:{name}") for name in names}


def _verify_requirement_files(
    plan: graph.Plan, manifest: dict, root: Path, *, source_fixture: bool = False
) -> None:
    expected = plan.requirement_files_revision
    if expected is None:
        if source_fixture:
            if "planRevision" not in manifest:
                return  # Legacy static sample inputs, explicitly executed as mocks.
            source = plan.root / "spine.json"
            if (
                source.is_file()
                and compile_manifest(load_spine(source))["planRevision"] == plan.plan_revision
            ):
                return  # Exact source-only compiler identity, never a composite publication.
        raise ValueError("published requirement files revision is missing")
    if requirement_files_revision(root, list(plan.requirement_files)) != expected:
        raise ValueError("protected requirement files revision mismatch")


def _validation_inputs(cwd: Path) -> tuple[str, bool]:
    """Observe tracked working bytes and index, even with Git's trust flags set."""
    listing = _git_run(cwd, "ls-files", "--stage", "-z").stdout
    head = _git(cwd, "rev-parse", "HEAD")
    digest = hashlib.sha256(head.encode() + b"\0" + listing)
    working = hashlib.sha256()
    names = []
    types_match = True
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode = metadata.split()[0]
        name = raw_name.decode("utf-8")
        names.append(name)
        path = cwd / name
        digest.update(name.encode("utf-8") + b"\0")
        if path.is_symlink():
            types_match = types_match and mode == b"120000"
            content = os.fsencode(os.readlink(path))
            digest.update(b"link\0" + content)
        elif path.is_file():
            types_match = types_match and mode in {b"100644", b"100755"}
            content = path.read_bytes()
            digest.update(str(path.stat().st_mode).encode() + b"\0")
            digest.update(hashlib.sha256(content).digest())
        else:
            types_match = False
            content = None
            digest.update(b"missing\0")
        normalized = content.replace(b"\r\n", b"\n") if content is not None else b""
        size = len(normalized) if content is not None else 2**64 - 1
        working.update(name.encode("utf-8") + b"\0" + size.to_bytes(8, "big") + normalized)
    index = _git_run(cwd, "diff", "--cached", "--quiet", head, ok=(0, 1))
    # Match the canonical claim's LF-normalized blob identity; inspect actual
    # files rather than Git status, which may trust assume-unchanged/skip flags.
    committed = (
        types_match
        and index.returncode == 0
        and working.hexdigest() == _content_identity(cwd, head, names)
    )
    return digest.hexdigest(), committed


def _validate(
    cwd: Path, manifest: dict, module: str, *, owned_files: tuple[str, ...] = ()
) -> dict[str, object]:
    before, before_committed = _validation_inputs(cwd)
    result = validate_proof(cwd, manifest, module, owned_files=owned_files)
    error = None
    try:
        after, after_committed = _validation_inputs(cwd)
    except (OSError, subprocess.CalledProcessError, ValueError) as failure:
        after = None
        after_committed = False
        error = str(failure)
    result["inputIntegrity"] = {
        "passed": before == after and before_committed and after_committed,
        "matchesCommit": before_committed and after_committed,
        "before": before,
        "after": after,
        "error": error,
    }
    result["passed"] = result["passed"] and result["inputIntegrity"]["passed"]
    return result


def _content_identity(repo: Path, commit: str, names) -> str:
    """SHA256 of sorted Git paths and LF-normalized blobs, never working files.

    Each path is UTF-8 followed by NUL, then an unsigned 8-byte big-endian
    length and the normalized blob. Missing selections use the length 2**64-1.
    Directories expand to tracked files; missing paths remain explicit inputs.
    """
    names = {str(PurePosixPath(name.replace("\\", "/"))) for name in names}
    if not names:
        return hashlib.sha256().hexdigest()
    listing = _git_run(repo, "ls-tree", "-rz", "--name-only", commit, "--", *sorted(names))
    tracked = {path.decode("utf-8") for path in listing.stdout.split(b"\0") if path}
    paths = tracked | {
        name
        for name in names
        if not any(path == name or path.startswith(name + "/") for path in tracked)
    }
    # Read all blobs in one subprocess; Git's batch protocol preserves exact
    # bytes (including trailing newlines), unlike the text-oriented _git helper.
    result = _git_run(
        repo,
        "cat-file",
        "--batch",
        "-z",
        input="".join(f"{commit}:{path}\0" for path in sorted(tracked)).encode("utf-8"),
    )
    blobs = {}
    cursor = 0
    for path in sorted(tracked):
        end = result.stdout.index(b"\n", cursor)
        _, kind, size = result.stdout[cursor:end].split()
        if kind != b"blob":
            raise ValueError(f"claim input is not a blob: {commit}:{path}")
        cursor = end + 1
        blobs[path] = result.stdout[cursor : cursor + int(size)].replace(b"\r\n", b"\n")
        cursor += int(size) + 1
    digest = hashlib.sha256()
    for path in sorted(paths):
        content = blobs.get(path, b"")
        size = len(content) if path in blobs else 2**64 - 1
        digest.update(path.encode("utf-8") + b"\0" + size.to_bytes(8, "big") + content)
    return digest.hexdigest()


def _claim_inputs(repo: Path, plan: graph.Plan, bead_id: str, commit: str) -> dict:
    """Bind the whole manifest test directory, VISION/PLAN and requirementFiles.

    Bead proofs bind transitive prerequisites by default. proofDependsOn can
    explicitly select exercised prerequisites (and their ancestors), or [] for
    fully stubbed unit proofs. Integration always binds every bead.
    """

    def ancestors(ids):
        result = set(ids)
        for dependency in ids:
            result.update(ancestors(plan.beads[dependency].depends_on))
        return result

    if bead_id == "integration":
        dependencies = set(plan.beads)
        owned = [name for bead in plan.beads.values() for name in bead.owned_files]
        module = plan.integration_proof_module
        requirements = plan.integration_requirements
    else:
        bead = plan.beads[bead_id]
        dependencies = ancestors(
            bead.depends_on if bead.proof_depends_on is None else bead.proof_depends_on
        )
        owned, module, requirements = bead.owned_files, bead.proof_module, bead.requirements
    runner = resolve_runner(plan)
    verification_identity = _content_identity(
        repo, commit, runner.protected_paths(plan.requirement_files)
    )
    if plan.verification is not None:
        verification_identity = hashlib.sha256(
            (verification_identity + json.dumps(dict(plan.verification), sort_keys=True)).encode()
        ).hexdigest()
    return {
        "bead": bead_id,
        "requirementIds": list(requirements),
        "proofModule": module,
        "verificationIdentity": verification_identity,
        "sourceIdentity": _content_identity(repo, commit, owned),
        "dependencyIdentities": {
            dependency: _content_identity(repo, commit, plan.beads[dependency].owned_files)
            for dependency in sorted(dependencies)
        },
    }


def _claim(repo: Path, plan: graph.Plan, bead_id: str, commit: str, passed: bool) -> dict:
    claim = {
        **_claim_inputs(repo, plan, bead_id, commit),
        "candidateCommit": commit,
        "candidateTree": _git(repo, "rev-parse", f"{commit}^{{tree}}"),
        "passed": passed,
        "at": _now(),
    }
    claim["claimId"] = (
        f"{bead_id}:"
        + hashlib.sha256(
            json.dumps(claim, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    return claim


def _changed_claim_inputs(old: dict | None, inputs: dict) -> dict:
    # Module/requirement selection is part of the assertion even when all
    # verification files are unchanged. Failed or absent evidence is never reused.
    changed = {
        key: {"previous": old.get(key) if old else None, "current": inputs[key]}
        for key in (
            "verificationIdentity",
            "sourceIdentity",
            "dependencyIdentities",
            "proofModule",
            "requirementIds",
        )
        if old is None or old.get(key) != inputs[key]
    }
    if old is not None and not old.get("passed"):
        changed["passed"] = {"previous": old.get("passed"), "current": "pass required"}
    return changed


def _refs_payload(refs) -> list[dict[str, str]]:
    return [{"id": requirement, "revision": revision} for requirement, revision in refs]


def _bead_refs(bead: graph.Bead) -> dict:
    refs = {"requirementRefs": _refs_payload(bead.requirement_refs)}
    if bead.spec_refs is not None:
        refs["specRefs"] = [
            {"id": node_id, "kind": kind, "revision": revision}
            for node_id, kind, revision in bead.spec_refs
        ]
    return refs


def _identity_refs(record: dict) -> list[dict]:
    return record["specRefs"] if "specRefs" in record else record.get("requirementRefs", [])


def _spec_binding(record: dict) -> dict:
    return {"specRefs": record["specRefs"]} if "specRefs" in record else {}


def _update_requirement_files(repo: Path, plan: graph.Plan, from_revision: str) -> dict:
    """Compare template requirement blobs with HEAD, then commit only their changes."""
    template = plan.root / "template"
    names = [str(PurePosixPath(name.replace("\\", "/"))) for name in plan.requirement_files]
    sources = requirement_file_sources(template, names)
    tracked = (
        set(_git(repo, "ls-tree", "-rz", "--name-only", "HEAD", "--", *names).split("\0")) - {""}
        if names
        else set()
    )
    paths = []
    for name in sorted(tracked | sources.keys()):
        destination = repo / name
        if destination.is_symlink() or not destination.resolve().is_relative_to(repo.resolve()):
            raise ValueError(f"unsafe requirement destination: {name}")
        source = sources.get(name)
        if (
            source is not None
            and name in tracked
            and (
                _git(repo, "hash-object", f"--path={name}", str(source))
                == _git(repo, "rev-parse", f"HEAD:{name}")
            )
        ):
            continue
        paths.append(name)
    for name in paths:
        destination = repo / name
        if name in sources:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sources[name], destination)
        else:
            destination.unlink()
    commit = None
    if paths:
        _git(repo, "add", "-A", "--", *paths)
        commit = _commit(
            repo,
            f"Plan revision {plan.plan_revision[:12]}: update requirement files\n\n"
            f"Harness-Plan-Revision: {plan.plan_revision}\nHarness-Plan-Update: true",
        )
    return {
        "fromRevision": from_revision,
        "toRevision": plan.plan_revision,
        "commit": commit,
        "paths": paths,
    }


def _refs_text(refs: list[dict]) -> str:
    return ", ".join(f"{ref['id']}@{ref['revision'][:8]}" for ref in refs) or "none"


def _file_sha256(executable: str | None) -> str | None:
    if not executable:
        return None
    path = Path(shutil.which(executable) or executable)
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _validation_tool_rules(
    provider: str, bead: dict, plan: graph.Plan | None = None
) -> tuple[str, ...]:
    if plan is not None and plan.verification is not None:
        runner = resolve_runner(plan)
        command = (
            runner.worker_command(bead["proofModule"])
            if runner.name == "command"
            else subprocess.list2cmdline(runner.argv_for(bead["proofModule"]))
        )
        if provider == "claude":
            return (f"Bash({command})", f"PowerShell({command})")
        if provider == "copilot":
            return (f"shell({command})",)
        return ()
    if provider == "claude":
        return ("Bash(python -B -m unittest:*)", "PowerShell(python -B -m unittest:*)")
    if provider == "copilot":
        return ("shell(python -B -m unittest)",)
    return ()


def _write_record(path: Path, record: dict) -> None:
    write_record_json(path, record)


def _did_execute_real_provider(record: dict) -> bool:
    attempt = record.get("attempt") or {}
    return bool(
        record["didRunProvider"]
        and not record["isMocked"]
        and attempt.get("launch", {}).get("cli") == record["provider"]
        and record["provider"] in {"codex", "claude", "copilot"}
    )


def _requirement_validation(
    bead: str,
    requirements,
    module: str | None,
    integrated: dict | None,
    plan_revision: str | None = None,
    requirement_refs: list[dict] | None = None,
    spec_refs: list[dict] | None = None,
) -> dict:
    validation = integrated["validation"] if integrated else {}
    entry = {
        "bead": bead,
        "requirements": list(requirements),
        "proofModule": module,
        "candidateCommit": integrated["integrationCommit"] if integrated else None,
        "command": validation.get("command"),
        "passed": validation.get("passed", False),
    }
    if plan_revision is not None:
        entry.update(planRevision=plan_revision, requirementRefs=requirement_refs or [])
    if spec_refs is not None:
        entry["specRefs"] = spec_refs
    return entry
