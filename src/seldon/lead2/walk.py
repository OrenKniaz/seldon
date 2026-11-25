"""C15/C16/C26: one independent product walk, then steer from its findings."""

from dataclasses import replace
from functools import partial
from pathlib import Path

from .. import gitwork, inference
from ..processes import reconcile_call
from ..schema import array, obj
from ..states import LEAD2_RUN
from ..states import advance as advance_run
from . import build_brief, chat, chunk_review
from .contracts import SPEC_EDITS, normalise_edits
from .graph import PROOF, Delta
from .owner_guide import owner_text
from .planning import UnusableAnswer
from .preview import launch_recipe

S, V = build_brief.STRING, build_brief.VERDICT
WALK = obj(
    steps=array(obj(story=S, role=S, action=S, expected=S, actual=S, outcome=V, screenshot=S)),
    promises=array(obj(promise=S, outcome=V, reason=S)),
    feedback=array(obj(story=S, note=S)),
    findings=array(obj(story=S, promise=S, defect=S, owner=S, finding=S)),
    limits=array(obj(story=S, reason=S)),
    code_map=V, code_map_reason=S, quality=V, quality_reason=S,
    guide=V, guide_reason=S, guide_path=S, sample_job=S,
)
STEER = obj(
    **build_brief.STEER["properties"],
    walk=array(obj(ref=S, scenario=S, interruption=S)),
    link=array(obj(source=S, targets=build_brief.REFS)),
    probe=build_brief.REFS,
)
CHANGE = obj(**STEER["properties"], promise=SPEC_EDITS["promise"])
advance = partial(advance_run, transitions=LEAD2_RUN)
OUTCOMES = {"works": "passed", "broken": "failed", "can't-test-here": "not-verifiable-here"}
ASSESSMENTS = {"code_map": "Code map", "quality": "Quality bar", "guide": "Fresh-copy guide"}
WALK_BRIEF = """Walk and judge the assigned stories in one fresh session. Follow the fresh-copy guide
in this checkout and use disposable sample data. For a web app, use checks/kit/serve.cjs with
{preview: true} on the primary launch. Pass the guide's startup arguments as an argv array and
use 127.0.0.1, {port} and {data} for local settings. This records the launch for Open this build.
Use checks/kit/browser.cjs headlessly. It watches for silence, so leave per-step timeouts at
their defaults. Exercise assigned stories on phone-sized and desktop views, separately signed in
as each role. Finish the whole walk and report all reproducible defects together.

Use local processes, files, clean copies and emulation to exercise real behavior. At each planned
interruption and recovery, inspect what each affected person can see and do before and after recovery.
What could disappear while this still passes? Use can't-test-here for unavailable physical devices or
external services. Name the missing prerequisite and record venue-only checks in limits.

Record one step per participant and visible outcome. Capture it at the original viewport with
shot(name, page), and use that participant and PNG basename on the same row. Use a supplied story
or scenario ID for each step.
For every material defect name the promise (when applicable), a short failure ID reusable on re-walk,
and the observed and expected behavior. Reuse the prior failure ID only for the same defect.
Give UX feedback and judge each promise against its not-met-if. Inspect the mapped source for incomplete
or placeholder behavior. Assess actual code and tests and the agreed quality bar. Find the fresh-copy
guide, exercise its sample job, and return the
exact repository-relative path and job you used. Report each assessment as a verdict and a reason."""



def step(store, spec, provider, user):
    cursor = store.cursor()
    if cursor.decision:
        return _reply(store, spec, user, cursor.decision)
    target, _, request = store.records("lead2_walk_target")[-1]
    run = next((row for _, _, row in reversed(store.records("lead2_walk"))
                if row["target"] == target), None)
    if cursor.state == "C":
        if run["revision"] != spec.snapshot().revision or run["candidate"] != cursor.candidate:
            return advance(store, "repair", target)
        return _offer(store, spec, run)
    reconcile_call(store)
    snapshot = spec.snapshot()
    if (run and "verdicts" in run and run["revision"] != snapshot.revision
            and _link_origins(store, spec, snapshot, run)):
        return
    if run is None or run["revision"] != snapshot.revision or "verdicts" not in run:
        return _walk(store, spec, provider, target, request)
    issues = _issues(run)
    owner = _owner_report(store, target)
    report = owner.get("words", "")
    if any(row["target"] == target and row["source"] == run["source"] and (issues or row.get("reply"))
           for _, _, row in store.records("lead2_walk_probe")):
        return _walk(store, spec, provider, target, request)
    held, waived = _defects(store, run, issues)
    actionable = [issue for issue in issues if _defect(issue) not in held | waived]
    if actionable or report:
        run = {**run, "owner_report": report, "change_requested": owner.get("change", False)}
        return _steer(store, spec, provider, target, run, actionable)
    if held:
        return _offer_defects(store, run, [issue for issue in issues if _defect(issue) in held])
    approved = _limits(store, run)
    limits = [p for p in _unverified(run) if p["promise"] not in approved]
    if limits:
        return _offer_limits(store, run, limits)
    if request["demo"] and store.read(request["delivery"])["outcome"] != "done":
        with store.transaction():
            advance(store, "continue", target)
        return
    with store.transaction():
        advance(store, "proved", target)


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
    if not final:
        delivered = {p["promise"] for p in mapping}
        stories = {ref: story for ref, story in stories.items() if delivered.intersection(story["promises"])}
    promises = {ref for ref, node in graph.nodes.items() if node["kind"] == "promise"} if final else {
        p for story in stories.values() for p in story["promises"]}
    previous = next((row for _, _, row in reversed(store.records("lead2_walk"))), None)
    workspace = gitwork.admit(store.root.parent, store.cursor().scratch, last["candidate"])
    evidence = Path(store.cursor().scratch) / "walk" / Path(workspace).name
    evidence.mkdir(parents=True)
    demo = store.read(request["demo"])["outcome"] if request["demo"] else ""
    direction = next((row.get("direction", "") for _, _, row in reversed(store.records("lead2_walk_probe"))
                      if row["target"] == target), "")
    question = build_brief.question("LEAD2-WALK", WALK, WALK_BRIEF, **{
        "Owner's words": [n["words"] for n in graph.nodes.values() if n["kind"] == "dream"],
        "Candidate": last["candidate"], "Selected demonstration": demo, "Stories to walk": stories,
        "Promises to judge": {p: graph.nodes[p] for p in sorted(promises)},
        "Scenarios": {w: graph.nodes[w] for w in sorted({w for s in stories.values() for w in s["walks"]})},
        "Accepted design, planned code and checks": {ref: n for ref, n in graph.nodes.items()
                                                     if n["kind"] in ("design", "check", "interface")},
        "Actual code map": mapping,
        "Evidence directory": str(evidence), "Prior findings": previous["findings"] if previous else [],
        "Rewalk direction": direction, "Venue limits from feature review": chunk_review.venue_limits(store),
    })
    question["facts"]["Prior defects"] = [f for f in (previous or {}).get("findings", []) if _defect(f)]
    question["execution"] = {"walk": workspace, "evidence": str(evidence)}

    def save(source, answer):
        _save(store, spec, target, snapshot, last, source, answer, stories, promises, mapping, evidence)

    try:
        inference.call(store, provider, question, normalise=build_brief.normalise, consume=save)
    finally:
        reconcile_call(store)
        gitwork.git(store.root.parent, "worktree", "remove", "--force", workspace)


def _story_ref(stories, ref):
    if ref in stories:
        return ref
    owners = [story for story, body in stories.items() if ref in body["walks"]]
    return owners[0] if len(owners) == 1 else ""


def _save(store, spec, target, snapshot, last, source, answer, stories, promises, mapping, evidence):
    attempt = store.read(source)["attempt"]
    session = next((row["session"] for _, _, row in reversed(store.records("call_session"))
                    if row["attempt"] == attempt), attempt)
    steps = []
    unresolved = set()
    for row in answer["steps"]:
        story = next(iter(stories)) if len(stories) == 1 else _story_ref(stories, row["story"])
        artifact = _artifact(evidence, row["screenshot"])
        outcome = row["outcome"]
        if not story:
            outcome = "pending"
        elif not (artifact and all(row[key] for key in ("role", "action", "expected", "actual"))):
            unresolved.add(story)
            if outcome == "works":
                outcome = "pending"
        steps.append({**row, "story": story, "artifact": artifact, "outcome": outcome})
    unresolved |= stories.keys() - {row["story"] for row in steps if row["outcome"] != "pending"}
    results = {row["promise"]: row for row in answer["promises"]
               if row["promise"] in promises and row["reason"]}
    for ref in sorted(promises):
        row = results.setdefault(ref, {"promise": ref, "outcome": "pending",
                                      "reason": "The walk returned no observation."})
        if row["outcome"] == "works" and any(ref in stories[story]["promises"] for story in unresolved):
            row.update(outcome="pending", reason="A story observation is missing or lacks a captured result.")
    candidate = last["candidate"]
    gaps = _observe(spec, snapshot, candidate, session, results.values())
    launch = launch_recipe(evidence, gitwork.paths(store.root.parent, candidate))
    if not launch and stories and any(evidence.glob("browser-visits-*.json")):
        gaps.append(dict(story=next(iter(stories)), owner="planner",
                         finding="Record the web startup with the local app helper’s preview option."))
    complete_map = all(any(row["promise"] == ref and row["modules"] and row["tests"] for row in mapping)
                       for ref in promises)
    path = answer["guide_path"].replace("\\", "/").removeprefix("./")
    tracked = {entry.split(b"\t", 1)[1].decode()
               for entry in gitwork.tree_entries(store.root.parent, candidate)
               if entry.startswith((b"100644 blob ", b"100755 blob "))}
    guide = (gitwork.blobs(store.root.parent, candidate, ("**",), named={path}).get(path)
             if path in tracked else None)
    guide = guide.decode("utf-8", "replace") if guide else ""
    verdicts = {key: {"outcome": answer[key], "reason": answer[f"{key}_reason"]} for key in ASSESSMENTS}
    if not complete_map:
        verdicts["code_map"] = {"outcome": "pending",
                                "reason": "The candidate map is missing module or test paths."}
    if not guide or not answer["sample_job"]:
        missing = "The walk needs a tracked guide and an exercised sample job."
        verdicts["guide"] = {"outcome": "pending", "reason": missing}
    findings = [{**row, "story": _story_ref(stories, row["story"])}
                for row in answer["findings"] if row["finding"]]
    for story in unresolved:
        gaps.append(dict(story=story, owner="planner", finding="Capture the result for each participant."))
    limits = [row for row in answer["limits"] if row["reason"]]
    if last["outcome"] == "done":
        limits += [{"story": "", "reason": reason} for reason in chunk_review.venue_limits(store)]
    store.record("lead2_walk", {"target": target, "source": source, "session": session,
        "candidate": candidate, "revision": snapshot.revision, "final": last["outcome"] == "done",
        "stories": stories, "steps": steps, "promises": list(results.values()),
        "feedback": [{**row, "story": ref} for row in answer["feedback"]
                     if row["note"] and (ref := _story_ref(stories, row["story"]))],
        "findings": findings + gaps, "limits": limits,
        "map": mapping, "verdicts": verdicts, "guide_path": path if guide else "",
        "guide_text": guide, "launch": launch, "sample_job": answer["sample_job"]})


def _observe(spec, snapshot, candidate, session, results):
    graph = snapshot.graph
    gaps = []
    for result in results:
        ref = result["promise"]
        witness = min((w for w in graph.follow(ref, PROOF) if graph.nodes[w]["kind"] == "walk"), default=None)
        origins = graph.follow(ref, upstream=True)
        source = any(graph.nodes[r]["kind"] == "dream" for r in origins)
        result["traceable"] = bool(witness and source)
        if not result["traceable"]:
            if not source:
                stories = ", ".join(sorted(r for r in origins if graph.nodes[r]["kind"] == "story"))
                detail = f"Link an owner word ID to story {stories} for promise {ref}."
            else:
                detail = f"Link a walk scenario to promise {ref}."
            story = next((r for r in sorted(origins) if graph.nodes[r]["kind"] == "story"), "")
            gaps.append(dict(story=story if not witness else "", owner="planner", finding=detail, trace=True))
        elif result["outcome"] in OUTCOMES:
            spec.observe(snapshot.revision, promises=[ref], witness=witness, commit=candidate,
                         session=result.setdefault("session", session), outcome=OUTCOMES[result["outcome"]],
                         result=result["reason"])
    return gaps


def _link_origins(store, spec, snapshot, run):
    delta = Delta(spec.snapshot(run["revision"]).graph, snapshot.graph)
    if delta.nodes or not delta.links or any(
        link not in snapshot.graph.links or snapshot.graph.kinds(link) != ("dream", "story")
        for link in delta.links
    ):
        return False
    with store.transaction():
        gaps = _observe(spec, snapshot, run["candidate"], run["session"], run["promises"])
        findings = [row for row in run["findings"] if not row.get("trace") and not (
            row["owner"] == "planner" and row["finding"].startswith("Link a walk and owner words to "))]
        store.record("lead2_walk", {**run, "revision": snapshot.revision, "findings": findings + gaps})
    return True


def _artifact(evidence, name):
    if not name or Path(name).name != name or Path(name).suffix.lower() != ".png":
        return None
    path = evidence / name
    if path.is_file() and path.resolve().parent == evidence.resolve():
        return f"{evidence.name}/{name}"


def verdict(run, key):
    if "verdicts" in run:
        return run["verdicts"][key]
    return {"outcome": run[key], "reason": run.get("limit_details", {}).get(key, "")}


def _issues(run):
    broken = {step["story"] for step in run["steps"] if step["outcome"] == "broken"}
    broken |= {ref for ref, story in run["stories"].items() if any(
        p["outcome"] == "broken" and p["promise"] in story["promises"] for p in run["promises"])}
    repair = any(verdict(run, key)["outcome"] == "broken" for key in ASSESSMENTS)
    findings = [{**row, "owner": owner} for row in run["findings"]
                if ((owner := row["owner"].lower()) == "planner"
                    and (row.get("trace") or row["story"] in run["stories"]))
                or (owner not in ("planner", "access") and (repair or row["story"] in broken))]
    observed = {s["story"] for s in run["steps"]}
    for ref in run["stories"].keys() - observed:
        findings.append(dict(story=ref, owner="planner", finding="The story has no recorded walk steps."))
    for key, title in ASSESSMENTS.items():
        assessment = verdict(run, key)
        outcome = assessment["outcome"]
        if outcome != "works" and (key != "quality" or outcome != "can't-test-here"):
            if run["final"] or key == "guide":
                detail = assessment["reason"] or outcome
                awaiting_walk = key == "quality" or key == "guide" and run["guide_text"]
                owner = "planner" if outcome == "pending" and awaiting_walk else "builder"
                findings.append(dict(story="", owner=owner, finding=f"{title}: {detail}"))
    for row in run["steps"]:
        if row["outcome"] == "broken" and not any(
            f["story"] == row["story"] and _defect(f) for f in findings
        ):
            findings.append(dict(story=row["story"], owner="builder",
                                 finding=f"{row['action']}: expected {row['expected']}; saw {row['actual']}"))
        elif row["story"] and row["outcome"] == "pending":
            findings.append(dict(story=row["story"], owner="planner", finding=f"Verify {row['action']}."))
    for row in run["promises"]:
        if row["outcome"] == "pending":
            findings.append(dict(story="", owner="planner", finding=row["reason"]))
        elif row["outcome"] == "broken" and not any(f.get("promise") == row["promise"] and _defect(f)
                                                 for f in findings):
            findings.append(dict(story="", owner="builder", finding=row["reason"],
                                 promise=row["promise"], defect=row["reason"]))
    return findings


def _steer(store, spec, provider, target, run, findings):
    snapshot = spec.snapshot()
    change = run["change_requested"]
    prompt = ("Propose the owner-requested promise edit for approval. Ask if the change is unclear. "
              if change else "Preserve promises. Send incomplete observations back to the walker. ")
    question = build_brief.question("LEAD2-WALK-STEER", CHANGE if change else STEER,
        "Direct one repair with all plausible defects together. Verify the gap still exists in this build. "
        "Fill missing owner trace links without rewording existing scenarios. "
        "Change or add walk scenarios only for stories named in the findings. "
        "Link owner word IDs to story IDs, and link any new scenario to its story. Use story IDs for probes. "
        + prompt,
        Stories=run["stories"], Observations=run["promises"], Findings=findings,
        Owner_report=run["owner_report"], Known_limits=_limits(store, run))
    question["facts"]["Walk scenarios"] = {p: n for p, n in snapshot.graph.nodes.items()
                                          if n["kind"] == "walk"}
    question["facts"]["Owner words"] = {ref: n["words"] for ref, n in snapshot.graph.nodes.items()
                                        if n["kind"] == "dream"}

    held = []
    report = run["owner_report"]

    def save(_, answer):
        affected = {f["story"] for f in findings if f["story"] in run["stories"]}
        affected |= {ref for ref, story in run["stories"].items() if any(
            row["outcome"] == "broken" and row["promise"] in story["promises"]
            for row in run["promises"])}
        edits = _plan_edits(snapshot.graph, answer, affected)
        updated = snapshot.revision
        if edits:
            updated = spec.apply(dict(revision=snapshot.revision, reason=answer["direction"],
                                      mode="substantive", edits=edits), author="planner").revision
        if change and (proposed := [row for row in answer["promise"]
                                    if snapshot.graph.nodes.get(row["ref"], {}).get("kind") == "promise"
                                    and all(row[key] for key in ("action", "result", "not_met_if"))]):
            graph = spec.snapshot().graph
            rows = {"promise": proposed, "retire": [], "link": [], "unlink": [], "replaces": []}
            changes, _ = normalise_edits(graph, rows, {ref: ref for ref in graph.nodes})
            if changes:
                proposal = spec.apply(dict(revision=updated, reason=answer["direction"],
                                           mode="scope-proposal", edits=changes), author="planner")
                if not proposal.accepted:
                    advance(store, "repair", target)
                    return
        probe = [ref for line in answer["probe"]
                 if (ref := _story_ref(run["stories"], line.partition(":")[0]))]
        groups = chat.product_groups(answer["questions"])
        repair = any(f["owner"] != "planner" for f in findings) or report
        if groups:
            chat.request(store, "Walk decision", spec.snapshot().revision, groups,
                         proposal=answer["direction"], summary="From the independent walk", target=target,
                         candidate=run["candidate"], source=run["source"], findings=findings,
                         promises=run["promises"])
        elif repair:
            _repair(store, target, answer["direction"], findings, run["promises"], run["owner_report"])
        elif probe and updated == snapshot.revision:
            store.record("lead2_walk_probe", {"target": target, "source": run["source"],
                "stories": probe, "direction": "\n".join([answer["direction"], *answer["probe"]])})
        elif updated == snapshot.revision:
            held.append(answer["direction"])

    inference.call(store, provider, question, normalise=build_brief.normalise, consume=save)
    if held:
        raise UnusableAnswer(f"Walk paused. {held[0]}")


def _plan_edits(graph, answer, affected):
    def kind(ref):
        return graph.nodes.get(ref, {}).get("kind")

    stories = _stories(graph)
    scope = {ref for story in affected & stories.keys()
             for ref in [story, *stories[story]["walks"], *stories[story]["promises"]]}
    new_walks = {row["ref"] for row in answer["walk"] if row["ref"] not in graph.nodes}
    new_walks &= {target for row in answer["link"] if row["source"] in scope for target in row["targets"]}
    walks, links = [], []
    for index, row in enumerate(answer["walk"]):
        if not row["scenario"] or not row["interruption"]:
            continue
        ref = row["ref"]
        if ref not in scope | new_walks:
            continue
        if kind(ref) == "story":
            name = f"walk-for-{ref}-{index}"
            walks.append({**row, "ref": name})
            links.append({"source": ref, "targets": [name]})
        else:
            walks.append(row)
    for row in answer["link"]:
        for target in row["targets"]:
            source = row["source"]
            if (kind(source) == "walk" or kind(target) == "walk" or
                    source in new_walks or target in new_walks) and not ({source, target} & scope):
                continue
            if {kind(source), kind(target)} == {"dream", "promise"}:
                words, promise = (source, target) if kind(source) == "dream" else (target, source)
                stories = [ref for ref in graph.follow(promise, upstream=True) if kind(ref) == "story"]
                links.append({"source": words, "targets": sorted(stories)})
            else:
                links.append({"source": source, "targets": [target]})
    rows = {"walk": walks, "link": links, "retire": [], "unlink": [], "replaces": []}
    edits, _ = normalise_edits(graph, rows, {ref: ref for ref in graph.nodes})
    return edits


def _repair(store, target, direction, findings=(), promises=(), owner_report=""):
    details = [r["finding"] for r in findings if r["owner"].lower() not in ("access", "planner")]
    if owner_report:
        details.append(owner_report)
    repair = store.record("lead2_walk_repair", {"target": target,
        "direction": "\n".join(dict.fromkeys([direction, *details])),
        "defects": sorted({key for r in findings if (key := _defect(r))}),
        "stories": sorted({r["story"] for r in findings if r["story"] and r["owner"] != "planner"}),
        "promises": sorted({r["promise"] for r in promises if r["outcome"] == "broken"})})
    advance(store, "repair", repair)


def _limits(store, run):
    approved = {p["promise"]: p["reason"] for _, _, row in store.records("lead2_walk_limit")
                if row["candidate"] == run["candidate"] and row["revision"] == run["revision"]
                for p in row["limits"]}
    _, waived = _defects(store, run, ())
    approved.update((_defect(row), "Known defect: " + row["finding"]) for row in _issues(run)
                    if _defect(row) in waived)
    return approved


def _unverified(run):
    limits = [p for p in run["promises"] if p["outcome"] == "can't-test-here"]
    for key, label in ASSESSMENTS.items():
        assessment = verdict(run, key)
        if assessment["outcome"] == "can't-test-here":
            detail = assessment["reason"] or "Unverified in this environment."
            limits.append(dict(promise=key, reason=f"{label}: {detail}"))
    for reason in dict.fromkeys(row["reason"] for row in run.get("limits", [])):
        limits.append(dict(promise=reason, reason=reason))
    return limits


def _defect(issue):
    if issue.get("promise") and issue.get("defect") and issue["owner"].lower() == "builder":
        return f"{issue['promise']}:{issue['defect'].casefold()}"
    return ""


def _defects(store, run, issues):
    reset = {}
    waived = set()
    for ref, _, decision in store.records("lead2_defect_direction"):
        for key in decision["defects"]:
            if decision["choice"] == "allow" and decision["revision"] == run["revision"]:
                waived.add(key)
            elif decision["choice"] == "approach":
                reset[key] = int(ref[1:])
    attempts = {}
    for ref, _, repair in store.records("lead2_walk_repair"):
        for key in repair.get("defects", []):
            if int(ref[1:]) > reset.get(key, 0):
                attempts[key] = attempts.get(key, 0) + 1
    held = {_defect(issue) for issue in issues if attempts.get(_defect(issue), 0) >= 2}
    return held - waived, waived


def _offer_defects(store, run, findings):
    options = chat.options(
        ("approach", "Try a different approach", "Ask the planner to rethink the repair."),
        ("allow", "Accept this as a known limit", "Keep the defect visible and continue."),
        ("change", "Change the promise", "Describe the outcome you want instead."),
        ("stop", "Stop", "End this run with the finding recorded."),
    )
    group = chat.group("defect", "The defect remains after two repairs. How should we continue?", options)
    chat.request(store, "Walk decision", run["revision"], [group],
                 proposal="\n".join(dict.fromkeys(row["finding"] for row in findings)),
                 summary="A defect needs your decision", target=run["target"], candidate=run["candidate"],
                 source=run["source"], defects=sorted({_defect(row) for row in findings}))


def _owner_report(store, target):
    handled = max((int(ref[1:]) for kind in ("lead2_walk_repair", "lead2_walk_probe")
                   for ref, _, row in store.records(kind) if row["target"] == target), default=0)
    return next((row for ref, _, row in reversed(store.records("lead2_walk_owner"))
                 if row["target"] == target and int(ref[1:]) > handled), {})


def _offer_limits(store, run, limits):
    options = chat.options(("access", "Provide access", "Tell Seldon how to exercise the checks."),
                           ("allow", "Accept these testing limits", "Leave these checks unverified."))
    chat.request(store, "Walk decision", run["revision"],
                 [chat.group("limits", "How should we handle these unverified checks?", options)],
                 proposal="\n".join(p["reason"] for p in limits),
                 summary="From the independent walk", target=run["target"], candidate=run["candidate"],
                 source=run["source"], limits=limits)


def _offer(store, spec, run):
    setup = store.read(store.cursor().kickoff)["setup"]
    options = chat.options(
        (chat.APPROVE, "Accept this build", "I tried the build and it meets the agreement."),
        (chat.REVISE, "Report what failed", "Describe what happened in your own words."))
    limits = _limits(store, run)
    proposal = "\n\n".join(filter(None, [run["guide_text"], *limits.values()]))
    chat.request(store, "C", spec.snapshot().revision,
                 [chat.group(chat.ROUND, "Does this build meet the agreement?", options)],
                 proposal=owner_text(proposal, store.cursor().scratch),
                 summary="Scripted acceptance" if setup.get("scriptedUser") else "Try the walked build",
                 target=run["target"], candidate=run["candidate"])


def _reply(store, spec, user, ref):
    item = store.read(ref)
    reply, words, choices = chat.read_reply(store, user, ref, item)
    subject = item["subject"]
    with store.transaction():
        raw = store.record("user_words", {**reply, "words": words, "origin": item["origin"]})
        store.cursor(replace(store.cursor(), decision=None, failure=""))
        if item["gate"] == "C":
            if (spec.snapshot().revision != subject["lead2_revision"]
                    or store.cursor().candidate != subject["candidate"]):
                advance(store, "repair", raw)
            elif not reply.get("words") and any(c["group"] == chat.ROUND and c["optionId"] == chat.APPROVE
                                              and not c["text"] for c in choices):
                scripted = store.read(store.cursor().kickoff)["setup"].get("scriptedUser", False)
                approval = store.record("approval", {"request": ref, "subject": subject,
                    "userWords": raw, "effect": "approve", "scripted": scripted})
                advance(store, "scripted" if scripted else "approve", approval)
            else:
                store.record("lead2_walk_owner", {"target": subject["target"], "words": words})
                advance(store, "repair", raw)
        elif "defects" in subject:
            if (subject["lead2_revision"] != spec.snapshot().revision
                    or subject["candidate"] != store.cursor().candidate):
                return
            choice = choices[0]["optionId"]
            if choice == "stop":
                advance(store, "stop", raw)
            elif choice == "allow" and not reply.get("words") and not choices[0]["text"]:
                store.record("lead2_defect_direction", {"defects": subject["defects"],
                    "choice": "allow", "revision": subject["lead2_revision"], "reply": raw})
            else:
                store.record("lead2_defect_direction", {"defects": subject["defects"],
                    "choice": "approach", "revision": subject["lead2_revision"], "reply": raw})
                direction = "Try a different repair approach." if choice == "approach" else words
                if choice == "change":
                    direction = "The owner wants to discuss changing the promise: " + words
                store.record("lead2_walk_owner", {"target": subject["target"], "words": direction,
                                                 "change": choice == "change"})
        elif any(c["group"] == "limits" and c["optionId"] == "allow" and not c["text"] for c in choices):
            store.record("lead2_walk_limit", {**subject, "revision": subject["lead2_revision"], "reply": raw})
        elif "limits" in subject:
            store.record("lead2_walk_probe", {"target": subject["target"], "source": subject["source"],
                                             "stories": [], "direction": words, "reply": raw})
        else:
            run = next((row for _, _, row in reversed(store.records("lead2_walk"))
                        if row["source"] == subject["source"]), None)
            findings = _issues(run) if run else subject["findings"]
            direction = words + "\n" + item["ask"]["proposal"]
            if any(row["owner"] == "builder" for row in findings):
                _repair(store, subject["target"], direction, findings, subject["promises"])
            else:
                store.record("lead2_walk_probe", {"target": subject["target"], "source": subject["source"],
                                                 "stories": [], "direction": direction, "reply": raw})
