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
COMMAND = {**array(TEXT, 1), "description": 'Executable argv: an installed verification tool or a declared product output, followed by separate arguments.'}
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
    "bead": obj(instructions=TEXT, testPaths=array(TEXT, 1),
                ownedPaths=array(TEXT, 1), dependencies=REFS,
                requirements=array(TEXT, 1), tests=array(TEXT, 1)),
    "wave": obj(goal=TEXT, beads=array(TEXT, 1)),
}
GAP = obj(target=TEXT, correction=TEXT)
FAULT = obj(cause=choice("evidence", "implementation", "plan"), target=TEXT, correction=TEXT)
PAYLOADS = {
    "ALIGN": VISION,
    "DECIDE": obj(choice=choice("approve", "revise", "decline", "unclear")),
    "REVIEW": obj(verdict=choice("adequate", "gap"), gaps=array(GAP)),
    "WORK": obj(outcome=choice("done", "blocked"), summary=TEXT,
                artifactPaths=REFS, discoveries=array(FAULT)),
    "IMPACT": obj(verdict=choice("within_scope", "scope_change")),
    "JUDGE": obj(verdict=choice("satisfied", "gap"),
                 assessments=array(obj(target=TEXT, satisfied={"type": "boolean"}, why=TEXT)),
                 gaps=array(FAULT)),
}


def answer_schema(kind, facts, context):
    from copy import deepcopy
    handles = list(context.get("handles", {}))
    slots = [f"new:{i}" for i in range(1, 33)]
    link = {"$ref": "#/$defs/link"}
    targets = list(dict.fromkeys([*facts, *handles, *context.get("assessments", []), *context.get("paths", [])]))
    reference = choice(*targets)
    need = obj(target=reference, question=TEXT)
    uncertain = obj(verdict=choice("uncertain"), neededFacts=array(need, 1))
    payload = deepcopy(PAYLOADS.get(kind, obj()))
    if kind in ("PLAN", "REPAIR"):
        puts = []
        bodies = {k: BODIES[k] for k in ("feature", "requirement", "test")}
        if kind == "REPAIR":
            bodies = {"vision": VISION, **bodies}
            if context.get("units"):
                bodies["bead"] = BODIES["bead"]
        for node_kind, body in bodies.items():
            body = deepcopy(body)
            from .plan import LINKS
            for relation in LINKS.get(node_kind, {}):
                body["properties"][relation]["items"] = link
            puts.append(obj(kind=choice(node_kind), targetHandle=choice(*context["units"]) if node_kind == "bead" else link, typedBody=body))
        edits = puts if kind == "PLAN" else [*puts,
            obj(op=choice("Retire"), targetHandle=choice(*handles), replacementHandles=array(link))]
        payload = obj(edits=array({"anyOf": edits}, 1))
        if kind == "PLAN":
            scope = obj(requirements=array(link, 1), tests=array(link, 1),
                productFiles=array(TEXT, 1), testFiles=array(TEXT, 1),
                dependencies=array({"type": "integer", "minimum": 1, "maximum": 32,
                                    "description": "1-based position of another unit in this units list"}))
            payload = obj(**payload["properties"], units=array(scope, 1))
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
    if kind == "WORK":
        blocked = deepcopy(payload)
        payload["properties"]["outcome"] = choice("done")
        blocked["properties"]["outcome"] = choice("blocked")
        blocked["properties"]["discoveries"]["minItems"] = 1
        variants = [payload, blocked, uncertain]
    contract = obj(answer={"anyOf": [obj(**p["properties"], why=TEXT, citations=array(choice(*facts), 1)) for p in variants]})
    if kind in ("PLAN", "REPAIR"):
        contract["$defs"] = {"link": choice(*handles, *slots)}
    return contract


def validate(value, contract, path="answer"):
    """Validate the emitted JSON-schema subset; never interpret product meaning."""
    errors = validation_errors(value, contract, path)
    if errors:
        raise ValueError("\n".join(errors))


def validation_errors(value, contract, path="answer", root=None):
    root = root or contract
    if "$ref" in contract:
        contract = root["$defs"][contract["$ref"].removeprefix("#/$defs/")]
    if "anyOf" in contract:
        branches = [validation_errors(value, child, path, root) for child in contract["anyOf"]]
        return [] if [] in branches else [error for branch in branches for error in branch]
    kind = contract["type"]
    expected = {"object": dict, "array": list, "string": str,
                "boolean": bool, "null": type(None), "integer": int}[kind]
    if type(value) is not expected:
        return [f"{path}: expected {kind}"]
    errors = []
    if kind == "integer" and not contract.get("minimum", value) <= value <= contract.get("maximum", value):
        errors.append(f"{path}: out of range")
    if kind == "object":
        fields = contract["properties"]
        errors += [f"{path}.{key}: required field missing" for key in fields.keys() - value.keys()]
        errors += [f"{path}.{key}: unexpected field" for key in value.keys() - fields.keys()]
        for key in fields.keys() & value.keys():
            errors += validation_errors(value[key], fields[key], f"{path}.{key}", root)
    elif kind == "array":
        if not contract["minItems"] <= len(value) <= contract["maxItems"]:
            errors.append(f"{path}: invalid array length")
        for index, child in enumerate(value):
            errors += validation_errors(child, contract["items"], f"{path}[{index}]", root)
    elif kind == "string":
        if "enum" in contract and value not in contract["enum"]:
            errors.append(f"{path}: expected one of {contract['enum']}")
        if not contract.get("minLength", 0) <= len(value) <= contract.get("maxLength", 8192):
            errors.append(f"{path}: invalid text length")
    return errors
