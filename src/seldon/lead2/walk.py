"""C15/C16/C26: one independent product walk, then steer from its findings."""

from dataclasses import replace
from functools import partial
from pathlib import Path

from .. import gitwork, inference
from ..processes import reconcile_call
from ..schema import array, choice, obj
from ..states import LEAD2_RUN
from ..states import advance as advance_run
from . import build_brief, chat, chunk_review
from .contracts import SPEC_EDITS, normalise_edits
from .graph import PROOF
from .owner_guide import owner_text
from .walk_record import (
    ASSESSMENTS, blockers, current_walk, limits, notes, open_defects, save_walk, saved_evidence, supplement,
)

S, V = build_brief.STRING, build_brief.VERDICT
WALK = obj(
    steps=array(obj(story=S, scenario=S, replaces=S, role=S, action=S, expected=S, actual=S,
                    outcome=V, screenshot=S)),
    promises=array(obj(promise=S, scenario=S, outcome=V, reason=S, evidence=build_brief.REFS)),
    checks=array(obj(ref=S, action=S, outcome=choice("works", "broken"), result=S)),
    feedback=array(obj(story=S, note=S)),
    findings=array(obj(story=S, promise=S, owner=S, finding=S, must_fix=build_brief.BOOLEAN)),
    rechecks=array(obj(ref=S, outcome=choice("resolved", "reproducible", "pending"), reason=S,
                       evidence=build_brief.REFS)),
    limits=array(obj(story=S, promise=S, decision=S, reason=S)),
    cleared_limits=build_brief.REFS,
    guide_path=S, sample_job=S,
)
STEER = obj(
    **(build_brief.STEER["properties"] | {"direction": S}),
    walk=array(obj(ref=S, scenario=S, interruption=S)),
    link=array(obj(source=S, targets=build_brief.REFS)),
    probe=build_brief.REFS, repair=build_brief.REFS,
    owner_decision=choice("access", "scope", "pending"), owner_promises=build_brief.REFS, limit=S,
    dispositions=array(obj(ref=S, disposition=choice("close", "downgrade"), reason=S)),
)
CHANGE = obj(**STEER["properties"], promise=SPEC_EDITS["promise"])
advance = partial(advance_run, transitions=LEAD2_RUN)
WALK_BRIEF = """Independently walk the assigned stories and judge the current build. Follow the fresh-copy guide
in this checkout and use disposable sample data. For a web app, use checks/kit/serve.cjs with
{preview: true} on the primary launch. Pass the guide's startup arguments as an argv array and
use 127.0.0.1, {port} and {data} for local settings. This records the launch for Open this build.
Use checks/kit/browser.cjs headlessly with its default timeouts and SELDON_EVIDENCE_DIR intact.
Exercise each participant with a separate sign-in and the relevant viewport, before and after
interruptions. Use local processes, files, clean copies and emulation. Judge each failure condition.
Use works for observed results, pending for incomplete local checks, and can't-test-here when missing
access prevents observing the result. Name any limit against its promise and cite a B deferral if it applies.
Capture each participant's step at its original viewport with shot(name, page). Report the participant,
exact PNG basename, story and exercised scenario IDs. For each verdict cite evidence by saved ID,
new PNG basename, or a check you completed here. In checks give each completed check a unique
reference, what you did, the observed result and its outcome. Use the same verdict rows to judge
the listed additional promises. Give the guide's exact tracked path and sample job.
File findings with observed versus expected behavior. Mark must_fix only if the defect must block C.
Advisory findings appear as notes. Reserve owner access findings for a real access decision.
Recheck existing defects by ID with new passing evidence when resolved. Give UX feedback."""



def step(store, spec, provider, user):
    cursor = store.cursor()
    if cursor.decision:
        return _reply(store, spec, user, cursor.decision)
    target, _, request = store.records("lead2_walk_target")[-1]
    snapshot = spec.snapshot()
    run = current_walk(store, snapshot.revision)
    reconcile_call(store)
    if not run or run["target"] != target or "issues" not in run:
        return advance(store, "repair", target) if cursor.state == "C" else _walk(store, spec, provider, target, request)
    checks = limits(store, run)
    issues = blockers(store, run, checks)
    access = [row for row in checks if row["access"] and not row["agreed"]]
    if cursor.state == "C":
        return advance(store, "repair", target) if issues or access else _offer(store, run)
    owner = _owner_report(store, target)
    if (probe := _probe(store, run["ref"])) and (issues or owner.get("from_C") or probe.get("reply")):
        return _walk(store, spec, provider, target, request)
    if run["final"] and access:
        return _offer_limits(store, run, access)
    if issues or owner.get("from_C"):
        return _steer(store, spec, provider, target, run, issues, owner)
    advance(store, "proved" if run["final"] else "continue", target)


def _probe(store, walk_ref):
    return next((row for _, _, row in reversed(store.records("lead2_walk_probe"))
                 if row.get("walk") == walk_ref), None)


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
        stories[ref] = {"title": node.get("title") or node["story"], "story": node["story"],
                        "roles": sorted(roles), "promises": sorted(promises), "walks": sorted(walks)}
    return stories


def _walk(store, spec, provider, target, request):
    last = store.read(request["delivery"])
    snapshot = spec.snapshot()
    graph = snapshot.graph
    final = last["outcome"] == "done"
    stories = _stories(graph)
    mapping = [row for row in last["code_map"] if row["promise"] in graph.nodes]
    promises = ({ref for ref, node in graph.nodes.items() if node["kind"] == "promise"} if final else
                {row["promise"] for row in mapping})
    if not final:
        stories = {ref: {**story, "promises": sorted(delivered)} for ref, story in stories.items()
                   if (delivered := promises.intersection(story["promises"]))}
    walks = store.records("lead2_walk")
    defects = open_defects(store, walks[-1][2]) if walks else {}
    approval = store.cursor().B
    access = store.read(store.read(approval)["subject"]["plan"]).get("access", [])
    deferrals = {f"{approval}/access-{i}": row["need"] for i, row in enumerate(access, 1) if row.get("deferred")}
    workspace = gitwork.admit(store.root.parent, store.cursor().scratch, last["candidate"])
    evidence = Path(store.cursor().scratch) / "walk" / Path(workspace).name
    evidence.mkdir(parents=True)
    demo = store.read(request["demo"])["outcome"] if request["demo"] else ""
    walked = {row["target"] for _, _, row in walks}
    demos = [store.read(row["demo"])["outcome"] for ref, _, row in store.records("lead2_walk_target")
             if ref in walked and ref != target and row["demo"]]
    prior_ref, prior = next(((ref, row) for ref, _, row in reversed(walks)
                             if row["candidate"] == last["candidate"] and row["final"] == final), (None, {}))
    probe = _probe(store, prior_ref) if prior.get("target") == target else None
    if probe and probe.get("revision", prior["revision"]) != snapshot.revision:
        probe = None
    saved = (supplement(store, spec, snapshot, prior, stories)
             if "verdicts" in prior and (probe or prior["revision"] != snapshot.revision) else {})
    question = build_brief.question("LEAD2-WALK", WALK, WALK_BRIEF, **{
        "Owner's words": [node["words"] for node in graph.nodes.values() if node["kind"] == "dream"],
        "Approved venue deferrals": deferrals,
        "Candidate": last["candidate"], "Spec revision": snapshot.revision,
        "Selected demonstration": demo, "Earlier demonstrations": demos,
        "Stories to walk": stories,
        "Promises to judge": {p: graph.nodes[p] for p in sorted(promises)},
        "Additional promises": ASSESSMENTS,
        "Scenarios": {w: graph.nodes[w] for w in sorted({w for s in stories.values() for w in s["walks"]})},
        "Accepted design, planned code and checks": {ref: n for ref, n in graph.nodes.items()
                                                     if n["kind"] in ("design", "check", "interface")},
        "Actual code map": mapping,
        "Defects to recheck": {ref: {**store.read(ref), "last_recheck": store.read(check) if check else None}
                              for ref, check in defects.items()},
        "Open probes": {**probe, "findings": {ref: prior["verdicts"][ref] if ref in ASSESSMENTS else store.read(ref)
                                             for ref in probe.get("findings", [])}} if probe else None,
        "Earlier review gaps to check": chunk_review.review_limits(store),
    })
    if saved:
        question["facts"]["Saved observations"] = saved_evidence(store, saved)
        question["facts"]["Saved limits"] = {ref: store.read(ref) for ref in saved["limits"]}
        question["prompt"] += ("\nStart with Open probes and gather only missing or contrary evidence. Judge using "
                               "saved and new evidence for this candidate. Omit unchanged verdicts and cite evidence "
                               "for changed ones. Cite replaced step or cleared limit IDs for contradictions.")
    question["execution"] = {"walk": workspace, "evidence": str(evidence)}

    save = partial(save_walk, store, spec, target, snapshot, last, stories=stories, promises=promises,
                   evidence=evidence, saved=saved, defects=defects, deferrals=deferrals, prior=prior)

    try:
        inference.call(store, provider, question, normalise=build_brief.normalise, consume=save)
    finally:
        reconcile_call(store)
        gitwork.git(store.root.parent, "worktree", "remove", "--force", workspace)


def _steer(store, spec, provider, target, run, findings, owner_report):
    snapshot = spec.snapshot()
    change, report = owner_report.get("change", False), owner_report.get("words", "")
    prompt = ("Propose the owner-requested promise edit for approval. Ask if the change is unclear. "
              if change else "Preserve promises. Send incomplete observations back to the walker. ")
    refs = {row["ref"] for row in findings}
    active = open_defects(store, run)
    uncovered = {row["promise"] for row in findings if row["ref"] not in active}
    promises = {row["promise"]: snapshot.graph.nodes[row["promise"]] for row in run["promises"]} | ASSESSMENTS
    rechecks = {ref: {**store.read(check), "record": check} for ref, check in active.items() if check}
    repairable = {row["ref"]: row for row in findings if row["ref"] in active and row["owner"] != "planner"
                  and rechecks.get(row["ref"], {}).get("outcome") != "pending"}
    attempts = {ref: row for ref, _, row in store.records("lead2_walk_repair") if set(row["defects"]) & refs}
    probes = {ref: row for ref, _, row in store.records("lead2_walk_probe") if row["target"] == target}
    stalled = run.get("stalled") or any(row.get("walk") == run["ref"]
                                       for _, _, row in store.records("lead2_walk_finding"))
    question = build_brief.question("LEAD2-WALK-STEER", CHANGE if change else STEER,
        "Use repair for exact Repairable IDs, or probe for missing evidence. New independent walks reuse saved "
        "evidence by reference. Close or downgrade by ID with a reason. Change approach after two failed repairs. "
        "For testing access use owner_decision access, exact Uncovered IDs in owner_promises, and the test's "
        "short unverified condition in limit. Use scope with options to change a promise. "
        "If the walk stalls, change the action. Link any newly added scenario. "
        + prompt,
        Stories=run["stories"], Observations=run["promises"], Findings=findings, No_progress=bool(stalled), Repair_attempts=attempts,
        Promises=promises, Uncovered=sorted(uncovered), Repairable=sorted(repairable),
        Defect_rechecks=rechecks, Earlier_probes=probes, Owner_report=report, Known_limits=limits(store, run),
        **{"Other verdicts": run["verdicts"],
           "Walk scenarios": {p: n for p, n in snapshot.graph.nodes.items() if n["kind"] == "walk"},
           "Owner words": {ref: n["words"] for ref, n in snapshot.graph.nodes.items() if n["kind"] == "dream"}})

    def save(source, answer):
        graph = snapshot.graph
        edits, errors = normalise_edits(graph, answer, {ref: ref for ref in graph.nodes})
        routes = (("probe", refs), ("repair", repairable.keys()))
        selected, repairs = (sorted(known & set(answer[key])) for key, known in routes)
        errors.extend(f"Use an exact finding ID instead of {key} {ref!r}."
                      for key, known in routes for ref in sorted(set(answer[key]) - known))
        direction = answer["direction"]
        groups, decision = chat.product_groups(answer["questions"]), answer["owner_decision"]
        subjects = set(answer["owner_promises"])
        owner = subjects and (decision == "access" and answer["limit"] and subjects <= uncovered
                              or decision == "scope" and direction and groups and subjects <= promises.keys())
        dispositions = {row["ref"]: row for row in answer["dispositions"]
                        if row["ref"] in active and row["reason"] and row["disposition"] in ("close", "downgrade")}
        if len(dispositions) != len(answer["dispositions"]):
            errors.append("Use an open must-fix ID once with a close or downgrade reason.")
        repairs = [repairable[ref] for ref in repairs if ref not in dispositions]
        if (decision != "pending" or subjects or groups) and not owner:
            errors.append("For testing limits use only Uncovered IDs in owner_promises and name the limit. "
                          "For scope use promise IDs and options.")
        if sum(map(bool, (owner, selected, repairs))) > 1 or owner and edits:
            errors.append("Choose one route: bound owner decision, probe, or repair.")
            owner, selected, repairs, edits = False, [], [], []
        repair = repairs or owner_report.get("from_C") and not change
        revision = snapshot.revision
        if direction and edits and not owner:
            mode = "scope-proposal" if change and answer["promise"] else "substantive"
            try:
                revision = spec.apply(dict(revision=revision, reason=direction, edits=edits, mode=mode),
                                      author="planner").revision
            except ValueError as error:
                errors.append(str(error))
            else:
                if revision == snapshot.revision:
                    errors.append("These edits left the trace unchanged. Return an actionable direction.")
        changed = revision != snapshot.revision
        usable = dispositions or owner or direction and (changed or selected or repair)
        if not usable:
            errors.append("The last walk changed no verdict. Give exact probe IDs, a trace edit or bound decision.")
        inference.correct(store, source, answer, errors)
        binding = dict(target=target, walk=run["ref"], candidate=run["candidate"])
        for row in dispositions.values():
            store.record("lead2_walk_disposition", {**binding, **row, "source": source})
        if not usable and stalled:
            gaps = [dict(reason=row["finding"], promises=[row["promise"]])
                    for row in findings if row["ref"] not in active and row.get("promise")]
            return _offer_limits(store, run, gaps, {row["promise"] for row in findings if row.get("promise")})
        if not usable:
            store.record("lead2_walk_finding", {"stalled": True, "source": source, "walk": run["ref"]})
        elif owner and decision == "access":
            _offer_limits(store, run, [dict(reason=answer["limit"], promises=sorted(subjects), access=True)])
        elif owner:
            chat.request(store, "Walk decision", revision, groups, proposal=direction, summary="Change the agreement",
                         **binding, change=True, promises=sorted(subjects), from_C=owner_report.get("from_C", False))
        elif changed and spec.snapshot(revision).mode == "scope-proposal":
            advance(store, "repair", source)  # The existing scope gate in build binds the exact proposal.
        elif selected or changed and not repair:
            store.record("lead2_walk_probe", {**binding, "revision": revision, "direction": direction,
                                             "findings": selected or sorted(refs)})
        elif repair:
            ref = store.record("lead2_walk_repair", {"target": target, "direction": direction,
                "defects": [row["ref"] for row in repairs],
                "stories": sorted({row["story"] for row in repairs if row["story"]}),
                "promises": sorted({row["promise"] for row in repairs if row.get("promise")})})
            advance(store, "repair", ref)

    inference.call(store, provider, question, normalise=build_brief.normalise, consume=save)


def _owner_report(store, target):
    handled = max((int(ref[1:]) for kind in ("lead2_walk_repair", "lead2_walk_probe")
                   for ref, _, row in store.records(kind) if row["target"] == target), default=0)
    return next((row for ref, _, row in reversed(store.records("lead2_walk_owner"))
                 if row["target"] == target and int(ref[1:]) > handled), {})


def _offer_limits(store, run, limits, promises=()):
    options = (("access", "Provide access", "Tell Seldon how to exercise the checks."),
               ("allow", "Accept these testing limits", "Leave these checks unverified.") if limits else
               ("scope", "Review the agreement", "Describe the promise that should change."))
    chat.request(store, "Walk decision", run["revision"],
                 [chat.group("limits", "How should we handle the remaining checks?", chat.options(*options))],
                 proposal="\n".join(row["reason"] for row in limits), summary="From the independent walk",
                 target=run["target"], candidate=run["candidate"], walk=run["ref"], limits=limits,
                 promises=sorted(set(promises) | {ref for row in limits for ref in row["promises"]}))


def _offer(store, run):
    options = chat.options(
        (chat.APPROVE, "Accept this build", "I tried the build and it meets the agreement."),
        (chat.REVISE, "Report what failed", "Describe what happened in your own words."))
    unverified = limits(store, run)
    advice = notes(store, run)
    proposal = "\n\n".join(filter(None, [run["guide_text"], *(row["reason"] for row in unverified),
                                            "Notes\n" + "\n".join(advice) if advice else ""]))
    chat.request(store, "C", run["revision"],
                 [chat.group(chat.ROUND, "Does this build meet the agreement?", options)],
                 proposal=owner_text(proposal, store.cursor().scratch),
                 summary="Try the walked build",
                 target=run["target"], candidate=run["candidate"], walk=run["ref"],
                 promises=[row["promise"] for row in run["promises"]] + list(ASSESSMENTS))


def _reply(store, spec, user, ref):
    item = store.read(ref)
    reply, words, choices = chat.read_reply(store, user, ref, item)
    subject = item["subject"]
    run = current_walk(store, spec.snapshot().revision)
    with store.transaction():
        raw = store.record("user_words", {**reply, "words": words, "origin": item["origin"]})
        store.cursor(replace(store.cursor(), decision=None, failure=""))
        bound = run and (run["ref"], run["candidate"], run["revision"]) == (
            subject.get("walk"), subject.get("candidate"), subject["lead2_revision"])
        if item["gate"] == "C":
            if not bound:
                advance(store, "repair", raw)
            elif not reply.get("words") and any(c["group"] == chat.ROUND and c["optionId"] == chat.APPROVE
                                              and not c["text"] for c in choices):
                scripted = store.read(store.cursor().kickoff)["setup"].get("scriptedUser", False)
                approval = store.record("approval", {"request": ref, "subject": subject,
                    "userWords": raw, "effect": "approve", "scripted": scripted})
                advance(store, "scripted" if scripted else "approve", approval)
            else:
                store.record("lead2_walk_owner", {"target": subject["target"], "words": words, "from_C": True})
                advance(store, "repair", raw)
        elif bound:
            picked = next((c["optionId"] for c in choices if c["group"] == "limits" and not c["text"]), None)
            if picked == "allow" and not reply.get("words"):
                for row in subject["limits"]:
                    store.record("lead2_walk_limit", {**row, "candidate": run["candidate"],
                                                     "revision": run["revision"], "reply": raw})
            elif subject.get("limits") and picked != "scope":
                store.record("lead2_walk_probe", {"target": run["target"], "walk": run["ref"],
                                                 "direction": words, "reply": raw})
            else:
                store.record("lead2_walk_owner", {"target": subject["target"], "words": words,
                                                 "from_C": subject.get("from_C", False),
                                                 "change": subject.get("change", False) or picked == "scope"})
