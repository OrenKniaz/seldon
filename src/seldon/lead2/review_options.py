"""C8/C10: attach ordinary spec edits to the options the owner sees."""

from . import chat
from .contracts import SPEC_EDITS, normalise_edits
from .graph import edited
from .views import changed_promises


def offer(snapshot, answer, references):
    groups, group_ids, option_ids = [], set(), {"other", "unclear"}
    for question in answer["questions"]:
        group_id, title = question["id"].strip(), question["question"].strip()
        if not (group_id and title) or group_id in group_ids:
            continue
        options, labels = [], {"other"}
        for role in ("recommended", "alternative"):
            ref, label = question[f"{role}_id"].strip(), question[role].strip()
            if not (ref and label) or ref in option_ids or label.casefold() in labels:
                continue
            rows = {kind: [row["ref"] if kind == "retire" else row
                           for row in answer[f"option_{kind}"] if row["option_id"].strip() == ref]
                    for kind in SPEC_EDITS}
            edits, _ = normalise_edits(snapshot.graph, rows, references, prefix=f"{len(option_ids)}-")
            graph, _ = edited(snapshot.graph, {"edits": edits})
            changes = changed_promises(snapshot.graph, graph, detailed=True)
            means = "\n".join((question[f"{role}_means"], *changes))
            options.append({"id": ref, "label": label, "means": means, "edits": edits})
            option_ids.add(ref)
            labels.add(label.casefold())
        if options:
            group_ids.add(group_id)
            groups.append(chat.group(group_id, title, options))
    return groups


def apply(spec, revision, approval, groups, picked, words):
    edits = [edit for group in groups for option in group["options"]
             if option["id"] == picked.get(group["id"], {}).get("optionId") for edit in option["edits"]]
    if not edits:
        return spec.snapshot(revision)
    edits.sort(key=lambda edit: edit["op"] == "retire")
    edits.append(dict(op="add", name=None, node=dict(kind="dream", words=words)))
    result = spec.apply(dict(revision=revision, mode="substantive", edits=edits,
                             reason=f"Apply the owner's review choices ({approval})"), author="owner")
    return spec.snapshot(result.revision)
