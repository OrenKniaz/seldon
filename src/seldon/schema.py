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
FEATURE = obj(story=TEXT, acceptance=TEXT)
POSITION = {"type": "integer", "minimum": 1}
BODIES = {
    "area": obj(responsibility=TEXT, scope=array(TEXT, 1)),
    "contract": obj(kind=TEXT, text=TEXT, provider=TEXT, consumers=REFS),
    "decision": obj(choice=TEXT, reason=TEXT, origin=choice("architecture", "research", "user"),
                    areas=array(TEXT, 1)),
    "feature": obj(**FEATURE["properties"], vision=array(choice("vision"), 1)),
    "requirement": obj(acceptance=TEXT, features=array(TEXT, 1)),
    "unit": obj(instructions=TEXT, scope=array(TEXT, 1),
                requirements=REFS, dependencies=REFS, area=TEXT),
}
GAP = obj(target=TEXT, correction=TEXT)
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
DETAIL_PLAN = obj(verdict=choice("planned"),
    edits=array({"anyOf": [obj(kind=choice(k), targetHandle=TEXT, typedBody=PLAN_BODIES[k])
                           for k in ("feature", "requirement")]}, 1),
    units=array({"anyOf": [PLANNED_UNIT, obj(**PLANNED_UNIT["properties"], provides=REFS)]}, 1))
DETAIL_SPLIT = obj(verdict=choice("subdivide"), subareas=array(AREA, 2), contracts=array(AREA_CONTRACT))
DETAIL_GAP = obj(verdict=choice("contract_gap"), need=TEXT)


def answer_schema(kind, targets=(), owners=()):
    gap = obj(target=choice(*targets), correction=TEXT, owners=array(choice(*owners), 1))
    payload = {
        "ALIGN": VISION, "ARCHITECT": ARCHITECT,
        "DETAIL": DETAIL_PLAN,
        "DECIDE": obj(choice=choice(*targets,
                                    "Other", "Stop", "Unclear")),
        "REVIEW": obj(verdict=choice("adequate", "gap"), gaps=array(GAP)),
        "WORK": obj(outcome=choice("done", "question"), summary=TEXT,
                    checkPaths=REFS, checks=array(obj(purpose=TEXT, command=array(TEXT, 1),
                        expectedEvidence=TEXT)),
                    discoveries=array(obj(owner=choice(*owners), correction=TEXT))),
        "COACH": obj(guidance=TEXT),
        "JUDGE": obj(verdict=choice("satisfied", "gap"), gaps=array(GAP)),
        "FLOW": obj(verdict=choice("satisfied", "gap"), gaps=array(gap)),
        "PRODUCT": obj(verdict=choice("satisfied", "gap"), gaps=array(gap)),
    }[kind]
    uncertain = obj(verdict=choice("uncertain"), neededFacts=array(TEXT, 1))
    alternatives = ((payload, DETAIL_SPLIT, DETAIL_GAP) if kind == "DETAIL" else
                    (payload, obj(scope=TEXT)) if kind == "ARCHITECT" else
                    (payload, obj(ask=ASK)) if kind == "COACH" else
                    (payload, uncertain) if kind in ("JUDGE", "FLOW", "PRODUCT", "REVIEW") else
                    (payload,))
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
        if "enum" in contract and value not in contract["enum"]:
            errors.append(f"{path}: expected one of {contract['enum']}")
        if len(value) < contract.get("minLength", 0):
            errors.append(f"{path}: invalid text length")
    elif kind == "integer" and value < contract.get("minimum", value):
        errors.append(f"{path}: out of range")
    return errors
