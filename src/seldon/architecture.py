"""C9/C10/C12; R4/R6: canonical endpoints, ownership and bounded agreement projections."""

from copy import deepcopy

from . import plan
from .schema import ARCHITECT, DETAIL_PLAN, DETAIL_SPLIT
from .schema import validate as validate_schema


class ContractGap(ValueError):
    def __init__(self, message, areas):
        super().__init__(message)
        self.areas = areas


class DetailCorrection(ValueError):
    """Merge defects belong to the areas which can correct their records."""

    def __init__(self, corrections):
        self.corrections = corrections
        super().__init__("; ".join(f"{area}: {facts}" for area, facts in corrections.items()))


def endpoint(unit, contract):
    if unit == contract["provider"]:
        return "provider"
    return "consumer" if unit in contract["consumers"] else None


def contracts_for(document, unit):
    units = plan.select(document, "unit")
    return {r: c for r, c in plan.select(document, "contract").items()
            if endpoint(unit, c) or units[unit]["area"] == units[c["provider"]]["area"]}


def crossed_contracts(document, requirements):
    units = plan.select(document, "unit")
    return {r: c for r, c in plan.select(document, "contract").items()
            if any(set(requirements) & set(units[u]["requirements"]) for u in c["consumers"])}


def validate(document):
    areas, contracts, units = (plan.select(document, k) for k in ("area", "contract", "unit"))
    if not areas:
        raise ValueError("Name an area and its responsibility")
    for area in areas.values():
        for pattern in area["scope"]:
            plan.safe_path(pattern)
    for ref, decision in plan.select(document, "decision").items():
        if set(decision["areas"]) - areas.keys():
            raise ValueError(f"{ref}: decisions must name existing areas")
    for ref, contract in contracts.items():
        consumers = contract["consumers"] = list(dict.fromkeys(
            r for r in contract["consumers"] if r != contract["provider"]))
        if contract["provider"] not in units or set(consumers) - units.keys():
            raise ValueError(f"{ref}: name existing provider and consumer units")
        if any(units[u]["area"] == units[contract["provider"]]["area"] for u in consumers):
            raise ValueError(f"{ref}: consumer endpoints must cross area boundaries; "
                             "use the providing area's contract text without consuming it")
    for ref, unit in units.items():
        if unit["area"] not in areas or not all(
                any(plan.contains(s, p) for s in areas[unit["area"]]["scope"]) for p in unit["scope"]):
            raise ValueError(f"{ref}: keep unit scopes inside its named area scopes")


def stale_consumers(before, after):
    old, new = (plan.select(d, "contract") for d in (before, after))
    return {u for r in old.keys() | new.keys() if old.get(r) != new.get(r)
            for c in (old.get(r), new.get(r)) if c for u in c["consumers"]}


def index(items):
    result = {item["handle"]: item for item in items}
    if len(result) != len(items):
        raise ValueError("Give each area or contract a distinct handle")
    return result


def commitments(vision):
    return {f"{field}:{i}": value for field in ("scopeIn", "improvements", "quality")
            for i, value in enumerate(vision[field], 1)}


def outline(answer, vision=None):
    validate_schema({k: v for k, v in answer.items() if k != "why"}, ARCHITECT)
    areas, contracts = index(answer["areas"]), index(answer["contracts"])
    assigned = {r for a in areas.values() for r in a["commitments"]}
    if vision is not None and assigned != set(commitments(vision)):
        raise ValueError("Assign every accepted commitment to an area, using its supplied handle")
    if areas.keys() & contracts.keys():
        raise ValueError("Area and contract handles must be distinct")
    for ref, area in areas.items():
        for path in area["scope"]:
            plan.safe_path(path)
        for other, sibling in areas.items():
            if ref < other and plan.overlaps(area["scope"], sibling["scope"]):
                raise ValueError("Areas need disjoint write scopes")
    for ref, contract in contracts.items():
        readers = contract["consumers"] = list(dict.fromkeys(
            r for r in contract["consumers"] if r != contract["provider"]))
        if contract["provider"] not in areas or set(readers) - areas.keys():
            raise ValueError(f"{ref}: name existing provider and consumer areas")
    if any(set(d["areas"]) - areas.keys() for d in answer["decisions"]):
        raise ValueError("Decisions must name existing areas")
    return areas, contracts


def local_plan(area, contracts, answer, vision):
    """Check ownership before merging; canonical trace validation runs on the merged document."""
    validate_schema({k: v for k, v in answer.items() if k != "why"}, DETAIL_PLAN)
    handles = {e["targetHandle"]: e["kind"] for e in answer["edits"]}
    if len(handles) != len(answer["edits"]):
        raise ValueError("Each local record needs a distinct handle")
    relations = {"requirement": ("features", "feature")}
    for edit in answer["edits"]:
        if edit["kind"] in relations:
            field, kind = relations[edit["kind"]]
            if any(handles.get(r) != kind and not (kind == "requirement" and r in contracts
                    and endpoint(area["handle"], contracts[r]) != "provider")
                   for r in edit["typedBody"][field]):
                raise ValueError(f"Local {field} must name this area's {kind} handles")
    for unit in answer["units"]:
        for path in unit["scope"]:
            plan.safe_path(path)
        missing = [p for p in unit["scope"]
                   if not any(plan.contains(s, p) for s in area["scope"])]
        if missing:
            raise ContractGap(f"Area {area['handle']}: keep work inside its named area scopes; "
                              f"ARCHITECT must assign missing scope for {missing}", [area["handle"]])
        if any(handles.get(r) != "requirement" for r in unit["requirements"]):
            raise ValueError("Units must own local requirement handles")
        if any(dep > len(answer["units"]) for dep in unit["dependencies"]):
            raise ValueError("Dependencies must name local unit positions")
    nodes = {"vision": {"kind": "vision", "body": {
        k: v for k, v in vision.items() if k != "why"}}}
    nodes.update({e["targetHandle"]: {"kind": e["kind"], "body": {
        **e["typedBody"], **({"vision": ["vision"]} if e["kind"] == "feature" else {})}}
        for e in answer["edits"]})
    units = {f"unit:{i}": {**{k: v for k, v in u.items() if k != "provides"}, "area": area["handle"],
             "dependencies": [f"unit:{d}" for d in u["dependencies"]]}
             for i, u in enumerate(answer["units"], 1)}
    nodes.update({r: {"kind": "unit", "body": u} for r, u in units.items()})
    document = {"nodes": nodes}
    plan.validate_records(document)
    plan.validate_owned_work(document)


def subdivision(parent, answer, known_contracts):
    validate_schema({k: v for k, v in answer.items() if k != "why"}, DETAIL_SPLIT)
    children = index(answer["subareas"])
    if {r for c in children.values() for r in c["commitments"]} != set(parent["commitments"]):
        raise ValueError("Sub-areas must cover the parent's commitments exactly")
    if children.keys() & (known_contracts.keys() | {parent["handle"]}):
        raise ValueError("Sub-areas need new distinct handles")
    # A split must narrow every child's ownership, preserving every parent path scope.
    for child in children.values():
        if (child["scope"] == parent["scope"] or not all(
                any(plan.contains(s, p) for s in parent["scope"]) for p in child["scope"])):
            raise ValueError("Sub-area scopes must narrow the parent area")
    local = {"areas": answer["subareas"], "contracts": answer["contracts"],
             "decisions": [], "waves": [{"goal": "Detail the parent responsibility"}]}
    outline(local)
    if index(answer["contracts"]).keys() & known_contracts.keys():
        raise ValueError("Each new contract needs a distinct handle")
    return children


def expand(architecture, parent, answer):
    """Preserve parent agreements across a split; merge resolves the unique implementing unit."""
    result = deepcopy(architecture)
    children = subdivision(parent, answer, index(result["contracts"]))
    if children.keys() & index(result["areas"]).keys():
        raise ValueError("Sub-area handles must be distinct from existing areas")
    result["areas"] = [a for a in result["areas"] if a["handle"] != parent["handle"]]
    result["areas"].extend(children.values())
    # Parent endpoint membership is retained explicitly, never inferred from source or prose.
    for child in result["areas"]:
        if child["handle"] in children:
            child["ancestors"] = [*parent.get("ancestors", []), parent["handle"]]
    result["contracts"].extend(answer["contracts"])
    for decision in result["decisions"]:
        if parent["handle"] in decision["areas"]:
            decision["areas"] = [a for a in decision["areas"] if a != parent["handle"]] + list(children)
    return result
