"""Loop-chosen questions and the one semantic boundary decoder."""

import json
from copy import deepcopy

from codex_harness.core.run_schemas import question_spec, author_contract
from codex_harness.core.planner_schema import validate_input, json_value


def response(pending, raw, record, provider=None):
    """Validate meaning once and attach CODE-owned identities for the 1j writer."""
    if provider is not None:
        from codex_harness.core.provider_response import _response
        raw = _response(raw, provider, structured=True)
    value = json_value(raw)
    family, purpose = pending["family"], pending["purpose"]
    retain = purpose == "trace" and isinstance(value,dict) and isinstance(value.get("edits"),dict)
    if retain:
        scope = record.get("diagnostics") or pending["context"]
        known = pending["answerSchema"]["properties"]["edits"]["properties"]
        retained = {**scope.get("rejectedEdits",{}), **{k:v for k,v in value["edits"].items() if k in known and (v is not None or not scope.get("obligations"))}}
        if scope.get("editableFields") is not None and any(k not in known for k in value["edits"]):
            raise ValueError(f"Repair only the refused fields {scope['editableFields']}")
        value["edits"] = ({k:None for k in known} if scope.get("obligations") else {k:v for k,v in retained.items() if k in known}) | value["edits"]
    try:
        validate_input(value, pending["answerSchema"], "answer")
    except ValueError as error:
        if retain:
            editable = {}
            for key in known:
                if any(path == "answer.edits."+key or path.startswith("answer.edits."+key+".") for path in error.locations):
                    ref, section = key.rsplit(":",1)
                    editable.setdefault(ref,[]).append(section)
            error.diagnostics = {**scope, "kind":"graph", "editableFields":editable or scope.get("editableFields",{}), "rejectedEdits":retained}
        raise
    if retain: value["edits"] = retained
    meaning = value if family in {"author", "direction", "repair"} else {k: v for k, v in value.items() if k not in {"verdict", "rationale", "gaps"}}
    verdict = value.get("verdict", "positive")
    if family == "direction":
        meaning = dict(value)
        meaning["option"] = meaning.pop("action")
    if family == "user-interpretation":
        verdict = "inconclusive" if value["decision"] == "unclear" else "positive"
    if purpose == "finding-disposition":
        meaning["memberDispositions"] = {r["memberRef"]: r["disposition"] for r in meaning.pop("dispositions")}
    if purpose in {"execution", "visualQuality"}:
        meaning["qualitativeChecks"] = {r["check"]: r["passed"] for r in meaning["qualitativeChecks"]}
        observation = next(r["payload"] for r in record["sourceRefs"] if r["kind"] == "candidate-observation")
        meaning.update({k: observation[k] for k in ("check", "evidenceBinding", "phase")})
    if purpose == "full-dream" and verdict == "positive":
        inventory = acceptance_inventory(record)
        for field, ref, expected in (("featureAssessments", "featureRef", inventory["featureInventory"]),
                                     ("qualityAssessments", "qualityRef", inventory["qualityInventory"])):
            keys = [r[ref] for r in meaning[field]]
            if len(keys) != len(set(keys)) or set(keys) != {r["ref"] for r in expected}:
                raise ValueError(f"answer.{field} must assess each supplied inventory item exactly once")
            if any(r["verdict"] != "positive" for r in meaning[field]):
                raise ValueError("A positive full-dream verdict requires positive item assessments")
        if meaning["stubOrMissingGaps"]:
            raise ValueError("Named stub/missing gaps cannot establish full-dream completion")
    gaps = value.get("gaps", []) + value.get("stubOrMissingGaps", [])
    supplied = {r["id"] for r in record["subjectRefs"] + record["evidenceRefs"]} | ({"plan"} if purpose in {"plan-review", "full-dream"} else set())
    for gap in gaps:
        if gap["subjectRef"] not in supplied or set(gap["evidenceRefs"]) - supplied:
            raise ValueError("answer.gaps must cite supplied subject/evidence references")
    return dict(judgmentId=record["id"], binding=record["binding"], verdict=verdict,
                           rationale=value["rationale"], meaning=meaning,
                           gaps=[g["issue"] for g in gaps])


def review_decoder(provider, shard):
    purpose = "full-dream" if shard.get("acceptanceRunId") else "plan-review"
    pending = dict(family="assess", purpose=purpose, answerSchema=question_spec("assess",purpose)[0])
    return lambda raw, record: response(pending,raw,record,provider)


def acceptance_inventory(record):
    features = [dict(ref=r["id"], feature=r["node"]) for r in record["subjectRefs"] if r["node"]["kind"] == "feature"]
    vision = next(r["node"] for r in record["subjectRefs"] if r["node"]["kind"] == "vision")
    f = vision["fields"]
    criteria = [f.get("quality_bar", ""), *f.get("qualityCriteria", [])]
    return dict(featureInventory=features, qualityInventory=[dict(ref=f"quality:{i}", criterion=c) for i,c in enumerate(criteria)])


def ask_question(store, run, family, purpose, *, judgment=None, context=None):
    from codex_harness.core.run_context import relevant_ids, repository_facts, recovery_context, direction_content
    from codex_harness.core.spine import _hash, _canonical

    context = context or {}
    if purpose == "trace" and context.get("obligations"):
        context = {**context, "nodes":context.get("nodes",[n for n in store.list_nodes() if n["kind"] not in {"vision","result"} and n["id"] != run["reviewerNodeId"]]),
                   "targets":context.get("targets",[]), "scenarios":context.get("scenarios",[])}
    if family == "author" and purpose == "plan-outline" and run.get("jobId"):
        context = {**context, **recovery_context(store,run)}
    def hold(reason):
        run.update(state="PAUSED", question=None, effect=None, semanticHeld=True,
                   reason=reason, nextAction="Describe changed requirements or new evidence in your own words; Seldon owns internal identifiers")
    if purpose in {"finding-dispositions", "finding-disposition"}:
        canonical = {m["id"]: m for m in store.finding_state()["members"]}
        members = context.get("members", [])
        if judgment:
            members = next((r["payload"].get("facts", {}).get("members", []) for r in judgment["sourceRefs"] if r["payload"].get("facts", {}).get("members")), members)
        if not members or any(m["id"] not in canonical for m in members):
            hold("No canonical finding member needs this disposition; a planning defect is not a finding disposition")
            return
        context = dict(context, members=[canonical[m["id"]] for m in members])
    schema, examples = question_spec(family,purpose)
    pending = dict(family=family,purpose=purpose,revision=store._revision(),context=context,
        role="mechanical" if family == "user-interpretation" or purpose == "planning-size" else "judge" if family == "assess" else "planner",
        answerSchema=author_contract(store,purpose,context) if family in {"author", "repair"} and purpose in {"trace","plan-outline"} else deepcopy(schema), examples=deepcopy(examples))
    if purpose == "plan-outline" and "targets" not in pending["answerSchema"]["properties"]:
        from codex_harness.core.run_schemas import SMALL_EXAMPLE
        pending["examples"] = [deepcopy(SMALL_EXAMPLE)]
    if purpose in {"finding-dispositions", "finding-disposition"}:
        props = pending["answerSchema"]["properties"]["dispositions"]["items"]["properties"]
        props["memberRef"] = dict(props["memberRef"], enum=[m["id"] for m in context["members"]])
    if family == "direction" and context.get("internalFailure"):
        pending["answerSchema"]["properties"]["action"]["enum"] = ["repair", "replan", "pause", "stop"]
        pending["answerSchema"]["properties"]["question"] = dict(type="null")
        pending["examples"] = [dict(action="pause", question=None, amendments=[], rationale="The internal repair needs new evidence", reply="Planning is paused; Seldon retains the failed work and its identifiers.")]
    if judgment is None:
        subjects = relevant_ids(store, family, purpose, context)
        words = [json.loads(r[0])["text"] for r in store.connection.execute(
            "SELECT payload_json FROM events WHERE kind='chat-message' AND json_extract(payload_json,'$.role')='user' ORDER BY id DESC LIMIT 12")]
        stable = {k:v for k,v in context.items() if k not in {"failedRequestId", "rejectedResponseEventId"}}
        if "priorDirections" in stable:
            stable["priorDirections"] = sorted({json.dumps(direction_content(r),sort_keys=True) for r in stable["priorDirections"]})
        digest = _hash(_canonical(dict(family=family, purpose=purpose,
            subjects=[store.spine().node(k) for k in subjects], context=stable, words=words[:1],
            repository=repository_facts(store.workspace) if family == "author" else None)))
        previous = store.connection.execute(
            "SELECT payload_json FROM events e WHERE kind='judgment-recorded' AND EXISTS "
            "(SELECT 1 FROM json_each(e.payload_json,'$.sourceRefs') r WHERE "
            "json_extract(r.value,'$.payload.factsDigest')=?) ORDER BY e.id DESC LIMIT 1", (digest,)).fetchone()
        if previous:
            retained = json.loads(previous[0])
            if retained["disposition"] in {"complete", "steer"} or retained.get("supersededBy") or retained["verdict"] in {"inconclusive", "unsupported"}:
                hold("No progress: this subject and its supplied facts are unchanged; no new attempt allowance was granted")
                store._event("run-no-progress", dict(previousRequestId=retained["id"], factsDigest=digest, allowanceGranted=False))
                return
            judgment = retained  # Reuse its exact-fix allowance, never reset it.
        else:
            store._event("loop-question", dict(family=family, purpose=purpose,
                         answerSchema=pending["answerSchema"], context=context, factsDigest=digest))
            event_id = store.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            judgment = store.prepare_judgment(question="loop-" + family, subject_ids=subjects,
                source_event_ids=[event_id] + ([run["userDecisionEventId"]] if run.get("userDecisionEventId") else []))
    pending.update(requestId=judgment["id"], candidate=judgment.get("candidate"))
    current = store.judgment(judgment["id"], candidate=judgment.get("candidate"))
    if not current["current"] or current["disposition"] in {"steer", "complete"}:
        run.update(question=None, semanticHeld=True, reason="Retained judgment is closed; no answerable request",
                   nextAction="Supply new words or evidence through harness_chat")
        return
    run.update(question=pending, nextAction="Await Seldon's semantic answer", reason=purpose, effect=None)
