"""Pure wave projections and static candidate interfaces. Never imports product code."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import subprocess
from copy import deepcopy
from pathlib import Path, PurePosixPath

from codex_harness.core import waves
from codex_harness.core.process_launch import hidden_process_kwargs
from codex_harness.core.spine import Spine, SpineError, _canonical, _hash, impact, requirement_refs

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_INTERFACE_BYTES = 1024 * 1024
MAX_INTERFACE_TOTAL_BYTES = 4 * 1024 * 1024
MAX_CONTEXT_BYTES = 8 * 1024 * 1024


def need(condition, reason, code="wave-refinement-invalid"):
    if not condition:
        raise SpineError(code, reason)


def digest(value):
    return _hash(_canonical(value))


def wave_values(spine):
    maps = [
        spine.node(k)
        for k in spine.ids("decision")
        if spine.node(k)["fields"].get("schema") == "dot-wave-map-v1"
    ]
    if not maps:
        return None
    need(len(maps) == 1, "Exactly one wave map required", "wave-map-invalid")
    owner = maps[0]
    details = [
        spine.node(k)
        for k in spine.ids("design")
        if spine.node(k)["parents"] == [owner["id"]]
        and spine.node(k)["fields"].get("schema") == "dot-wave-detail-v1"
    ]
    validated = waves.validate_map(spine, owner["fields"], [n["fields"] for n in details])
    return owner, details, validated


def execution_scope(spine, template=None):
    values = wave_values(spine)
    if values is None or len(values[0]["fields"]["order"]) == 1:
        return None
    owner, details, _ = values
    plan = owner["fields"]
    by_wave = {n["fields"]["waveId"]: n["fields"] for n in details}
    prefix, hole = [], False
    for wid in plan["order"]:
        detailed = by_wave[wid]["detail"] == "detailed"
        need(not (hole and detailed), "Detailed wave after sketch hole", "wave-scope-invalid")
        if detailed:
            prefix.append(wid)
        else:
            hole = True
    need(bool(prefix), "First wave must contain real detail", "wave-scope-invalid")
    beads = {bid for wid in prefix for bid in by_wave[wid]["beadIds"]}
    need(
        beads == set(spine.ids("bead")),
        "Unassigned or out-of-scope executable bead",
        "wave-scope-invalid",
    )
    need(
        all(set(spine.node(b)["fields"]["dependsOn"]) <= beads for b in beads),
        "Dependency into sketch",
        "wave-scope-invalid",
    )
    selected = [w for w in plan["waves"] if w["id"] in prefix]
    requirements = sorted({r for w in selected for r in w["requirementIds"]})
    for w in plan["waves"]:
        if w["id"] not in prefix:
            covered = {r for s in by_wave[w["id"]]["sketches"] for r in s["requirementIds"]}
            need(
                set(w["requirementIds"]) - set(requirements) <= covered,
                "Later requirement lacks sketch",
                "wave-coverage-missing",
            )
    boundary = next(w for w in selected if w["id"] == prefix[-1])
    declarations = [
        spine.node(k)["fields"]
        for k in boundary["exitScenarioIds"]
        if "waveBoundaryIntegrationTestId" in spine.node(k)["fields"]
    ]
    need(
        len(declarations) == 1,
        "Boundary requires one protected test/module declaration",
        "wave-boundary-integration-invalid",
    )
    declaration = declarations[0]
    test_id = declaration["waveBoundaryIntegrationTestId"]
    module = declaration.get("waveBoundaryIntegrationModule")
    need(
        test_id in spine.ids("test") and spine.node(test_id)["fields"]["module"] == module,
        "Declared boundary test/module unavailable",
        "wave-boundary-integration-invalid",
    )
    need(
        set(requirements) <= {r["id"] for r in requirement_refs(spine, test_id)},
        "Boundary test lacks cumulative trace",
        "wave-boundary-integration-invalid",
    )
    if template is not None:
        from codex_harness.core.runners import resolve_runner

        runner = resolve_runner(
            {
                **spine.node(spine.ids("vision")[0])["fields"],
                "beads": {},
                "integration": {"proofModule": module},
            }
        )
        if runner.name == "unittest":
            proofs = runner.proof_paths(Path(template))
        else:
            runner.proof_argument(module)
            proofs = (Path(template) / module,)
        need(
            len(proofs) == 1
            and proofs[0].is_file()
            and proofs[0].resolve().is_relative_to(Path(template).resolve())
            and not any(
                p.is_symlink()
                for p in [proofs[0], *proofs[0].parents]
                if p != Path(template).parent
            ),
            "Declared protected proof file missing",
            "wave-boundary-integration-invalid",
        )
    global_tests = [k for k in spine.ids("test") if spine.node(k)["fields"]["level"] == "e2e"]
    need(
        len(global_tests) == 1, "Retain unique global e2e test", "wave-boundary-integration-invalid"
    )
    global_id = global_tests[0]
    return dict(
        schema="dot-wave-execution-scope-v1",
        throughWaveId=prefix[-1],
        waveIds=prefix,
        waveMapRevision=spine.revision(owner["id"]),
        sourceSpineRevision=spine.plan_revision(),
        productLockDigest=waves.product_digest(spine, plan, [n["fields"] for n in details]),
        requiredObligationIds=sorted(
            o["id"] for o in plan["obligations"] if o["requiredForFullVision"]
        ),
        allRequirementIds=spine.ids("requirement"),
        allScenarioIds=spine.ids("acceptance"),
        inScopeRequirementIds=requirements,
        laterSketchIds=sorted(
            s["id"] for wid in plan["order"] if wid not in prefix for s in by_wave[wid]["sketches"]
        ),
        boundaryIntegrationTestId=test_id,
        boundaryIntegrationModule=module,
        globalIntegration=dict(
            testId=global_id,
            proofModule=spine.node(global_id)["fields"]["module"],
            requirementIds=[r["id"] for r in requirement_refs(spine, global_id)],
            state="outstanding",
        ),
    )


def parse_json(raw):
    need(type(raw) is bytes and len(raw) <= MAX_RESPONSE_BYTES, "Expected bounded captured bytes")

    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"Nonfinite JSON number: {value}")

    def number(value):
        result = float(value)
        need(math.isfinite(result), "Overflowing JSON number")
        return result

    try:
        text = raw.decode("utf-8", errors="strict")
        need(not text.startswith("\ufeff"), "UTF-8 BOM is not accepted")
        result = json.loads(
            text, object_pairs_hook=pairs, parse_constant=invalid, parse_float=number
        )
        need(isinstance(result, dict), "Expected JSON object")
        return result
    except (UnicodeError, ValueError, RecursionError) as error:
        raise SpineError("wave-response-invalid", str(error)) from error


def git(repo, *args, binary=False):
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=30,
            **hidden_process_kwargs(),
        )
    except subprocess.TimeoutExpired as error:
        raise SpineError(
            "wave-interface-unavailable", "Local Git read exceeded 30 seconds"
        ) from error
    need(
        len(result.stdout) <= MAX_INTERFACE_TOTAL_BYTES,
        "Git read exceeds size bound",
        "wave-interface-unavailable",
    )
    return result.stdout if binary else result.stdout.decode("utf-8").strip()


def _return_shape(annotation, aliases):
    if annotation is None:
        return "unknown"
    root = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    name = ast.unparse(root)
    name = aliases.get(name, name).split(".")[-1]
    if name in {"list", "List", "tuple", "Tuple", "Sequence", "Iterable"}:
        return "array"
    if name in {"dict", "Dict", "Mapping", "MutableMapping"}:
        return "object"
    return {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}.get(
        name, "unknown"
    )


def extract_interfaces(repo, commit, paths):
    """Read only regular Git blobs; AST/strict JSON projection never executes them."""
    commit = git(repo, "rev-parse", f"{commit}^{{commit}}")
    records = []
    total = 0
    tree = git(repo, "rev-parse", f"{commit}^{{tree}}")
    for path in sorted(set(paths)):
        relative = PurePosixPath(path.replace("\\", "/"))
        need(
            not relative.is_absolute() and ".." not in relative.parts and ":" not in path,
            "Unsafe interface path",
        )
        if relative.suffix not in (".py", ".json"):
            continue
        entry = git(repo, "ls-tree", commit, "--", relative.as_posix()).split()
        need(
            len(entry) >= 3 and entry[0] in ("100644", "100755") and entry[1] == "blob",
            "Interface is not a regular candidate blob",
        )
        blob = entry[2]
        size = int(git(repo, "cat-file", "-s", blob))
        total += size
        need(
            size <= MAX_INTERFACE_BYTES and total <= MAX_INTERFACE_TOTAL_BYTES,
            "Interface selection exceeds size bound",
            "wave-interface-unavailable",
        )
        raw = git(repo, "cat-file", "blob", blob, binary=True)
        record = dict(
            path=relative.as_posix(),
            commit=commit,
            tree=tree,
            blob=blob,
            rawSha256=hashlib.sha256(raw).hexdigest(),
            symbols=[],
            unknown=[],
        )
        if relative.suffix == ".json":
            record["jsonSchema"] = parse_json(raw)
            shape = record["jsonSchema"].get("type", "unknown")
            if not isinstance(shape, str) or shape not in {
                "array",
                "object",
                "string",
                "integer",
                "number",
                "boolean",
                "null",
            }:
                shape = "unknown"
            if '"$ref"' in _canonical(record["jsonSchema"]):
                record["unknown"].append("JSON references retained without resolution")
                shape = "unknown"
            record["symbols"].append(dict(symbol="#", kind="json-schema", returnShape=shape))
        else:
            try:
                module = ast.parse(raw.decode("utf-8"))
                aliases = {
                    a.asname or a.name: a.name
                    for n in module.body
                    if isinstance(n, ast.ImportFrom)
                    for a in n.names
                }
                record["aliases"] = aliases
                for node in module.body:
                    if isinstance(
                        node, ast.FunctionDef | ast.AsyncFunctionDef
                    ) and not node.name.startswith("_"):
                        record["symbols"].append(
                            dict(
                                symbol=node.name,
                                kind="function",
                                signature=ast.unparse(node.args),
                                returns=ast.unparse(node.returns) if node.returns else "unknown",
                                returnShape=_return_shape(node.returns, aliases),
                            )
                        )
                    elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                        record["symbols"].append(
                            dict(
                                symbol=node.name,
                                kind="class",
                                returnShape="object"
                                if any(isinstance(n, ast.AnnAssign) for n in node.body)
                                else "unknown",
                                decorators=[ast.unparse(n) for n in node.decorator_list],
                                keywords={
                                    n.arg: ast.unparse(n.value) for n in node.keywords if n.arg
                                },
                                defaults={
                                    n.target.id: ast.unparse(n.value)
                                    for n in node.body
                                    if isinstance(n, ast.AnnAssign)
                                    and isinstance(n.target, ast.Name)
                                    and n.value is not None
                                },
                                bases=[
                                    aliases.get(ast.unparse(b), ast.unparse(b)) for b in node.bases
                                ],
                                fields={
                                    n.target.id: ast.unparse(n.annotation)
                                    for n in node.body
                                    if isinstance(n, ast.AnnAssign)
                                    and isinstance(n.target, ast.Name)
                                },
                                methods=[
                                    dict(
                                        symbol=n.name,
                                        signature=ast.unparse(n.args),
                                        returns=ast.unparse(n.returns) if n.returns else "unknown",
                                    )
                                    for n in node.body
                                    if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                                    and not n.name.startswith("_")
                                ],
                            )
                        )
                if not record["symbols"]:
                    record["unknown"].append("No supported public static interface")
            except (SyntaxError, UnicodeError) as error:
                record["unknown"].append(str(error))
        records.append(record)
    return records


def refinement_impact(old, new, *, protected_files_changed=False):
    result = impact(old, new)
    changed = set(result["changed"])
    beads = set(result["affectedBeads"])
    tests = set(result["affectedTests"])
    maps = [wave_values(s) for s in (old, new)]
    contract_sets = [{c["id"]: c for c in v[0]["fields"]["contracts"]} if v else {} for v in maps]
    changed_contracts = {
        k
        for k in contract_sets[0].keys() | contract_sets[1].keys()
        if contract_sets[0].get(k) != contract_sets[1].get(k)
    }
    contract_waves = set()
    for spine in (old, new):
        if protected_files_changed:
            beads.update(spine.ids("bead"))
            tests.update(spine.ids("test"))
    while True:
        before = set(beads)
        for spine in (old, new):
            for key in spine.ids("bead"):
                if set(spine.node(key)["fields"]["dependsOn"]) & beads:
                    beads.add(key)
            values = wave_values(spine)
            if values:
                plan, details = values[0]["fields"], values[1]
                by_wave = {d["fields"]["waveId"]: set(d["fields"]["beadIds"]) for d in details}
                for contract in plan["contracts"]:
                    if contract["id"] in changed_contracts or contract["specRef"]["id"] in changed:
                        beads.update(by_wave[contract["producerWaveId"]])
                        contract_waves.update(
                            [contract["producerWaveId"], *contract["consumerWaveIds"]]
                        )
                    if (
                        by_wave[contract["producerWaveId"]] & beads
                        or contract["specRef"]["id"] in changed
                        or contract["id"] in changed_contracts
                    ):
                        for wid in contract["consumerWaveIds"]:
                            beads.update(by_wave[wid])
        if beads == before:
            break
    requirements, affected_waves = set(), contract_waves
    for spine in (old, new):
        for key in beads & set(spine.ids("bead")):
            requirements.update(r["id"] for r in requirement_refs(spine, key))
            tests.update(k for k in spine.descendants(key) if k in spine.ids("test"))
            tests.update(
                k
                for k in spine.ids("test")
                if spine.node(k)["fields"]["module"] == spine.node(key)["fields"]["proofModule"]
            )
        values = wave_values(spine)
        if values:
            for d in values[1]:
                fields = d["fields"]
                if set(fields["beadIds"]) & beads or any(
                    set(s["pendingProducerIds"]) & beads for s in fields["sketches"]
                ):
                    affected_waves.add(fields["waveId"])
    while True:
        previous = set(affected_waves)
        for values in maps:
            if not values:
                continue
            plan = values[0]["fields"]
            for detail in values[1]:
                if detail["id"] in changed or any(
                    s["detailAfterWaveId"] in affected_waves for s in detail["fields"]["sketches"]
                ):
                    affected_waves.add(detail["fields"]["waveId"])
            for contract in plan["contracts"]:
                if contract["producerWaveId"] in affected_waves:
                    affected_waves.update(contract["consumerWaveIds"])
        if previous == affected_waves:
            break
    for values in maps:
        if values:
            requirements.update(
                r
                for w in values[0]["fields"]["waves"]
                if w["id"] in affected_waves
                for r in w["requirementIds"]
            )
    return {
        **result,
        "editedNodeIds": sorted(changed),
        "affectedBeads": sorted(beads),
        "affectedTests": sorted(tests),
        "impactedRequirementIds": sorted(requirements),
        "affectedWaves": sorted(affected_waves),
        "retiredBeads": sorted(set(old.ids("bead")) - set(new.ids("bead"))),
        "untouchedBeads": sorted(set(new.ids("bead")) - beads),
        "proofInvalidationReasons": ["requirementFilesRevision changed"]
        if protected_files_changed
        else [],
    }


def proposal(spine, context, raw):
    response = parse_json(raw)

    def no_authority(value):
        if isinstance(value, dict):
            for key, item in value.items():
                need(
                    not (
                        key.lower()
                        in {"accepted", "approved", "ready", "pass", "passed", "evidence"}
                        and isinstance(item, bool)
                    ),
                    "Response cannot supply authority/evidence booleans",
                )
                no_authority(item)
        elif isinstance(value, list):
            for item in value:
                no_authority(item)

    no_authority(response)
    need(
        set(response) == {"schema", "contextDigest", "refinements", "contradictions", "questions"}
        and response["schema"] == "dot-wave-refinement-response-v1"
        and response["contextDigest"] == context["contextDigest"],
        "Response schema/context mismatch",
    )
    need(
        all(isinstance(response[k], list) for k in ("refinements", "contradictions", "questions")),
        "Expected response arrays",
    )
    target = context["targetDetail"]
    sketches = {s["id"]: s for s in target["fields"]["sketches"]}
    refs = {f"{r['blob']}:{s['symbol']}" for r in context["interfaces"] for s in r["symbols"]}
    symbols_by_ref = {
        f"{r['blob']}:{s['symbol']}": s for r in context["interfaces"] for s in r["symbols"]
    }
    nodes, refined, cards = [], set(), []
    unresolved = list(response["questions"])
    for row in response["refinements"]:
        need(
            isinstance(row, dict)
            and set(row) == {"sketchId", "nodes", "interfaceRefs", "reason", "replacedAssumptions"},
            "Invalid refinement keys",
        )
        sid = row["sketchId"]
        need(
            sid in sketches and sid not in refined and isinstance(row["nodes"], list),
            "Unknown/duplicate sketch",
        )
        need(
            isinstance(row["reason"], str)
            and bool(row["reason"].strip())
            and isinstance(row["replacedAssumptions"], list)
            and bool(row["replacedAssumptions"]),
            "Reason and replaced assumptions required",
        )
        need(
            isinstance(row["interfaceRefs"], list)
            and bool(row["interfaceRefs"])
            and set(row["interfaceRefs"]) <= refs,
            "Missing/foreign static interface reference",
        )
        for ref in row["interfaceRefs"]:
            symbol = symbols_by_ref[ref]
            if symbol.get("returnShape", "unknown") == "unknown":
                unresolved.append(f"Required static interface shape is unknown: {ref}")
        need(
            all(n.get("kind") in ("bead", "test") for n in row["nodes"]),
            "Only target bead/test edits allowed",
        )
        need(
            not any(n["id"] in spine.ids() or n["id"] in sketches for n in row["nodes"]),
            "Cannot recycle existing node identity",
        )
        nodes.extend(deepcopy(row["nodes"]))
        refined.add(sid)
    need(len({n["id"] for n in nodes}) == len(nodes), "Duplicate proposed node")
    owner, wave_details, _ = wave_values(spine)
    plan = owner["fields"]
    by_wave = {n["fields"]["waveId"]: n["fields"]["beadIds"] for n in wave_details}
    protected_ids = set(spine.ids()) - set(spine.ids("bead")) - set(spine.ids("test"))
    contracts = {c["id"]: c for c in plan["contracts"]}
    for card in response["contradictions"]:
        need(
            isinstance(card, dict)
            and set(card) == {"sourceId", "interfaceRefs", "resolution", "classification"}
            and card["classification"] in ("detail", "locked", "unresolved"),
            "Invalid contradiction",
        )
        need(
            card["sourceId"] in protected_ids | set(contracts)
            and isinstance(card["interfaceRefs"], list)
            and bool(card["interfaceRefs"])
            and set(card["interfaceRefs"]) <= refs
            and isinstance(card["resolution"], str)
            and bool(card["resolution"].strip()),
            "Contradiction source/observation required",
        )
        cards.append(
            {
                **card,
                "classification": "locked"
                if card["sourceId"] in protected_ids | set(contracts)
                else card["classification"],
            }
        )
    # Known shape conflicts are source facts, regardless of the response's classification.
    for cid in sorted({cid for s in sketches.values() for cid in s["expectedContractIds"]}):
        contract = contracts[cid]
        producers = set(by_wave[contract["producerWaveId"]]) & {
            p
            for s in sketches.values()
            if cid in s["expectedContractIds"]
            for p in s["pendingProducerIds"]
        }
        paths = {path for p in producers for path in spine.node(p)["fields"]["ownedFiles"]}
        shape = contract["interface"]["outputShape"].get("type")
        observed = [
            (r, s)
            for r in context["interfaces"]
            if r.get("path") in paths
            for s in r["symbols"]
            if s["symbol"] == contract["interface"]["symbol"]
        ]
        symbols = [s for _, s in observed]
        if not symbols or any(s.get("returnShape", "unknown") == "unknown" for s in symbols):
            unresolved.append(f"Required contract interface remains unknown: {contract['id']}")
        expected = (
            {"dict": "object", "list": "array"}.get(shape, shape)
            if isinstance(shape, str)
            else None
        )
        known = {"object", "array", "string", "integer", "number", "boolean", "null"}
        compatible = {expected, "integer"} if expected == "number" else {expected}
        if expected in known and any(s.get("returnShape") in known - compatible for s in symbols):
            cards.append(
                dict(
                    sourceId=contract["id"],
                    classification="locked",
                    resolution="Observed interface shape conflicts with locked output",
                    interfaceRefs=sorted({f"{r['blob']}:{s['symbol']}" for r, s in observed}),
                )
            )
    missing = sorted(set(sketches) - refined)
    complete = (
        not missing
        and not unresolved
        and not any(c["classification"] in ("locked", "unresolved") for c in cards)
    )
    detail, candidate, projected = None, spine, None
    if complete:
        detail = deepcopy(target)
        detail["fields"].update(
            detail="detailed",
            sketches=[],
            beadIds=sorted(n["id"] for n in nodes if n["kind"] == "bead"),
        )
        candidate = Spine(
            {
                **spine.to_dict(),
                "nodes": [spine.node(k) for k in spine.ids() if k != target["id"]]
                + nodes
                + [detail],
            }
        )
        waves.validate_map(
            candidate,
            plan,
            [
                detail["fields"] if n["id"] == target["id"] else n["fields"]
                for n in wave_values(spine)[1]
            ],
        )
        observations = {}
        for row in response["refinements"]:
            closure = set()
            pending = [n["id"] for n in row["nodes"] if n["kind"] == "bead"]
            while pending:
                key = pending.pop()
                for dep in candidate.node(key)["fields"]["dependsOn"]:
                    if dep not in closure:
                        closure.add(dep)
                        pending.append(dep)
            sketch = sketches[row["sketchId"]]
            need(
                set(sketch["pendingProducerIds"]) <= closure,
                "Original sketch producer dependency omitted",
            )
            for cid in sketch["expectedContractIds"]:
                contract = contracts[cid]
                producers = set(by_wave[contract["producerWaveId"]]) & closure
                observed = [
                    r
                    for r in context["interfaces"]
                    if any(
                        r.get("path") in spine.node(p)["fields"]["ownedFiles"] for p in producers
                    )
                    and f"{r['blob']}:{contract['interface']['symbol']}" in row["interfaceRefs"]
                ]
                need(
                    bool(observed),
                    "Expected contract lacks actual producer-owned interface binding",
                )
                observations.setdefault(cid, []).extend(observed)
            for rid in sketches[row["sketchId"]]["requirementIds"]:
                need(
                    any(
                        rid in {r["id"] for r in requirement_refs(candidate, n["id"])}
                        for n in row["nodes"]
                        if n["kind"] == "bead"
                    ),
                    "Missing locked sketch requirement trace",
                )
                need(
                    any(
                        rid in {r["id"] for r in requirement_refs(candidate, n["id"])}
                        for n in row["nodes"]
                        if n["kind"] == "test"
                    ),
                    "Missing locked sketch test trace",
                )
        if observations:
            boundary = context["boundary"]
            detail["fields"]["observedInterfaces"] = [
                dict(
                    contractId=cid,
                    code=dict(
                        repo=boundary["repo"],
                        commit=boundary["candidateCommit"],
                        tree=boundary["candidateTree"],
                        integratedRevision=boundary["productBytesRevision"],
                        algorithm="tree-bytes-v2",
                        isClean=True,
                        dirtyPaths=[],
                        untrackedPaths=[],
                    ),
                    paths=sorted({r["path"] for r in records}),
                    digest=digest(records),
                )
                for cid, records in sorted(observations.items())
            ]
            candidate = Spine(
                {
                    **candidate.to_dict(),
                    "nodes": [
                        detail if n["id"] == detail["id"] else n
                        for n in candidate.to_dict()["nodes"]
                    ],
                }
            )
            wave_values(candidate)
        from codex_harness.core.spine_render import render_markdown

        projected = refinement_impact(
            spine,
            candidate,
            protected_files_changed=render_markdown(spine) != render_markdown(candidate),
        )
    body = dict(
        schema="dot-wave-refinement-proposal-v1",
        contextDigest=context["contextDigest"],
        context=context,
        responseUtf8=raw.decode("utf-8"),
        responseRawSha256=hashlib.sha256(raw).hexdigest(),
        proposedNodes=nodes,
        proposedDetail=detail,
        state="complete" if complete else "unapplied",
        unrefinedSketchIds=missing,
        questions=unresolved,
        cards=cards,
        impact=projected,
        readiness="not-run",
        review="required",
        publication="required",
        completedRealDelta=0,
    )
    need(body["responseUtf8"].encode("utf-8") == raw, "Raw response round-trip failed")
    return {**body, "proposalId": digest(body)}
