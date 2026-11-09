"""C4/I3/I10: closed, product-neutral answers to one typed question."""


def obj(**fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def array(item, minimum=0):
    return {"type": "array", "items": item, "minItems": minimum, "maxItems": 128}


def choice(*values):
    return {"type": "string", "enum": list(values)}


TEXT = {"type": "string", "minLength": 1, "maxLength": 8192}
REFS = array(TEXT)
COMMAND = {**array(TEXT, 1), "description": 'Executable argv, never shell text. Example: ["C:/node/node.exe", "tests/play.cjs"]. argv[0] must resolve to an installed executable; each argument is a separate element.'}
VISION = obj(size=choice("tiny", "small", "medium", "large"), understanding=TEXT,
             users=array(TEXT, 1), scopeIn=array(TEXT, 1), scopeOut=REFS,
             improvements=REFS, quality=array(obj(criterion=TEXT, verification=TEXT), 1),
             unknowns=REFS)
BODIES = {
    "feature": obj(story=TEXT, acceptance=TEXT, vision=array(TEXT, 1)),
    "requirement": obj(obligation=TEXT, design=TEXT, contract=TEXT, features=array(TEXT, 1)),
    "test": obj(purpose=TEXT, stage=choice("verification", "walkthrough"),
                command=COMMAND, requiredEnvironment=REFS, expectedEvidence=TEXT,
                requirements=array(TEXT, 1)),
    "bead": obj(task=choice("tests", "build", "guide"), instructions=TEXT,
                ownedPaths=array(TEXT, 1), dependencies=REFS,
                requirements=array(TEXT, 1), tests=array(TEXT, 1)),
    "wave": obj(goal=TEXT, beads=array(TEXT, 1)),
}
GAP = obj(target=TEXT, correction=TEXT)
PAYLOADS = {
    "ALIGN": VISION,
    "DECIDE": obj(choice=choice("approve", "revise", "decline", "unclear")),
    "REVIEW": obj(verdict=choice("adequate", "gap"), gaps=array(GAP)),
    "WORK": obj(outcome=choice("done", "blocked"), summary=TEXT,
                artifactPaths=REFS, discoveries=array(GAP)),
    "IMPACT": obj(verdict=choice("within_scope", "scope_change")),
    "JUDGE": obj(verdict=choice("satisfied", "gap"),
                 assessments=array(obj(target=TEXT, satisfied={"type": "boolean"}, why=TEXT)),
                 gaps=array(GAP)),
}


def answer_schema(kind, facts, context):
    from copy import deepcopy
    handles = list(context.get("handles", {}))
    slots = [f"new:{i}" for i in range(1, 33)]
    targets = list(dict.fromkeys([*facts, *handles, *context.get("assessments", [])]))
    reference = choice(*targets)
    need = obj(target=reference, question=TEXT)
    uncertain = obj(verdict=choice("uncertain"), neededFacts=array(need, 1))
    payload = deepcopy(PAYLOADS.get(kind, obj()))
    if kind in ("PLAN", "REPAIR"):
        puts = []
        bodies = {k: BODIES[k] for k in ("feature", "requirement", "test")}
        if kind == "REPAIR":
            bodies = {"vision": VISION, **bodies}
        for node_kind, body in bodies.items():
            body = deepcopy(body)
            from .plan import LINKS
            for relation in LINKS.get(node_kind, {}):
                body["properties"][relation]["items"] = choice(*handles, *slots)
            puts.append(obj(kind=choice(node_kind), targetHandle=choice(*handles, *slots), typedBody=body))
        edits = puts if kind == "PLAN" else [*puts,
            obj(op=choice("Retire"), targetHandle=choice(*handles), replacementHandles=array(choice(*handles, *slots)))]
        payload = obj(edits=array({"anyOf": edits}, 1 if kind == "PLAN" else 0))
        if kind == "PLAN":
            payload = obj(**payload["properties"], productFiles=array(TEXT, 1), testFiles=array(TEXT, 1))
    for field in ("gaps", "discoveries", "neededFacts"):
        if field in payload["properties"]:
            payload["properties"][field]["items"]["properties"]["target"] = reference
    if kind == "WORK":
        paths = [path for fact in facts.values() if isinstance(fact["boundedContent"], dict)
                 for path in fact["boundedContent"].get("ownedPaths", [])]
        payload["properties"]["artifactPaths"]["items"] = choice(*paths)
    if kind == "JUDGE":
        payload["properties"]["assessments"]["items"]["properties"]["target"] = choice(*context["assessments"])
    variants = [payload, uncertain]
    return obj(answer={"anyOf": [obj(**p["properties"], why=TEXT, citations=array(choice(*facts), 1))
                                  for p in variants]})


def validate(value, contract, path="answer"):
    """Validate the emitted JSON-schema subset; never interpret product meaning."""
    errors = validation_errors(value, contract, path)
    if errors:
        raise ValueError("\n".join(errors))


def validation_errors(value, contract, path="answer"):
    if "anyOf" in contract:
        branches = [validation_errors(value, child, path) for child in contract["anyOf"]]
        return [] if [] in branches else [error for branch in branches for error in branch]
    kind = contract["type"]
    expected = {"object": dict, "array": list, "string": str,
                "boolean": bool, "null": type(None)}[kind]
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
        if not contract["minItems"] <= len(value) <= contract["maxItems"]:
            errors.append(f"{path}: invalid array length")
        for index, child in enumerate(value):
            errors += validation_errors(child, contract["items"], f"{path}[{index}]")
    elif kind == "string":
        if "enum" in contract and value not in contract["enum"]:
            errors.append(f"{path}: expected one of {contract['enum']}")
        if not contract.get("minLength", 0) <= len(value) <= contract.get("maxLength", 8192):
            errors.append(f"{path}: invalid text length")
    return errors
