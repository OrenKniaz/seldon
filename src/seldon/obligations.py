"""C15/R5: skeleton placeholders remain open until final-commit requirement judgment."""

from . import plan, questions


def validate(document):
    units = {r: u for r, u in plan.select(document, "unit").items() if r != "guide"}
    skeleton = document["skeleton"]
    if len(set(skeleton)) != len(skeleton) or set(skeleton) != (set(units) if len(units) > 1 else set()):
        raise ValueError("Wave 1 must name every product unit as the skeleton for a multi-unit dream")
    for ref, item in plan.select(document, "obligation").items():
        if (item["owner"] not in skeleton
                or item["closingRequirement"] not in units[item["owner"]]["requirements"]):
            raise ValueError(f"{ref}: a skeleton obligation needs an owner and its closing requirement")


def for_area(document, area):
    units = plan.select(document, "unit")
    return {r: item for r, item in plan.select(document, "obligation").items()
            if units[item["owner"]]["area"] == area}


def remaining(store):
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    satisfied = {v["subject"] for ref in cursor.requirementVerdicts for v in [store.read(ref)]
                 if v["commit"] == cursor.candidate and questions.satisfied(v["answer"], final=True)
                 and any(a["target"] == v["subject"] for a in v["answer"]["assessments"])}
    return {r: item for r, item in plan.select(document, "obligation").items()
            if item["closingRequirement"] not in satisfied}
