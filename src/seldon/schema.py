"""C4/I3/I10; R6/R12: strict product-neutral answers, with requirement-local verdicts."""


def obj(**fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def array(item, minimum=0):
    return {"type": "array", "items": item, "minItems": minimum}


def choice(*values):
    return {"type": "string", "enum": list(values)}


TEXT = {"type": "string", "minLength": 1}
REFS = array(TEXT)
OPTION = obj(label=TEXT, means=TEXT)
ASK = obj(question=TEXT, options=array(OPTION, 2))
VISION = obj(size=choice("tiny", "small", "medium", "large"), understanding=TEXT,
             users=array(TEXT, 1), scopeIn=array(TEXT, 1), scopeOut=REFS, improvements=REFS,
             quality=array(obj(criterion=TEXT, verification=TEXT), 1), alternatives=array(OPTION, 1))
GROWN_VISION = obj(size=VISION["properties"]["size"], sizeWhy=TEXT, understanding=TEXT,
    users=array(TEXT, 1), stories=array(obj(handle=TEXT, feature=TEXT, story=TEXT, outcome=TEXT,
        constraints=REFS, sources=REFS), 1),
    constraints=array(obj(handle=TEXT, value=TEXT, meaning=TEXT, source=TEXT)),
    quality=array(obj(handle=TEXT, criterion=TEXT, verification=TEXT, constraints=REFS, sources=REFS), 1),
    scopeOut=array(obj(outcome=TEXT, sources=REFS)))
ALIGN_ROUND = obj(vision=GROWN_VISION, changed=TEXT,
                  questions=array(obj(affects=TEXT, question=TEXT, options=array(OPTION, 2))),
                  recommendation=choice("revise", "done"), why=TEXT)


def vision_contract(vision):
    return GROWN_VISION if "stories" in vision else VISION


def vision_body(answer):
    """Project a recorded round or a legacy ALIGN answer onto the body locked at A."""
    body = answer.get("vision", answer)
    return {key: body[key] for key in vision_contract(body)["properties"]}


def batch_decision(groups):
    return obj(selections=obj(**{group["id"]: obj(
        optionId=choice(*dict.fromkeys([*(option["id"] for option in group["options"]), "other", "unclear"])),
        text={"type": "string"}) for group in groups}), why=TEXT)


FEATURE = obj(story=TEXT, acceptance=TEXT)
GROWN_FEATURE = obj(**FEATURE["properties"], commitments=array(TEXT, 1))
POSITION = {"type": "integer", "minimum": 1}
BODIES = {
    "area": obj(responsibility=TEXT, scope=array(TEXT, 1)),
    "contract": obj(kind=TEXT, text=TEXT, provider=TEXT, consumers=REFS),
    "decision": obj(choice=TEXT, reason=TEXT, origin=choice("architecture", "research", "user", "environment"),
                    areas=array(TEXT, 1)),
    "feature": obj(**FEATURE["properties"], vision=array(choice("vision"), 1)),
    "requirement": obj(acceptance=TEXT, features=array(TEXT, 1)),
    "unit": obj(instructions=TEXT, scope=array(TEXT, 1),
                requirements=REFS, dependencies=REFS, area=TEXT),
}
# Stored units can predate the trial. A blank stored brief also uses legacy rendering.
BODIES["unit"] = {"anyOf": [BODIES["unit"], obj(**BODIES["unit"]["properties"],
                                              brief={"type": "string"})]}
SEVERITY = choice("must-fix", "nit", "pre-existing")
GAP = obj(target=TEXT, correction=TEXT, severity=SEVERITY)
PLAN_BODIES = {**BODIES, "feature": FEATURE,
               "contract": obj(**{**BODIES["contract"]["properties"],
                                  "provider": POSITION, "consumers": array(POSITION)})}
PLANNED_UNIT = obj(scope=array(TEXT, 1), requirements=array(TEXT, 1),
                   dependencies=array(POSITION), instructions=TEXT)
PLAN = obj(edits=array({"anyOf": [obj(kind=choice(kind), targetHandle=TEXT,
                                     typedBody=body)
                                  for kind, body in PLAN_BODIES.items() if kind != "unit"]}, 1),
           units=array({"anyOf": [obj(**PLANNED_UNIT["properties"], area=TEXT),
                                 obj(**PLANNED_UNIT["properties"], area=TEXT, provides=REFS)]}, 1))

# Architecture names areas, never work units. DETAIL owns only its local records.
AREA = obj(handle=TEXT, responsibility=TEXT, scope=array(TEXT, 1), commitments=array(TEXT, 1))
AREA_CONTRACT = obj(handle=TEXT, **BODIES["contract"]["properties"])
ARCHITECT = obj(areas=array(AREA), contracts=array(AREA_CONTRACT),
                decisions=array(BODIES["decision"]), waves=array(obj(goal=TEXT, commitments=REFS), 1))


def detail_plan(feature, *, planner_brief=False, guide_brief=False, continuing=False):
    unit = {**PLANNED_UNIT["properties"], "requirements": REFS}
    if planner_brief:
        unit["brief"] = TEXT
    assignments = [obj(**unit), obj(**unit, provides=REFS)]
    if continuing:
        del unit["scope"]
        assignments.extend([obj(**unit), obj(**unit, provides=REFS)])
    return obj(verdict=choice("planned"), edits=array({"anyOf": [
        obj(kind=choice("feature"), targetHandle=TEXT, typedBody=feature),
        obj(kind=choice("requirement"), targetHandle=TEXT, typedBody=PLAN_BODIES["requirement"])]}, 1),
        units=array({"anyOf": assignments}, 1),
        **({"guideBrief": TEXT} if guide_brief else {}))


DETAIL_PLAN = detail_plan(FEATURE)
GROWN_DETAIL_PLAN = detail_plan(GROWN_FEATURE)
DETAIL_SPLIT = obj(verdict=choice("subdivide"), subareas=array(AREA, 2), contracts=array(AREA_CONTRACT))
DETAIL_GAP = obj(verdict=choice("contract_gap"), need=TEXT)
CHECK = dict(purpose=TEXT, command=array(TEXT, 1), expectedEvidence=TEXT, requirements=REFS)


def lead_plan_schema(bound, promises):
    promise = obj(vision=choice(*promises), action=TEXT, result=TEXT, **({"notMetIf": TEXT} if bound else {}))
    milestone = obj(outcome=TEXT, design=TEXT, promises=array(promise, 1))
    return obj(answer=obj(milestones=array(milestone, 1)))


def folded_judge_schema(subjects, owner):
    def finding(subject):
        gap = obj(target=choice(subject), correction=TEXT, owners=array(choice(owner), 1), severity=SEVERITY)
        uncertain = obj(verdict=choice("uncertain"), neededFacts=array(TEXT, 1), why=TEXT)
        return {"anyOf": [
            obj(verdict=choice("satisfied"), gaps=array(gap), why=TEXT),
            obj(verdict=choice("gap"), gaps=array(gap, 1), why=TEXT),
            uncertain,
        ]}
    return obj(answer=obj(findings=obj(**{s: finding(s) for s in subjects}), why=TEXT))


def answer_schema(kind, targets=(), owners=(), *, grown=False, coach_contracts=False,
                  planner_brief=False, guide_brief=False, continuing=False):
    gap = obj(target=choice(*targets), correction=TEXT, owners=array(choice(*owners), 1), severity=SEVERITY)
    payload = {
        "ALIGN": VISION, "ARCHITECT": obj(**{**ARCHITECT["properties"],
            "waves": array(ARCHITECT["properties"]["waves"]["items"])}),
        "DETAIL": GROWN_DETAIL_PLAN if grown else DETAIL_PLAN,
        "DECIDE": obj(choice=choice(*targets,
                                    "Other", "Stop", "Unclear")),
        "REVIEW": obj(verdict=choice("adequate", "gap"), gaps=array(GAP)),
        "WORK": obj(outcome=choice("done"), summary=TEXT,
                    checkPaths=REFS, checks=array({"anyOf": [
                        obj(**CHECK), obj(update=TEXT, **CHECK), obj(retire=TEXT)]}),
                    discoveries=array(obj(owner=choice(*owners), correction=TEXT))),
        "COACH": obj(guidance=TEXT),
        "JUDGE": obj(verdict=choice("satisfied", "gap"),
                     gaps=array(obj(**GAP["properties"], owners=gap["properties"]["owners"]))),
        "FLOW": obj(verdict=choice("satisfied", "gap"), gaps=array(gap)),
        "PRODUCT": obj(verdict=choice("satisfied", "gap"), gaps=array(gap)),
    }[kind]
    if kind == "DETAIL" and (planner_brief or continuing):
        payload = detail_plan(GROWN_FEATURE if grown else FEATURE, planner_brief=planner_brief,
                              guide_brief=guide_brief, continuing=continuing)
    uncertain = obj(verdict=choice("uncertain"), neededFacts=array(TEXT, 1))
    alternatives = ((payload, DETAIL_SPLIT, DETAIL_GAP) if kind == "DETAIL" else
                    (payload, obj(scope=TEXT)) if kind == "ARCHITECT" else
                    (payload, obj(**{**payload["properties"], "outcome": choice("question"),
                                     "question": TEXT})) if kind == "WORK" else
                    (payload, obj(ask=ASK)) if kind == "COACH" else
                    (payload, uncertain) if kind in ("JUDGE", "FLOW", "PRODUCT", "REVIEW") else
                    (payload,))
    if coach_contracts:
        alternatives += (obj(contract=obj(resolution=choice("answer", "gap"), text=TEXT)),)
    return obj(answer={"anyOf": [obj(**item["properties"], why=TEXT) for item in alternatives]})


def validate(value, contract, path="answer"):
    errors = validation_errors(value, contract, path)
    if errors:
        raise ValueError("\n".join(errors))


def validation_errors(value, contract, path="answer"):
    if "anyOf" in contract:
        branches = [validation_errors(value, child, path) for child in contract["anyOf"]]
        return [] if [] in branches else min(branches, key=len)
    kind = contract["type"]
    expected = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int}[kind]
    if type(value) is not expected:
        return [f"{path}: expected {kind}"]
    errors = []
    if "enum" in contract and value not in contract["enum"]:
        errors.append(f"{path}: expected one of {contract['enum']}")
    if kind == "object":
        fields = contract["properties"]
        errors += [f"{path}.{key}: required field missing" for key in fields.keys() - value.keys()]
        errors += [f"{path}.{key}: unexpected field" for key in value.keys() - fields.keys()]
        for key in fields.keys() & value.keys():
            errors += validation_errors(value[key], fields[key], f"{path}.{key}")
    elif kind == "array":
        if len(value) < contract.get("minItems", 0):
            errors.append(f"{path}: too few items")
        for index, child in enumerate(value):
            errors += validation_errors(child, contract["items"], f"{path}[{index}]")
    elif kind == "string":
        if len(value) < contract.get("minLength", 0):
            errors.append(f"{path}: invalid text length")
    elif kind == "integer" and value < contract.get("minimum", value):
        errors.append(f"{path}: out of range")
    return errors
