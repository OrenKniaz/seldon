def source_only_expansion(store):
    """Synthetic proposed choices, never user consent or product acceptance."""
    vision = store.spine().node(store.spine().ids("vision")[0])
    vision["fields"].update(
        quality_bar="Retain every cent",
        done_feels_like="Shares add up",
        expansion=dict(
            mode="quality",
            reason="Precise CLI scope",
            upgrades=[
                dict(
                    id=str(i),
                    text=f"Upgrade {i}",
                    kind="risk",
                    impact=f"Impact {i}",
                    disposition=choice,
                )
                for i, choice in enumerate(("accept", "reject", "defer"), 1)
            ],
        ),
    )
    store.upsert_node(vision)
