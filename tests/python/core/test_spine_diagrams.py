"""Chat views use real spine ancestry, dependencies and coverage gaps."""

from pathlib import Path

from codex_harness.core.spine import SCHEMA, Spine

KINDS = ("feature-map", "user-journey", "decision-map", "bead-dag", "trace")
FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/dogfood-tipsplit/spine.json"


def node(key, kind, parents=(), **fields):
    return dict(id=key, kind=kind, title=key, text="", parents=list(parents), fields=fields)


def spine(*nodes):
    return Spine(dict(schema=SCHEMA, name="Example", nodes=[node("V", "vision"), *nodes]))


def bead(key, dependencies=(), parents=()):
    return node(
        key,
        "bead",
        parents,
        dependsOn=list(dependencies),
        ownedFiles=[f"{key}.py"],
        proofModule="test.py",
        lessons=[],
    )
