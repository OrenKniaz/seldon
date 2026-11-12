"""C4/I3/I10; R6/R12: strict product-neutral answers, with requirement-local verdicts."""


def obj(**fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def array(item, minimum=0):
    return {"type": "array", "items": item, "minItems": minimum}


def choice(*values):
    return {"type": "string", "enum": list(values)}


TEXT = {"type": "string", "minLength": 1, "maxLength": 8192}
REFS = array(TEXT)
VISION = obj(size=choice("tiny", "small", "medium", "large"), understanding=TEXT,
             users=array(TEXT, 1), scopeIn=array(TEXT, 1), scopeOut=REFS, improvements=REFS,
             quality=array(obj(criterion=TEXT, verification=TEXT), 1), unknowns=REFS)
FEATURE = obj(story=TEXT, acceptance=TEXT)
POSITION = {"type": "integer", "minimum": 1}
BODIES = {
    "area": obj(responsibility=TEXT, scope=array(TEXT, 1)),
    "contract": obj(
        kind=choice("api", "schema", "file-format", "shared-type", "ui-structure", "command-line"),
        text=TEXT, provider=TEXT, consumers=array(TEXT, 1)),
    "decision": obj(choice=TEXT, reason=TEXT, origin=choice("architecture", "research", "user"),
                    areas=array(TEXT, 1)),
    "obligation": obj(what=TEXT, owner=TEXT, closingRequirement=TEXT),
    "feature": obj(**FEATURE["properties"], vision=array(choice("vision"), 1)),
    "requirement": obj(obligation=TEXT, design=TEXT, contract=TEXT, features=array(TEXT, 1)),
    "check": obj(purpose=TEXT, mode=choice("establishes", "preserves"), command=array(TEXT, 1),
                 requiredEnvironment=REFS, expectedEvidence=TEXT, requirements=array(TEXT, 1)),
    "unit": obj(instructions=TEXT, scope=array(TEXT, 1), checkPaths=array(TEXT, 1),
                requirements=REFS, dependencies=REFS, area=TEXT, provides=REFS, consumes=REFS, reads=REFS),
}
GAP = obj(target=TEXT, correction=TEXT)
PLAN_BODIES = {**BODIES, "feature": FEATURE,
               "contract": obj(**{**BODIES["contract"]["properties"],
                                  "provider": POSITION, "consumers": array(POSITION, 1)}),
               "obligation": obj(what=TEXT, owner=POSITION, closingRequirement=TEXT)}
PLAN = obj(edits=array({"anyOf": [obj(kind=choice(kind), targetHandle=TEXT,
                                     typedBody=body)
                                  for kind, body in PLAN_BODIES.items() if kind != "unit"]}, 1),
           units=array(obj(scope=array(TEXT, 1), checkPaths=array(TEXT, 1), requirements=array(TEXT, 1),
                           dependencies=array(POSITION), instructions=TEXT, area=TEXT,
                           provides=REFS, consumes=REFS, reads=REFS), 1),
           skeleton=array(POSITION),
           quality=array(obj(criterion={"type": "integer", "minimum": 1},
                             level=choice("unit", "product"), checks=array(TEXT, 1)), 1))


def answer_schema(kind, targets=(), unit_criteria=()):
    flag = {"type": "boolean"}
    assessment = obj(target=choice(*targets) if targets else TEXT, meaningful=flag,
                     noStubsMocksMissing=flag, why=TEXT)
    if kind == "JUDGE":
        requirement = obj(**{**assessment["properties"],
                             "target": choice(*(t for t in targets if t not in unit_criteria))},
                          exercisedAtP=flag, baselineDemonstrated=flag)
        assessment = ({"anyOf": [requirement, obj(**{**assessment["properties"],
                                                   "target": choice(*unit_criteria)})]}
                      if unit_criteria else requirement)
    payload = {
        "ALIGN": VISION, "PLAN": PLAN, "REPAIR": PLAN,
        "DECIDE": obj(choice=choice("approve", "revise", "decline", "unclear")),
        "REVIEW": obj(verdict=choice("adequate", "gap"), gaps=array(GAP)),
        "WORK": obj(outcome=choice("done", "blocked"), summary=TEXT, discoveries=array(GAP)),
        "JUDGE": obj(verdict=choice("satisfied", "gap"), assessments=array(assessment), gaps=array(GAP)),
        "DEMO": obj(verdict=choice("satisfied", "gap"), assessments=array(assessment), gaps=array(GAP)),
        "PRODUCT": obj(verdict=choice("satisfied", "gap"), assessments=array(assessment), gaps=array(GAP)),
    }[kind]
    uncertain = obj(verdict=choice("uncertain"), neededFacts=array(TEXT, 1),
                    **({"evidenceLevel": choice("unit", "product")} if kind in ("DEMO", "PRODUCT") else {}))
    return obj(answer={"anyOf": [obj(**item["properties"], why=TEXT) for item in (payload, uncertain)]})


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
        if not contract.get("minLength", 0) <= len(value) <= contract.get("maxLength", len(value)):
            errors.append(f"{path}: invalid text length")
    elif kind == "integer" and value < contract.get("minimum", value):
        errors.append(f"{path}: out of range")
    return errors
