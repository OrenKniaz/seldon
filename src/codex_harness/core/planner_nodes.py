"""The existing CLI node-batch writer, shared by public CLI and MCP adapters."""

from codex_harness.core.spine import Spine


def batch_nodes(store, nodes: list, validate_node) -> str:
    """Validate with the owning public node validator, then write one transaction.

    Adapters pass their existing single-node validator; no authority, approval or
    confirmation is manufactured here. This is the CLI algorithm relocated intact.
    """
    if not nodes:
        raise ValueError("Node batch must not be empty")
    for node in nodes:
        validate_node(node)
    if len({node["id"] for node in nodes}) != len(nodes):
        raise ValueError("Duplicate node ids in batch")
    with store._transaction():
        data = store._data()
        merged = {node["id"]: node for node in data["nodes"]}
        merged.update({node["id"]: node for node in nodes})
        spine = Spine({**data, "nodes": list(merged.values())}, previous=data)
        for node in nodes:
            store._write_node(spine, node["id"])
            store._event(
                "node-upserted", {"id": node["id"], "plan_revision": spine.plan_revision()}
            )
    return "\n".join(f"Changed node: {node['id']} (upserted)." for node in nodes)
