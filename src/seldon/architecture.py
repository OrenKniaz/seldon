"""C9/C10/C12; R4/R6: canonical endpoints, ownership and bounded agreement projections."""

from copy import deepcopy

from . import plan
from .answers import Mismatch
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
    units, contracts = (plan.select(document, k) for k in ("unit", "contract"))
    if unit == "guide":
        return {r: c for r, c in contracts.items() if unit in c["consumers"]}
    providers = {unit} | {c["provider"] for c in contracts.values() if unit in c["consumers"]}
    return {r: c for r, c in contracts.items()
            if c["provider"] in providers or units[unit]["area"] == units[c["provider"]]["area"]}


def crossed_contracts(document, requirements):
    units = plan.select(document, "unit")
    return {r: c for r, c in plan.select(document, "contract").items()
            if any(set(requirements) & set(units[u]["requirements"])
                   for u in [c["provider"], *c["consumers"]])}


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


def stale_units(before, after):
    """A changed contract is judged again at both sides: its provider and its consumers (C10)."""
    old, new = (plan.select(d, "contract") for d in (before, after))
    return {u for r in old.keys() | new.keys() if old.get(r) != new.get(r)
            for c in (old.get(r), new.get(r)) if c for u in [c["provider"], *c["consumers"]]}


def index(items, field="areas/contracts"):
    result = {item["handle"]: item for item in items}
    if any(item != result[item["handle"]] for item in items):
        raise Mismatch(field, f"Seldon expected one entry per handle in {field}; got "
                       f"conflicting entries among {len(items)} entries for {len(result)} handles. "
                       "Send one agreed entry per handle.")
    return result


def commitments(vision):
    return {f"{field}:{i}": value for field in ("scopeIn", "improvements", "quality")
            for i, value in enumerate(vision[field], 1)}


def merge(previous, answer, completed=()):
    """STEER may resend the horizon or name only the changed handles."""
    if "scope" in answer:
        return answer
    result = deepcopy(previous)
    for field in ("areas", "contracts"):
        items = index(result[field], field)
        index(answer.get(field, []), field)
        for item in answer.get(field, []):
            items[item["handle"]] = {**items.get(item["handle"], {}), **deepcopy(item)}
        result[field] = list(items.values())
    for decision in answer.get("decisions", []):
        if decision not in result["decisions"]:
            result["decisions"].append(deepcopy(decision))
    if answer.get("waves"):
        waves = deepcopy(answer["waves"])
        prefix = list(completed)
        if waves[:len(prefix)] == prefix:
            waves = waves[len(prefix):]
        result["waves"] = deepcopy(prefix) + waves
    return result


def detail_areas(outline, wave, previous=None):
    active = set(outline["waves"][wave - 1]["commitments"])
    old = index(previous["contracts"]) if previous else {}
    old = {r: {**c, "consumers": sorted(c["consumers"])} for r, c in old.items()}
    changed = {a for c in outline["contracts"]
               if old.get(c["handle"]) != {**c, "consumers": sorted(c["consumers"])}
               for a in c["consumers"]} if previous else set()
    return [a for a in outline["areas"] if active.intersection(a["commitments"])
            or changed.intersection([a["handle"], *a.get("ancestors", [])])]


def outline(answer, vision=None):
    if "scope" in answer:
        return answer
    assigned_waves = {r for w in answer["waves"] for r in w["commitments"]}
    accepted = commitments(vision) if vision is not None else {
        r: None for a in answer["areas"] for r in a["commitments"]}
    if not answer["waves"]:
        raise Mismatch("waves", "Seldon expected a wave horizon; got no waves. Send at least "
                       "one wave with its goal and commitments; when steering, an empty list keeps the horizon.")
    answer["waves"][-1]["commitments"].extend(r for r in accepted if r not in assigned_waves)
    validate_schema({k: v for k, v in answer.items() if k != "why"}, ARCHITECT)
    areas, contracts = index(answer["areas"], "areas"), index(answer["contracts"], "contracts")
    answer["areas"], answer["contracts"] = list(areas.values()), list(contracts.values())
    assigned = {r for a in areas.values() for r in a["commitments"]}
    if vision is not None and assigned != set(commitments(vision)):
        raise Mismatch("areas.commitments", f"Seldon expected the accepted commitment handles "
            f"{list(accepted)}; got {sorted(assigned)}. Send every accepted handle assigned to an area.")
    if vision is not None and not detail_areas(answer, 1):
        first, *later = answer["waves"]
        later_work = ", ".join(f"wave {i} has {w['commitments']}" for i, w in enumerate(later, 2))
        owners = {ref: area["commitments"] for ref, area in areas.items()}
        raise Mismatch("waves.commitments", f"For wave 1 ({first['goal']}), Seldon expected "
            "commitment handles owned by an area so it can plan the first working slice. "
            f"It got {first['commitments']} with no match among these areas: {owners}. "
            + (f"Later assignments: {later_work}. " if later else "")
            + "Send wave 1 with the area-owned commitments it starts, and keep them in later waves "
            "when those waves still have work on them.")
    if areas.keys() & contracts.keys():
        raise ValueError("Area and contract handles must be distinct")
    for ref, area in areas.items():
        for path in area["scope"]:
            plan.safe_path(path)
        for other, sibling in areas.items():
            if ref < other and plan.overlaps(area["scope"], sibling["scope"]):
                raise Mismatch("areas.scope", f"Seldon expected disjoint write scopes; got "
                    f"{ref} {area['scope']} overlapping {other} {sibling['scope']}. "
                    "Send scopes with one area owning each path.")
    for ref, contract in contracts.items():
        readers = contract["consumers"] = list(dict.fromkeys(
            r for r in contract["consumers"] if r != contract["provider"]))
        if contract["provider"] not in areas or set(readers) - areas.keys():
            raise Mismatch("contracts.provider/consumers", f"Seldon expected area handles "
                f"{list(areas)} for {ref}; got provider {contract['provider']} and consumers {readers}. "
                "Send the handles of the areas that provide and consume this contract.")
    if any(set(d["areas"]) - areas.keys() for d in answer["decisions"]):
        raise Mismatch("decisions.areas", f"Seldon expected area handles {list(areas)}; got "
            f"{[d['areas'] for d in answer['decisions']]}. Send existing area handles for each decision.")
    return areas, contracts


def local_plan(area, contracts, answer, vision):
    """Check ownership before merging; canonical trace validation runs on the merged document."""
    validate_schema({k: v for k, v in answer.items() if k != "why"}, DETAIL_PLAN)
    handles = {e["targetHandle"]: e["kind"] for e in answer["edits"]}
    if len(handles) != len(answer["edits"]):
        raise Mismatch("edits.targetHandle", f"Seldon expected distinct local record handles; got "
            f"{[e['targetHandle'] for e in answer['edits']]}. Send each local handle once.")
    relations = {"requirement": ("features", "feature")}
    for edit in answer["edits"]:
        if edit["kind"] in relations:
            field, kind = relations[edit["kind"]]
            if any(handles.get(r) != kind and not (kind == "requirement" and r in contracts
                    and endpoint(area["handle"], contracts[r]) != "provider")
                   for r in edit["typedBody"][field]):
                raise Mismatch(f"edits.typedBody.{field}", f"Seldon expected this area's {kind} "
                    f"handles {[h for h, k in handles.items() if k == kind]}; got {edit['typedBody'][field]}. "
                    "Send the matching local handles from this answer's edits.")
    for unit in answer["units"]:
        for path in unit["scope"]:
            plan.safe_path(path)
        missing = [p for p in unit["scope"]
                   if not any(plan.contains(s, p) for s in area["scope"])]
        if missing:
            raise ContractGap(f"Area {area['handle']}: keep work inside its named area scopes; "
                              f"ARCHITECT must assign missing scope for {missing}", [area["handle"]])
        if any(handles.get(r) != "requirement" for r in unit["requirements"]):
            raise Mismatch("units.requirements", f"Seldon expected local requirement handles "
                f"{[h for h, k in handles.items() if k == 'requirement']}; got {unit['requirements']}. "
                "Send requirement handles from this answer's edits.")
        if any(dep > len(answer["units"]) for dep in unit["dependencies"]):
            raise Mismatch("units.dependencies", f"Seldon expected positions 1 through "
                f"{len(answer['units'])}; got {unit['dependencies']}. Send one-based positions "
                "of the units this unit depends on.")
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
             "decisions": [], "waves": [{"goal": "Detail the parent responsibility",
                                         "commitments": parent["commitments"]}]}
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
