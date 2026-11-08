"""Question-specific canonical consumers; CODE allocates IDs and resolves links."""

from uuid import uuid4
from codex_harness.core.planner_nodes import batch_nodes
from codex_harness.core.semantic_schema import TRACE_LINKS
from codex_harness.core.spine import _hash, _canonical
from codex_harness.core.spine import ALLOWED_PARENTS, SpineError
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
    if purpose == "plan-outline":
        outline = {}
        for group, rows in meaning.items():
            if group not in {"rationale", "reply"}:
                outline[group] = [dict(id=row["ref"] or group[:2]+"_"+uuid4().hex[:10], **{k:v for k,v in row.items() if k != "ref"},
                                      **({"kind":group} if group not in {"targets","scenarios"} else {})) for row in rows]
        for rows in outline.values(): identities(rows,"id")
        store._event("trace-outline",dict(requestId=pending["requestId"], nodes=[r for k,v in outline.items() if k not in {"targets","scenarios"} for r in v], targets=outline["targets"], scenarios=outline["scenarios"]))
        return
    if pending["family"] == "repair":
        context = pending["context"]
        row = store.connection.execute("SELECT payload_json FROM events WHERE id=? AND kind='judgment-response'", (context["rejectedResponseEventId"],)).fetchone()
        import json
        from codex_harness.core.provider_response import _response
        retained = json.loads(row[0])
        provider = retained["source"].split(":",2)[1]
        original = json.loads(_response(retained["response"],provider,structured=True))
        changes = {r["childHandle"]: r["parentRefs"] for r in meaning["relationships"]}
        required = {r["childHandle"] for r in context["relationships"]}
        if set(changes) != required or len(changes) != len(meaning["relationships"]):
            raise ValueError("Repair each supplied rejected child exactly once")
        for node in original["records"]:
            if node["ref"] in changes:
                node["parents"] = changes[node["ref"]]
        apply_answer(store, dict(pending, family="author", purpose="trace", context=next(s["payload"]["context"] for s in store._judgment_record(context["failedRequestId"])["sourceRefs"] if s["kind"] == "loop-question")), original)
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
    known = {n["id"]: dict(ref=n["id"],kind=n["kind"],title=n["title"],text=n["text"]) for n in store.list_nodes() if n["fields"].get("schema") != "dot-reviewer-selection-v1"}
    known.update({r["ref"]: dict(ref=r["ref"],kind=r["kind"],title=r["title"],text=r["text"]) for r in rows})
    def relationship(r):
        return dict(childHandle=r["ref"], childKind=r["kind"], childText=r["text"],
        rejectedParents=r["parents"], allowedParentKinds=sorted(ALLOWED_PARENTS[r["kind"]]),
        eligibleParents=[p for p in known.values() if p["kind"] in ALLOWED_PARENTS[r["kind"]] and p["ref"] != r["ref"]])
    relationships = [relationship(r) for r in rows if not r["parents"] or any(p not in known or known[p]["kind"] not in ALLOWED_PARENTS[r["kind"]] for p in r["parents"])]
    if relationships:
        error = PlanStoreError("trace-relationship-invalid", "Rejected parent relationship; use the supplied child, parent and allowed parent kinds")
        error.diagnostics = dict(kind="graph", relationships=relationships)
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
    proposed = {n["id"]: n for n in nodes}
    resolved, visiting = {}, set()
    def materialize(key):
        if key in resolved:
            return resolved[key]
        if key in visiting:
            raise ValueError("Trace reference cycle; author acyclic links")
        if key not in proposed:
            return store.spine().node(key)
        visiting.add(key)
        node = proposed[key]
        def bound(handle):
            target = ref(handle)
            return dict(id=target, revision=_hash(_canonical(materialize(target))))
        def fields(value):
            return {k: [bound(x) if isinstance(x,str) else x for x in v] if k in TRACE_LINKS or k == "targetRefs" else
                    bound(v) if k == "sourceRef" and isinstance(v,str) else
                    fields(v) if k == "baseline" else v for k,v in value.items()}
        node["fields"] = fields(node["fields"])
        visiting.remove(key)
        resolved[key] = node
        return node
    if nodes:
        try:
            batch_nodes(store, [materialize(n["id"]) for n in nodes], lambda n: None)
        except SpineError as cause:
            error = PlanStoreError(cause.code, str(cause))
            error.diagnostics = dict(kind="graph", relationships=[relationship(r) for r in rows])
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
