"""Question-specific canonical consumers; CODE allocates IDs and resolves links."""

from uuid import uuid4
from codex_harness.core.planner_nodes import batch_nodes
from codex_harness.core.semantic_schema import TRACE_LINKS, required_fields, REFERENCE_KINDS
from codex_harness.core.planner_schema import json_value
from codex_harness.core.spine import SpineError, content_revision, ALLOWED_PARENTS
from codex_harness.core.plan_types import PlanStoreError


def canonical(value):
    return [canonical(v) for v in value] if isinstance(value,list) else {k:{r["clause"]:r["coverage"] for r in v} if k == "clauseCoverage" and isinstance(v,list) else
            json_value(v["value"]) if k in {"json","jsonEquals","stdoutJsonEquals","fileJsonEquals"} else canonical(v)
            for k,v in value.items() if v is not None and not (k == "expectedErrors" and not v)} if isinstance(value,dict) else value


def apply_answer(store, pending, meaning):
    purpose = pending["purpose"]
    if purpose == "plan-outline":
        outline = {}
        for group, rows in meaning.items():
            if group not in {"rationale", "reply"}:
                outline[group] = [dict(id=row["ref"] or group[:2]+"_"+uuid4().hex[:10], **{k:v for k,v in row.items() if k != "ref"},
                                      **({"kind":group} if group not in {"targets","scenarios"} else {})) for row in rows]
        if any(len(rows) != len({r["id"] for r in rows}) for rows in outline.values()):
            raise ValueError("Use each supplied identity at most once")
        kinds = {n["kind"] for n in store.list_nodes()} | {k for k,v in outline.items() if v}
        missing = {r["id"]:sorted(ALLOWED_PARENTS[k]) for k,rows in outline.items() if k in ALLOWED_PARENTS and not ALLOWED_PARENTS[k] & kinds for r in rows}
        supplied = {n["id"]:n for n in store.list_nodes()} | {r["id"]:r for k,rows in outline.items() if k in ALLOWED_PARENTS for r in rows}
        from codex_harness.core.semantic_schema import eligible_refs
        missing.update({r["id"]+".fields."+key:REFERENCE_KINDS[key] for k,rows in outline.items() if k in ALLOWED_PARENTS for r in rows
            for key,c in required_fields(k,store.spine().semantic).items() if key in REFERENCE_KINDS and (c.get("minItems") or c["type"] != "array") and (k,key) != ("iteration","resultRefs") and not eligible_refs(supplied,key)})
        if missing: raise ValueError(f"Outline needs eligible handles before trace authoring: {missing}; add the missing declarations")
        store._event("trace-outline",dict(requestId=pending["requestId"], nodes=[r for k,v in outline.items() if k not in {"targets","scenarios"} for r in v], targets=outline["targets"], scenarios=outline["scenarios"]))
        return
    if purpose == "understanding":
        vision = store.spine().node(store.spine().ids("vision")[0])
        f = vision["fields"]
        previous = f.get("expansion", {}).get("upgrades", [])
        upgrades = []
        for u in meaning["upgrades"]:
            fields = dict(text=u["proposal"], **{k:u[k] for k in ("kind", "impact", "disposition")})
            retained = next((old["id"] for old in previous if {k:v for k,v in old.items() if k != "id"} == fields), None)
            upgrades.append(dict(id=retained or "upgrade_" + uuid4().hex, **fields))
        f.update(vision=meaning["understanding"], users=meaning["users"],
            first_user_moment=meaning["firstUserMoment"], scope_in=meaning["scopeIn"],
            scope_out=meaning["scopeOut"], quality_bar=meaning["qualityBar"],
            done_feels_like=meaning["doneFeelsLike"], assumptions=meaning["assumptions"],
            qualityCriteria=meaning["qualityCriteria"], openQuestions=meaning["openQuestions"],
            metrics=meaning["metrics"], stopConditions=meaning["stopConditions"],
            expansion=dict(mode="cofounder", reason=meaning["rationale"], upgrades=upgrades))
        vision["text"] = meaning["understanding"]
        store.upsert_node(vision)
        if meaning["readiness"] == "ready":
            from codex_harness.core.intake import routing
            routing(store, vision)
        return
    if pending["family"] == "direction":
        spine = store.spine()
        for amendment in meaning["amendments"]:
            def bound(key):
                return dict(id=key, revision=spine.revision(key))
            fields = dict(amendment, sourceRef=bound(amendment["sourceRef"]), targetRefs=[bound(k) for k in amendment["targetRefs"]], decisionRefs=[])
            store.upsert_node(dict(id="amendment_"+uuid4().hex,kind="amendment", title=amendment["proposedChange"][:100],
                text=amendment["proposedChange"],parents=[amendment["sourceRef"]],fields=fields))
        return
    if purpose == "finding-dispositions":
        store.resolve_findings([dict(id=r["memberRef"], status=r["disposition"], resolution=r["resolution"]) for r in meaning["dispositions"]])
        return
    drafts = {n["id"]:n for n in pending["context"]["nodes"]}
    spine = store.spine()
    envelope = trace_envelope(spine, pending["context"], meaning["edits"])
    vision = spine.node(spine.ids("vision")[0])
    nodes = [dict(drafts[k], parents=r["parents"], fields={**{key:[] for key,c in required_fields(drafts[k]["kind"],spine.semantic).items()
        if key in REFERENCE_KINDS and c["type"] == "array" and not c.get("minItems")}, **canonical(r["fields"])}) for k,r in envelope.items() if k in drafts]
    revisions = {node["id"]: content_revision(node) for node in nodes}
    def bound(target):
        return dict(id=target, revision=revisions[target] if target in revisions else store.spine().revision(target))
    def fields(value, historical=False):
        return {k: [bound(x if isinstance(x,str) else x["id"]) if (not historical and k != "evidenceRefs") or isinstance(x,str) else x for x in v] if k in TRACE_LINKS or k == "targetRefs" else
                bound(v) if k == "sourceRef" and isinstance(v,str) else
                fields(v,True) if k == "baseline" else v for k,v in value.items()}
    for node in nodes:
        node["fields"] = fields(node["fields"])
    if nodes:
        try:
            batch_nodes(store, nodes, lambda n: None)
        except SpineError as cause:
            editable = getattr(cause, "editable_fields", {})
            if not any(editable.values()):
                raise RuntimeError(f"CODE defect: structural refusal omitted its node/section: {cause}") from cause
            error = PlanStoreError(cause.code, str(cause))
            error.diagnostics = dict(kind="graph", rejectedEdits=meaning["edits"], editableFields=editable)
            raise error from cause
    if any("target" in r or "scenario" in r for r in envelope.values()):
        targets = {k:canonical(r["target"]) for k,r in envelope.items() if "target" in r}
        previous = {s["id"]:s for s in vision["fields"].get("scenarios",{}).get("scenarios",[])}
        scenarios = dict(previous)
        for key, row in envelope.items():
            if "scenario" not in row: continue
            s = dict(canonical(row["scenario"]), id=key)
            old = previous.get(s["id"],{})
            snapshots = {}
            for observation in s["observe"]:
                if "bytesUnchangedFrom" in observation:
                    path = observation["bytesUnchangedFrom"]
                    if path not in snapshots: snapshots[path] = next((r["id"] for r in old.get("setup",{}).get("snapshots",[]) if r["path"] == path),None) or "sn_"+uuid4().hex[:10]
                    observation["bytesUnchangedFrom"] = snapshots[path]
            s["setup"]["snapshots"] = [dict(id=key,path=path) for path,key in snapshots.items()]
            for index,action in enumerate(s["actions"]):
                prior = old.get("actions",[])[index] if index < len(old.get("actions",[])) else {}
                action["id"] = prior.get("id") or "a_"+uuid4().hex[:10]
                s["observe"].extend(dict(action=action["id"],**check) for check in action.pop("checks"))
                for j,step in enumerate(action.get("web",{}).get("steps",[])):
                    steps = prior.get("web",{}).get("steps",[])
                    step["id"] = (steps[j].get("id") if j < len(steps) else None) or "w_"+uuid4().hex[:10]
            s["verdict"] = "all"
            scenarios[s["id"]] = s
        vision["fields"].update(targets={**vision["fields"].get("targets",{}), **targets}, scenarios=dict(schema="seldon/scenarios-v1", scenarios=list(scenarios.values())))
        store.upsert_node(vision)


def trace_envelope(spine, context, edits=None):
    ids = set(spine.ids())
    envelope = {n["id"]: {f:spine.node(n["id"])[f] if n["id"] in ids else [] if f == "parents" else {}
                         for f in ("parents", "fields")} for n in context["nodes"]}
    for key, value in (edits or {}).items():
        ref, section = key.rsplit(":", 1)
        parts, owner = section.split("."), envelope.setdefault(ref,{})
        for part in parts[:-1]: owner = owner.setdefault(part,{})
        owner[parts[-1]] = value
    return envelope
