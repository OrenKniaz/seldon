"""Short new paths; recorded identities and legacy paths remain authoritative."""

import hashlib
from pathlib import Path


def short(value):
    return value if len(value) <= 12 else hashlib.sha256(value.encode()).hexdigest()[:12]


def directory(parent, identity):
    legacy = Path(parent) / identity
    return legacy if legacy.exists() or legacy.is_symlink() else Path(parent) / short(identity)


def run_directory(work_root, run_id, beads, resume=None):
    root = Path(resume).resolve() if resume else Path(work_root).resolve() / run_id
    tails = ["memory-workspace/.codex-harness/project-memory.sqlite3-journal"] + [
        f"evidence/{short(bead)}/retry-9999999999/attempt/{req}/request.json"
        for bead, item in beads.items()
        for req in item["requirements"]
    ]
    longest = max((root / tail for tail in tails), key=lambda p: len(str(p)))
    if not resume and len(str(longest)) > 200:
        raise ValueError(f"Managed path >200 (safe Windows limit): {longest}; shorten workspace")
    return root
