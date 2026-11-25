"""C8/C10: bind a reviewer's alternative edits to the displayed choices."""

from . import chat
from .contracts import normalise_edits, option_rows
from .graph import edited
from .views import changed_promises


def offer(store, snapshot, answer, references):
    groups, patches = [], {}
    for question in chat.product_groups(answer["questions"]):
        if len(choices := question["options"]) != 2:
            continue
        group_id = question["id"]
        by_option = {}
        for key, option in zip(("recommended", "alternative"), choices, strict=True):
            selected = [r for r in answer["option_edits"]
                        if r["question"].strip() == question["question"] and r["option"] == key]
            edits, _ = normalise_edits(snapshot.graph, option_rows(selected), references,
                                       prefix=f"{group_id}-{option['id']}-")
            if not edits:
                break
            by_option[option["id"]] = edits
            graph, _ = edited(snapshot.graph, {"edits": edits})
            if changes := "\n".join(changed_promises(snapshot.graph, graph, detailed=True)):
                option["means"] += "\n" + changes
        else:
            groups.append(question)
            patches[group_id] = by_option
    return store.record("lead2_review_options", {"groups": groups, "patches": patches}) if groups else None
