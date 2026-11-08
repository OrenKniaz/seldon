"""Question-specific output contracts. Control is closed; product meaning is open."""

TEXT = dict(type="string", minLength=1)
STRING = dict(type="string")
BOOL = dict(type="boolean")


def obj(**properties):
    return dict(type="object", properties=properties, required=list(properties), additionalProperties=False)


def array(items=TEXT):
    return dict(type="array", items=items)


def choice(*values):
    return dict(type="string", enum=list(values))


def nullable(schema):
    return dict(anyOf=[schema, dict(type="null")])


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

# Only literal application data uses a recursive JSON value. Executable
# interfaces (actions, launches, observations) have explicit contracts below.
DATA = {"$ref": "#/$defs/data"}
DATA_DEF = dict(anyOf=[STRING, dict(type="number"), BOOL, dict(type="null"),
                      array(DATA), obj(entries=array(obj(key=STRING, value=DATA)))])
ARGV = array(dict(anyOf=[STRING, obj(path=TEXT), obj(input=TEXT)]))
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
    ref=TEXT, requirementRefs=array(), contractRefs=array(), testRef=nullable(TEXT), rationale=nullable(STRING),
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

TARGET = obj(ref=TEXT, kind=nullable(choice("web")), **LAUNCH["properties"],
             server=nullable(obj(**LAUNCH["properties"], readyPath=TEXT, readyWithin=dict(type="number"))))

# The trace writer remains a canonical structural boundary. Handles are local to
# one proposed slice; durable IDs are allocated by CODE, never chosen by a model.
from codex_harness.core.semantic_schema import TRACE_LINKS
COMMON = dict(expansionRefs=array(), rationale=nullable(STRING), **{k:nullable(array()) for k in TRACE_LINKS})
EXECUTION = obj(entry=nullable(TEXT), module=nullable(TEXT), runtime=nullable(TEXT),
                argv=ARGV,
                testRoots=array(), rationale=TEXT)
TRACE_FIELDS = {
    "feature": obj(**COMMON, baseline=nullable(obj(source=TEXT, behavior=TEXT, rationale=TEXT, codeRefs=array(), testRefs=array(), resultRefs=array()))), "story": obj(**COMMON), "requirement": obj(**COMMON),
    "architecture": obj(**COMMON), "design": obj(**COMMON, clauseCoverage=array(obj(clause=TEXT, coverage=TEXT))), "contract": obj(**COMMON),
    "decision": obj(**COMMON),
    "test": obj(**COMMON, module=TEXT, level=choice("semantic", "e2e"), execution=nullable(EXECUTION)),
    "bead": obj(**COMMON, ownedFiles=array(), dependsOn=array(), lessons=array(),
                semanticPhase=choice("contracts", "build"), capabilityRefs=array()),
    "discovery": obj(source=TEXT), "research": obj(**COMMON),
    "metric": obj(definition=TEXT, unit=TEXT),
    "stop-condition": obj(**{**COMMON,"metricRefs":array()}, condition=TEXT),
    "amendment": obj(**{**COMMON,**AMENDMENT["properties"]}),
    "iteration": obj(**{**COMMON,"metricRefs":array(),"resultRefs":array(),"stopConditionRefs":array()}),
    "baton": obj(**{**COMMON,"rationale":TEXT}, assumptions=array(),openQuestions=array(),currentState=TEXT,nextSafeAction=TEXT),
    "code": obj(**COMMON), "acceptance": obj(**COMMON),
}
TRACE = obj(records=array(obj()),
            remove=array(), targets=array(TARGET), scenarios=array(SCENARIO), rationale=TEXT, reply=STRING)
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


QUESTIONS = {
    'planning-size': (obj(**{**VERDICT, **dict(size=choice('tiny', 'small', 'medium', 'large'), process=TEXT, overrideAccepted=BOOL)}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(size='small', process='Focused review of consequence and recovery', overrideAccepted=False))]),
    'vision-intent': (obj(**{**VERDICT, **dict(impact=choice('preserved', 'changed', 'narrowed'))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(impact='changed'))]),
    'scope-intent': (obj(**{**VERDICT, **dict(impact=choice('WHAT', 'HOW'))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(impact='WHAT'))]),
    'vision-expansion': (obj(**{**VERDICT, **{}}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **{})]),
    'finding-equivalence': (obj(**{**VERDICT, **dict(groups=array(array()))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(groups=[]))]),
    'finding-disposition': (obj(**{**VERDICT, **dict(dispositions=array(obj(memberRef=TEXT, disposition=choice('resolved', 'rejected', 'deferred', 'open'))))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(dispositions=[]))]),
    'review-reuse': (obj(**{**VERDICT, **dict(reuse=BOOL)}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(reuse=False))]),
    'plan-convergence': (obj(**{**VERDICT, **dict(action=choice('ready', 'revise', 'continue-review', 'ask_user'))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(action='continue-review'))]),
    'plan-review': (obj(**{**VERDICT, **dict(findings=array(FINDING), depth=TEXT)}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(findings=[], depth='Inspect consequence and failure behavior'))]),
    'full-dream': (obj(**{**VERDICT, **dict(findings=array(FINDING), depth=TEXT, featureAssessments=array(obj(featureRef=TEXT, verdict=choice('positive', 'negative', 'inconclusive', 'unsupported'), rationale=TEXT)), qualityAssessments=array(obj(qualityRef=TEXT, verdict=choice('positive', 'negative', 'inconclusive', 'unsupported'), rationale=TEXT)), stubOrMissingGaps=array(GAP))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(findings=[], depth="Inspect the whole accepted dream", featureAssessments=[], qualityAssessments=[], stubOrMissingGaps=[]))]),
    'execution': (obj(**{**VERDICT, **dict(meaningful=BOOL, correctFailureReason=BOOL, noStubMockGaps=BOOL, qualitativeChecks=array(obj(check=TEXT, passed=BOOL)))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(meaningful=False, correctFailureReason=False, noStubMockGaps=False, qualitativeChecks=[]))]),
    'visualQuality': (obj(**{**VERDICT, **dict(qualitativeChecks=array(obj(check=TEXT, passed=BOOL)))}), [dict(verdict="inconclusive", gaps=[], rationale="The supplied observations do not establish this question", **dict(qualitativeChecks=[]))]),
    'user-decision': (DECISION, [dict(words='Yes, build exactly that', answer=dict(decision='approve', requestedChanges='', reply='I will build the displayed plan.', rationale='Unqualified approval of the displayed subject')), dict(words='How would it handle audio input?', answer=dict(decision='unclear', requestedChanges='', reply='Let us clarify audio input before deciding.', rationale='A question asks for explanation, not consent'))]),
    'direction': (STEERING, [dict(action='ask_user', amendments=[], question='May the accepted scope change?', reply='This discovery affects the agreed scope.', rationale='The observed gap requires a user scope decision')]),
    'repair': (obj(relationships=array(obj(childHandle=TEXT,parentRefs=array())),rationale=TEXT,reply=STRING), [dict(relationships=[], rationale='Repair only the supplied rejected relationships', reply='I will preserve the complete proposed slice and correct its links.')]),
    'understanding': (UNDERSTANDING, [dict(understanding='Compare measured performance with an agreed goal', users=['A person investigating a measurement'], firstUserMoment='Load a measurement and see its result', scopeIn=['Measure and compare'], scopeOut=['Unrequested changes'], qualityBar='Make uncertainty visible', doneFeelsLike='The person can reproduce and explain the result', assumptions=[], openQuestions=['Which measurement defines success?'], upgrades=[dict(proposal='Show uncertainty alongside the result', kind='micro', impact='Avoid misleading confidence', disposition='accept')], qualityCriteria=['Results are reproducible'], metrics=[], stopConditions=[], readiness='ask_user', question='Which measurement defines success?', reply='Let us agree on the success measurement before locking the vision.', rationale='The metric changes what we build')]),
    'finding-dispositions': (obj(dispositions=array(obj(memberRef=TEXT,disposition=choice("resolved","rejected","deferred"),resolution=TEXT)),rationale=TEXT,reply=STRING), [dict(dispositions=[], rationale='Await the current repair evidence before proposing closure', reply='The concern remains open.')]),
    "trace": (dict(TRACE, **{"$defs":{"data":DATA_DEF}}), [dict(records=[],remove=[],targets=[],scenarios=[],rationale="Author the supplied plan handles",reply="The linked plan is ready.")]),
    "plan-outline": (OUTLINE, [dict(**{k:[] for k in OUTLINE["properties"] if k not in {"rationale","reply"}},rationale="Propose the complete plan in one batch",reply="I will link the proposed plan next.")]),
}


def question_spec(family,purpose):
    return QUESTIONS["repair" if family == "repair" else "direction" if family == "direction" else "user-decision" if family == "user-interpretation" else purpose]


def author_contract(store, purpose, context):
    from copy import deepcopy
    from codex_harness.core.run_context import relevant_ids
    spine = store.spine()
    supplied = {key:spine.node(key) for key in relevant_ids(store,"author",purpose,context)}
    schema = deepcopy(question_spec("author",purpose)[0])
    if purpose == "plan-outline":
        vision = spine.node(spine.ids("vision")[0])["fields"]
        for group in OUTLINE["properties"].keys()-{"rationale","reply"}:
            refs = list(vision.get("targets",{})) if group == "targets" else [s["id"] for s in vision.get("scenarios",{}).get("scenarios",[])] if group == "scenarios" else [key for key,node in supplied.items() if node["kind"] == group]
            schema["properties"][group]["items"]["properties"]["ref"] = nullable(choice(*refs)) if refs else dict(type="null")
        return schema
    supplied.update({n["id"]:n for n in context["nodes"]})
    if context["nodes"]:
        schema["properties"]["records"]["items"] = dict(anyOf=[
            obj(ref=choice(*(n["id"] for n in context["nodes"] if n["kind"] == kind)), parents=array(choice(*supplied)), fields=fields)
            for kind,fields in TRACE_FIELDS.items() if any(n["kind"] == kind for n in context["nodes"])])
    else:
        schema["properties"]["records"]["maxItems"] = 0
    for field in ("targets", "scenarios"):
        if context[field]:
            schema["properties"][field]["items"]["properties"]["ref"] = choice(*(n["id"] for n in context[field]))
        else:
            schema["properties"][field]["maxItems"] = 0
    return schema
