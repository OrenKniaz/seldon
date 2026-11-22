"""C5/C9/C10/C12; R4/R6/R19: planning policy, ownership and canonical agreements."""

from copy import deepcopy

from . import answers, gitwork, plan, questions
from .answers import Mismatch, links, normalised
from .schema import ARCHITECT, DETAIL_SPLIT, FEATURE, GROWN_FEATURE, detail_plan, vision_body
from .schema import validate as validate_schema


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
            gitwork.safe_path(pattern)
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


def planning_approach(setup, vision, *, continuing):
    if not setup.get("sizeFirst"):
        return {}
    if continuing:
        return {"Steering policy": "Use the recorded outline. Settle the named gap or plan the unfinished "
                "horizon from the built result. Keep finished work and its owners. Add a writer only for "
                "a named responsibility on disjoint paths. Preserve accepted scope."}
    split = setup.get("split", "auto")
    guidance = ("Plan a split with runnable waves or independently writable areas and real agreements. "
                "Keep the accepted effort and all product commitments."
                if split == "force" else
                "When the accepted work fits one session, start with one area, one wave and one builder, "
                "with scoped product paths and no peer agreements. Add an owner only for a named "
                "independent responsibility. "
                "Use runnable checkpoints when the owner requests staging or the work spans sessions.")
    return {"Planning approach": {"size": vision["size"], "split": split, "starting shape":
            "Use the accepted single-agent effort. Follow the user's explicit staging. " + guidance}}


def merge(previous, answer, completed=(), *, record=None):
    """STEER may resend the horizon or name only the changed handles."""
    if "scope" in answer:
        return answer
    result = deepcopy(previous)
    for field in ("areas", "contracts"):
        items = answers.index(result[field], field)
        for item in answers.index(answer.get(field, []), field, record=record).values():
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
    old = answers.index(previous["contracts"]) if previous else {}
    old = {r: {**c, "consumers": sorted(c["consumers"])} for r, c in old.items()}
    changed = {a for c in outline["contracts"]
               if old.get(c["handle"]) != {**c, "consumers": sorted(c["consumers"])}
               for a in c["consumers"]} if previous else set()
    return [a for a in outline["areas"] if active.intersection(a["commitments"])
            or changed.intersection([a["handle"], *a.get("ancestors", [])])]


def outline(answer, vision=None, *, record=None):
    if "scope" in answer:
        return answer
    accepted = questions.commitments(vision) if vision is not None else {
        r: None for a in answer["areas"] for r in a["commitments"]}
    if not answer["waves"]:
        raise Mismatch("waves", "Seldon expected a wave horizon; got no waves. Send at least "
                       "one wave with its goal and commitments; when steering, an empty list keeps the horizon.")
    areas = answers.index(answer["areas"], "areas", record=record)
    contracts = answers.index(answer["contracts"], "contracts", record=record)
    answer["areas"], answer["contracts"] = list(areas.values()), list(contracts.values())
    for ref, area in areas.items():
        claimed = links(area["commitments"], accepted, record=record, field="areas.commitments", subject=ref)
        if len(areas) == 1 and set(claimed) <= accepted.keys():
            claimed = normalised(record, "areas.commitments", claimed,
                list(dict.fromkeys([*claimed, *accepted])), f"Assigned remaining commitments to the sole area {ref}.")
        area["commitments"] = claimed
    assigned = {r for a in areas.values() for r in a["commitments"]}
    if vision is not None and assigned != accepted.keys():
        raise Mismatch("areas.commitments", f"Seldon expected the accepted commitment handles "
            f"{list(accepted)}; got {sorted(assigned)}. Send every accepted handle assigned to an area.")
    for wave in answer["waves"]:
        wave["commitments"] = links(wave["commitments"], accepted, record=record,
                                    field="waves.commitments", subject=wave["goal"])
    assigned_waves = {r for w in answer["waves"] for r in w["commitments"]}
    extra = assigned_waves - accepted.keys()
    if extra and vision is not None and "stories" in vision_body(vision):
        raise Mismatch("waves.commitments", f"Unknown wave commitments {sorted(extra)}. "
                       f"Use accepted handles from {list(accepted)}.")
    last = answer["waves"][-1]
    last["commitments"] = normalised(record, "waves.commitments", last["commitments"],
        [*last["commitments"], *(r for r in accepted if r not in assigned_waves)],
        "Kept commitments omitted from the wave horizon in its final wave.")
    validate_schema({k: v for k, v in answer.items() if k != "why"}, ARCHITECT)
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
        area["scope"] = plan.product_scope(area["scope"], "areas.scope", ref, record)
    for ref, area in areas.items():
        for other, sibling in areas.items():
            if ref < other and plan.overlaps(area["scope"], sibling["scope"]):
                raise Mismatch("areas.scope", f"Seldon expected disjoint write scopes; got "
                    f"{ref} {area['scope']} overlapping {other} {sibling['scope']}. "
                    "Send scopes with one area owning each path.")
    external = []
    for ref, contract in contracts.items():
        provider = links([contract["provider"]], areas, record=record,
                         field="contracts.provider", subject=ref)[0]
        contract["provider"] = provider
        readers = links(contract["consumers"], areas, record=record, field="contracts.consumers", subject=ref)
        if set(readers) <= areas.keys() and provider.casefold() in (
                "environment", "runtime", "environment/runtime") and provider not in areas:
            if not readers:
                readers = list(areas)
            external.append(ref)
            decision = {"choice": contract["text"],
                        "reason": f"{provider} supplies {ref} ({contract['kind']}).",
                        "origin": "environment", "areas": readers}
            answer["decisions"] = [d for d in answer["decisions"] if d["reason"] != decision["reason"]]
            answer["decisions"].append(normalised(record, "contracts", contract, decision,
                f"Kept {ref} as a runtime assumption for {', '.join(readers)}."))
            continue
        contract["consumers"] = readers = normalised(record, "contracts.consumers", readers,
            [r for r in readers if r != provider], f"Removed self-consumption from {ref}.")
        if provider not in areas or set(readers) - areas.keys():
            raise Mismatch("contracts.provider/consumers", f"Seldon expected area handles "
                f"{list(areas)} for {ref}; got provider {provider} and consumers {readers}. "
                "Send the handles of the areas that provide and consume this contract.")
    for ref in external:
        del contracts[ref]
    answer["contracts"] = list(contracts.values())
    for decision in answer["decisions"]:
        decision["areas"] = links(decision["areas"], areas, record=record,
                                  field="decisions.areas", subject=decision["choice"])
    if any(set(d["areas"]) - areas.keys() for d in answer["decisions"]):
        raise Mismatch("decisions.areas", f"Seldon expected area handles {list(areas)}; got "
            f"{[d['areas'] for d in answer['decisions']]}. Send existing area handles for each decision.")
    return areas, contracts


def local_plan(area, answer, vision, *, record=None):
    """Check ownership before merging; canonical trace validation runs on the merged document."""
    vision = vision_body(vision)
    grown = "stories" in vision
    structure = {k: v for k, v in answer.items() if k not in ("why", "guideBrief")}
    if "units" in structure:
        structure["units"] = [{k: v for k, v in unit.items() if k != "brief"}
                              for unit in structure["units"]]
    validate_schema(structure, detail_plan(GROWN_FEATURE if grown else FEATURE, continuing=True))
    edits = answers.index(answer["edits"], "edits.targetHandle", key="targetHandle", record=record)
    answer["edits"] = list(edits.values())
    features = {r: e["typedBody"] for r, e in edits.items() if e["kind"] == "feature"}
    requirements = {r: e["typedBody"] for r, e in edits.items() if e["kind"] == "requirement"}
    commitments_to_features = {}
    for ref, feature in features.items():
        if grown:
            field = "edits.typedBody.commitments"
            refs = feature["commitments"] = links(feature["commitments"], area["commitments"],
                                                 record=record, field=field, subject=ref)
            if set(refs) - set(area["commitments"]):
                raise Mismatch(field, f"Feature {ref!r} cites {refs}. "
                    f"Use this area's accepted handles {area['commitments']}.")
            for handle in refs:
                commitments_to_features.setdefault(handle, []).append(ref)
    for ref, requirement in requirements.items():
        field = "edits.typedBody.features"
        refs = requirement["features"] = links(requirement["features"], features,
            via=commitments_to_features, record=record, field=field, subject=ref)
        if set(refs) - features.keys():
            raise Mismatch(field, f"Seldon expected this area's feature handles {list(features)}; got {refs}. "
                "Send the matching local handles from this answer's edits.")
    trace = plan.trace_requirements(features, requirements)
    units = answer["units"]
    if sum("scope" not in unit for unit in units) > 1:
        raise Mismatch("units.scope", "Give the continuing owner one assignment. Scope each new writer separately.")
    for i, unit in enumerate(units, 1):
        if "scope" in unit:
            subject = f"{area['handle']} unit:{i}"
            unit["scope"] = plan.product_scope(unit["scope"], "units.scope", subject, record)
            missing = [p for p in unit["scope"] if not any(plan.contains(s, p) for s in area["scope"])]
            if missing:
                raise answers.ContractGap(f"Area {area['handle']}: keep work inside its named area scopes; "
                                  f"ARCHITECT must assign missing scope for {missing}", [area["handle"]])
        unit["requirements"] = links(unit["requirements"], requirements, via=trace, record=record,
                                     field="units.requirements", subject=f"{area['handle']} unit:{i}")
        if set(unit["requirements"]) - requirements.keys():
            raise Mismatch("units.requirements", f"Seldon expected local requirement handles "
                f"{list(requirements)}; got {unit['requirements']}. "
                "Send requirement handles from this answer's edits.")
        if len(answer["units"]) == 1:
            unit["requirements"] = normalised(record, "units.requirements", unit["requirements"],
                list(dict.fromkeys([*unit["requirements"], *requirements])),
                f"Assigned omitted requirements to the sole unit in {area['handle']}.")
        elif not unit["requirements"]:
            raise Mismatch("units.requirements", f"{area['handle']} unit:{i} has no named requirements. "
                           "Name what it delivers, or remove the assignment if it has no work.")
        unit["dependencies"] = normalised(record, "units.dependencies", unit["dependencies"],
            list(dict.fromkeys(d for d in unit["dependencies"] if d != i or len(answer["units"]) > 1)),
            f"Removed redundant dependencies from {area['handle']} unit:{i}.")
        if any(dep > len(answer["units"]) for dep in unit["dependencies"]):
            raise Mismatch("units.dependencies", f"Seldon expected positions 1 through "
                f"{len(answer['units'])}; got {unit['dependencies']}. Send one-based positions "
                "of the units this unit depends on.")
    nodes = {"vision": {"kind": "vision", "body": vision}}
    nodes.update({e["targetHandle"]: {"kind": e["kind"], "body": plan.edit_body(e, grown)}
        for e in answer["edits"]})
    plan.validate_records({"nodes": nodes})
    # The compiled plan checks actual overlap after binding any implicit owner.
    units = {f"unit:{i}": {"scope": [], **{k: v for k, v in u.items() if k != "provides"},
             "area": area["handle"],
             "dependencies": [f"unit:{d}" for d in u["dependencies"]]}
             for i, u in enumerate(answer["units"], 1)}
    nodes.update({r: {"kind": "unit", "body": u} for r, u in units.items()})
    plan.validate_owned_work({"nodes": nodes})


def subdivision(parent, answer, known_contracts, *, record=None):
    validate_schema({k: v for k, v in answer.items() if k != "why"}, DETAIL_SPLIT)
    children = answers.index(answer["subareas"], "subareas", record=record)
    answer["subareas"] = list(children.values())
    if len(children) < 2:
        raise Mismatch("subareas", "A subdivision needs two distinct areas. Send their responsibilities and scopes, "
                       "or plan the parent without subdividing.")
    for ref, child in children.items():
        child["commitments"] = links(child["commitments"], parent["commitments"], record=record,
                                     field="subareas.commitments", subject=ref)
    if {r for c in children.values() for r in c["commitments"]} != set(parent["commitments"]):
        raise ValueError("Sub-areas must cover the parent's commitments exactly")
    if children.keys() & (known_contracts.keys() | {parent["handle"]}):
        raise ValueError("Sub-areas need new distinct handles")
    # A split must narrow every child's ownership, preserving every parent path scope.
    for child in children.values():
        child["scope"] = plan.product_scope(child["scope"], "subareas.scope", child["handle"], record)
        if (set(child["scope"]) == set(parent["scope"]) or not all(
                any(plan.contains(s, p) for s in parent["scope"]) for p in child["scope"])):
            raise ValueError("Sub-area scopes must narrow the parent area")
    local = {"areas": answer["subareas"], "contracts": answer["contracts"],
             "decisions": [], "waves": [{"goal": "Detail the parent responsibility",
                                         "commitments": parent["commitments"]}]}
    outline(local, record=record)
    if answers.index(local["contracts"]).keys() & known_contracts.keys():
        raise ValueError("Each new contract needs a distinct handle")
    return children, local


def expand(architecture, parent, answer, *, record=None):
    """Preserve parent agreements across a split; merge resolves the unique implementing unit."""
    result = deepcopy(architecture)
    children, local = subdivision(parent, answer, answers.index(result["contracts"]), record=record)
    if children.keys() & answers.index(result["areas"]).keys():
        raise ValueError("Sub-area handles must be distinct from existing areas")
    result["areas"] = [a for a in result["areas"] if a["handle"] != parent["handle"]]
    # Parent endpoint membership is retained explicitly, never inferred from source or prose.
    result["areas"].extend({**child, "ancestors": [*parent.get("ancestors", []), parent["handle"]]}
                           for child in children.values())
    result["contracts"].extend(local["contracts"])
    result["decisions"].extend(local["decisions"])
    for decision in result["decisions"]:
        if parent["handle"] in decision["areas"]:
            decision["areas"] = [a for a in decision["areas"] if a != parent["handle"]] + list(children)
    return result
