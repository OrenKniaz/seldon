"""Public schema/batch -> persisted ancestry -> CLI/MCP advice, without providers.

Protect feature-local turns: a complete feature hid a second empty feature, and
global test gaps could preempt finishing the current feature's bead. Existing
single-feature coverage tests cannot expose either cross-feature error. Compiler
help is checked against real public compilation, not a copied fixture answer.
Native setup uses the existing labelled synthetic client; no human claim.
"""

from tests.python.core import test_planner_replay_gaps
from tests.python.core.test_planner_batch import invoke

project = test_planner_replay_gaps.project


def save(server, nodes):
    result = invoke(server, "harness_upsert_nodes", nodes=nodes)
    assert not result["isError"], result
