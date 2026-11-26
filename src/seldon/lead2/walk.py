"""C15/C16/C26: independently walk the stories and offer the owner's release decision."""

from dataclasses import replace
from functools import partial
from pathlib import Path
import re
from urllib.parse import quote

from .. import gitwork, inference
from ..processes import reconcile_call
from ..schema import REFS, TEXT, array, choice, obj
from ..states import LEAD2_RUN, advance as advance_run
from . import build_brief, chat
from .graph import PROOF
from .owner_guide import owner_text
from .walk_record import CATEGORIES, current_walk, release_notes, save_walk

S = build_brief.STRING
WALK = obj(
    stories=array(obj(story=TEXT, outcome=choice("works", "broken", "can't-test-here"),
                      reason=TEXT, screenshot=S, defect=S)),
    notes=array(obj(kind=choice(*CATEGORIES), stories=REFS, reason=TEXT)),
    guide_path=S, resolved=REFS,
)
ACTIONS = obj(actions=array(obj(kind=choice("repair", "note", "ask"), ids=REFS, reason=TEXT)))
advance = partial(advance_run, transitions=LEAD2_RUN)
BRIEF = """Use the real app and disposable sample data. Walk every assigned story once in this session.
For must stories, exercise the main path and the planned tricky cases you can reproduce. For should
and nice stories, do one quick main-path click-through. Return one outcome and a specific observed
result per story. Use can't-test-here only when you cannot exercise the story and say what is needed.
If the main path works but a variant needs a real venue or device, record it separately as an untested
note. For a broken story, give a short stable defect name (same as before when the same behavior fails).
For visual paths, save one representative PNG per story using checks/kit/browser.cjs shot(name, page).
Return its exact basename. For other paths, describe the command or action and observed output.
For a web app record its startup with checks/kit/serve.cjs, preview: true, 127.0.0.1, {port} and {data}.
Report concrete rough edges, quality or trace gaps, documented setup problems and tests needing other
conditions as notes. Do not do separate source, guide or quality proof passes. Give the tracked guide
path if available. For a fix recheck, revisit only the listed stories and report what happened to the
selected notes that the builder worked on. List verified resolved note IDs in resolved."""


def step(store, spec, provider, user):
    cursor = store.cursor()
    if cursor.decision:
        return _reply(store, spec, user, cursor.decision)
    target, _, request = store.records("lead2_walk_target")[-1]
    snapshot = spec.snapshot()
    run = current_walk(store, snapshot.revision)
    reconcile_call(store)
    if not run or run["target"] != target or "results" not in run:
        if cursor.state == "C":
            return advance(store, "repair", target)
        return _walk(store, snapshot, provider, target, request)
    notes = release_notes(store, run)
    if not notes:
        return _plan(store, provider, run)
    if run["missing"]:
        raise ValueError("The walk did not return verdicts for " + ", ".join(run["missing"]))
    broken = [item for item in notes["items"] if item["blocked"]]
    if cursor.state == "C":
        return advance(store, "repair", target) if broken else _offer(store, run, notes)
    if broken:
        ids = [row["id"] for row in broken if not row["repeated"]]
        if ids:
            return _repair(store, run, notes, ids)
        choices = chat.options(("retry", "Try a different approach", "Tell the builder what to try."),
                               ("stop", "Stop here", "End with the current build."))
        return _offer(store, run, notes, gate="Walk decision", options=choices)
    advance(store, "proved" if run["final"] else "continue", target)


def _stories(graph):
    stories = {}
    for ref, node in graph.nodes.items():
        if node["kind"] != "story":
            continue
        promises = {p for p in graph.follow(ref) if graph.nodes[p]["kind"] == "promise"}
        walks = {w for p in promises for w in graph.follow(p, PROOF) if graph.nodes[w]["kind"] == "walk"}
        walks |= {b for a, b in graph.links if a == ref and graph.nodes[b]["kind"] == "walk"}
        roles = {p["role"] for a, b in graph.links if b in walks | {ref}
                 and (p := graph.nodes[a])["kind"] == "person"}
        stories[ref] = dict(title=node.get("title") or node["story"], story=node["story"],
                            priority=node.get("priority", "must"), roles=sorted(roles),
                            promises=sorted(promises), walks=sorted(walks))
    return stories


def _walk(store, snapshot, provider, target, request):
    last = store.read(request["delivery"])
    graph = snapshot.graph
    stories = _stories(graph)
    final = last["outcome"] == "done"
    if not final and last.get("code_map"):
        delivered = {row["promise"] for row in last["code_map"]}
        stories = {ref: row for ref, row in stories.items() if delivered.intersection(row["promises"])}
    prior = store.read(request["prior"]) if request.get("prior") else {}
    if prior and prior["revision"] != snapshot.revision:
        prior = {}
    touched = set(request.get("stories", ())) if prior else stories.keys()
    chosen = {ref: row for ref, row in stories.items() if ref in touched or ref not in prior.get("results", {})}
    workspace = gitwork.admit(store.root.parent, store.cursor().scratch, last["candidate"])
    evidence = Path(store.cursor().scratch) / "walk" / Path(workspace).name
    evidence.mkdir(parents=True)
    question = build_brief.question("LEAD2-WALK", WALK, BRIEF, **{
        "Owner's words": [node["words"] for node in graph.nodes.values() if node["kind"] == "dream"],
        "Candidate": last["candidate"],
        "Stories to walk": chosen,
        "Agreed behaviors": {ref: graph.nodes[ref] for row in chosen.values() for ref in row["promises"]},
        "Scenarios": {ref: graph.nodes[ref] for row in chosen.values()
                      if row["priority"] == "must" for ref in row["walks"]},
        "Notes selected for this fix": {ref: store.read(ref) for ref in request.get("items", [])},
        "Previous outcomes for these stories": {story: store.read(ref)
                                                 for story, ref in prior.get("results", {}).items()
                                                 if story in chosen},
    })
    question["execution"] = {"walk": workspace, "evidence": str(evidence)}
    try:
        inference.call(store, provider, question, consume=lambda ref, reply: save_walk(
            store, target, snapshot, last, stories, chosen, prior, request, evidence, ref, reply))
    finally:
        reconcile_call(store)
        gitwork.git(store.root.parent, "worktree", "remove", "--force", workspace)


def _plan(store, provider, run):
    rows = {row["ref"]: row for row in run["results"].values()}
    rows.update({ref: store.read(ref) for ref in run["notes"]})
    prompt = ("Write one plain sentence per item from the recorded observations. Select repair only for a "
              "broken must story, note for anything else, or ask for a can't-test-here owner question. "
              "For an optional access question, write the missing condition as a note for C. "
              "Use a single exact ID per sentence. Preserve outcomes and disclose all limitations. "
              "For a broken story, describe what failed in plain words.")
    question = build_brief.question("LEAD2-WALK-STEER", ACTIONS, prompt, Observations=rows,
                                    Stories=run["stories"])

    def save(source, answer):
        sentences, errors = {}, []
        for action in answer["actions"]:
            for ref in action["ids"]:
                row = rows.get(ref)
                allowed = {"note"}
                if row and row.get("outcome") == "broken" and run["stories"][row["story"]]["priority"] == "must":
                    allowed.add("repair")
                if row and row.get("outcome") == "can't-test-here":
                    allowed.add("ask")
                if not row or ref in sentences or action["kind"] not in allowed:
                    errors.append(f"{ref!r}: use a shown ID once with its permitted action ({', '.join(sorted(allowed))}).")
                else:
                    sentences[ref] = action["reason"]
        inference.correct(store, source, answer, errors)
        store.record("lead2_release_notes", {"walk": run["ref"], "source": source, "sentences": sentences})

    inference.call(store, provider, question, consume=save)


def _repair(store, run, notes, ids, direction=""):
    with store.transaction():
        items = [row for row in notes["items"] if row["id"] in ids]
        ref = store.record("lead2_walk_repair", {
            "target": run["target"], "prior": run["ref"], "defects": [row["id"] for row in items],
            "stories": sorted({story for row in items for story in row["stories"]}),
            "identities": [row["identity"] for row in items],
            "direction": direction or "Fix the selected items in one batch. Keep other behavior working.",
        })
        if store.cursor().state == "C":
            advance(store, "repair", ref)
        advance(store, "repair", ref)


def _offer(store, run, notes, gate="C", options=None):
    from .status_page import registered_links

    choices = options or chat.options(
        (chat.APPROVE, "Accept as is", "Accept the walked build with these release notes."),
        (chat.REVISE, "Fix these", "Enter the item IDs you want fixed, separated by spaces."),
        ("stop", "Stop here", "End with the current build."))
    scratch = store.cursor().scratch
    base = registered_links(store.root.parent).get("statusPage", "")
    lines = ["### What works"]
    for row in notes["works"]:
        link = f" [Screenshot]({base}walk-shot/{quote(row['artifact'])})" if row["artifact"] and base else ""
        lines.append(f"- {row['text']}{link}")
    if run["guide_text"] and base:
        lines += ["", f"[Open the build and setup guide]({base}walk)"]
    if notes["items"]:
        lines += ["", "### What's weaker", *(f"- {r['id']} · {r['label']}: {r['text']}"
                                               + (" *(Fix these)*" if r["fixable"] else "") for r in notes["items"])]
    chat.request(store, gate, run["revision"], [chat.group(chat.ROUND, "How should we continue?", choices)],
                 proposal=owner_text("\n".join(lines), scratch), summary="Release notes",
                 candidate=run["candidate"], walk=run["ref"], release=notes["ref"])


def _reply(store, spec, user, ref):
    item = store.read(ref)
    reply, words, choices = chat.read_reply(store, user, ref, item)
    subject = item["subject"]
    run = current_walk(store, spec.snapshot().revision)
    notes = release_notes(store, run) if run else None
    bound = notes and (run["ref"], run["candidate"], run["revision"], notes["ref"]) == (
        subject.get("walk"), subject.get("candidate"), subject["lead2_revision"], subject.get("release"))
    with store.transaction():
        picked = next((row["optionId"] for row in choices if row["group"] == chat.ROUND), None)
        if bound and picked in {chat.REVISE, "other"}:
            text = reply.get("words") or next((row["text"] for row in choices if row["group"] == chat.ROUND), "")
            ids = set(filter(None, re.split(r"[\s,]+", text.strip())))
            valid = {row["id"] for row in notes["items"] if row["fixable"]}
            if not ids or not ids <= valid:
                store.record("user_words", {**reply, "words": words, "origin": item["origin"], "rejected": True})
                store.cursor(replace(store.cursor(), failure="Choose current item IDs marked Fix these."))
                return
        raw = store.record("user_words", {**reply, "words": words, "origin": item["origin"]})
        store.cursor(replace(store.cursor(), decision=None, failure=""))
        if not bound:
            if store.cursor().state == "C":
                advance(store, "repair", raw)
        elif picked == "stop":
            advance(store, "stop", raw)
        elif picked == chat.APPROVE and item["gate"] == "C" and not reply.get("words"):
            scripted = store.read(store.cursor().kickoff)["setup"].get("scriptedUser", False)
            approval = store.record("approval", {"request": ref, "subject": subject,
                "userWords": raw, "effect": "approve", "scripted": scripted})
            advance(store, "scripted" if scripted else "approve", approval)
        elif picked in {chat.REVISE, "other"}:
            _repair(store, run, notes, ids)
        elif picked == "retry" and item["gate"] == "Walk decision":
            _repair(store, run, notes, [row["id"] for row in notes["items"] if row.get("blocked")], words)
        else:
            store.record("lead2_release_reply", {"release": notes["ref"], "reply": raw})
            store.cursor(replace(store.cursor(), failure="Choose a release action or give the release-note IDs to fix."))
