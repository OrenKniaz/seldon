"""Question-specific output contracts. Control is closed; product meaning is open."""
from codex_harness.core.semantic_schema import ARGV, TRACE_LINKS, REFERENCE_KINDS, eligible_refs, required_fields, outstanding_fields, small_plan

TEXT = dict(type="string", pattern=r"\S")
STRING = dict(type="string")
BOOL = dict(type="boolean")


def obj(**properties):
    return dict(type="object", properties=properties, required=list(properties), additionalProperties=False)


def array(items=TEXT):
    return dict(type="array", items=items)


def choice(*values):
    return dict(type="string", enum=list(values))


def nullable(schema):
    return schema if any(b.get("type") == "null" for b in schema.get("anyOf",[])) else dict(anyOf=[*schema.get("anyOf",[schema]), dict(type="null")])


GAP = obj(subjectRef=TEXT, evidenceRefs=array(), issue=TEXT)
VERDICT = dict(verdict=choice("positive", "negative", "inconclusive", "unsupported"),
               gaps=array(GAP), rationale=TEXT)
DECISION = obj(decision=choice("approve", "changes_requested", "decline", "unclear"),
               requestedChanges=STRING, reply=STRING, rationale=TEXT)
FINDING = obj(node_id=TEXT, severity=choice("blocker", "major", "minor", "note"),
              finding=TEXT, suggestion=STRING, classification=choice("defect-in-plan", "beyond-brief"),
              scope_reason=TEXT)


AMENDMENT = obj(sourceRef=TEXT, targetRefs=array(), proposedChange=TEXT, rationale=TEXT)
STEERING = obj(action=choice("continue", "repair", "replan", "research", "ask_user", "pause", "stop"),
               question=nullable(TEXT), amendments=array(AMENDMENT), rationale=TEXT, reply=STRING)

DATA = dict(type="string", description="JSON-encoded literal application data")
LAUNCH = obj(entry=nullable(TEXT), module=nullable(TEXT), runtime=nullable(TEXT), argv=ARGV)
HTTP = obj(method=TEXT, path=TEXT, statusEquals=dict(type="integer"), json=nullable(obj(value=DATA)), jsonEquals=nullable(obj(value=DATA)))
LOCATOR = dict(anyOf=[obj(text=TEXT), obj(selector=TEXT), obj(role=TEXT, name=TEXT)])
WEB_STEP = obj(op=choice("open", "assertVisible", "assertHidden", "focus", "fill", "click", "key", "assertFocus", "assertFragment", "request"),
               locator=nullable(LOCATOR), key=nullable(TEXT), fragment=nullable(TEXT), value=nullable(STRING),
               request=nullable(HTTP),
               expectedErrors=array(obj(method=TEXT, path=TEXT, statusEquals=dict(type="integer"))))
ACTION_CHECK =         obj(**{k: nullable(v) for k, v in dict(
            exitEquals=dict(type="integer"), elapsedAtMost=dict(type="integer"), stdinOpenAtExit=BOOL,
            stderrNonempty=BOOL, stdoutEquals=STRING, stdoutEndsWith=STRING, stdoutContains=STRING,
            stderrEquals=STRING, stderrContains=STRING, stdoutJsonEquals=obj(value=DATA),
            noHorizontalOverflow=BOOL, consoleErrors=dict(type="integer"), externalRequests=dict(type="integer"),
            textContrastAA=BOOL, headingStructure=BOOL, reducedMotionRespected=BOOL).items()})
SCENARIO = obj(
    requirementRefs=array(), contractRefs=array(), testRef=nullable(TEXT), rationale=nullable(STRING),
    setup=obj(dirs=array(), files=array(obj(path=TEXT, utf8=nullable(STRING), base64=nullable(STRING)))),
    actions=array(dict(anyOf=[
        obj(checks=array(ACTION_CHECK), cli=obj(target=TEXT, operation=TEXT, argv=ARGV,
                             stdin=nullable(dict(anyOf=[obj(utf8=STRING), obj(base64=STRING), obj(holdOpen=BOOL, exitWithin=dict(type="integer"))]))), cwd=nullable(TEXT)),
        obj(checks=array(ACTION_CHECK), process=LAUNCH, cwd=nullable(TEXT)),
        obj(checks=array(ACTION_CHECK), http=obj(target=TEXT, **HTTP["properties"])),
        obj(checks=array(ACTION_CHECK), web=obj(target=TEXT, operation=TEXT, viewport=obj(width=dict(type="integer"), height=dict(type="integer")),
                              reducedMotion=choice("reduce", "no-preference"), steps=array(WEB_STEP))),
    ])),
    observe=array(obj(path=TEXT, fileJsonEquals=nullable(obj(value=DATA)), fileBytesEquals=nullable(STRING), fileAbsent=nullable(BOOL), bytesUnchangedFrom=nullable(TEXT))),
)

TARGET = obj(kind=nullable(choice("web")), **LAUNCH["properties"],
             server=nullable(obj(**LAUNCH["properties"], readyPath=TEXT, readyWithin=dict(type="number"))))

# CODE retains identity; questions expose only requested meaning.
COMMON = dict(expansionRefs=array(), rationale=nullable(STRING), **{k:array() for k in TRACE_LINKS})
EXECUTION = obj(**LAUNCH["properties"], testRoots=array(), rationale=TEXT)
from codex_harness.core.spine import ALLOWED_PARENTS, SCENARIO_LINKS
TRACE_FIELDS = {
    kind: obj(**({} if kind in {"discovery", "metric"} else COMMON), **{
        "feature": dict(baseline=nullable(obj(source=TEXT, behavior=TEXT, rationale=TEXT, codeRefs=array(), testRefs=array(), resultRefs=array()))),
        "design": dict(clauseCoverage=array(obj(clause=TEXT, coverage=TEXT))),
        "test": dict(execution=nullable(EXECUTION)),
        "bead": dict(capabilityRefs=array()),
    }.get(kind, {}))
    for kind in ALLOWED_PARENTS if kind not in {"vision", "result"}
}
UNDERSTANDING = obj(
    understanding=TEXT, users=array(), firstUserMoment=TEXT, scopeIn=array(), scopeOut=array(),
    qualityBar=TEXT, doneFeelsLike=TEXT, assumptions=array(), openQuestions=array(),
    upgrades=array(obj(proposal=TEXT, kind=choice("micro", "macro"), impact=TEXT,
                       disposition=choice("accept", "reject", "defer"))),
    qualityCriteria=array(TEXT), metrics=array(obj(definition=TEXT, unit=TEXT)),
    stopConditions=array(), readiness=choice("draft", "ready", "research", "ask_user"),
    question=nullable(TEXT), reply=TEXT, rationale=TEXT,
)


OUTLINE = obj(**{kind:array(obj(ref=nullable(TEXT), title=TEXT, text=STRING)) for kind in TRACE_FIELDS},
              targets=array(obj(ref=nullable(TEXT), purpose=TEXT)), scenarios=array(obj(ref=nullable(TEXT), purpose=TEXT)), rationale=TEXT, reply=STRING)
SMALL_EXAMPLE = dict(feature=[dict(key="notes",title="Save notes",text="Reopen saved text",parents=["<supplied vision ID>"],fields=dict(story="As a note taker I can reopen my saved text"))], requirement=[dict(key="persist",title="Preserve text",text="A reopened note contains the exact saved text",parents=["notes"],fields=dict(architecture=[dict(boundary="Storage",contract="A local file persists the note",rationale="Survive restart")],design=[],interfaceContracts=[]))], test=[dict(key="reopen",title="Reopen a saved note",text="Save Unicode text, restart, and compare readback",parents=["persist"],fields=dict(execution=dict(entry="checks/reopen",module=None,runtime=None,argv=[],testRoots=["checks"],rationale="Observe persistence across restart")))], contractsBead=dict(title="Write the journey",text="Observe RED for missing persistence",parents=["reopen"],fields=dict(ownedFiles=["checks/reopen"])),buildBead=dict(title="Implement notes",text="Make the same journey GREEN",parents=["persist"],fields=dict(ownedFiles=["notes"])),rationale="One complete small plan; keys are local references, CODE allocates IDs. Include only real boundaries; empty section arrays mean no separate boundary.",reply="Review the complete note plan")

QUESTIONS = {
    'planning-size': (obj(**{**VERDICT, **dict(size=choice('tiny', 'small', 'medium', 'large'), process=TEXT, overrideAccepted=BOOL)}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(size='small', process='Focused review of consequence and recovery', overrideAccepted=False))]),
    'vision-intent': (obj(**{**VERDICT, **dict(impact=choice('preserved', 'changed', 'narrowed'))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(impact='changed'))]),
    'scope-intent': (obj(**{**VERDICT, **dict(impact=choice('WHAT', 'HOW'))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(impact='WHAT'))]),
    'vision-expansion': (obj(**{**VERDICT, **{}}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **{})]),
    'finding-equivalence': (obj(**{**VERDICT, **dict(groups=array(array()))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(groups=[]))]),
    'finding-disposition': (obj(**{**VERDICT, **dict(dispositions=array(obj(memberRef=TEXT, disposition=choice('resolved', 'rejected', 'deferred', 'open'))))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(dispositions=[]))]),
    'plan-convergence': (obj(**{**VERDICT, **dict(action=choice('ready', 'revise', 'continue-review', 'ask_user'))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(action='continue-review'))]),
    'plan-review': (obj(**{**VERDICT, **dict(findings=array(FINDING), depth=TEXT)}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(findings=[], depth='Inspect consequence and failure behavior'))]),
    'full-dream': (obj(**{**VERDICT, **dict(findings=array(FINDING), depth=TEXT, featureAssessments=array(obj(featureRef=TEXT, verdict=choice('positive', 'negative', 'inconclusive', 'unsupported'), rationale=TEXT)), qualityAssessments=array(obj(qualityRef=TEXT, verdict=choice('positive', 'negative', 'inconclusive', 'unsupported'), rationale=TEXT)), stubOrMissingGaps=array(GAP))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(findings=[], depth="Inspect the whole accepted dream", featureAssessments=[], qualityAssessments=[], stubOrMissingGaps=[]))]),
    'execution': (obj(**{**VERDICT, **dict(meaningful=BOOL, correctFailureReason=BOOL, noStubMockGaps=BOOL, qualitativeChecks=array(obj(check=TEXT, passed=BOOL)))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(meaningful=False, correctFailureReason=False, noStubMockGaps=False, qualitativeChecks=[]))]),
    'visualQuality': (obj(**{**VERDICT, **dict(qualitativeChecks=array(obj(check=TEXT, passed=BOOL)))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(qualitativeChecks=[]))]),
    'user-decision': (DECISION, [dict(words='Yes, build exactly that', answer=dict(decision='approve', requestedChanges='', reply='I will build the displayed plan.', rationale='Unqualified approval of the displayed subject')), dict(words='How would it handle audio input?', answer=dict(decision='unclear', requestedChanges='', reply='Let us clarify audio input before deciding.', rationale='A question asks for explanation, not consent'))]),
    'direction': (STEERING, [dict(action='ask_user', amendments=[], question='May the accepted scope change?', reply='This discovery affects the agreed scope.', rationale='The observed gap requires a user scope decision')]),
    'understanding': (UNDERSTANDING, [dict(understanding='Compare measured performance with an agreed goal', users=['A person investigating a measurement'], firstUserMoment='Load a measurement and see its result', scopeIn=['Measure and compare'], scopeOut=['Unrequested changes'], qualityBar='Make uncertainty visible', doneFeelsLike='The person can reproduce and explain the result', assumptions=[], openQuestions=['Which measurement defines success?'], upgrades=[dict(proposal='Show uncertainty alongside the result', kind='micro', impact='Avoid misleading confidence', disposition='accept')], qualityCriteria=['Results are reproducible'], metrics=[], stopConditions=[], readiness='ask_user', question='Which measurement defines success?', reply='Let us agree on the success measurement before locking the vision.', rationale='The metric changes what we build')]),
    'finding-dispositions': (obj(dispositions=dict(array(obj(memberRef=TEXT,disposition=choice("resolved","rejected","deferred","open"),resolution=TEXT)),minItems=1),rationale=TEXT,reply=STRING), [dict(dispositions=[], rationale='Propose open until current repair evidence supports closure; use the supplied member handles', reply='The concern remains open.')]),
    "trace": (obj(edits=obj(), rationale=TEXT, reply=STRING), [dict(edits={},rationale="No sections changed; CODE retains all members. Supply every required handle:section property with its typed value; never copy unchanged sections. Leave codeRefs empty until a code node exists.",reply="The retained plan is ready.")]),
    "plan-outline": (OUTLINE, [dict(**{k:[] for k in OUTLINE["properties"] if k not in {"rationale","reply"}},rationale="Propose the complete plan in one batch",reply="I will link the proposed plan next.")]),
}


def question_spec(family,purpose):
    return QUESTIONS["direction" if family == "direction" else "user-decision" if family == "user-interpretation" else purpose]


def author_contract(store, purpose, context):
    from copy import deepcopy
    spine = store.spine()
    supplied = {n["id"]:n for n in context.get("nodes",[])} | {key:spine.node(key) for key in spine.ids()}
    schema = deepcopy(question_spec("author",purpose)[0])
    vision = spine.node(spine.ids("vision")[0])["fields"]
    if purpose == "plan-outline":
        if small_plan(supplied):
            return obj(**{kind:dict(array(obj(key=TEXT, title=TEXT, text=TEXT, parents=dict(array(), minItems=1),
                fields=obj(**{k:v for k,v in required_fields(kind,spine.semantic,supplied).items() if k not in {"module", "level"}}))), minItems=1)
                for kind in ("feature", "requirement", "test")}, **{slot:obj(title=TEXT,text=TEXT,parents=dict(array(),minItems=1),
                fields=obj(**{k:v for k,v in required_fields("bead",spine.semantic,supplied).items() if k not in {"lessons","dependsOn","semanticPhase"}})) for slot in ("contractsBead","buildBead")}, rationale=TEXT, reply=STRING)
        for group in OUTLINE["properties"].keys()-{"rationale","reply"}:
            refs = list(vision.get("targets",{})) if group == "targets" else [s["id"] for s in vision.get("scenarios",{}).get("scenarios",[])] if group == "scenarios" else [key for key,node in supplied.items() if node["kind"] == group]
            schema["properties"][group]["items"]["properties"]["ref"] = nullable(choice(*refs)) if refs else dict(type="null")
        return schema
    handles = {k:eligible_refs(supplied,k) for k in REFERENCE_KINDS}
    handles.update({k:[key for key,n in supplied.items() if n["kind"] in kinds] for k,kinds in {**SCENARIO_LINKS,"testRef":{"test"}}.items()})
    handles.update(target=sorted(set(vision.get("targets",{})) | {n["id"] for n in context["targets"]}), expansionRefs=[u["id"] for u in vision.get("expansion",{}).get("upgrades",[])])
    defs = {}
    def reference(name, refs):
        if not refs: return dict(type="null")
        defs[name] = choice(*refs)
        return {"$ref":"#/$defs/"+name}
    def constrain(value):
        for key, child in value.get("properties", {}).items():
            if key in handles:
                ref = reference(key,handles[key])
                if handles[key] and (key.endswith("Refs") or key in {"dependsOn","lessons"}):
                    ref = dict(array(ref), **({"minItems":child["minItems"]} if "minItems" in child else {}))
                child = nullable(ref) if "anyOf" in child and handles[key] else ref
            value["properties"][key] = constrain(child)
        if "items" in value: value["items"] = constrain(value["items"])
        for child in value.get("anyOf",[]): constrain(child)
        return value
    from codex_harness.core.run_content import trace_envelope, canonical
    envelope = trace_envelope(spine,context,context.get("rejectedEdits"))
    requested = context.get("editableFields")
    edits = {}
    for node in context["nodes"]:
        ref, kind = node["id"], node["kind"]
        fields = deepcopy(TRACE_FIELDS[kind]["properties"])
        required = required_fields(kind,spine.semantic,supplied)
        fields.update(required)
        contracts = dict(parents=dict(array(reference("parents_"+kind,[k for k,n in supplied.items() if n["kind"] in ALLOWED_PARENTS[kind]])),minItems=1), fields=constrain(obj(**fields)))
        row = envelope[ref]
        sections = requested.get(ref,[]) if requested is not None else ([] if row["parents"] else ["parents"]) + outstanding_fields(dict(node, fields=canonical(row["fields"])), spine.semantic, supplied, authoring=True)
        if context.get("reviewFindings") and requested is None: sections = ["parents", *("fields."+k for k in fields if k in row["fields"] or k in required)]
        if "editableFields" not in context and ref not in spine.ids(): sections = ["parents",*("fields."+k for k in fields if k not in handles or handles[k] or k in required)]
        for section in sections:
            value = contracts[section.split(".")[0]]
            for part in section.split(".")[1:]:
                value = next(branch["properties"][part] for branch in value.get("anyOf",[value]) if part in branch.get("properties",{}))
            edits[ref+":"+section] = nullable(value) if context.get("obligations") else value
    for group,section,contract in (("targets","target",TARGET),("scenarios","scenario",SCENARIO)):
        existing = vision.get("targets",{}) if group == "targets" else {s["id"] for s in vision.get("scenarios",{}).get("scenarios",[])}
        edits.update({r["id"]+":"+section:constrain(deepcopy(contract)) for r in context[group]
            if (section in requested.get(r["id"],[]) if requested is not None else r["id"] not in existing and section not in envelope.get(r["id"],{}))})
    schema["properties"]["edits"] = obj(**edits)
    if kinds := [kind for kind in context.get("addKinds",[]) if kind != "bead" or not small_plan(supplied)]:
        schema["properties"]["add"] = array(dict(anyOf=[obj(kind=choice(kind), key=TEXT, title=TEXT, text=TEXT,
            parents=dict(array(),minItems=1), fields=obj(**{k:v for k,v in
            {**TRACE_FIELDS[kind]["properties"], **required_fields(kind,spine.semantic,supplied)}.items() if k != "lessons"})) for kind in kinds]))
        schema["required"].append("add")
    if defs: schema["$defs"] = defs
    return schema


def strict_schema(schema):
    """Audit the emitted common subset; references only share finite enum choices."""
    import json
    original, definitions, shared = schema.get("$defs",{}), {}, {}
    def compact(node, chain=(), depth=0):
        if "$ref" in node:
            ref = node["$ref"]
            if set(node) != {"$ref"} or not ref.startswith("#/$defs/") or ref in chain or ref.rsplit("/",1)[1] not in original:
                raise ValueError("CODE defect: nonlocal/recursive schema reference")
            return compact(original[ref.rsplit("/",1)[1]],(*chain,ref),depth)
        depth += node.get("type") in ("object","array")
        if depth>10 or "$defs" in node: raise ValueError("CODE defect: unsupported schema nesting")
        node = {**node, **({"properties":{k:compact(v,chain,depth) for k,v in node["properties"].items()}} if "properties" in node else {}),
                **({"items":compact(node["items"],chain,depth)} if "items" in node else {}),
                **({"anyOf":[compact(v,chain,depth) for v in node["anyOf"]]} if "anyOf" in node else {})}
        if "anyOf" in node or "enum" in node:
            key = json.dumps(node,sort_keys=True)
            name = shared.setdefault(key,"s"+str(len(shared)))
            definitions[name] = node
            return {"$ref":"#/$defs/"+name}
        return node
    result = compact({k:v for k,v in schema.items() if k != "$defs"})
    def declarations(node):
        return [node] + [v for child in [*node.get("properties",{}).values(),*node.get("anyOf",[]),*([node["items"]] if "items" in node else [])] for v in declarations(child)]
    rows = declarations(result) + [v for d in definitions.values() for v in declarations(d)]
    totals = [0,0,sum(map(len,definitions))]
    if result.get("type") != "object": raise ValueError("CODE defect: strict root must be an object")
    for node in rows:
        if set(node)-{"type","properties","required","additionalProperties","items","anyOf","enum","pattern","minItems","$ref","description"} or node.get("type") not in (None,"object","array","string","number","integer","boolean","null"):
            raise ValueError("CODE defect: unsupported strict schema keyword/type")
        if node.get("type") == "object" and (node.get("additionalProperties") is not False or set(node.get("required",[])) != set(node.get("properties",{}))):
            raise ValueError("CODE defect: strict object properties must equal required and additionalProperties=false")
        if node.get("minItems",0) not in (0,1) or "pattern" in node and node["pattern"] != r"\S": raise ValueError("CODE defect: unsupported common schema constraint")
        values = node.get("enum",[])
        size = sum(len(v) for v in values if isinstance(v,str))
        if "enum" in node and (not values or any(isinstance(v,(dict,list)) for v in values) or len(values)>250 and size>15000): raise ValueError("CODE defect: invalid/oversized strict enum")
        totals = [totals[0]+len(node.get("properties",{})),totals[1]+len(values),totals[2]+size+sum(map(len,node.get("properties",{})))]
    if sum("anyOf" in n for n in rows)>16 or any(v>limit for v,limit in zip(totals,(5000,1000,120000))): raise ValueError("CODE defect: strict schema complexity budget exceeded")
    if definitions: result["$defs"] = definitions
    return result
