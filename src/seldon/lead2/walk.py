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
from .walk_record import CATEGORIES, current_walk, release_notes, review_findings, save_walk

S = build_brief.STRING
BOOLEAN = build_brief.BOOLEAN
WRONG_RESULT = dict(wrong_result_why=TEXT, wrong_or_lost_result=BOOLEAN)
WALK = obj(
    stories=array(obj(story=TEXT, outcome=choice("works", "broken", "can't-test-here"),
                      reason=TEXT, screenshot=S, defect=S, **WRONG_RESULT)),
    notes=array(obj(kind=choice(*CATEGORIES), stories=REFS, reason=TEXT, finding=S, **WRONG_RESULT)),
    guide_path=S,
)
ACTIONS = obj(notes=array(obj(id=TEXT, sentence=TEXT, is_problem_why=TEXT, is_problem=BOOLEAN,
                              surprise_later_why=TEXT, surprise_later=BOOLEAN)))
advance = partial(advance_run, transitions=LEAD2_RUN)
BRIEF = """Use the real app and disposable sample data. Walk every assigned story once in this session.
For must stories, exercise the main path and the planned tricky cases you can reproduce. For should
and nice stories, walk the main path. Try invalid or interrupted actions that could quietly change a
saved result, even on those stories. For handoffs, keep both roles in open sessions, act on one side,
and record what the other side sees before reloading. Return one outcome and a specific observed
result per story. Use can't-test-here only when you cannot exercise the story and say what is needed.
If the main path works but a variant needs a real venue or device, record it separately as an untested
note. For a broken story, give a short stable defect name (same as before when the same behavior fails).
For visual paths, save one PNG with checks/kit/browser.cjs shot(name, page) per story you can capture.
Use the supplied evidence folder as-is. Return the exact filename including the width suffix, or "".
For other paths, describe the command or action and observed output.
For a web app start it with checks/kit/serve.cjs and {preview: true}. Pass 127.0.0.1, {port} and
{data}. Use the app's tracked, repo-relative Python or Node entry point directly. Keep
SELDON_EVIDENCE_DIR as supplied so the helper saves preview-launch.json for the Open this build button.
Report concrete rough edges, quality or trace gaps, documented setup problems and tests needing other
conditions as notes. Do not do separate source, guide or quality proof passes. Give the tracked guide
path if available. Try each review finding's reported action and return a note with its exact finding
reference, affected story IDs and actual result. Return one note per recheck ID, including fixes
that work. For a review summary, record what the review covered. Leave finding empty for new notes.
For each verdict and note, explain whether a result was wrong or lost before answering.
Bakery order list: yes if a saved order disappears, no if the list has cramped spacing."""
STEER_BRIEF = """Write one plain owner-facing sentence per exact ID. Say who can do what and what fails.
Preserve observed outcomes and limits. Use everyday words for the behavior and its consequence.
Explain before each yes/no. Bakery order list: is_problem yes if a clerk can change another
clerk's orders, no for a passing test summary. surprise_later yes if offline orders silently
disappear when reconnecting, no if an optional sort control is visibly absent."""


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
    if run["missing"]:
        raise ValueError("The walk did not return verdicts for " + ", ".join(run["missing"]))
    notes = release_notes(store, run)
    if not notes:
        return _plan(store, provider, run)
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
    if not final and (point := request["demo"]):
        plan = store.read(store.read(store.cursor().B)["subject"]["plan"])["build_order"]
        demo = store.read(point)
        through = demo.get("index") or next((i for i, _ in enumerate(plan, 1)
                                              if f"delivery-{i}" == demo.get("delivery")), 0)
        shown = {ref for row in plan[:through] for ref in row.get("stories", ())}
        mapped = {row["promise"] for row in last.get("code_map", ())}
        stories = {ref: row for ref, row in stories.items() if ref in shown or
                   not shown and (not mapped or mapped.intersection(row["promises"]))}
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
        "Review findings": review_findings(store, last["candidate"]) if not prior else {},
        "Notes selected for this fix": {ref: store.read(ref)["reason"] for ref in request.get("items", [])
                                        if ref in prior.get("notes", [])},
        "Previous outcomes for these stories": {
            story: {key: row[key] for key in ("outcome", "reason", "defect")}
            for story, ref in prior.get("results", {}).items() if story in chosen and (row := store.read(ref))},
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
    question = build_brief.question("LEAD2-WALK-STEER", ACTIONS, STEER_BRIEF, Observations=rows,
                                    Stories=run["stories"])

    def save(source, answer):
        notes = {row["id"]: row for row in answer["notes"] if row["id"] in rows}
        inference.correct(store, source, answer, ["Use one exact shown ID per sentence."]
                          if len(notes) != len(answer["notes"]) else [])
        store.record("lead2_release_notes", {"walk": run["ref"], "source": source, "notes": notes})

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
    if (run["launch"] or run["guide_text"]) and base:
        lines += ["", f"[Open the build and walk]({base}walk)"]
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
