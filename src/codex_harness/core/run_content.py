"""Question-specific canonical consumers; CODE allocates IDs and resolves links."""

from uuid import uuid4
from copy import deepcopy
from codex_harness.core.planner_nodes import batch_nodes
from codex_harness.core.semantic_schema import TRACE_LINKS
from codex_harness.core.spine import SpineError, content_revision
from codex_harness.core.plan_types import PlanStoreError


def canonical(value, data=False):
    """Decode literal application data and omit absent semantic fields once."""
    if isinstance(value,list):
        return [canonical(v,data) for v in value]
    if not isinstance(value,dict):
        return value
    if data:
        if len({r["key"] for r in value["entries"]}) != len(value["entries"]):
            raise ValueError("JSON data entries must have unique keys")
        return {r["key"]:canonical(r["value"],True) for r in value["entries"]}
    return {k:canonical(v["value"],True) if k in {"json","jsonEquals","stdoutJsonEquals","fileJsonEquals"} else canonical(v)
            for k,v in value.items() if v is not None and not (k == "expectedErrors" and not v)}


def identities(rows, key, expected=None):
    values = [r[key] for r in rows]
    if len(values) != len(set(values)) or expected is not None and set(values) != expected:
        raise ValueError("Use each supplied identity exactly once; retain the complete proposed membership")


def apply_answer(store, pending, meaning):
    purpose = pending["purpose"]
    if pending["family"] == "repair":
        scope = store._judgment_record(pending["requestId"]).get("diagnostics") or pending["context"]
        retained = deepcopy(scope["rejectedAnswer"])
        supplied = {r["ref"]: r for r in meaning["records"]}
        for row in retained["records"]:
            for field in scope["editableFields"].get(row["ref"], []):
                parts, owner, source = field.split("."), row, supplied.get(row["ref"], {})
                for part in parts[:-1]:
                    owner, source = owner[part], source.get(part, {})
                owner[parts[-1]] = source.get(parts[-1])
        if meaning["remove"] or any(meaning[k] != retained[k] for k in ("records", "targets", "scenarios")):
            raise ValueError(f"Repair only the refused fields {scope['editableFields']}; preserve all other nodes/content and remove nothing")
    if purpose == "plan-outline":
        outline = {}
        for group, rows in meaning.items():
            if group not in {"rationale", "reply"}:
                outline[group] = [dict(id=row["ref"] or group[:2]+"_"+uuid4().hex[:10], **{k:v for k,v in row.items() if k != "ref"},
                                      **({"kind":group} if group not in {"targets","scenarios"} else {})) for row in rows]
        for rows in outline.values(): identities(rows,"id")
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
    rows = [dict(drafts[r["ref"]], **r) for r in meaning["records"]]
    ids = set(store.spine().ids())
    for group, supplied in (("records","nodes"),("targets","targets"),("scenarios","scenarios")):
        identities(meaning[group],"ref",{r["id"] for r in pending["context"][supplied]})
    if empty := {r["ref"]: ["parents"] for r in rows if not r["parents"]}:
        error = PlanStoreError("trace-relationship-invalid", "Supply parents for the named nodes; use the supplied trace identities and their kinds")
        error.diagnostics = dict(kind="graph", rejectedAnswer=meaning, editableFields=empty)
        raise error
    def ref(key):
        if key not in ids | set(drafts):
            raise ValueError(f"Unknown trace handle {key!r}; use a supplied reference or a proposed slice handle")
        return key
    nodes = []
    for row in rows:
        identifier = row["ref"]
        fields = canonical(row["fields"])
        if "clauseCoverage" in fields:
            fields["clauseCoverage"] = {r["clause"]: r["coverage"] for r in fields["clauseCoverage"]}
        if identifier in ids:
            fields = {**store.spine().node(identifier)["fields"], **fields}
        nodes.append(dict(id=identifier, kind=row["kind"], title=row["title"], text=row["text"],
                          parents=[ref(r) for r in row["parents"]], fields=fields))
    revisions = {node["id"]: content_revision(node) for node in nodes}
    def bound(handle):
        target = ref(handle)
        return dict(id=target, revision=revisions[target] if target in revisions else store.spine().revision(target))
    def fields(value):
        return {k: [bound(x) if isinstance(x,str) else x for x in v] if k in TRACE_LINKS or k == "targetRefs" else
                bound(v) if k == "sourceRef" and isinstance(v,str) else
                fields(v) if k == "baseline" else v for k,v in value.items()}
    for node in nodes:
        node["fields"] = fields(node["fields"])
    if nodes:
        try:
            batch_nodes(store, nodes, lambda n: None)
        except SpineError as cause:
            editable = getattr(cause, "editable_fields", {})
            defect = not any(editable.values())
            if defect:
                editable = getattr(cause, "refused_sections", {})
            if not any(editable.values()):
                raise RuntimeError(f"CODE defect: structural refusal omitted its node/section: {cause}") from cause
            message = f"CODE defect: missing editable scope; repair only {editable}. {cause}" if defect else str(cause)
            error = PlanStoreError(cause.code, message)
            error.diagnostics = dict(kind="graph", rejectedAnswer=meaning,
                                     editableFields=editable, repairScopeDefect=defect)
            raise error from cause
    for key in meaning["remove"]:
        from codex_harness.core.run_loop import cursor
        if ref(key) == cursor(store).get("reviewerNodeId"):
            raise ValueError("Reviewer setup belongs to kickoff")
        store.remove_node(ref(key))
    if meaning["targets"] or meaning["scenarios"]:
        vision = store.spine().node(store.spine().ids("vision")[0])
        targets = {r["ref"]: canonical({k: v for k, v in r.items() if k != "ref"}) for r in meaning["targets"]}
        previous = {s["id"]:s for s in vision["fields"].get("scenarios",{}).get("scenarios",[])}
        scenarios = []
        for raw in meaning["scenarios"]:
            s = canonical(raw)
            s["id"] = s.pop("ref")
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
            for key in ("requirementRefs", "contractRefs"):
                s[key] = [ref(r) for r in s[key]]
            if "testRef" in s:
                s["testRef"] = ref(s["testRef"])
            scenarios.append(s)
        vision["fields"].update(targets=targets, scenarios=dict(schema="seldon/scenarios-v1", scenarios=scenarios))
        store.upsert_node(vision)
