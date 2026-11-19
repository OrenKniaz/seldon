"""C9/C12/I6/I7; R6/R14: scoped unit ownership and check declarations."""

import fnmatch
from graphlib import CycleError, TopologicalSorter
from pathlib import PurePosixPath

from .answers import Mismatch
from .schema import BODIES, GROWN_FEATURE, vision_body, vision_contract
from .schema import validate as validate_schema

GUIDE_PATH = "docs/try-it.md"
GUIDE_SCOPE = (GUIDE_PATH, "docs/try-it/**")
GUIDE_WALKTHROUGH = ("In docs/try-it.md, show how to try every accepted user surface, with one runnable "
                     "example per CLI command when there is a CLI. Supply inputs or show how to create them. "
                     "Declare a check under docs/try-it/ that runs those documented steps from a clean "
                     "checkout, checks the promised results and stops anything it starts.")
GUIDE_WORK = (GUIDE_WALKTHROUGH + " Use the contracts to walk through this wave's commitments. "
              "Leave unseen forms to the page owner's check and capture relevant visual evidence.")


def safe_path(value):
    path = PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or path.is_absolute()
            or path.as_posix() != value
            or any(p in ("..", ".git", ".seldon") or p.endswith((".", " ")) for p in path.parts)):
        raise ValueError(f"Use a project-relative path or pattern: {value!r}")
    return value


def matches(path, patterns):
    # ** spans directories; a directory's /** includes its complete subtree.
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def prefix(pattern):
    index = next((i for i, char in enumerate(pattern) if char in "*?["), len(pattern))
    return pattern[:index]


def contains(outer, inner):
    return outer == "**" or outer == inner or (outer.endswith("/**") and inner.startswith(outer[:-2]))


def overlaps(left, right):
    # Conservative for general glob intersections: never admit potentially shared paths.
    def intersect(a, b):
        if prefix(a) == a:
            return matches(a, [b])
        if prefix(b) == b:
            return matches(b, [a])
        return prefix(a).startswith(prefix(b)) or prefix(b).startswith(prefix(a))
    return any(intersect(a, b) for a in left for b in right)


def product_scope(scope, field, subject, record=None):
    """Reserve the guide before checking or binding planner-proposed ownership."""
    guide_dir = GUIDE_SCOPE[1].removesuffix("**")
    remaining = [p for p in scope if p not in (guide_dir, guide_dir.rstrip("/"))
                 and not overlaps([safe_path(p)], GUIDE_SCOPE)]
    if not remaining:
        raise Mismatch(field, f"{subject}: expected a product write scope; got {scope}, "
                       f"which overlaps {GUIDE_PATH} or docs/try-it/ reserved for the walkthrough owner. "
                       "Send at least one project-relative product path or narrower pattern outside the guide.")
    if record and remaining != scope:
        record("scope_normalisation", {"field": field, "subject": subject,
            "removed": [p for p in scope if p not in remaining], "remaining": remaining})
    return remaining


def select(document, kind):
    return {ref: n["body"] for ref, n in document["nodes"].items() if n["kind"] == kind}


def guide_owner(document):
    """The approved write scope owns the guide for the lifetime of the plan."""
    return next((ref for ref, unit in select(document, "unit").items()
                 if GUIDE_PATH in unit["scope"]), None)


def sole_owner(document):
    owner = guide_owner(document)
    if owner and owner != "guide" and len(select(document, "unit")) == 1:
        return owner


def prompt_context(document, requirements=()):
    """Accepted audience and the features traced from this assignment's requirements."""
    from . import architecture

    vision = document["nodes"].get("vision", {}).get("body", {})
    nodes = document["nodes"]
    traced = dict.fromkeys(f for r in requirements for f in nodes[r]["body"]["features"])
    stories = select(document, "feature")
    goals = architecture.commitments(vision) if "stories" in vision else {}
    relevant = {h for f in traced for h in stories[f].get("commitments", [])}
    attached = {h for goal in goals.values() for h in goal.get("constraints", [])}
    constraints = {h for key in relevant for h in goals.get(key, {}).get("constraints", [])}
    return {"audience": vision.get("users", []),
            "stories": {f: {k: stories[f][k] for k in ("story", "acceptance")} for f in traced},
            "constraints": [c for c in vision.get("constraints", [])
                            if c["handle"] in constraints or c["handle"] not in attached]}


def guide_checks(document):
    """Find the declared walkthrough without borrowing the folded owner's product checks."""
    owner = guide_owner(document)
    return {ref: check for ref, check in select(document, "check").items()
            if check["owner"] == owner and (owner == "guide"
                or any(matches(arg, GUIDE_SCOPE[1:]) for arg in check["command"]))}


def saved_checks(declaration, ref, requirements):
    checks = declaration["checks"]
    if isinstance(checks, dict):
        return checks.copy(), declaration["nextCheck"]
    # Older records have only the last list and positional observation IDs.
    return {f"{ref}:check:{i}": {**check, "requirements": requirements}
            for i, check in enumerate(checks, 1)}, len(checks) + 1


def work_checks(store, ref, report, answer):
    document = store.plan(store.cursor().revision)
    requirements = select(document, "unit")[ref]["requirements"]
    prior = next((b for _, _, b in reversed(store.records("worker_checks")) if b["unit"] == ref),
                 {"checks": [], "checkPaths": []})
    checks, next_check = saved_checks(prior, ref, requirements)
    known = set(checks)
    for edit in answer["checks"]:
        handle = edit.get("retire") or edit.get("update")
        if handle and handle not in known:
            raise ValueError(f"Check {handle!r} is not owned by this unit. Use an Existing checks handle "
                             "to update or retire it, or omit the handle to add a check.")
        if "retire" in edit:
            checks.pop(handle, None)
            continue
        links = list(dict.fromkeys(edit["requirements"] if "requirements" in edit else acceptance(document, ref)))
        if unknown := set(links) - set(requirements):
            raise ValueError(f"Check {edit['purpose']!r} names unassigned requirements {sorted(unknown)}. "
                             "Use the handles under Requirements for the behaviour it proves.")
        check = {k: v for k, v in edit.items() if k != "update"}
        check["requirements"] = links
        if handle:
            checks[handle] = check
        elif not any(c["command"] == check["command"] for c in checks.values()):
            checks[f"{ref}:check:{next_check}"] = check
            next_check += 1
    store.record("worker_checks", {"unit": ref, "report": report, "checks": checks, "nextCheck": next_check,
        "checkPaths": list(dict.fromkeys([*prior["checkPaths"], *answer["checkPaths"]])),
        "discoveries": answer["discoveries"]})


def execution(store):
    """Worker declarations are canonical records; execution projects them onto the approved intent."""
    from copy import deepcopy
    document = deepcopy(store.plan(store.cursor().revision))
    latest = {b["unit"]: b for _, _, b in store.records("worker_checks")}
    for ref, unit in select(document, "unit").items():
        row = store.unit(ref)
        declaration = ({} if row and row.state == "DROPPED" else latest).get(
            ref, {"checkPaths": [], "checks": []})
        unit["handed"] = {d["owner"]: d["correction"] for d in declaration.get("discoveries", [])
                          if d["owner"] != ref}
        unit["checkPaths"] = [p for p in declaration["checkPaths"] if matches(p, unit["scope"])]
        checks, _ = saved_checks(declaration, ref, unit["requirements"])
        for handle, check in checks.items():
            links = [r for r in check["requirements"] if r in unit["requirements"]
                     and document["nodes"].get(r, {}).get("kind") == "requirement"]
            if links or not check["requirements"]:
                document["nodes"][handle] = {"kind": "check", "body": {
                    **check, "owner": ref, "requirements": links}}
    return document


def acceptance(document, ref):
    requirements = select(document, "unit")[ref]["requirements"]
    last = max((int(r.split(".")[0]) for r in requirements), default=0)
    return [r for r in requirements if r.startswith(f"{last}.")]


def left_out(store):
    document = execution(store)
    return {ref: {"requirements": {r: document["nodes"][r]["body"]["acceptance"]
                                  for r in unit["requirements"]},
                  "commitments": next(a["commitments"] for a in document["architecture"]["areas"]
                                      if a["handle"] == unit["area"]),
                  "reason": "left out by the user"}
            for ref, unit in select(document, "unit").items()
            if store.unit(ref) and store.unit(ref).state == "DROPPED"}


def assigned(document, *, local=False):
    result = {r: {} for r in select(document, "unit")}
    for ref, check in select(document, "check").items():
        if not local or not (check["owner"] == "guide"
                            or select(document, "unit")[check["owner"]]["handed"]
                            or wave_requirements(document, check["owner"])):
            result[check["owner"]][ref] = check
    return result


def wave_requirements(document, ref):
    """Proof consuming a peer outside the dependency base belongs to the integrated wave."""
    units = select(document, "unit")
    available = {ref}
    while expanded := {d for u in available for d in units[u]["dependencies"]} - available:
        available.update(expanded)
    peers = {c["provider"] for c in select(document, "contract").values() if ref in c["consumers"]}
    return units[ref]["requirements"] if peers - available else []


def traced_checks(document, subject):
    checks = select(document, "check")
    body = document["nodes"][subject]["body"]
    return ({r: c for r, c in checks.items()
             if select(document, "unit")[c["owner"]]["area"] == body["area"]}
            if document["nodes"][subject]["kind"] == "criterion" else
            {r: c for r, c in checks.items() if subject in c["requirements"]})


def traced_units(document, checks):
    requirements = {r for c in checks.values() for r in c["requirements"]}
    writers = {c.get("owner") for c in checks.values()}
    return {r: u for r, u in select(document, "unit").items()
            if r in writers or requirements.intersection(u["requirements"])}


def coverage(document):
    return {r: {"checks": list(checks), "units": list(traced_units(document, checks))}
            for r in {**select(document, "requirement"), **select(document, "criterion")}
            for checks in [traced_checks(document, r)]}


def edit_body(edit, grown):
    body = edit["typedBody"]
    if edit["kind"] == "feature" and not grown:
        return {**body, "vision": ["vision"]}
    return body


def merge(vision, architecture, leaves, previous=None, wave=1, *, fold_guide=False,
          retain_trace=False, record=None):
    """Accumulate one wave; a scope keeps its owner and its earlier requirements."""
    from copy import deepcopy
    from . import architecture as owner

    document = deepcopy(previous) if previous else {"nodes": {}}
    nodes = document["nodes"]
    prior_features = linked_features(document)
    vision = vision_body(vision)
    grown = "stories" in vision
    nodes["vision"] = {"kind": "vision", "body": vision}
    goals = owner.commitments(vision)
    units = select(document, "unit")
    guide = guide_owner(document)
    folded = guide if guide != "guide" else None
    endpoints = {r: {"provides": [c["provider"]], "consumes": [u for u in c["consumers"] if u != "guide"]}
                 for r, c in select(document, "contract").items()}
    feature_handles = {}
    for area, answer in leaves:
        local_contracts = {r: {**c,
            "provider": area["handle"] if c["provider"] in area.get("ancestors", []) else c["provider"],
            "consumers": [area["handle"] if a in area.get("ancestors", []) else a for a in c["consumers"]]}
            for r, c in owner.index(architecture["contracts"]).items()}
        owner.local_plan(area, local_contracts, answer, vision)
        # Requirements use the final feature refs, even if the reply sent them first.
        handles = {}
        edits = list(enumerate(answer["edits"], 1))
        for i, edit in sorted(edits, key=lambda pair: pair[1]["kind"] != "feature"):
            kind, handle = edit["kind"], edit["targetHandle"]
            body = deepcopy(edit_body(edit, grown))
            if kind == "requirement":
                body["features"] = [handles[r] for r in body["features"]]
            prefix = f"{wave}.{area['handle']}.{kind}"
            ref = f"{prefix}{i}"
            node = {"kind": kind, "body": body}
            if kind == "feature":
                prior = feature_handles.setdefault(handle, [])
                ref = next((r for seen, r in prior if seen == body), ref)
            if retain_trace:
                # Earlier checks and results still cite these IDs after an in-place replan.
                while ref in nodes and nodes[ref] != node:
                    i += 1
                    ref = f"{prefix}{i}"
            if kind == "feature":
                prior.append((body, ref))
            handles[handle] = ref
            nodes[ref] = node
        local_units = {}
        for i, unit in enumerate(answer["units"], 1):
            ref = next((r for r, u in units.items()
                        if set(u["scope"]) - (set(GUIDE_SCOPE) if r == folded else set())
                        == set(unit["scope"])), None)
            if ref is None:
                number = 1
                while (ref := f"{wave}.{area['handle']}.{number}") in units or ref in local_units.values():
                    number += 1
            local_units[i] = ref
        for i, unit in enumerate(answer["units"], 1):
            ref = local_units[i]
            before = units.get(ref, {"requirements": [], "dependencies": []})
            body = {k: deepcopy(v) for k, v in unit.items() if k != "provides"}
            body.update(area=area["handle"],
                        requirements=list(dict.fromkeys([*before["requirements"],
                                                         *(handles[r] for r in unit["requirements"])])),
                        dependencies=list(dict.fromkeys([*before["dependencies"],
                                                         *(local_units[d] for d in unit["dependencies"])])))
            if ref == folded:
                body["scope"].extend(GUIDE_SCOPE)
            nodes[ref] = {"kind": "unit", "body": body}
            units[ref] = body
            for handle, contract in local_contracts.items():
                bound = endpoints.setdefault(handle, {"provides": [], "consumes": []})
                if area["handle"] in contract["consumers"] and ref not in bound["consumes"]:
                    bound["consumes"].append(ref)
                if (contract["provider"] == area["handle"]
                        and (len(answer["units"]) == 1 or handle in unit.get("provides", []))
                        and ref not in bound["provides"]):
                    bound["provides"].append(ref)
    if guide is None and fold_guide and len(units) == len(architecture["areas"]) == len(architecture["waves"]) == 1:
        guide, = units
        units[guide]["scope"].extend(GUIDE_SCOPE)
    for area in architecture["areas"]:
        ref = area["handle"]
        nodes[ref] = {"kind": "area", "body": {
            "responsibility": area["responsibility"], "scope": list(area["scope"])}}
        if guide and units[guide]["area"] == ref:
            nodes[ref]["body"]["scope"].extend(GUIDE_SCOPE)
        for commitment, criterion in goals.items():
            if commitment.startswith("quality:") and commitment in area["commitments"]:
                handle = commitment if grown else f"quality:{int(commitment.split(':')[1]):03}"
                nodes[f"{handle}:{ref}"] = {
                    "kind": "criterion", "body": {**criterion, "area": ref}}
    corrections = {}
    for contract in architecture["contracts"]:
        bound = endpoints.get(contract["handle"], {"provides": [], "consumes": []})
        if not bound["provides"] and not any(
                contract["provider"] in {a["handle"], *a.get("ancestors", [])} for a, _ in leaves):
            continue  # A later agreement has no implementing unit yet.
        if len(bound["provides"]) != 1:
            for area, _ in leaves:
                if contract["provider"] in {area["handle"], *area.get("ancestors", [])}:
                    corrections.setdefault(area["handle"], []).append({
                        "error": f"{contract['handle']}: bind exactly one provider",
                        "otherSide": {"contract": contract["text"]}})
            continue
        nodes[contract["handle"]] = {"kind": "contract", "body": {
            "kind": contract["kind"], "text": contract["text"],
            "provider": bound["provides"][0], "consumers": bound["consumes"]}}
    for i, decision in enumerate(architecture["decisions"]):
        nodes[f"architecture-decision:{i}"] = {"kind": "decision", "body": deepcopy(decision)}
    for ref, unit in units.items():
        for other, writer in units.items():
            if ref < other and overlaps(unit["scope"], writer["scope"]):
                for area in {unit["area"], writer["area"]}:
                    corrections.setdefault(area, []).append({
                        "error": "Scope ownership overlaps; assign disjoint scopes",
                        "otherSide": {unit["area"]: unit["scope"], writer["area"]: writer["scope"]}})
    if corrections:
        raise owner.DetailCorrection(corrections)
    superseded = sorted(prior_features - linked_features(document))
    for ref in superseded:
        del nodes[ref]
    document["architecture"] = deepcopy(architecture)
    validate(document)
    if superseded and record:
        record("trace_normalisation", {"removed": superseded, "reason": "Replaced draft requirement links"})
    return add_guide(document, wave) if guide in (None, "guide") else document


def validate_records(document):
    from . import architecture

    nodes = document["nodes"]
    vision = nodes["vision"]["body"]
    validate_schema(vision, vision_contract(vision))
    accepted = architecture.commitments(vision) if "stories" in vision else {}
    relation_types = {"feature": ("vision", "vision"), "requirement": ("features", "feature"),
                      "unit": ("requirements", "requirement")}
    for ref, node in nodes.items():
        if ref == "vision":
            continue
        kind, body = node["kind"], node["body"]
        if kind == "criterion":
            continue
        if kind == "feature" and "commitments" in body:
            validate_schema(body, GROWN_FEATURE, ref)
            if unknown := set(body["commitments"]) - accepted.keys():
                raise Mismatch("edits.typedBody.commitments", f"Feature {ref!r} cites unknown accepted "
                    f"commitments {sorted(unknown)}. Use handles from {list(accepted)}.")
            continue
        validate_schema(body, BODIES[kind], ref)
        if kind in relation_types:
            relation, target_kind = relation_types[kind]
            if wrong := [r for r in body[relation] if nodes.get(r, {}).get("kind") != target_kind]:
                raise Mismatch(relation, f"{ref!r} links {wrong} in {relation}. Expected {target_kind} "
                    "handles. Send handles of that kind from this plan.")


def linked_features(document):
    return {f for requirement in select(document, "requirement").values() for f in requirement["features"]}


def validate_owned_work(document):
    """The same area-owned rules serve DETAIL exact-fix and canonical plan validation."""
    features, requirements, units = (
        select(document, k) for k in ("feature", "requirement", "unit"))
    if uncovered := sorted(features.keys() - linked_features(document)):
        raise Mismatch("edits.typedBody.features", f"Features {uncovered} have no requirement links. "
            "Send requirements that name each feature handle they deliver in typedBody.features.")
    if not all((features, requirements, units)):
        raise Mismatch("edits/units", "The plan needs features, linked requirements and a unit to deliver "
            "them. Send feature and requirement edits and assign each requirement to one unit.")
    for ref, unit in units.items():
        for pattern in unit["scope"]:
            try:
                safe_path(pattern)
            except ValueError as error:
                raise Mismatch("units.scope", f"{ref}: got {pattern!r}. Send a project-relative write "
                    "scope inside the unit's area.") from error
        if invalid := [d for d in unit["dependencies"] if d not in units or d == ref]:
            raise Mismatch("units.dependencies", f"{ref} depends on {invalid}. Send positions of other "
                "units that must finish first, or an empty list if none are needed.")
        for other, owner in units.items():
            if (ref < other and unit["area"] == owner["area"]
                    and overlaps(unit["scope"], owner["scope"])):
                raise Mismatch("units.scope", f"{ref} {unit['scope']} and {other} {owner['scope']} can "
                    "both write the same paths. Send disjoint subtrees or literal paths.")

    for ref in requirements:
        owners = [u for u, body in units.items() if ref in body["requirements"]]
        if len(owners) != 1:
            raise Mismatch("units.requirements", f"Requirement {ref!r} needs one unit owner, got {owners}. "
                "Send its handle in the requirements list of exactly one unit.")
    try:
        tuple(TopologicalSorter({r: u["dependencies"] for r, u in units.items()}).static_order())
    except CycleError as error:
        raise Mismatch("units.dependencies", f"These dependencies form a loop: {error.args[1]}. "
            "Send dependencies that let one unit in the loop start first.") from error


def validate(document):
    validate_records(document)
    validate_owned_work(document)
    units = select(document, "unit")
    for ref, unit in units.items():
        for other, owner in units.items():
            if (ref < other and unit["area"] != owner["area"]
                    and overlaps(unit["scope"], owner["scope"])):
                raise Mismatch("units.scope", f"{ref} and {other} can write the same paths across areas. "
                    "Assign disjoint scopes with one owner per path.")
    from . import architecture
    architecture.validate(document)
    return coverage(document)


def add_guide(document, wave=1):
    from copy import deepcopy
    result = deepcopy(document)
    previous = select(result, "unit").get("guide")
    area = previous["area"] if previous else next(iter(select(result, "area")))
    area_scope = result["nodes"][area]["body"]["scope"]
    area_scope.extend(p for p in GUIDE_SCOPE if p not in area_scope)
    current = {r for r, u in select(result, "unit").items()
               if any(q.startswith(f"{wave}.") for q in u["requirements"])}
    readers = {c["handle"]: c["consumers"] for c in document["architecture"]["contracts"]}
    for handle, contract in select(result, "contract").items():
        contract["consumers"] = [u for u in contract["consumers"] if u != "guide"]
        # An agreement whose consumer areas have no unit yet belongs to a later wave's walkthrough.
        later = readers.get(handle) and not contract["consumers"]
        if current.intersection([contract["provider"], *contract["consumers"]]) and not later:
            contract["consumers"].append("guide")
    result["nodes"]["guide"] = {"kind": "unit", "body": {
        "scope": list(GUIDE_SCOPE), "requirements": [],
        "area": area, "dependencies": [],
        "instructions": GUIDE_WORK + "\nThis wave: " + document["architecture"]["waves"][wave - 1]["goal"]}}
    return result


def built(store):
    """Facts from the finished wave, without turning worker reports into verdicts."""
    cursor = store.cursor()
    document = store.plan(cursor.revision)
    units = select(document, "unit")
    finished = max((row.wave for row in store.units().values()), default=cursor.wave)
    rows = {}
    for ref, row in store.units().items():
        if row.wave != finished:
            continue
        answer = store.read(row.report)["answer"] if row.report else {}
        rows[ref] = {"scope": units[ref]["scope"], "summary": answer.get("summary", ""),
                     "discoveries": answer.get("discoveries", []),
                     "open gaps": [b for _, _, b in store.records("open_gap") if b["unit"] == ref]}
    words = store.read(cursor.checkin)["words"] if cursor.checkin else None
    if isinstance(words, str):
        words = store.read(words)["words"]
    return {"Previous architecture": document["architecture"], "Finished wave": rows,
            "User check-in": words, "Remaining waves": document["architecture"]["waves"][finished:]}


def approved(store):
    cursor = store.cursor()
    subject = store.read(cursor.B)["subject"]
    vision = subject["plan"]["nodes"]["vision"]["body"]
    return vision == vision_body(store.read(cursor.vision)["answer"])
