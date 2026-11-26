"""C9/C15/C26: record independent observations and their owner-visible limits."""

from pathlib import Path

from .. import gitwork, inference, proof
from .evidence import current_observations
from .graph import PROOF
from .owner_guide import owner_text
from .preview import launch_recipe

OUTCOMES = {"works": "passed", "broken": "failed", "can't-test-here": "not-verifiable-here"}
ASSESSMENTS = {"code_map": "Code map", "quality": "Quality bar", "guide": "Fresh-copy guide"}


def read_walk(store, ref, run):
    result = {**run, "ref": ref}
    for field in ("steps", "promises", "issues"):
        if field in run:
            result[field] = [_read_result(store, item) for item in run[field]]
    verdicts = run.get("verdicts") or {key: dict(outcome=run[key], reason=run.get("limit_details", {}).get(key, ""))
                                       for key in ASSESSMENTS}
    result["verdicts"] = {key: _read_result(store, item) for key, item in verdicts.items()}
    return result


def saved_evidence(store, saved):
    refs = (*saved.get("steps", []), *saved.get("checks", []), *saved.get("promises", []),
            *saved.get("verdicts", {}).values())
    return {ref: store.read(ref) for ref in refs if isinstance(ref, str)}


def current_walk(store, revision):
    walks = store.records("lead2_walk")
    if walks and (run := walks[-1][2])["revision"] == revision and run["candidate"] == store.cursor().candidate:
        return read_walk(store, walks[-1][0], run)


def supplement(store, spec, snapshot, prior, stories):
    if prior["revision"] == snapshot.revision:
        return prior
    valid = current_observations(spec, snapshot, prior["candidate"])
    return {**prior, "steps": [ref for ref in prior["steps"] if store.read(ref)["story"] in stories],
            "promises": [ref for ref in prior["promises"] if store.read(ref)["observation"] in valid]}


def save_walk(store, spec, target, snapshot, last, source, answer, *, stories, promises,
              evidence, saved, defects, deferrals, prior):
    attempt = store.read(source)["attempt"]
    session = next((row["session"] for _, _, row in reversed(store.records("call_session"))
                    if row["attempt"] == attempt), attempt)
    stamp = dict(source=source, session=session, candidate=last["candidate"], revision=snapshot.revision)
    errors = []
    subjects = promises | ASSESSMENTS.keys()
    for field, key, refs in (("promises", "promise", subjects), ("feedback", "story", stories),
                             ("findings", "story", stories), ("findings", "promise", subjects),
                             ("limits", "story", stories), ("limits", "promise", subjects), ("rechecks", "ref", defects)):
        errors.extend(f"{field}: replace {row[key]!r} with the corresponding {key} ID shown in the brief."
                      for row in answer[field] if row[key] and row[key] not in refs)
    errors.extend("Access findings need the promise ID they are about." for row in answer["findings"]
                  if row["owner"].lower() == "access" and not row["promise"])
    steps, captured = _steps(store, answer, stories, evidence, saved, session, errors)
    fresh = {row["screenshot"]: ref for ref, row in captured.items()
             if row["artifact"] and row["outcome"] in OUTCOMES}
    evidence_refs = {ref: ref for ref in saved_evidence(store, saved)}
    checks = {}
    for row in answer["checks"]:
        ref = row["ref"]
        if (not ref or ref in checks or ref in fresh or ref in evidence_refs
                or not row["action"] or not row["result"] or row["outcome"] not in ("works", "broken")):
            errors.append(f"Check {ref!r}: give a unique reference, the completed action and its observed result.")
        else:
            checks[ref] = store.record("lead2_walk_check", {**stamp, **row})
    fresh.update(checks)
    evidence_refs.update(fresh)
    passing = {ref for ref in fresh.values() if store.read(ref)["outcome"] == "works"}
    launch = launch_recipe(evidence, gitwork.paths(store.root.parent, stamp["candidate"])) or saved.get("launch")
    guide = _guide(store, answer, saved, stamp["candidate"])
    results = _results(store, spec, snapshot, answer, stories, subjects, saved, stamp, evidence_refs, errors)
    verdicts = {key: results.pop(key) for key in ASSESSMENTS}
    if not launch and any(evidence.glob("browser-visits-*.json")):
        errors.append("Record tracked startup with serve.cjs preview enabled and 127.0.0.1 and {port} in argv.")
    if _read_result(store, verdicts["guide"])["outcome"] == "works" and not (
            guide["guide_text"] and guide["sample_job"]):
        errors.append("Return the exact tracked guide path and the sample job you exercised.")
        verdicts["guide"] = dict(outcome="pending", reason=errors[-1])
    run = dict(stamp, target=target, final=last["outcome"] == "done", stories=stories, steps=steps,
               checks=saved.get("checks", []) + list(checks.values()), promises=list(results.values()),
               feedback=saved.get("feedback", []) + answer["feedback"], verdicts=verdicts, launch=launch, **guide)
    limits, open_defects, issues = _findings(store, stories, subjects, answer, defects, evidence_refs, passing,
                                           saved, stamp, deferrals, errors)
    run.update(defects=open_defects, limits=limits, deferrals=deferrals)
    issue_refs = list(open_defects) + [store.record("lead2_walk_finding", {**row, "source": source})
                                       for row in issues]
    old_notes = [ref for ref in saved.get("issues", []) if ref not in saved.get("defects", {})]
    inference.correct(store, source, answer, errors)
    store.record("lead2_walk", {**run, "issues": old_notes + issue_refs,
                                "stalled": bool(prior and not (_verdicts(store, run) - _verdicts(store, prior)
                                                or defects.keys() - open_defects.keys()))})


def _steps(store, answer, stories, evidence, saved, session, errors):
    saved_ids = saved.get("steps", [])
    replaced, captured = set(), {}
    for row in answer["steps"]:
        story, artifact = row["story"], _artifact(evidence, row["screenshot"])
        if row["outcome"] in OUTCOMES and row["outcome"] != "can't-test-here" and (
                not artifact or not all(row[key] for key in ("role", "action", "expected", "actual"))):
            errors.append(f"Step {row['action']!r}: give role, action, expected and actual results and the exact "
                          f"PNG basename instead of {row['screenshot']!r}. Move misplaced receipts to {evidence}.")
            row = {**row, "outcome": "pending"}
        if story not in stories:
            errors.append(f"Step {row['action']!r} uses {story!r}. Use its ID from Stories to walk.")
            continue
        scenario = _scenario(row["scenario"], set(stories[story]["walks"]), errors, story)
        if not scenario:
            row = {**row, "outcome": "pending"}
        if ref := row["replaces"]:
            old = store.read(ref) if ref in saved_ids else {}
            if (old.get("story"), old.get("scenario")) != (story, scenario):
                errors.append(f"Step {row['action']!r}: {ref} must be a saved step for this story and scenario.")
            elif row["outcome"] != "pending":
                replaced.add(ref)
        step = {**row, "scenario": scenario, "artifact": artifact, "session": session}
        captured[store.record("lead2_walk_step", step)] = step
    return [ref for ref in saved_ids if ref not in replaced] + list(captured), captured


def _citations(row, available, errors, subject):
    missing = set(row["evidence"]) - available.keys()
    if missing:
        errors.append(f"{subject}: replace {', '.join(sorted(missing))} with evidence IDs from this build.")
    return [available[ref] for ref in dict.fromkeys(row["evidence"]) if ref in available], not missing


def _results(store, spec, snapshot, answer, stories, subjects, saved, stamp, available, errors):
    graph = snapshot.graph
    results = {store.read(ref)["promise"]: ref for ref in saved.get("promises", [])}
    results.update(saved.get("verdicts", {}))
    addressed = {row["promise"]: row for row in answer["promises"]}
    for ref in sorted(subjects):
        linked = {story for story, row in stories.items() if ref in row["promises"]}
        witnesses = {w for w in graph.follow(ref, PROOF) if graph.nodes.get(w, {}).get("kind") in ("walk", "check")}
        traceable = ref not in graph.nodes or linked and witnesses and any(
            graph.nodes[r]["kind"] == "dream" for r in graph.follow(ref, upstream=True))
        row = addressed.get(ref)
        if not traceable and row and row["outcome"] in ("works", "broken"):
            errors.append(f"{ref}: the promise has no complete trace to a story and proof witness.")
        if not row and ref in results:
            continue
        row = row or dict(outcome="pending", reason="No walk observation.", evidence=[])
        refs, valid = _citations(row, available, errors, ref)
        outcome, reason = row["outcome"], row["reason"]
        if not reason or outcome in ("works", "broken") and not any(
                store.read(item)["outcome"] in ("works", "broken") for item in refs):
            errors.append(f"{ref}: give a reason and cite evidence from this build for any observed verdict.")
            valid = False
        witness = ""
        if traceable and witnesses:
            supplied = row.get("scenario") or ""
            cited = {store.read(item).get("scenario") for item in refs} & witnesses
            scenarios = {w for w in witnesses if graph.nodes[w]["kind"] == "walk"}
            options = witnesses if supplied else cited or scenarios or witnesses
            witness = (_scenario(supplied, options, errors, ref)
                       if supplied or outcome in ("works", "broken") else min(options))
            valid &= bool(witness)
            witness = witness or min(witnesses)
        if not valid or not traceable:
            outcome = "pending"
            reason = ("Awaiting valid evidence for this verdict." if traceable else
                      f"Link owner words through a story and promise {ref} to its walk scenario or check.")
        observation = (spec.observe(snapshot.revision, promises=[ref], witness=witness, commit=stamp["candidate"],
                       session=stamp["session"], outcome=OUTCOMES.get(outcome, outcome), result=reason,
                       walk_verdict=True).id if witness else None)
        results[ref] = store.record("lead2_walk_result", {**stamp, "promise": ref, "outcome": outcome,
                                  "reason": reason, "scenario": witness, "observation": observation,
                                  "evidence": refs, "trace": not traceable})
    return results


def _read_result(store, item):
    return {**store.read(item), "ref": item} if isinstance(item, str) else item


def _guide(store, answer, saved, candidate):
    path = answer["guide_path"].replace("\\", "/").removeprefix("./")
    if not path:
        return {key: saved.get(key, "") for key in ("guide_path", "guide_text", "sample_job")}
    guide = gitwork.blobs(store.root.parent, candidate, ("**",), named={path}).get(path)
    return dict(guide_path=path if guide else "", guide_text=guide.decode("utf-8", "replace") if guide else "",
                sample_job=answer["sample_job"])


def _rechecks(store, answer, defects, available, passing, saved, stamp, errors):
    checked = {row["ref"]: row for row in answer["rechecks"] if row["ref"] in defects and row["reason"]}
    active = {}
    for ref, previous in defects.items():
        if ref not in checked and saved:
            active[ref] = previous
            continue
        if ref not in checked:
            errors.append(f"Recheck defect {ref} or explain why it remains pending.")
        row = checked.get(ref, dict(ref=ref, outcome="pending", reason="No recheck reported.", evidence=[]))
        refs, valid = _citations(row, available, errors, ref)
        if row["outcome"] == "resolved" and (not valid or not passing.intersection(refs)):
            errors.append(f"Recheck {ref}: cite a new passing observation or completed check.")
            row = {**row, "outcome": "pending"}
        check = store.record("lead2_defect_recheck", {**stamp, **row, "evidence": refs})
        if row["outcome"] != "resolved":
            active[ref] = check
    return active


def _findings(store, stories, subjects, answer, defects, available, passing, saved, stamp, deferrals, errors):
    limit_refs = set(saved.get("limits", []))
    errors.extend(f"Cannot clear unknown limit {ref!r}. Use a saved limit ID."
                  for ref in answer["cleared_limits"] if ref not in limit_refs)
    limit_refs.difference_update(answer["cleared_limits"])
    findings = [row for row in answer["findings"] if row["finding"] and
                (not row["story"] or row["story"] in stories)]
    access = [dict(story=row["story"], promise=row["promise"], reason=row["finding"], decision="", access=True)
              for row in findings if row["owner"].lower() == "access" and row["promise"] in subjects and not row["must_fix"]]
    for row in answer["limits"] + access:
        if not row["reason"] or (row["story"] and row["story"] not in stories):
            continue
        if row["decision"] and row["decision"] not in deferrals:
            errors.append(f"Use an approved venue deferral ID instead of {row['decision']!r}.")
            row = {**row, "decision": ""}
        if not any(store.read(ref) == row for ref in limit_refs):
            limit_refs.add(store.record("lead2_walk_limit_observation", row))
    active = _rechecks(store, answer, defects, available, passing, saved, stamp, errors)
    known = {ref: store.read(ref) for ref in defects}
    issues = []
    for row in findings:
        if not row["must_fix"]:
            if row["owner"].lower() != "access":
                issues.append(row)
            continue
        match = next((ref for ref, old in known.items() if all(
            old.get(k) == row.get(k) for k in ("story", "promise", "assessment", "finding"))), None)
        if match:
            errors.append(f"Re-filed defect {match}: use its ID in rechecks, with the observed result.")
            if not active.get(match) or store.read(active[match])["outcome"] != "reproducible":
                active[match] = store.record("lead2_defect_recheck", dict(
                    stamp, ref=match, outcome="reproducible", reason=row["finding"]))
        else:
            ref = store.record("lead2_defect", {**stamp, **row})
            known[ref], active[ref] = row, None
    return sorted(limit_refs, key=lambda ref: int(ref[1:])), active, issues


def _verdicts(store, run):
    return ({(ref, row["outcome"]) for ref, row in _decisions(read_walk(store, "", run))
             if row["outcome"] != "pending"} |
            {(ref, check and store.read(check)["outcome"] or "reproducible")
             for ref, check in run.get("defects", {}).items() if not check or store.read(check)["outcome"] != "pending"})


def _decisions(run):
    return [(row["promise"], row) for row in run["promises"]] + list(run["verdicts"].items())


def _scenario(supplied, available, errors, subject):
    if supplied in available:
        return supplied
    if not supplied and len(available) == 1:
        return next(iter(available))
    if supplied or available:
        errors.append(f"{subject}: use the exercised scenario ID from {', '.join(sorted(available)) or 'its story'}.")
    return ""


def _artifact(evidence, name):
    if not name or Path(name).name != name or Path(name).suffix.lower() != ".png":
        return None
    path = evidence / name
    try:
        if path.resolve().parent == evidence.resolve() and proof.image(path.read_bytes()):
            return f"{evidence.name}/{name}"
    except OSError:
        return None


def open_defects(store, run):
    closed = {row["ref"] for _, _, row in store.records("lead2_walk_disposition")}
    return {ref: check for ref, check in run.get("defects", {}).items() if ref not in closed}


def blockers(store, run, checks):
    covered = {ref for row in checks for ref in row["promises"]}
    defects = [_read_result(store, ref) for ref in open_defects(store, run)]
    return defects + [
        dict(ref=row.get("ref", ref), promise=ref, story="", owner="planner", trace=row.get("trace", False),
             finding=f"{ASSESSMENTS.get(ref, ref)}: {row['reason']}")
        for ref, row in _decisions(run) if row["outcome"] != "works" and ref not in covered
        and (run["final"] or ref not in {"quality", "code_map"})
    ]


def notes(store, run):
    findings = run.get("issues", [])
    advice = [row["finding"] for row in findings if not (
        row.get("ref") in run.get("defects", {}) or row.get("stalled") or row.get("trace"))
        and (row.get("must_fix") is False or any(
            item.get("finding") == row["finding"] for item in
            store.read(row["source"]).get("answer", {}).get("findings", [])))]
    advice += [f"{store.read(row['ref'])['finding']} ({row['reason']})"
               for _, _, row in store.records("lead2_walk_disposition")
               if row["candidate"] == run["candidate"] and row["disposition"] == "downgrade"]
    return list(dict.fromkeys(advice))


def limits(store, run):
    approvals = {ref: row for ref, _, row in store.records("lead2_walk_limit")
                 if (row["candidate"], row["revision"]) == (run["candidate"], run["revision"])}
    approved = {item for row in approvals.values() for item in row.get("limit_refs", [row.get("ref")])}
    rows = {ref: store.read(ref) for ref in run.get("limits", []) if isinstance(ref, str)}
    rows.update({row.get("ref", ref): {**row, "promise": ref} for ref, row in _decisions(run)
                 if row["outcome"] == "can't-test-here"})
    rows.update({ref: row for ref, row in approvals.items() if row.get("reason")})
    result = {}
    for ref, row in rows.items():
        deferred = run.get("deferrals", {}).get(row.get("decision"))
        reason = owner_text(deferred or row["reason"], store.cursor().scratch)
        agreed = ref in approvals or ref in approved or bool(deferred)
        if reason:
            entry = result.setdefault(reason, dict(ref=ref, reason=reason, agreed=False, promises=[],
                                                   venue="story" in row and not row.get("access"), access=False))
            entry["agreed"] |= agreed
            entry["access"] |= row.get("access", False)
            entry["promises"] = list(dict.fromkeys(entry["promises"] +
                                     row.get("promises", [row["promise"]] if row.get("promise") else [])))
    return list(result.values())
