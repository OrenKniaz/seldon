"""Planner record shapes, input validation and read-only inspection; PlanStore owns truth."""

import ast
import hashlib
import sys
from copy import deepcopy
from typing import Literal, NotRequired, TypedDict

from codex_harness.core.spine import (
    ALLOWED_PARENTS,
    Spine,
    bead_contract,
    semantic_spec,
)


class Judgment(TypedDict):
    """One event-backed semantic record; evidence state and human authority are separate."""

    id: str
    question: str
    subjectRefs: list[dict]
    evidenceRefs: list[dict]
    sourceRefs: list[dict]
    candidate: str | None
    binding: str
    status: Literal["pending-judgment", "judged"]
    verdict: Literal["positive", "negative", "inconclusive", "unsupported"] | None
    rationale: str | None
    meaning: dict
    gaps: list[str]
    fixesIssued: int
    disposition: Literal["judge", "exact-fix", "steer", "complete"]
    owner: None
    source: str | None
    sourceVerified: bool
    supersededBy: NotRequired[str]
    error: str | None


JUDGMENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "judgmentId": {"type": "string", "minLength": 1},
        "binding": {"type": "string", "minLength": 1},
        "verdict": {
            "type": "string",
            "enum": ["positive", "negative", "inconclusive", "unsupported"],
        },
        "rationale": {"type": "string", "minLength": 1},
        "meaning": {"type": "object"},
        "gaps": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "required": ["judgmentId", "binding", "verdict", "rationale", "meaning", "gaps"],
    "additionalProperties": False,
}


def judgment_refs(spine, ids):
    missing = set(ids) - set(spine.ids())
    if missing:
        raise ValueError(
            f"Missing subject/evidence-record IDs {sorted(missing)!r}; evidence_ids uses trace result IDs, not observation digests. For candidate assessment answer the controller-bound execution question with harness_loop_answer"
        )
    return [
        dict(id=key, revision=spine.revision(key), node=spine.node(key)) for key in sorted(set(ids))
    ]


def judgment_example(record):
    """A checked shape, never an assessment or approval of the bound evidence."""
    import json

    facts = next(
        (r["payload"]["facts"] for r in record["sourceRefs"] if "facts" in r["payload"]), {}
    )
    meaning = {
        "plan-review": dict(
            findings=[],
            depth="Focused review because this small local change has bounded consequences; inspect persistence and failure paths",
        ),
        "finding-equivalence": dict(groups=[[r["id"]] for r in facts.get("members", [])]),
        "finding-disposition": dict(
            memberDispositions={r["id"]: "open" for r in facts.get("members", [])}
        ),
        "review-reuse": dict(reuse=False),
        "plan-convergence": dict(action="continue-review"),
        "user-decision": dict(decision="changes_requested"),
        "vision-intent": dict(impact="changed"),
        "scope-intent": dict(impact="WHAT"),
        "planning-size": dict(
            size="small",
            process="Focused review of consequence and recovery",
            overrideAccepted=False,
        ),
    }.get(record["question"], {})
    loop = next(
        (ref["payload"] for ref in record["sourceRefs"] if ref["kind"] == "loop-question"), None
    )
    if loop:
        meaning = dict(
            option="draft" if loop["family"] == "author" else "continue",
            rationale="Example only; author the requested meaning",
            content={},
        )
    packet = next(
        (r["payload"] for r in record["sourceRefs"] if r["kind"] == "candidate-observation"), None
    )
    if packet:
        meaning = {k: packet[k] for k in ("check", "evidenceBinding", "phase")}
    example = dict(
        judgmentId=record["id"],
        binding=record["binding"],
        verdict="inconclusive",
        rationale="Example only; assess the bound evidence before answering",
        meaning=meaning,
        gaps=[],
    )
    judgment_response(json.dumps(dict(example, verdict="positive")), record)
    # Symmetric shapes so the example never anchors the WHAT/HOW meaning.
    alternatives = (
        [dict(example, meaning=dict(impact="HOW"))] if record["question"] == "scope-intent" else []
    )
    for alternative in alternatives:
        judgment_response(json.dumps(dict(alternative, verdict="positive")), record)
    return dict(
        responseExample=example,
        responseAlternatives=alternatives,
        responseGuidance=(
            "Replace example semantics with your assessment. Verdict: positive=established; negative=concrete gaps; "
            "inconclusive=unresolved; unsupported=unavailable basis. meaning.depth accepts any nonblank string stating "
            "chosen depth and consequence-based reason, not an enum. Findings use packet node_id, severity "
            "(blocker|major|minor|note), finding and suggestion. For plan findings include classification "
            "(defect-in-plan|beyond-brief) and scope_reason explaining WHAT/HOW against the brief; beyond-brief is "
            "advisory only by explicit judgment. Empty or minor-only findings never imply approval."
        ),
    )


def judgment_response(response, record):
    """Check only shape and identity. The caller's LLM supplies all semantic meaning."""
    import json

    def reject_constant(value):
        raise ValueError(f"Use finite JSON values instead of {value}")

    parsed = json.loads(response, parse_constant=reject_constant)
    validate_input(parsed, JUDGMENT_RESPONSE_SCHEMA, "response")
    for ref in record["sourceRefs"]:
        if ref["kind"] == "loop-question":
            validate_input(parsed["meaning"], ref["payload"]["answerSchema"], "answer")
    if parsed["judgmentId"] != record["id"] or parsed["binding"] != record["binding"]:
        raise ValueError("Copy judgmentId and binding exactly from the prepared judgment")
    for ref in record["sourceRefs"]:
        if ref["kind"] == "candidate-observation":
            expected = {k: ref["payload"][k] for k in ("check", "evidenceBinding", "phase")}
            if any(parsed["meaning"].get(k) != v for k, v in expected.items()):
                raise ValueError(
                    f"Candidate assessment requires meaning {expected!r}; copy these exact identities from the prepared request"
                )
    if parsed["verdict"] == "negative" and not parsed["gaps"]:
        raise ValueError("A negative verdict needs at least one concrete gap in gaps")
    if parsed["verdict"] == "positive" and parsed["gaps"]:
        raise ValueError("Resolve the gaps or return a negative/inconclusive verdict")
    facts = next(
        (
            ref["payload"]["facts"]
            for ref in record["sourceRefs"]
            if isinstance(ref.get("payload", {}).get("facts"), dict)
        ),
        {},
    )
    if record["question"] == "plan-review":
        from codex_harness.core.roundtable import _invalid_reason

        findings = parsed["meaning"].get("findings")
        depth = parsed["meaning"].get("depth")
        if not isinstance(depth, str) or not depth.strip():
            raise ValueError(
                "meaning.depth must state chosen review depth and consequence-based reason"
            )
        if not isinstance(findings, list):
            raise ValueError(
                "meaning.findings must be a list; verdict and rationale remain explicit"
            )
        for item in findings:
            reason = _invalid_reason(item)
            if reason:
                raise ValueError(reason)
    if record["question"] == "finding-equivalence" and parsed["verdict"] == "positive":
        ids = {row["id"] for row in facts.get("members", [])}
        groups = parsed["meaning"].get("groups")
        if not isinstance(groups, list) or any(
            not isinstance(group, list) or not group for group in groups
        ):
            raise ValueError("meaning.groups must list nonempty groups of exact member IDs")
        flat = [key for group in groups for key in group]
        if any(not isinstance(key, str) or key not in ids for key in flat) or len(set(flat)) != len(
            flat
        ):
            raise ValueError("Equivalence groups must contain disjoint captured member IDs")
    if record["question"] == "finding-disposition" and parsed["verdict"] == "positive":
        ids = {row["id"] for row in facts.get("members", [])}
        dispositions = parsed["meaning"].get("memberDispositions")
        if (
            not isinstance(dispositions, dict)
            or set(dispositions) != ids
            or any(
                value not in {"resolved", "rejected", "deferred", "open"}
                for value in dispositions.values()
            )
        ):
            raise ValueError(
                "meaning.memberDispositions must disposition every captured member exactly once"
            )
    if (
        record["question"] == "review-reuse"
        and parsed["verdict"] == "positive"
        and type(parsed["meaning"].get("reuse")) is not bool
    ):
        raise ValueError("meaning.reuse must explicitly judge the actual revision delta")
    if parsed["verdict"] == "positive":
        meaning = parsed["meaning"]
        if record["question"] == "user-decision" and meaning.get("decision") not in {
            "approve",
            "decline",
            "changes_requested",
            "unclear",
        }:
            raise ValueError(
                "meaning.decision must name approve, decline, changes_requested or unclear"
            )
        if record["question"] == "user-decision" and meaning.get("decision") == "unclear":
            parsed["verdict"] = "inconclusive"
        if record["question"] in {"vision-intent", "scope-intent"} and meaning.get(
            "impact"
        ) not in (
            {"WHAT", "HOW"}
            if record["question"] == "scope-intent"
            else {"preserved", "changed", "narrowed"}
        ):
            raise ValueError("meaning.impact must answer the bound intent question")
        if record["question"] == "planning-size":
            if (
                meaning.get("size") not in {"tiny", "small", "medium", "large"}
                or not isinstance(meaning.get("process"), str)
                or not meaning["process"].strip()
            ):
                raise ValueError(
                    "meaning needs size=tiny|small|medium|large and a nonblank process"
                )
            if (
                any(
                    ref["payload"]
                    .get("facts", {})
                    .get("intake", {})
                    .get("fields", {})
                    .get("sizeOverride")
                    for ref in record["sourceRefs"]
                )
                and type(meaning.get("overrideAccepted")) is not bool
            ):
                raise ValueError(
                    "meaning.overrideAccepted must interpret the recorded user's size override"
                )
    return {key: parsed[key] for key in ("verdict", "rationale", "meaning", "gaps")}


FIELD_RULES = {
    "vision": (
        "New vision.text: a short product statement + Full brief: <actual source path>. "
        "Use PRODUCT-BRIEF.md only when that is the existing source; invent no path. Preserve "
        "the complete brief/provenance and derive every requirement/scenario from it. This "
        "teaching never migrates, truncates, rewrites or narrows existing/locked visions. "
        "Project setup and provider choices belong to operator kickoff, not planner content. "
        "Never read credentials or git config except --get user.name or --get user.email. "
        "Understanding fields/aliases: vision, users, first_user_moment/firstUserMoment, "
        "scope_in/scopeIn, scope_out/scopeOut, quality_bar/qualityBar/quality, "
        "done_feels_like/doneFeelsLike/done, assumptions. First-present precedence; "
        "done_means is additional detail, never a primary Done alias. Other finite JSON "
        "fields and their full words remain retained; these fields are not approval/proof. "
        "Web Align asks missing audience/action, tone, 1Ã¢â‚¬â€œ3 references/adjectives, colours/fonts "
        "and sections. Record taste={audience,tone,references,brand,requiredSections,sourceRef}; "
        "map into users/requirements, quality_bar and done_feels_like. Show defaults/unknowns "
        "at Lock; never infer consent. Optional designRefs=[{path,type,sha256,provenance,use}] "
        "names existing relative hashed visual files, never instructions or executable code. "
        "URLs grant no browsing/runtime permission; report unviewable/conflicting refs. "
        "Before A, record expansion={mode,reason,upgrades:[{id,text,kind,impact, "
        "disposition:accept|reject|defer}]} with stable unique IDs and quality/Done. "
        "An LLM judges adequacy, never a count. Record qualityCriteria for applicable UX/visual, "
        "performance, maintainability, architecture, observability and failures; research reasons "
        "and choices; openLoop={metric,provenance,stopConditions} agreed at Lock; brownfield "
        "existing behavior/evidence before change planning. All full fields appear in A. "
        "Accepted macro/micro upgrades retain expansionRefs downstream. Exact user words "
        "are interpreted by a bound 1j judgment; no upsert or proposed disposition grants consent. "
        "Legacy compile needs fields.validation (Python argv string array) and requirementFiles "
        "(extra protected relative paths, possibly empty); see compilationMetadata. Initial "
        "vision authoring needs neither."
    ),
    "test": "fields.module: opaque nonblank worker test selector; fields.level: semantic or e2e. "
    "Optional execution={entry:candidate path,runtime:absolute executable,argv:[literal|{path:candidate}|{input:DATA}],testRoots:[paths],rationale:nonblank}; cwd/import/document roots stay candidate-owned.",
    "feature": "Optional baseline={source,behavior,rationale,codeRefs,testRefs,resultRefs}; "
    "all three reference arrays are nonempty revision-bound existing graph nodes.",
    "result": "evidence={itemRef:{id,revision},candidate,source:{reference,realProvider:boolean,"
    "historical:boolean},observation:null|{reference,outcome:passed|failed},state}. States: awaiting, "
    "failed, stale, integrated, validated live. Missing observation stays awaiting; historical/fake "
    "evidence cannot be validated live. Result descends from itemRef; no acceptance is inferred.",
    "discovery": "source: nonblank provenance reference. Record meaning in text/rationale.",
    "amendment": "sourceRef:{id,revision} discovery/research/result; targetRefs:[{id,revision}] "
    "nonempty upstream targets; proposedChange,rationale nonblank; decisionRefs:[{id,revision}]. "
    "Recording an amendment grants no consent and changes no target.",
    "metric": "definition,unit: nonblank; definition describes measurement, never its achievement.",
    "stop-condition": "metricRefs:[{id,revision}] nonempty; condition: nonblank agreed stopping rule.",
    "iteration": "metricRefs,resultRefs,stopConditionRefs: nonempty revision-bound node arrays. "
    "Results retain measurements in observations. Finishing an iteration is not acceptance.",
    "baton": "decisionRefs revision-bound array; assumptions/openQuestions string arrays; "
    "currentState,nextSafeAction,rationale nonblank. Reference via batonRefs, never a second store.",
}


def _node(kind, node_id, parents, fields=None, text=None):
    return {
        "id": node_id,
        "kind": kind,
        "title": "Reject malformed parser input",
        "text": text or "Parser boundary: reject malformed input before saving any data.",
        "parents": parents,
        "fields": fields or {},
    }


def node_example(kind, semantic=False):
    """Small planning examples, never observations or evidence of acceptance."""
    vision = _node("vision", "V1", [])
    if kind == "vision":
        vision["text"] += "\n\nFull brief: <actual source path>"
        vision["fields"] = {
            "vision": vision["text"],
            "users": ["A person submitting parser input"],
            "first_user_moment": "Submit malformed input and see a clear error.",
            "scope_in": ["Parse input", "Reject malformed input without saving data"],
            "scope_out": ["GUI"],
            "quality_bar": "Keep original data unchanged when input is invalid.",
            "done_feels_like": "A new user submits malformed input, sees a clear error, "
            "and no data is saved.",
            "assumptions": [],
        }
    if semantic:
        vision["fields"]["planMode"] = "semantic-v1"
        if kind == "vision":
            vision["fields"].update(
                targets={"parser": {"entry": "hello.py", "argv": []}},
                scenarios={
                    "schema": "seldon/scenarios-v1",
                    "scenarios": [
                        dict(
                            id="S1",
                            requirementRefs=["R1"],
                            contractRefs=[],
                            setup=dict(
                                dirs=[],
                                files=[dict(path="{data}/input.json", utf8="{")],
                                snapshots=[dict(id="before", path="{data}/input.json")],
                            ),
                            actions=[
                                dict(
                                    id="reject",
                                    cli=dict(
                                        target="parser",
                                        operation="reject malformed input",
                                        argv=[{"input": "{data}/input.json"}],
                                    ),
                                )
                            ],
                            observe=[
                                dict(action="reject", exitEquals=1),
                                dict(path="{data}/input.json", bytesUnchangedFrom="before"),
                            ],
                            verdict="all",
                        )
                    ],
                },
            )
    feature = _node("feature", "F1", ["V1"])
    requirement = _node(
        "requirement",
        "R1",
        ["F1"],
        text="Parser boundary: malformed input must be rejected without saving data. "
        "Planned semantic verification: submit truncated JSON and assert no record is saved. "
        "Credible bug: parser saves partial data before raising. Existing happy-path "
        "proof misses this rejection and rollback behavior; add the negative case.",
    )
    examples = {
        "vision": ([], vision),
        "feature": ([vision], feature),
        "story": ([vision, feature], _node("story", "S1", ["F1"])),
        "requirement": ([vision, feature], requirement),
        "acceptance": ([vision], _node("acceptance", "A1", ["V1"])),
        "decision": ([vision], _node("decision", "D1", ["V1"])),
        "design": ([vision, feature, requirement], _node("design", "G1", ["R1"])),
        "bead": (
            [vision, feature, requirement],
            _node(
                "bead",
                "B1",
                ["R1"],
                {k: example for k, (example, _) in bead_contract(semantic).items()},
            ),
        ),
        "test": (
            [vision, feature, requirement],
            _node(
                "test",
                "T1",
                ["R1"],
                {
                    "module": "test_parser",
                    "level": "semantic",
                    "execution": dict(
                        entry="test_parser.py",
                        runtime=sys.executable,
                        argv=[],
                        testRoots=["test_parser.py"],
                        rationale="Observe parser rejection and unchanged data",
                    ),
                },
            ),
        ),
    }
    if kind in examples:
        prerequisites, example = examples[kind]
        if semantic and kind == "bead":
            test = node_example("test", True)[1]
            prerequisites.append(test)
            example["parents"] = [test["id"]]
            example["fields"]["semanticPhase"] = "build"
        snapshot = Spine(
            dict(
                schema="codex-harness/spine-v1",
                name="Schema example",
                nodes=[*prerequisites, example],
            )
        )
        if semantic:
            semantic_spec(snapshot, draft=True)
        return prerequisites, example
    # Templates bind real IDs/revisions at authoring time; never invent observations.
    parents = ["V1"] if "vision" in ALLOWED_PARENTS[kind] else ["R1"]
    return [vision, feature, requirement], _node(kind, "N1", parents)


def validate_input(value, schema: dict, label="arguments") -> None:
    expected = schema["type"]
    types = {
        "string": isinstance(value, str),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "null": value is None,
    }
    allowed = expected if isinstance(expected, list) else [expected]
    if not any(types[kind] for kind in allowed):
        raise ValueError(
            f"{label} must be {expected}; allowed keys {sorted(schema.get('properties', {}))}"
        )
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{label} must be one of {schema['enum']}")
    if isinstance(value, str) and schema.get("minLength") and not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    if isinstance(value, dict) and "properties" in schema:
        missing = set(schema["required"]) - value.keys()
        extra = value.keys() - schema["properties"].keys()
        if missing or extra:
            raise ValueError(
                f"{label}: missing {sorted(missing)}, unexpected {sorted(extra)}; allowed keys {sorted(schema['properties'])}"
            )
        for key, item in value.items():
            validate_input(item, schema["properties"][key], f"{label}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{label} needs at least {schema['minItems']} item(s)")
        for item in value:
            validate_input(item, schema["items"], label)


def inspect_node(
    self,
    node_id: str,
    *,
    body: bool = False,
    test: str | None = None,
    include_source: bool = False,
) -> dict:
    """Inspect linked protected Python tests using AST only, never imports."""
    from codex_harness.core.plan_store import PlanStoreError

    if body and not test:
        raise ValueError("--body requires --test <name>")
    nodes = {node["id"]: node for node in self.list_nodes()}
    if node_id not in nodes:
        raise ValueError(f"Unknown node: {node_id}; use harness_nodes for available IDs")
    node = nodes[node_id]
    result = {
        **node,
        "children": sorted(key for key, child in nodes.items() if node_id in child["parents"]),
        "testFiles": [],
    }
    if node["kind"] not in ("requirement", "test"):
        return result
    linked = {node_id}
    while True:
        descendants = linked | {
            key for key, child in nodes.items() if linked.intersection(child["parents"])
        }
        if descendants == linked:
            break
        linked = descendants
    modules = {nodes[key]["fields"]["module"] for key in linked if nodes[key]["kind"] == "test"}
    template = (self.workspace / "template").resolve()
    protected = set()
    from codex_harness.core.graph import test_dir

    for vision in (item for item in nodes.values() if item["kind"] == "vision"):
        fields = vision["fields"]
        for name in (
            *fields.get("requirementFiles", []),
            test_dir({"validation": fields.get("validation", [])}),
        ):
            path = (template / name).resolve()
            if not path.is_relative_to(template):
                raise ValueError(f"Protected file outside template: {name}")
            candidates = path.rglob("*.py") if path.is_dir() else [path]
            for candidate in candidates:
                if not candidate.resolve().is_relative_to(template):
                    raise ValueError(f"Protected file outside template: {candidate}")
                if candidate.suffix == ".py" and candidate.is_file():
                    protected.add(candidate)
    remaining = 8 * 1024
    source_remaining = 256 * 1024
    found = False
    for path in sorted(protected):
        relative = path.relative_to(template).as_posix()
        dotted = relative[:-3].replace("/", ".")
        if not any(
            module in (relative, path.name, dotted, path.stem) or dotted.endswith("." + module)
            for module in modules
        ):
            continue
        raw = path.read_bytes()
        source = raw.decode("utf-8-sig")
        tree = ast.parse(source, filename=relative)
        functions = []
        for item in tree.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                functions.append((item.name, item))
            elif isinstance(item, ast.ClassDef):
                functions.extend(
                    (f"{item.name}.{method.name}", method)
                    for method in item.body
                    if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
                )
        functions = [(name, item) for name, item in functions if item.name.startswith("test")]
        entry = {
            "path": relative,
            "functions": [name for name, _ in functions],
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if include_source:
            source_remaining -= len(raw)
            if source_remaining < 0:
                raise PlanStoreError("plan-test-source-too-large", "256 KiB source budget")
            entry["source"] = source
        if body:
            selected = [(name, item) for name, item in functions if test in (name, item.name)]
            if len(selected) > 1:
                raise ValueError(f"Ambiguous test {test} in {relative}; use Class.test_name")
            if selected:
                found = True
                name, item = selected[0]
                start = min([item.lineno] + [d.lineno for d in item.decorator_list])
                text = "".join(source.splitlines(keepends=True)[start - 1 : item.end_lineno])
                encoded = text.encode("utf-8")
                clipped = encoded[:remaining].decode("utf-8", errors="ignore")
                remaining -= len(clipped.encode("utf-8"))
                entry.update(
                    test=name,
                    body=clipped,
                    truncated=len(encoded) > len(clipped.encode("utf-8")),
                )
        result["testFiles"].append(entry)
    if body and not found:
        raise ValueError(f"Test not found in linked protected files: {test}")
    return result


def schema_help(tools, *, tool=None, kind=None, include_teaching=False, semantic=False):
    """Expose the current three-command surface and actual canonical content shapes."""
    selected = tool or ("harness_loop_answer" if kind else "harness_plan_start")
    contract = next((t for t in tools if t["name"] == selected), None)
    if contract is None:
        raise ValueError(
            "Unknown tool; use harness_plan_start, harness_loop_answer or harness_steer"
        )
    result = dict(
        tool=selected,
        inputSchema=deepcopy(contract["inputSchema"]),
        tools=[t["name"] for t in tools],
        authority=contract["description"],
        semantics="Canonical content and rationale remain domain-open. Workers author code/tests. Actual user words keep native/chat provenance; no model authenticates a human.",
    )
    if kind is not None:
        prerequisites, node = node_example(kind, semantic)
        result.update(
            prerequisiteNodes=prerequisites,
            node=node,
            fieldRules=FIELD_RULES.get(kind, "Canonical fields"),
            example=dict(
                option="draft", rationale="Author the requested meaning", content=dict(nodes=[node])
            ),
        )
    if selected == "harness_loop_answer":
        from codex_harness.core.run_content import CONTENT_SCHEMA

        result.update(
            contentSchema=CONTENT_SCHEMA, judgmentResponse=deepcopy(JUDGMENT_RESPONSE_SCHEMA)
        )
    return result
