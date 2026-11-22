"""C3/C15/I2/I10; R2/R9/R11: one area question, criterion verdicts, local fixup, code ANDs."""

from dataclasses import replace
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp

from . import architecture, features, fixups, gitwork, inference, plan, proof, questions
from .answers import Mismatch
from .observations import code_shape, judge_fields
from .schema import POSITION, REFS, TEXT, answer_schema, array, choice, folded_judge_schema, obj
from .states import advance
LEAD_BINDINGS = {"tryItGuide": {"result": "The documented steps work from a fresh checkout."},
                 "sourceAcceptance": {"result": "Real implementation with no stubs, mocks or missing behavior."}}


WALKTHROUGH_CHECKOUT = (
    "Each final check starts in a clean Git checkout of the delivered commit, separate from the delivery. "
    "The recorded command is the check's entry point. Its execution and cleanup establish that the check "
    "ran in that checkout. Use its observed results to identify the documented steps it exercised."
)


def folded_owner(store, document):
    if owner := plan.sole_owner(document):
        row = store.unit(owner)
        if row and row.state != "DROPPED" and plan.select(document, "unit")[owner]["requirements"]:
            return owner


def folded_subjects(document):
    return {
        **plan.select(document, "feature"),
        **subjects(document, "FLOW"),
        **subjects(document, "PRODUCT"),
        "sourceAcceptance": {"criterion": "Real implementation, with no stubs, mocks or missing work",
            "verification": "Show that checks exercise real behavior and fail for the right reason."},
    }


def milestone(store, provider, base, commit, subjects, current, refs):
    cursor = store.cursor()
    source, prior = next(((ref, row) for ref, _, row in reversed(store.records("lead_verdict"))
                          if (row["milestone"], row["B"]) == (cursor.wave, cursor.B)), (None, {}))
    if prior.get("commit") == commit:
        return source, prior
    prior = prior if prior.get("fixes") else {}
    subjects = {**subjects, **LEAD_BINDINGS}
    observations = {ref: store.read(ref) for ref in refs}
    executed = {ref: row for ref, row in observations.items() if row["commit"] == commit and proof.executed(row)}
    links = [obj(action=POSITION)]
    if executed:
        links.append(obj(observation=choice(*executed)))
    finding = dict(promise=choice(*subjects), reason=TEXT)
    defect = obj(**finding, kind=choice("defect"), field=choice("action", "result", "notMetIf"),
                 evidenceLink={"anyOf": links})
    other = obj(**finding, kind=choice("uncertain", "amend", "note"), evidence=TEXT)
    schema = obj(answer=obj(why=TEXT, actions=array(obj(
        action=TEXT, actual=TEXT, promises=array(choice(*subjects)), baseline={"type": "string"}, screenshots=REFS)),
        feedback=array(obj(step=TEXT, note=TEXT), minimum=1), findings=array({"anyOf": [defect, other]})))
    runtime = Path(mkdtemp(prefix="guard-", dir=cursor.scratch))
    work, before, evidence_dir = (runtime / name for name in ("product", "base", "evidence"))
    question = questions.question("JUDGE",
        ("Re-walk failed and repair-affected subjects only. Reuse Base and unaffected evidence. "
         if prior else "Walk each B promise on Candidate. For new promises record the unmet Base action in "
         "baseline; reuse a shared Base failure for every promise it blocks. ")
        + "Use the real CLI, API or headless browser. Exercise restart, failure and manual guide steps when "
        "in scope. For keyboard access, Tab through the UI and operate controls with keys. Measure rendered "
        "text/background contrast. For SQLite integrity, query the file directly. "
        "Inspect source against B for stubs, mocks or gaps. Link actions or findings to each subject revisited. "
        "Use uncertain for missing observations or machine limits; say what was partly seen and why. "
        "Give human feedback on a "
        "screen or action, describing friction or ease. If blocked, describe the blocked step. "
        "For a demonstrated defect, select the violated B field and link the evidence for that promise: "
        "the one-based position in this answer's actions or an executed observation ID from Observations. "
        "Explain how the linked result shows the product defect or materially broken retained witness, "
        "even if its process exited successfully. Missing observation belongs to the guard. "
        "Use amend for ambiguous B and note for advice. "
        "Use one plain command per action inside the copies and their runtime. Record denials without retrying. "
        "Use supplied clean-checkout observations for guide commands already run. On resume finish missing actions. "
        "Save evidence in SELDON_EVIDENCE_DIR, cite PNG names and stop your processes.",
        {"Candidate": str(work), "Candidate commit": commit, "Base": str(before), "Base commit": base,
         "Guide path": plan.GUIDE_PATH, "Subjects": subjects, "New promises": list(current),
         "Observations": observations,
         **({"Previous walk": {k: prior[k] for k in ("commit", "actions", "fixes", "limitations")},
             "Repair delta": gitwork.git(store.root.parent, "diff", "--no-ext-diff", "-U1", prior["commit"], commit)}
            if prior else {})}, schema,
        [{"actions": [{"action": "Run hello Ada", "actual": "Hello, Ada!", "promises": [next(iter(subjects))],
                      "baseline": "Run hello Ada: entry point absent.", "screenshots": []}],
          "findings": [], "feedback": [{"step": "Greeting", "note": "Easy to scan."}], "why": "Exact greeting."}],
        execution={"walk": str(work)})

    def consume(ref, answer):
        fixes, findings = [], []
        for index, item in enumerate(answer["findings"]):
            promise, kind, reason = (item[k] for k in ("promise", "kind", "reason"))
            if kind != "defect":
                findings.append({**item, "quote": ""})
                continue
            bound = subjects[promise]
            quote = bound.get(item["field"]) if isinstance(bound, dict) else None
            if not quote:
                raise Mismatch(f"findings[{index}].field", f"Select an approved B field for {promise}.")
            link = item["evidenceLink"]
            if "action" in link:
                position = link["action"]
                action = answer["actions"][position - 1] if position <= len(answer["actions"]) else None
                if not (action and promise in action["promises"] and action["action"].strip()
                        and action["actual"].strip()):
                    raise Mismatch(f"findings[{index}].evidenceLink",
                                   f"Link a current action with an actual result for {promise}, or use uncertain.")
                evidence, actual = action["action"], action["actual"]
            else:
                observation = link["observation"]
                row = executed[observation]
                if promise not in row["requirements"]:
                    raise Mismatch(f"findings[{index}].evidenceLink",
                                   f"Observation {observation} does not cover {promise}. "
                                   "Link an observation or current action that covers it, or use uncertain.")
                evidence = f"{observation}: {row['command']}"
                details = judge_fields(row)
                if row["evidenceFiles"]:
                    details["evidenceFiles"] = row["evidenceFiles"]
                actual = proof.explain([row]) + (f"\nRecorded witness: {details}" if details else "")
            findings.append({**item, "quote": quote, "evidence": evidence})
            fixes.append(f"B: {quote}\nReproduce: {evidence}\nObserved: {actual}\nDefect: {reason}")

        files = {p.name: store.artifact(p.read_bytes(), ref) for p in sorted(evidence_dir.iterdir())
                 if p.is_file() and p.resolve().parent == evidence_dir}
        actions = [{**a, "commit": commit, "screenshots": [
            files[n] for n in a["screenshots"] if n in files and proof.image(Path(files[n]).read_bytes())
        ]} for a in answer["actions"]]
        walked = {p for a in actions for p in a["promises"]}
        affected = walked | {f["promise"] for f in findings if f["kind"] != "note"} | {
            f["promise"] for f in prior.get("findings", []) if f["kind"] == "defect"}
        baselines = set(prior.get("baselines", [])) | {p for a in actions if a["baseline"].strip() for p in a["promises"]}
        result = {**answer, "source": ref, "commit": commit, "milestone": cursor.wave, "B": cursor.B,
                  "previous": source if prior else None, "baselines": sorted(baselines),
                  "fixes": fixes, "findings": findings, "evidenceFiles": prior.get("evidenceFiles", {}) | files,
                  "amendments": [f"{f['promise']}: {f['reason']}" for f in findings if f["kind"] == "amend"],
                  "limitations": [f for f in prior.get("limitations", []) if f["promise"] not in affected]
                                 + [f for f in findings if f["kind"] == "uncertain"]}
        result["actions"] = actions + [
            {**a, "promises": kept} for a in prior.get("actions", [])
            if (kept := [p for p in a["promises"] if p not in affected])]
        linked = {p for a in result["actions"] for p in a["promises"]}
        missing = dict.fromkeys(current.keys() - baselines, "Failure on the base was not observed.")
        missing.update(dict.fromkeys(subjects.keys() - linked, "No independent action recorded."))
        result["limitations"] += [{"promise": p, "reason": missing[p]} for p in subjects
                                  if p in missing and not any(f["promise"] == p for f in result["limitations"])]
        store.record("lead_verdict", result)

    for directory in (evidence_dir, runtime / "temp", work, before):
        directory.mkdir()
    index = {"GIT_INDEX_FILE": str(runtime / "index")}
    for revision, copy in ((base, before), (commit, work)):
        gitwork.git(store.root.parent, "read-tree", revision, env=index)
        gitwork.git(store.root.parent, "checkout-index", "--all", "--prefix=" + copy.as_posix() + "/", env=index)
    inference.call(store, provider, question, binding=commit, consume=consume)
    rmtree(runtime)
    ref, _, result = store.records("lead_verdict")[-1]
    return ref, result


def walk_feedback(store, proof_ref):
    verdict = store.read(store.read(proof_ref)["judge"])
    return {key: verdict.get(key) for key in ("why", "actions", "feedback", "findings", "evidenceFiles")}


def folded_brief(store, document, owner, observations):
    commit = store.cursor().candidate
    question = questions.judge_brief(store, owner, commit, observations)
    subjects_to_judge = folded_subjects(document)
    facts = question["facts"]
    if features.enabled("judgeEvidence"):
        facts["Code shape (measured)"] = code_shape(facts["Product files"])
        subjects_to_judge["codeQuality"] = {
            "criterion": "Main implementation is readable and safe to change.",
            "verification": (
                "Use the owner's delivered implementation under Product files. Mark a defect must-fix "
                "when a maintainer would struggle to read or safely change the code, even when behavior "
                "checks pass. Look for packed statements that hide control flow, single-line pages or "
                "scripts, cryptic names, or validation, persistence and lifecycle tangled in one expression. "
                "Cite the file or symbol, the concrete maintenance consequence and the smallest correction. "
                "Formatting preferences alone are nits. For example, a booking handler that tangles "
                "validation, saving and confirmation is must-fix. Separate those steps so the failed-save "
                "path is clear. Preferring a trailing comma in a readable record is a nit. "
                "Use measured code shape as evidence and decide maintainability from the implementation."
            ),
        }
    facts["Subjects"] = subjects_to_judge
    facts["Requirement to feature links"] = {
        ref: row["features"] for ref, row in subjects(document, "FLOW").items()}
    facts["Guide path"] = plan.GUIDE_PATH
    checks = plan.guide_checks(document)
    facts["Walkthrough checks"] = list(checks)
    facts["Walkthrough checkout"] = WALKTHROUGH_CHECKOUT
    prompt = (
        "Does this owner's delivered experience meet the accepted plan? Give one finding with evidence "
        "for every listed subject. Use the base and delivered observations to judge the meaning of the "
        "checks. For the guide, look for an execution of its actual steps from a fresh checkout. "
        "Name missing facts as uncertainty and send concrete repairs to the owner. "
        + questions.JUDGMENT_LEEWAY)
    sample = {subject: {"verdict": "satisfied", "gaps": [],
                        "why": "The delivered reader reloads the value it stored."}
              for subject in subjects_to_judge}
    sample["tryItGuide"] = {"verdict": "uncertain", "neededFacts": ["Fresh-copy CLI output"],
                            "why": "The documented CLI command was not observed."}
    sample["sourceAcceptance"]["why"] = "The check fails on the missing reader and reloads real stored data."
    if "codeQuality" in sample:
        sample["codeQuality"]["why"] = "Named load and save functions make the failure paths easy to follow."
    return questions.question("JUDGE", prompt, facts, folded_judge_schema(subjects_to_judge, owner),
        [{"findings": sample, "why": "The guide needs another observation."}], subjects_to_judge,
        images=question["images"], context=question.get("renderContext"))


def record_folded(store, owner, source, answer, tree, observations, question):
    cursor = store.cursor()
    findings = {}
    for subject, finding in answer["findings"].items():
        if finding["verdict"] == "uncertain":
            findings[subject] = {**finding, "verdict": "not judged", "gaps": []}
        else:
            findings[subject] = {**finding,
                "verdict": "gap" if questions.must_fix(finding) else "satisfied"}
    facts = question["facts"]
    guide = findings["tryItGuide"]
    guide_text = (facts["Product files"].get(plan.GUIDE_PATH)
                  or facts["Check sources"].get(plan.GUIDE_PATH, ""))
    if guide["verdict"] == "satisfied" and not (guide_text.strip() and facts["Walkthrough checks"]):
        findings["tryItGuide"] = {**guide, "verdict": "not judged", "gaps": [],
            "neededFacts": ["A delivered guide and its declared fresh-copy walkthrough check"]}
    combined_answer = combined([{"answer": finding} for finding in findings.values()])
    result = store.record("unit_verdict", {"commit": cursor.candidate, "revision": cursor.revision,
        "scopeTree": tree, "source": source, "answer": {**combined_answer, "why": answer["why"]},
        "findings": findings, "observations": observations})
    store.unit(owner, replace(store.unit(owner), judgment=result))
    return result, combined_answer


def folded_evidence(store, document, owner, observations):
    cursor = store.cursor()
    refs = [store.read(r) for r in observations]
    checks = plan.assigned(document)[owner]
    scope = plan.select(document, "unit")[owner]["scope"]
    tree = gitwork.scope_tree(store.root.parent, cursor.candidate, scope, cursor.scratch)
    if (len(refs) != len(checks)
            or {r["check"]: (r["command"], r["requirements"]) for r in refs}
            != {r: (c["command"], c["requirements"]) for r, c in checks.items()}
            or any(r["purpose"] != "final" or r["unit"] != owner or r["commit"] != cursor.candidate
                   or r.get("scopeTree") != tree for r in refs) or proof.failures(store, observations)):
        raise ValueError("One-owner judgment needs passing integrated checks on the delivered revision. "
                         "Rerun integrated proof for the current candidate.")
    return tree


def folded_answer(store, document, owner):
    cursor = store.cursor()
    source = store.unit(owner).judgment
    row = store.read(source) if source else {}
    if (cursor.finalCommit != cursor.candidate or row.get("commit") != cursor.candidate
            or row.get("revision") != cursor.revision
            or row.get("scopeTree") != folded_evidence(store, document, owner, cursor.finalObservations)
            or set(row.get("observations", ())) != set(cursor.finalObservations)):
        raise ValueError("One-owner findings are stale or absent. "
                         "Rerun integrated checks and judgment on this candidate before closing proof.")
    findings = row.get("findings", {})
    if missing := set(folded_subjects(document)) - set(findings):
        raise ValueError(f"One-owner judgment omitted {', '.join(sorted(missing))}. "
                         "Record an explicit finding for each missing subject before closing proof.")
    if not all(ready(answer) for answer in findings.values()):
        raise ValueError("One-owner judgment still has a must-fix finding. "
                         "Repair it and rerun integrated proof.")
    return source, {owner: row["scopeTree"]}, findings


def subjects(document, kind):
    if kind == "FLOW":
        return plan.select(document, "requirement")
    commitments = {ref: {"criterion": text,
        "verification": "Trace the accepted experience end to end across its areas",
        "areas": [a["handle"] for a in document["architecture"]["areas"] if ref in a["commitments"]]}
        for ref, text in architecture.commitments(document["nodes"]["vision"]["body"]).items()
        if ref.startswith(("scopeIn:", "improvements:"))}
    return {**plan.select(document, "criterion"), **commitments, "tryItGuide": {
        "criterion": "The try-it guide is usable from the delivered directory",
        "verification": plan.GUIDE_WORK, "checks": []}}


def trace(document, kind, subject):
    body = subjects(document, kind)[subject]
    if "areas" in body:
        walkthroughs = plan.guide_checks(document)
        units = plan.select(document, "unit")
        return {r: c for r, c in plan.select(document, "check").items()
                if r in walkthroughs or units[c["owner"]]["area"] in body["areas"]}
    return (plan.guide_checks(document) if subject == "tryItGuide" else
            plan.traced_checks(document, subject))


def owners(document, kind, subject):
    body = subjects(document, kind)[subject]
    guide = plan.guide_owner(document)
    if "areas" in body:
        return {r: u for r, u in plan.select(document, "unit").items()
                if r == guide or u["area"] in body["areas"]}
    if kind == "FLOW":
        return {r: u for r, u in plan.select(document, "unit").items()
                if subject in u["requirements"]}
    if "area" in body:
        return {r: u for r, u in plan.select(document, "unit").items() if u["area"] == body["area"]}
    return plan.traced_units(document, trace(document, kind, subject))


def scope_trees(store, owned):
    cursor = store.cursor()
    trees = {b["unit"]: b["scopeTree"] for r in cursor.finalObservations for b in [store.read(r)]
             if b["unit"] in owned and b["commit"] == cursor.candidate}
    if missing := set(owned) - trees.keys():
        units = plan.select(plan.execution(store), "unit")
        for ref in missing:
            trees[ref] = gitwork.scope_tree(store.root.parent, cursor.candidate,
                                           units[ref]["scope"], cursor.scratch)
    return trees


def current(verdict, trees):
    return all(trees.get(r) == tree for r, tree in verdict["scopeTrees"].items())


def brief(store, kind, selected, *, trees=None):
    subject = selected[0]
    cursor = store.cursor()
    document = plan.execution(store)
    checks = trace(document, kind, subject)
    refs = [r for r in cursor.finalObservations if store.read(r)["check"] in checks]
    if (cursor.finalCommit != cursor.candidate or {store.read(r)["check"] for r in refs} != set(checks)
            or any(store.read(r)["commit"] != cursor.finalCommit for r in refs)):
        raise ValueError("Judge evidence requires every traced check on the final commit")
    owned = owners(document, kind, subject)
    trees = trees if trees is not None else scope_trees(store, owned)
    verdicts = []
    for ref in owned:
        if store.unit(ref).state == "DROPPED":
            verdicts.append({"unit": ref, "why": "left out by the user"})
            continue
        verdict = store.read(store.unit(ref).judgment)
        if verdict["scopeTree"] != trees[ref]:
            raise ValueError(f"{ref} needs its unit JUDGE on the changed scope before final judgment")
        if "answer" in verdict:
            verdicts.append({"unit": ref, "commit": verdict["commit"], **verdict["answer"]})
    final = proof.account(store, refs, cursor.finalCommit)
    criteria = {s: {k: subjects(document, kind)[s][k] for k in ("criterion", "verification")}
                for s in selected}
    facts = {"Left out by the user": plan.left_out(store), "Criteria": criteria, "Checks": checks, "Final observations": final,
             "Source judgment": verdicts,
             "Source availability": "Source is judged at the unit; source text is unavailable "
                                    "after integration.",
             "Reader's starting directory": str(store.root.parent.resolve())}
    body = subjects(document, kind)[subject]
    if "areas" in body:
        features = {f for u in plan.select(document, "unit").values() if u["area"] in body["areas"]
                    for r in u["requirements"]
                    for f in plan.select(document, "requirement")[r]["features"]}
        facts["Feature verdicts"] = [{"feature": row["feature"],
            **plan.select(document, "feature")[row["feature"]], "answer": row["answer"]}
            for ref in cursor.demoVerdicts for row in [store.read(ref)] if row["feature"] in features]
    if subject == "tryItGuide":
        facts["Walkthrough checkout"] = WALKTHROUGH_CHECKOUT
        facts["Guide file"] = {"path": plan.GUIDE_PATH, "commit": cursor.finalCommit,
                               "text": gitwork.git(store.root.parent, "show",
                                                   f"{cursor.finalCommit}:{plan.GUIDE_PATH}")}
        guide = plan.guide_owner(document)
        paths = plan.select(document, "unit")[guide]["checkPaths"]
        if plan.select(document, "unit")[guide]["requirements"]:
            paths = [p for p in paths if plan.matches(p, ["docs/try-it/**"])]
        facts["Walkthrough check sources"] = {p: b.decode("utf-8", errors="replace") for p, b in
            gitwork.blobs(store.root.parent, cursor.finalCommit, paths).items()}
    context = {"audience": document["nodes"]["vision"]["body"]["users"],
               "productMode": "commitment" if "areas" in body else
                              "guide" if subject == "tryItGuide" else "criterion"}
    question = questions.build(kind, facts, selected, images=questions.images(store, refs), context=context)
    if "areas" in body:
        question["prompt"] = ("Was this accepted commitment delivered end to end across its areas? "
            "Passing features alone may leave a gap between them. Use the feature verdicts and guide "
            "walkthrough evidence; name the responsible correction owners for any behaviour gap. "
            + questions.JUDGMENT_LEEWAY)
    return {**question, "schema": review_schema(document, kind)}


def review_schema(document, kind):
    return answer_schema(kind, sorted(subjects(document, kind)),
                         sorted([*plan.select(document, "unit"), *plan.select(document, "contract")]))


def corrections(store, subject, source, answer):
    contracts = plan.select(plan.execution(store), "contract")
    owners = {}
    for gap in questions.must_fix(answer):
        for owner in gap["owners"]:
            owners.setdefault(owner, []).append(gap["correction"])
    # A contract owner means the agreement itself needs correction: the planner's work (R4).
    planner = features.enabled("contractGap")
    return [{"subject": subject if planner or owner not in contracts else owner,
             "kind": "contract" if planner and owner in contracts else "flow",
             "commit": store.cursor().candidate,
             "units": [contracts[owner]["provider"] if owner in contracts and not planner else owner],
             "correction": "; ".join(items), "source": source} for owner, items in owners.items()]


def ready(answer):
    # DV6: deferred judgment is visible at C; it is never satisfaction.
    return questions.satisfied(answer) or answer["verdict"] == "not judged"


def not_judged(store):
    cursor = store.cursor()
    deferred = {"review:" + subject: "not judged: " + row["reason"]
                for subject, row in (store.read(cursor.review)["notJudged"].items()
                                     if cursor.review else [])}
    deferred.update({v["subject"]: "not judged: " + "; ".join(dict.fromkeys(v["answer"]["neededFacts"]))
                     for v in ([store.read(r) for r in [*cursor.requirementVerdicts, *cursor.qualityVerdicts]]
                               + [{"subject": ref, "answer": store.read(row.judgment).get("answer", {})}
                                  for ref, row in store.units().items() if row.judgment])
                     if v["answer"].get("verdict") == "not judged"})
    limits = []
    for ref in cursor.finalObservations:
        observation = store.read(ref)
        if observation["commit"] != cursor.candidate or observation["purpose"] not in ("final", "milestone"):
            continue
        for row in observation.get("operationLifecycle", ()):
            step = row.get("step") or row.get("operation") or row.get("command_or_interaction")
            if row.get("observed") is False and isinstance(step, str) and step.strip():
                reason = row.get("reason")
                limits.append(f"{step}: {reason}" if isinstance(reason, str) and reason.strip() else step)
    if cursor.productVerdict and (judge := store.read(cursor.productVerdict).get("judge")):
        deferred.update({f"guard:{item['promise']}": "not judged: " + item["reason"]
                         for item in store.read(judge).get("limitations", [])})
    if limits:
        deferred["tryItGuide:walkthrough"] = "not judged: " + "; ".join(dict.fromkeys(limits))
    return deferred


def notes(store):
    """Show recorded nonblocking findings alongside the verdict at C."""
    cursor = store.cursor()
    refs = [*cursor.demoVerdicts, *cursor.qualityVerdicts,
            *(u.judgment for u in store.units().values() if u.judgment)]
    if cursor.review:
        refs.append(cursor.review)
    return [{"source": ref, **gap} for ref in refs
            for row in [store.read(ref)] if not row.get("foldedFrom")
            for gap in row.get("answer", {}).get("gaps", []) if not features.blocking(gap)]


def combined(verdicts):
    answers = [v["answer"] for v in verdicts]
    return {"verdict": ("gap" if not all(ready(a) for a in answers) else
                        "not judged" if any(a["verdict"] == "not judged" for a in answers) else "satisfied"),
            "gaps": [g for a in answers for g in a["gaps"]],
            "neededFacts": [fact for a in answers for fact in a.get("neededFacts", [])],
            "why": "Combined recorded subject verdicts; unresolved facts remain not judged (DV6)"}


def step(store, provider):
    cursor = store.cursor()
    kind = "PRODUCT"
    document = plan.execution(store)
    if owner := folded_owner(store, document):
        source, trees, findings = folded_answer(store, document, owner)
        with store.transaction():
            refs = [store.record("subject_verdict", {"subject": subject, "kind": kind,
                "commit": cursor.candidate, "answer": findings[subject], "source": source,
                "scopeTrees": trees, "foldedFrom": source}) for subject in subjects(document, kind)]
            verdict = store.record("product_verdict", {"commit": cursor.candidate,
                "answer": combined([store.read(r) for r in refs]), "sources": refs})
            advance(store, "satisfied", verdict, qualityVerdicts=refs, productVerdict=verdict)
        return
    trees = scope_trees(store, plan.select(document, "unit"))
    with store.transaction():
        retained = {field: [ref for ref in getattr(cursor, field) for row in [store.read(ref)]
                            if not row.get("foldedFrom") and current(row, trees)]
                    for field in ("requirementVerdicts", "qualityVerdicts")}
        cursor = replace(cursor, **retained)
        store.cursor(cursor)
    done = {store.read(r)["subject"] for r in cursor.qualityVerdicts}
    groups = {}
    for subject, body in subjects(document, kind).items():
        if subject not in done:
            groups.setdefault(body.get("area", subject), []).append(subject)
    gaps = []
    def consume(area, result, answer):
        ss = groups[area]
        if answer["verdict"] == "uncertain":
            answer = {**answer, "verdict": "not judged", "gaps": []}
        result = store.record("area_verdict", {"area": area, "source": result, "answer": answer,
            "commit": cursor.candidate, "observations": [r for r in cursor.finalObservations
                             if store.read(r)["check"] in trace(document, kind, ss[0])]})
        local_gaps = corrections(store, area, result, answer)
        for subject in ss:
            targeted = [g for g in answer["gaps"] if g["target"] == subject]
            individual = {**answer, "gaps": targeted, "verdict": (
                "not judged" if answer["verdict"] == "not judged" else
                "gap" if questions.must_fix({"gaps": targeted}) else "satisfied")}
            if not ready(individual):
                continue
            ref = store.record("subject_verdict", {"subject": subject, "kind": kind,
                "commit": cursor.candidate, "answer": individual, "source": result,
                "scopeTrees": {r: trees[r] for r in owners(document, kind, subject)}})
            row = store.cursor()
            store.cursor(replace(row, qualityVerdicts=row.qualityVerdicts + [ref]))
        gaps.extend(local_gaps)

    inference.batch(store, provider, {
        area: brief(store, kind, ss, trees=trees) for area, ss in groups.items()},
        consume=consume, field="gaps.target/owners")
    if contracted := [g for g in gaps if g["kind"] == "contract"]:
        return fixups.replan(store, contracted)
    with store.transaction():
        if gaps:
            fixups.apply(store, gaps)
        else:
            cursor = store.cursor()
            verdict = store.record("product_verdict", {"commit": cursor.candidate,
                "answer": combined([store.read(r) for r in cursor.qualityVerdicts]),
                "sources": cursor.qualityVerdicts})
            advance(store, "satisfied", verdict, productVerdict=verdict)
