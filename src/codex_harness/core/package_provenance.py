"""Identity of the actual adapter Python sources, without inferred build commits."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from importlib import metadata
from pathlib import Path

from codex_harness.core.process_launch import hidden_process_kwargs


def _source_digest(package: Path) -> str:
    sources = [
        (path.relative_to(package).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(package.rglob("*.py"), key=lambda p: p.relative_to(package).as_posix())
        if path.is_file()
    ]
    return hashlib.sha256(
        json.dumps(sources, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def adapter_provenance() -> dict:
    """Installed sources never query Git; a source checkout must own this exact file."""
    package = Path(__file__).resolve().parents[1]
    root = package.parent.parent
    commit = None
    version = None
    origin = "installed-package"
    # Do not discover an enclosing repository: only the conventional own checkout
    # layout with its own Git marker and matching project metadata qualifies.
    project = root / "pyproject.toml"
    if package.parent.name == "src" and (root / ".git").exists() and project.is_file():
        data = tomllib.loads(project.read_text(encoding="utf-8"))
        if data.get("project", {}).get("name") == "seldon":
            origin = "source-tree"
            version = data["project"].get("version")
            try:
                top = subprocess.run(
                    ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                    **hidden_process_kwargs(),
                    capture_output=True, encoding="utf-8", errors="replace",
                    check=True,
                ).stdout.strip()
                if Path(top).resolve() != root:
                    raise ValueError("source checkout root mismatch")
                tracked = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "ls-files",
                        "--error-unmatch",
                        "--",
                        "src/codex_harness/core/package_provenance.py",
                    ],
                    **hidden_process_kwargs(),
                    capture_output=True, encoding="utf-8", errors="replace",
                    check=True,
                )
                if tracked.stdout.strip() == "src/codex_harness/core/package_provenance.py":
                    commit = (
                        subprocess.run(
                            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
                            **hidden_process_kwargs(),
                            capture_output=True, encoding="utf-8", errors="replace",
                            check=True,
                        ).stdout.strip()
                        or None
                    )
            except (OSError, ValueError, subprocess.CalledProcessError):
                pass
    if origin == "installed-package":
        try:
            distribution = metadata.distribution("seldon")
            files = distribution.files or ()
            if any(
                Path(distribution.locate_file(item)).resolve() == Path(__file__).resolve()
                for item in files
            ):
                version = distribution.version
        except metadata.PackageNotFoundError:
            pass
    return {
        "adapterCommit": commit,
        "adapterProvenance": {
            "schema": "seldon/adapter-source-v1",
            "origin": origin,
            "distribution": "seldon",
            "version": version,
            "pythonSourceSha256": _source_digest(package),
        },
    }
