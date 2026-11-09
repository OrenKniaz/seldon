"""C4/I3/I10: closed, product-neutral answers to one typed question."""


def obj(**fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def array(item, minimum=0):
    return {"type": "array", "items": item, "minItems": minimum, "maxItems": 128}


def choice(*values):
    return {"type": "string", "enum": list(values)}


TEXT = {"type": "string", "minLength": 1, "maxLength": 8192}
BOOL = {"type": "boolean"}
NULL_TEXT = {"anyOf": [TEXT, {"type": "null"}]}
REFS = array(TEXT)
VISION = obj(size=choice("tiny", "small", "medium", "large"), understanding=TEXT,
             users=array(TEXT, 1), scopeIn=array(TEXT, 1), scopeOut=REFS,
             improvements=REFS, quality=array(obj(criterion=TEXT, verification=TEXT), 1),
             unknowns=REFS)
BODIES = {
    "feature": obj(story=TEXT, acceptance=TEXT, vision=array(TEXT, 1)),
    "requirement": obj(obligation=TEXT, design=TEXT, contract=TEXT, features=array(TEXT, 1)),
    "test": obj(purpose=TEXT, stage=choice("verification", "walkthrough"),
                command=array(TEXT, 1), requiredEnvironment=REFS, expectedEvidence=TEXT,
                requirements=array(TEXT, 1)),
    "bead": obj(task=choice("tests", "build", "guide"), instructions=TEXT,
                ownedPaths=array(TEXT, 1), dependencies=REFS,
                requirements=array(TEXT, 1), tests=array(TEXT, 1)),
    "wave": obj(goal=TEXT, beads=array(TEXT, 1)),
}
PUTS = [{**obj(kind={"const": kind}, targetHandle=TEXT, typedBody=body)}
        for kind, body in BODIES.items()]
GAP = obj(target=TEXT, correction=TEXT)
NEED = obj(target=TEXT, question=TEXT)
PAYLOADS = {
    "ALIGN": VISION,
    "DECIDE": obj(choice=choice("approve", "revise", "decline", "unclear"),
                  requestedChange=NULL_TEXT),
    "PLAN": obj(edits=array({"anyOf": PUTS}, 1)),
    "REVIEW": obj(verdict=choice("adequate", "gap", "uncertain"),
                  gaps=array(GAP), neededFacts=array(NEED)),
    "WORK": obj(outcome=choice("done", "blocked"), summary=TEXT,
                artifactPaths=REFS, discoveries=array(GAP)),
    "JUDGE": obj(verdict=choice("satisfied", "gap", "uncertain"),
                 assessments=array(obj(target=TEXT, satisfied=BOOL, why=TEXT), 1),
                 gaps=array(GAP), neededFacts=array(NEED)),
    "STEER": obj(choice=choice("continue"), targets=array(TEXT, 1),
                 action=obj(kind=choice("user_decision"))),
}


def answer_schema(kind):
    return obj(**PAYLOADS[kind]["properties"], why=TEXT, citations=array(TEXT, 1))


def validate(value, contract, path="answer"):
    """Validate the emitted JSON-schema subset; never interpret product meaning."""
    if "anyOf" in contract:
        errors = []
        for alternative in contract["anyOf"]:
            try:
                validate(value, alternative, path)
                return
            except ValueError as error:
                errors.append(str(error))
        raise ValueError("; ".join(errors))
    if "const" in contract:
        if value != contract["const"]:
            raise ValueError(f"{path}: expected {contract['const']!r}")
        return
    kind = contract["type"]
    expected = {"object": dict, "array": list, "string": str,
                "boolean": bool, "null": type(None)}[kind]
    if type(value) is not expected:
        raise ValueError(f"{path}: expected {kind}")
    if kind == "object":
        if value.keys() != contract["properties"].keys():
            raise ValueError(f"{path}: fields must be {contract['required']}")
        for key, child in value.items():
            validate(child, contract["properties"][key], f"{path}.{key}")
    elif kind == "array":
        if not contract["minItems"] <= len(value) <= contract["maxItems"]:
            raise ValueError(f"{path}: invalid array length")
        for index, child in enumerate(value):
            validate(child, contract["items"], f"{path}[{index}]")
    elif kind == "string":
        if "enum" in contract and value not in contract["enum"]:
            raise ValueError(f"{path}: expected one of {contract['enum']}")
        if not contract.get("minLength", 0) <= len(value) <= contract.get("maxLength", 8192):
            raise ValueError(f"{path}: invalid text length")
