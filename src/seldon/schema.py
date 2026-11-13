"""C4/I3/I10; R6/R12: strict product-neutral answers, with requirement-local verdicts."""


def obj(**fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def array(item, minimum=0):
    return {"type": "array", "items": item, "minItems": minimum}


def choice(*values):
    return {"type": "string", "enum": list(values)}


def small(item, minimum=0, maximum=12):
    return {**array(item, minimum), "maxItems": maximum}


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
                             checks=array(TEXT, 1)), 1))

# Architecture names areas, never work units. DETAIL owns only its local records.
AREA = obj(handle=TEXT, responsibility=TEXT, scope={**small(TEXT, 1), "description":
    "Complete disjoint ownership: product paths, this area's check subtree, and any shared check "
    "helpers it owns. DETAIL may divide this scope but cannot enlarge it."}, commitments=small(TEXT, 1))
AREA_CONTRACT = obj(handle=TEXT, **{**BODIES["contract"]["properties"], "consumers": small(TEXT, 1)})
ARCHITECT = obj(areas=small(AREA, 1), contracts=small(AREA_CONTRACT, maximum=24),
                decisions=small(BODIES["decision"]), waves=small(obj(goal=TEXT), 1, 8))
DETAIL_PLAN = obj(verdict=choice("planned"),
    edits=small({"anyOf": [obj(kind=choice(k), targetHandle=TEXT, typedBody=PLAN_BODIES[k])
                           for k in ("feature", "requirement", "check", "obligation")]}, 1, 48),
    units=small(obj(**{k: (small({"anyOf": [POSITION, TEXT]}) if k == "dependencies" else v)
                      for k, v in PLAN["properties"]["units"]["items"]["properties"].items()
                      if k != "area"}), 1),
    quality=small(obj(criterion=POSITION, checks=small(TEXT, 1))))
DETAIL_SPLIT = obj(verdict=choice("subdivide"), subareas=small(AREA, 2),
                   contracts=small(AREA_CONTRACT, maximum=24))
DETAIL_GAP = obj(verdict=choice("contract_gap"), need=TEXT)


def bounded(contract):
    """Planning lists and text stay small, including nested trace links and argv."""
    if isinstance(contract, dict):
        result = {k: bounded(v) for k, v in contract.items()}
        if result.get("type") == "array":
            result.setdefault("maxItems", 12)
        if result.get("type") == "string" and "maxLength" in result:
            result["maxLength"] = min(2048, result["maxLength"])
        return result
    return [bounded(v) for v in contract] if isinstance(contract, list) else contract


ARCHITECT, DETAIL_PLAN, DETAIL_SPLIT, DETAIL_GAP = (
    bounded(s) for s in (ARCHITECT, DETAIL_PLAN, DETAIL_SPLIT, DETAIL_GAP))


# Fixed source obligations are assessed by every product unit JUDGE, independently of criteria.
SOURCE_TARGETS = (
    "No stubs, mocks or missing behaviour in this unit",
    "Meaningful tests for this unit's requirements",
    "This unit owns its stated responsibility",
    "No reliance on sibling implementation files without an explicit dependency",
)


def answer_schema(kind, targets=()):
    flag = {"type": "boolean"}
    assessment = obj(target=choice(*targets) if targets else TEXT, meaningful=flag,
                     noStubsMocksMissing=flag, why=TEXT)
    if kind == "JUDGE":
        requirement = obj(**{**assessment["properties"],
                             "target": choice(*(t for t in targets if t not in SOURCE_TARGETS))},
                          exercisedAtP=flag, baselineDemonstrated=flag)
        assessment = {"anyOf": [requirement, obj(**{**assessment["properties"],
                                                   "target": choice(*SOURCE_TARGETS)})]}
    payload = {
        "ALIGN": VISION, "PLAN": PLAN, "REPAIR": PLAN, "ARCHITECT": ARCHITECT,
        "DETAIL": DETAIL_PLAN,
        "DECIDE": obj(choice=choice("approve", "revise", "decline", "unclear")),
        "REVIEW": obj(verdict=choice("adequate", "gap"), gaps=array(GAP)),
        "WORK": obj(outcome=choice("done", "blocked"), summary=TEXT, discoveries=array(GAP)),
        "JUDGE": obj(verdict=choice("satisfied", "gap"), assessments=array(assessment), gaps=array(GAP)),
        "DEMO": obj(verdict=choice("satisfied", "gap"), assessments=array(assessment), gaps=array(GAP)),
        "PRODUCT": obj(verdict=choice("satisfied", "gap"), assessments=array(assessment), gaps=array(GAP)),
    }[kind]
    uncertain = obj(verdict=choice("uncertain"), neededFacts=array(TEXT, 1))
    alternatives = ((payload, DETAIL_SPLIT, DETAIL_GAP, uncertain) if kind == "DETAIL"
                    else (payload, uncertain))
    result = obj(answer={"anyOf": [obj(**item["properties"], why=TEXT) for item in alternatives]})
    return bounded(result) if kind in ("ARCHITECT", "DETAIL") else result


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
        if len(value) > contract.get("maxItems", len(value)):
            errors.append(f"{path}: too many items; subdivide this area")
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
