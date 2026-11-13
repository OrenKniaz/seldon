"""C9/C10/C12; R4/R6: canonical endpoints, ownership and bounded agreement projections."""

from copy import deepcopy
from graphlib import TopologicalSorter

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


def area_decisions(document, area):
    return {r: d for r, d in plan.select(document, "decision").items() if area in d["areas"]}


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
        consumers = contract["consumers"]
        if (contract["provider"] not in units or set(consumers) - units.keys()
                or contract["provider"] in consumers or len(set(consumers)) != len(consumers)):
            raise ValueError(f"{ref}: name one existing provider and distinct existing consumers")
        if any(units[u]["area"] == units[contract["provider"]]["area"] for u in consumers):
            raise ValueError(f"{ref}: consumer endpoints must cross area boundaries; "
                             "use the providing area's contract text without consuming it")
        providers = [r for r, u in units.items() if ref in u["provides"]]
        readers = {r for r, u in units.items() if ref in u["consumes"]}
        if providers != [contract["provider"]] or readers != set(consumers):
            raise ValueError(f"{ref}: bind exactly its one provider and every named consumer")
    for ref, unit in units.items():
        if unit["area"] not in areas or not all(
                any(plan.contains(s, p) for s in areas[unit["area"]]["scope"]) for p in unit["scope"]):
            raise ValueError(f"{ref}: keep unit scopes inside its named area scopes")
        for field in ("provides", "consumes"):
            if len(set(unit[field])) != len(unit[field]) or set(unit[field]) - contracts.keys():
                raise ValueError(f"{ref}: name each existing contract once in {field}")
        for path in unit["reads"]:
            plan.safe_path(path)
            if "*" in path or "?" in path:
                raise ValueError(f"{ref}: reads must name a concrete file: {path}")
            owners = [r for r, u in units.items() if path in u["scope"] or plan.matches(path, u["scope"])]
            if len(owners) != 1:
                raise ValueError(f"{ref}: {path} needs one planned file owner")
            owner, = owners
            if owner != ref and owner not in plan.prerequisites(document, ref):
                raise ValueError(f"{ref}: reading {path} needs an explicit dependency edge to {owner}")


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
        readers = contract["consumers"]
        if (contract["provider"] not in areas or set(readers) - areas.keys()
                or contract["provider"] in readers or len(set(readers)) != len(readers)):
            raise ValueError(f"{ref}: name one existing provider area and distinct existing consumer areas")
    if any(set(d["areas"]) - areas.keys() for d in answer["decisions"]):
        raise ValueError("Decisions must name existing areas")
    return areas, contracts


def local_plan(area, contracts, answer, vision, single_area):
    """Check ownership before merging; canonical trace validation runs on the merged document."""
    validate_schema({k: v for k, v in answer.items() if k != "why"}, DETAIL_PLAN)
    handles = {e["targetHandle"]: e["kind"] for e in answer["edits"]}
    if len(handles) != len(answer["edits"]):
        raise ValueError("Each local record needs a distinct handle")
    relations = {"requirement": ("features", "feature"), "check": ("requirements", "requirement")}
    for edit in answer["edits"]:
        if edit["kind"] in relations:
            field, kind = relations[edit["kind"]]
            if any(handles.get(r) != kind and not (
                    kind == "requirement" and r in contracts
                    and endpoint(area["handle"], contracts[r]))
                   for r in edit["typedBody"][field]):
                raise ValueError(f"Local {field} must name this area's {kind} handles")
        if edit["kind"] == "obligation":
            item = edit["typedBody"]
            if (item["owner"] > len(answer["units"])
                    or item["closingRequirement"] not in answer["units"][item["owner"]-1]["requirements"]):
                raise ValueError("A skeleton obligation needs an owner and its closing requirement")
    for unit in answer["units"]:
        for path in [*unit["scope"], *unit["checkPaths"]]:
            plan.safe_path(path)
        missing = [p for p in [*unit["scope"], *unit["checkPaths"]]
                   if not any(plan.contains(s, p) for s in area["scope"])]
        if missing:
            raise ContractGap(f"Area {area['handle']}: keep work inside its named area scopes; "
                              f"ARCHITECT must assign missing scope for {missing}", [area["handle"]])
        if any(handles.get(r) != "requirement" for r in unit["requirements"]):
            raise ValueError("Units must own local requirement handles")
        for dep in unit["dependencies"]:
            if isinstance(dep, int):
                if dep > len(answer["units"]):
                    raise ValueError("Dependencies must name local unit positions")
            else:
                plan.safe_path(dep)
                if "*" in dep or "?" in dep:
                    raise ValueError("A file prerequisite must name a concrete file")
        for field, side in (("provides", "provider"), ("consumes", "consumer")):
            if len(set(unit[field])) != len(unit[field]):
                raise ValueError(f"Name each existing contract once in {field}")
            for ref in unit[field]:
                if (field == "consumes" and ref in contracts
                        and endpoint(area["handle"], contracts[ref]) == "provider"):
                    raise ValueError(f"{ref}: use this area's contract text without a consumer endpoint; "
                                     "remove it from consumes and retain actual file dependencies")
                if ref not in contracts or endpoint(area["handle"], contracts[ref]) != side:
                    raise ValueError(f"{ref}: bind one provider and its agreed consumer endpoints; "
                                     "report a contract gap")
    for quality in answer["quality"]:
        if quality["criterion"] > len(vision["quality"]):
            raise ValueError("Quality coverage must name an accepted criterion position")
        if any(handles.get(r) != "check" for r in quality["checks"]):
            raise ValueError("Quality coverage must name local checks")
    # Preserve external contract handles: assigned() correctly leaves these checks at wave level.
    nodes = {"vision": {"kind": "vision", "body": {
        k: v for k, v in vision.items() if k != "why"}}}
    nodes.update({e["targetHandle"]: {"kind": e["kind"], "body": {
        **e["typedBody"], **({"vision": ["vision"]} if e["kind"] == "feature" else {}),
        **({"owner": f"unit:{e['typedBody']['owner']}"} if e["kind"] == "obligation" else {})}}
        for e in answer["edits"]})
    units = {f"unit:{i}": {**u, "scope": plan.owned_scope(u), "area": area["handle"],
             "dependencies": [f"unit:{d}" for d in u["dependencies"] if isinstance(d, int)]}
             for i, u in enumerate(answer["units"], 1)}
    nodes.update({r: {"kind": "unit", "body": u} for r, u in units.items()})
    document = {"nodes": nodes}
    plan.validate_records(document, contracts)
    plan.validate_owned_work(document)
    if single_area:
        from . import obligations
        document["skeleton"] = list(units) if len(units) > 1 else []
        obligations.validate(document)
    for (ref, unit), original in zip(units.items(), answer["units"], strict=True):
        files = [d for d in original["dependencies"] if isinstance(d, str)]
        for path in [*unit["reads"], *files]:
            plan.safe_path(path)
            if "*" in path or "?" in path:
                raise ValueError(f"{path}: reads and file prerequisites must name a concrete file")
            if plan.matches(path, area["scope"]):
                writers = [r for r, u in units.items() if plan.matches(path, u["scope"])]
                if len(writers) != 1:
                    raise ValueError(f"{path}: needs one planned file owner in {area['handle']}")
                owner, = writers
                if path in files:
                    if owner == ref:
                        raise ValueError(f"{path}: a file prerequisite needs one other planned writer")
                    unit["dependencies"].append(owner)
                elif owner != ref and owner not in unit["dependencies"]:
                    raise ValueError(f"{ref}: reading {path} needs a dependency edge to {owner}")
    tuple(TopologicalSorter({r: u["dependencies"] for r, u in units.items()}).static_order())


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
