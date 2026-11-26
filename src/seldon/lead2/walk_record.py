"""C9/C15/C26: candidate-bound story observations and the canonical release notes."""

from pathlib import Path

from .. import gitwork, inference, proof
from .owner_guide import owner_text
from .preview import launch_recipe

CATEGORIES = {"quality": "Not high quality", "documentation": "Not documented properly",
              "untested": "Not tested here", "rough": "Known rough edges"}


def read_walk(store, ref, run):
    result = {**run, "ref": ref}
    if "results" in run:
        result["results"] = {key: {**store.read(value), "ref": value} for key, value in run["results"].items()}
        return result
    for field in ("steps", "promises", "issues"):
        if field in run:
            result[field] = [_read_result(store, item) for item in run[field]]
    return result


def current_walk(store, revision):
    walks = store.records("lead2_walk")
    if walks and (run := walks[-1][2])["revision"] == revision and run["candidate"] == store.cursor().candidate:
        return read_walk(store, walks[-1][0], run)


def review_findings(store, candidate):
    return {f"{ref}:{index}": text for ref, _, review in store.records("lead2_end_review")
            if review.get("candidate", candidate) == candidate for index, text in enumerate(review["notes"], 1)}


def blocks(item, stories):
    return bool(item.get("wrong_or_lost_result") or item.get("outcome") == "broken"
                and stories[item["story"]]["priority"] == "must")


def save_walk(store, target, snapshot, last, stories, chosen, prior, request, evidence, source, answer):
    stamp = dict(source=source, candidate=last["candidate"], revision=snapshot.revision)
    results = {key: ref for key, ref in prior.get("results", {}).items() if key in stories and key not in chosen}
    errors, seen, steps = [], set(), []
    for row in answer["stories"]:
        story = row["story"]
        if story not in chosen or story in seen:
            errors.append(f"{story!r}: give one verdict for its exact ID from Stories to walk.")
            continue
        seen.add(story)
        artifact = _artifact(evidence, row["screenshot"])
        if row["screenshot"] and not artifact:
            errors.append(f"{story}: save a readable PNG in {evidence} and give its exact basename.")
        if row["outcome"] == "broken" and not row["defect"]:
            errors.append(f"{story}: name the broken behavior in defect. Reuse its previous name if unchanged.")
        result = {**stamp, **row, "artifact": artifact, "stories": [story], "kind": "rough",
                  "identity": [story, (row["defect"] or row["reason"]).casefold()]}
        ref = store.record("lead2_story_result", result)
        results[story] = ref
        steps.append(dict(story=story, role=", ".join(stories[story]["roles"]), action=stories[story]["title"],
                          expected=stories[story]["story"], actual=row["reason"], artifact=artifact,
                          outcome=row["outcome"]))
    missing = chosen.keys() - seen
    if missing:
        errors.append("Return the missing story verdicts: " + ", ".join(sorted(missing)))
    findings = {} if prior else review_findings(store, last["candidate"])
    rechecks = set(request.get("items", [])) & set(prior.get("notes", []))
    notes = list(prior.get("notes", []))
    reported = set()
    story_ids = {ref for ref, node in snapshot.graph.nodes.items() if node["kind"] == "story"}
    for row in answer["notes"]:
        finding = row["finding"]
        if set(row["stories"]) - story_ids or (row["wrong_or_lost_result"] and not row["stories"]):
            errors.append(f"{row['reason']}: link the finding to its affected story IDs.")
            continue
        if finding and (finding not in findings.keys() | rechecks or finding in reported):
            errors.append(f"{finding!r}: use each review or recheck ID once.")
            continue
        reported.add(finding)
        identity = (store.read(finding)["identity"] if finding in rechecks and row["kind"] != "untested" else
                    [finding] if finding else _identity(row))
        notes = [ref for ref in notes if store.read(ref)["identity"] != identity]
        notes.append(store.record("lead2_release_item", {**stamp, **row, "identity": identity}))
    untried = (findings.keys() | rechecks) - reported
    if untried:
        errors.append("Record what happened for these findings: " + ", ".join(sorted(untried)))
    files = gitwork.paths(store.root.parent, last["candidate"])
    guide = answer["guide_path"].replace("\\", "/").removeprefix("./")
    guide_text = (gitwork.git(store.root.parent, "show", f"{last['candidate']}:{guide}") if guide in files else "")
    if guide and not guide_text:
        errors.append(f"Guide {guide!r}: use the exact tracked file path, or leave it empty.")
    if not guide and prior.get("guide_text"):
        guide_text = prior["guide_text"]
    if not guide_text and not any(store.read(ref)["kind"] == "documentation" for ref in notes):
        notes.append(_note(store, stamp, "documentation", "A setup guide was not available to the walker.", []))
    inference.correct(store, source, answer, errors)
    if answer.get("correction_note"):
        notes.append(_note(store, stamp, "rough", answer["correction_note"], []))
    launch = launch_recipe(evidence, files) or prior.get("launch")
    blocked = {story for ref in [*results.values(), *notes] if blocks(item := store.read(ref), stories)
               for story in item["stories"]}
    store.record("lead2_walk", {**stamp, "target": target, "final": last["outcome"] == "done",
        "stories": stories, "results": results, "notes": notes, "blocked_stories": sorted(blocked),
        "guide_text": guide_text, "launch": launch,
        "missing": sorted(missing | untried),
        "steps": [step for step in prior.get("steps", []) if step["story"] in stories.keys() - chosen.keys()] + steps})


def _identity(row):
    return [row["kind"], sorted(row["stories"]), row["reason"].casefold()]


def _note(store, stamp, kind, reason, stories):
    row = dict(kind=kind, reason=reason, stories=stories)
    return store.record("lead2_release_item", {**stamp, **row, "identity": _identity(row)})


def release_notes(store, run):
    if "results" not in run:
        return None
    for release, _, row in reversed(store.records("lead2_release_notes")):
        if row["walk"] == run["ref"]:
            break
    else:
        return None
    attempts = [item["identities"] for ref, _, item in store.records("lead2_walk_repair")
                if "identities" in item and int(ref[1:]) < int(run["ref"][1:])]
    works, items, omitted = [], [], []
    observations = {item["ref"]: item for item in run["results"].values()}
    advice = row["notes"]
    for key in sorted([*observations, *run["notes"]], key=lambda key: not advice.get(key, {}).get("surprise_later")):
        item = observations.get(key) or store.read(key)
        note = advice.get(key, {})
        text = owner_text(note.get("sentence", item["reason"]), store.cursor().scratch)
        outcome = item.get("outcome")
        blocked = blocks(item, run["stories"])
        if outcome == "works" and not blocked:
            if item["candidate"] != run["candidate"]:
                text += " Walked on an earlier build and unaffected by this fix."
            works.append(dict(text=text, artifact=item["artifact"], id=key))
            continue
        if not blocked and not outcome and item["kind"] != "untested" and not note.get("is_problem", True):
            omitted.append(dict(id=key, text=text, reason=note["is_problem_why"]))
            continue
        repeated = sum(item["identity"] in batch for batch in attempts) >= 2
        label = CATEGORIES["untested" if outcome == "can't-test-here" else item["kind"]]
        items.append(dict(id=key, text=text + (" This still happens after two fixes." if repeated else ""),
                          label=label, blocked=blocked, repeated=repeated,
                          fixable=label != CATEGORIES["untested"] and not repeated,
                          stories=item["stories"], identity=item["identity"]))
    return dict(ref=release, works=works, items=items, omitted=omitted)

def _artifact(evidence, name):
    if not name or Path(name).name != name or Path(name).suffix.lower() != ".png":
        return None
    path = evidence / name
    try:
        if path.resolve().parent == evidence.resolve() and proof.image(path.read_bytes()):
            return f"{evidence.name}/{name}"
    except OSError:
        return None

def _read_result(store, item):
    return {**store.read(item), "ref": item} if isinstance(item, str) else item
