"""C9/C10/C12; R4/R6: canonical endpoints, ownership and bounded agreement projections."""

from . import plan


def endpoint(unit, contract):
    if unit == contract["provider"]:
        return "provider"
    return "consumer" if unit in contract["consumers"] else None


def contracts_for(document, unit):
    return {r: c for r, c in plan.select(document, "contract").items() if endpoint(unit, c)}


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
            if plan.prefix(path) != path:
                raise ValueError(f"{ref}: reads must name a concrete file: {path}")
            owners = [r for r, u in units.items() if plan.matches(path, u["scope"])]
            if len(owners) != 1:
                raise ValueError(f"{ref}: {path} needs one planned file owner")
            owner, = owners
            if owner != ref and owner not in plan.prerequisites(document, ref):
                raise ValueError(f"{ref}: reading {path} needs a contract with {owner} or a dependency edge")


def stale_consumers(before, after):
    old, new = (plan.select(d, "contract") for d in (before, after))
    return {u for r in old.keys() | new.keys() if old.get(r) != new.get(r)
            for c in (old.get(r), new.get(r)) if c for u in c["consumers"]}
