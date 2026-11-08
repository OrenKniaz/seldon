"""Domain-open trace records and retained CLI/web observation adapters; no proof authority."""

import base64
import json
from pathlib import Path

LIMIT = 16384
ACTION = (
    "exitEquals stdoutEquals stdoutJsonEquals stdoutEndsWith stdoutContains "
    "stdinOpenAtExit elapsedAtMost stderrNonempty stderrEquals stderrContains"
).split()
FILE = "fileJsonEquals fileBytesEquals fileAbsent bytesUnchangedFrom".split()
WEB = (
    "noHorizontalOverflow consoleErrors externalRequests "
    "textContrastAA headingStructure reducedMotionRespected"
).split()

EVIDENCE_STATES = ("awaiting", "failed", "stale", "integrated", "validated live")
TRACE_LINKS = {
    "decisionRefs": {"decision"}, "researchRefs": {"research"},
    "metricRefs": {"metric"}, "stopConditionRefs": {"stop-condition"},
    "iterationRefs": {"iteration"}, "batonRefs": {"baton"},
    "evidenceRefs": {"result"}, "codeRefs": {"code"},
    "testRefs": {"test"}, "resultRefs": {"result"},
}


def trace_refusal(node_id, message, *sections):
    error = ValueError(message)
    error.refused_sections = {node_id: list(sections)}
    error.editable_fields = {node_id: list(sections)}
    return error


def validate_trace_fields(node, nodes, previous=None):
    """Validate shape/identity, never product eligibility or semantic quality."""
    def refusal(message, *labels):
        return trace_refusal(node["id"], message, *("fields." + label for label in labels))

    def text(value, label):
        if not isinstance(value, str) or not value.strip():
            raise refusal(f"{node['id']}.fields.{label}: supply a nonblank trace value", label)

    def refs(values, kinds=None, label="references"):
        if not isinstance(values, list):
            raise refusal(f"{node['id']}.fields.{label}: supply revision-bound references as an array", label)
        seen = set()
        for ref in values:
            if not isinstance(ref, dict) or set(ref) != {"id", "revision"}:
                raise refusal(f"{node['id']}.fields.{label}: bind an ID and revision", label)
            key, revision = ref["id"], ref["revision"]
            text(key, label)
            if (key not in nodes or key in seen or kinds and nodes[key]["kind"] not in kinds
                or not isinstance(revision, str) or len(revision) != 64
                or any(c not in "0123456789abcdef" for c in revision)):
                raise refusal(f"{node['id']}.fields.{label}: bind an existing unique ID of kind {sorted(kinds) if kinds else 'any'} and sha256 revision; rejected {key!r}", label)
            seen.add(key)

    f, kind = node["fields"], node["kind"]
    for key, kinds in TRACE_LINKS.items():
        if key in f:
            refs(f[key], kinds, key)
    if "baseline" in f:
        if kind != "feature":
            raise refusal("Existing-baseline provenance belongs to an ordinary feature", "baseline")
        baseline = f["baseline"]
        required = ("source", "behavior", "rationale", "codeRefs", "testRefs", "resultRefs")
        try:
            fields(baseline, required, required, f"{node['id']}.fields.baseline")
        except ValueError as error:
            raise refusal(str(error), "baseline") from error
        for key in ("source", "behavior", "rationale"):
            text(baseline[key], "baseline." + key)
        for key in ("codeRefs", "testRefs", "resultRefs"):
            refs(baseline[key], TRACE_LINKS[key], "baseline." + key)
            if not baseline[key]:
                raise refusal(f"Baseline {key} must map observed existing behavior before changes", "baseline." + key)
        for ref in baseline["resultRefs"]:
            evidence = nodes[ref["id"]]["fields"].get("evidence")
            if not isinstance(evidence, dict) or evidence.get("observation") is None:
                prior = next((n for n in (previous or {}).get("nodes", []) if n["id"] == ref["id"]), {})
                if prior.get("fields", {}).get("evidence", {}).get("observation") is not None:
                    raise trace_refusal(ref["id"], "Baseline evidence requires its retained observation; restore the result", "fields.evidence.observation", "fields.evidence.state")
                raise refusal("Baseline resultRefs require observed evidence before change planning", "baseline.resultRefs")
    if kind == "result":
        e = f.get("evidence")
        required = ("itemRef", "candidate", "source", "observation", "state")
        if not isinstance(e, dict) or any(key not in e for key in required):
            raise refusal("Result requires evidence itemRef/candidate/source/observation/state", "evidence")
        refs([e["itemRef"]], label="evidence.itemRef")
        subject = e["itemRef"]["id"]
        if nodes[subject]["kind"] == "result":
            raise refusal("Evidence must bind a product/plan item, not another result", "evidence.itemRef")
        text(e["candidate"], "evidence.candidate")
        source = e["source"]
        if not isinstance(source, dict) or any(k not in source for k in
                                              ("reference", "realProvider", "historical")):
            raise refusal("Evidence source requires reference, realProvider and historical", "evidence.source")
        text(source["reference"], "evidence.source.reference")
        if any(type(source[k]) is not bool for k in ("realProvider", "historical")):
            raise refusal("Evidence source provenance flags must be explicit booleans", "evidence.source")
        state, observation = e["state"], e["observation"]
        if not isinstance(state, str) or state not in EVIDENCE_STATES:
            raise refusal("Use awaiting, failed, stale, integrated or validated live", "evidence.state")
        if observation is None:
            if state != "awaiting":
                raise refusal("Missing observation stays awaiting", "evidence.state", "evidence.observation")
        else:
            if not isinstance(observation, dict) or observation.get("outcome") not in ("passed", "failed"):
                raise refusal("Observation requires a recorded passed/failed outcome", "evidence.observation")
            text(observation.get("reference"), "evidence.observation.reference")
            expected = {"integrated": "passed", "validated live": "passed", "failed": "failed"}.get(state)
            if expected and observation["outcome"] != expected:
                raise refusal(f"{state} evidence requires an observed {expected} outcome", "evidence.state", "evidence.observation")
        if state == "validated live" and (not source["realProvider"] or source["historical"]):
            raise refusal("Historical/fake-provider evidence cannot establish validated live", "evidence.state")
    elif kind == "amendment":
        required = ("sourceRef", "targetRefs", "proposedChange", "rationale", "decisionRefs")
        if any(key not in f for key in required):
            raise refusal("Amendment requires discovery/source, target revisions, change, rationale and decisions", *[key for key in required if key not in f])
        refs([f["sourceRef"]], {"discovery", "research", "result"}, "sourceRef")
        refs(f["targetRefs"], label="targetRefs")
        if not f["targetRefs"]:
            raise refusal("Amendment must name its affected upstream item revisions", "targetRefs")
        text(f["proposedChange"], "proposedChange")
        text(f["rationale"], "rationale")
    elif kind == "discovery":
        text(f.get("source"), "source")
    elif kind == "metric":
        text(f.get("definition"), "definition")
        text(f.get("unit"), "unit")
    elif kind in ("stop-condition", "iteration"):
        required = ("metricRefs",) if kind == "stop-condition" else ("metricRefs", "resultRefs", "stopConditionRefs")
        if any(not f.get(key) for key in required):
            missing = [key for key in required if not f.get(key)]
            raise refusal(f"{node['id']}.fields: {kind} requires nonempty {missing}", *missing)
        if kind == "stop-condition":
            text(f.get("condition"), "condition")
    elif kind == "baton":
        for key in ("currentState", "nextSafeAction", "rationale"):
            text(f.get(key), key)
        for key in ("assumptions", "openQuestions", "decisionRefs"):
            if key not in f or not isinstance(f[key], list):
                raise refusal(f"Baton requires {key}", key)
        for key in ("assumptions", "openQuestions"):
            for value in f[key]: text(value, key)
    if kind == "test" and "execution" in f:
        execution, section = f["execution"], "execution"
        try:
            fields(execution, ("entry", "module", "runtime", "argv", "rationale", "testRoots"), ("argv", "rationale", "testRoots"), f"{node['id']}.fields.execution")
            section = "execution.testRoots"
            if not isinstance(execution["testRoots"], list) or not execution["testRoots"]:
                raise ValueError("Declare complete testRoots (modules, helpers and fixtures)")
            from codex_harness.core.graph import _file_path, _string
            for root in execution["testRoots"]:
                _file_path(_string(root))
            from codex_harness.core.semantic_runner import launch
            section = "execution"
            launch({k: v for k, v in execution.items() if k not in ("rationale", "testRoots")}, Path.cwd(), position=f"{node['id']}.fields.execution")
        except ValueError as error:
            raise refusal(f"{node['id']}.fields.{section}: {error}", section) from error
        text(execution["rationale"], "execution.rationale")


def http_request(v, *, target=False):
    keys = ["method", "path", "json", "statusEquals", "jsonEquals"]
    fields(v, [*keys, *(["target"] if target else [])], ["method", "path", "statusEquals", *(["target"] if target else [])])
    if target and (not isinstance(v["target"], str) or not v["target"]):
        raise ValueError("HTTP requires a nonblank target ID")
    if (not isinstance(v["method"], str) or not v["method"]
            or not isinstance(v["path"], str) or not v["path"].startswith("/") or v["path"].startswith("//")
            or type(v["statusEquals"]) is not int or not 100 <= v["statusEquals"] <= 599):
        raise ValueError("HTTP requires a method, loopback-relative path and statusEquals:100..599")


def operation(v):
    if not isinstance(v["operation"], str) or not v["operation"] or "\n" in v["operation"]:
        raise ValueError("Declare a literal operation marker")


def web_journey(v, targets, *, draft=False, label="vision.fields.scenarios.scenarios.actions.web"):
    fields(
        v,
        ("target", "operation", "viewport", "reducedMotion", "steps"),
        ("target", "operation", "viewport", "reducedMotion", "steps"), label,
    )
    operation(v)
    if (
        type(v["target"]) is not str
        or (v["target"] in targets or not draft)
        and targets.get(v["target"], {}).get("kind") != "web"
    ):
        raise ValueError("Web journey requires a web target")
    fields(v["viewport"], ("width", "height"), ("width", "height"), f"{label}.viewport")
    if any(type(v["viewport"][k]) is not int or v["viewport"][k] <= 0
           for k in ("width", "height")):
        raise ValueError("Declare positive integer viewport dimensions")
    if v["reducedMotion"] not in ("reduce", "no-preference"):
        raise ValueError("Declare a reducedMotion preference")
    steps = array(v["steps"])
    if not steps or not isinstance(steps[0], dict) or steps[0].get("op") != "open":
        raise ValueError("Journey must start with open")
    step_ids = set()
    for index, step in enumerate(steps):
        fields(step, ("op", "locator", "key", "fragment", "request", "value", "id", "expectedErrors"), ("op",), f"{label}.steps[{index}]")
        if "id" in step or "expectedErrors" in step:
            if not isinstance(step.get("id"), str) or not step["id"].strip() or step["id"] in step_ids:
                raise ValueError("Expected errors belong to one step with a unique nonblank id")
            step_ids.add(step["id"])
        for response in array(step.get("expectedErrors", [])):
            fields(response, ("method", "path", "statusEquals"), ("method", "path", "statusEquals"))
            http_request(response)
        step = {k: v for k, v in step.items() if k not in {"id", "expectedErrors"}}
        if step["op"] == "request":
            fields(step, ("op", "request"), ("op", "request"))
            http_request(step["request"])
            continue
        if step["op"] == "fill":
            fields(step, ("op", "locator", "value"), ("op", "locator", "value"))
            if not isinstance(step["value"], str):
                raise ValueError("Fill requires a string value")
        op = step["op"]
        operand = {
            "open": None,
            "assertVisible": "locator",
            "assertHidden": "locator",
            "focus": "locator", "fill": "locator",
            "click": "locator",
            "key": "key",
            "assertFocus": "locator",
            "assertFragment": "fragment",
        }
        if op not in operand or set(step) != {"op", *([operand[op]] if operand[op] else []), *(["value"] if op == "fill" else [])}:
            raise ValueError(f"Unknown web step or operands: {step!r}; use op/operand shapes {operand!r}")
        if op == "open" and index:
            raise ValueError("Open occurs once at the start")
        if "locator" in step:
            loc = step["locator"]
            fields(loc, ("text", "selector", "role", "name"), label=f"{label}.steps[{index}].locator")
            if set(loc) not in ({"text"}, {"selector"}, {"role", "name"}) or any(
                type(x) is not str or not x or len(x) > 256 for x in loc.values()
            ):
                raise ValueError("Use one text, CSS selector, or role/name locator")
            if "selector" in loc and ">>" in loc["selector"]:
                raise ValueError("Use plain CSS selectors without locator engines")
        if op == "key" and step["key"] not in ("Tab", "Enter", "Space", "Escape"):
            raise ValueError("Supported keys: Tab/Enter/Space/Escape")
        if op == "assertFragment" and (
            type(step["fragment"]) is not str or not step["fragment"].startswith("#")
        ):
            raise ValueError("Declare a fragment beginning with #")

def fields(value, allowed, required=(), label="fields"):
    if not isinstance(value, dict):
        raise ValueError(f"{label}: expected object; allowed keys {sorted(allowed)}; use harness_upsert_node")
    unknown, missing = set(value) - set(allowed), set(required) - value.keys()
    if unknown or missing:
        raise ValueError(
            f"{label}: unknown {sorted(unknown)}, missing {sorted(missing)}; "
            f"allowed keys {sorted(allowed)}; use harness_upsert_node"
        )


def path(root, name, data=None):
    reference = name
    if isinstance(name, str) and name.startswith("{data}/"):
        root, name = data or root, name[7:]
    if type(name) is not str or not name or "\\" in name or ":" in name:
        raise ValueError(f"Path {reference!r}: use a relative sandbox path without backslashes or drive prefixes")
    if name.startswith("/") or any(p.endswith((".", " ")) for p in name.split("/") if p != "." or root != data):
        raise ValueError(f"Path {reference!r}: use canonical sandbox paths without trailing dots/spaces or traversal")
    raw = Path(root).absolute() / name
    if any(p.is_symlink() or p.is_junction() for p in (raw, *raw.parents)):
        raise ValueError(f"Path {reference!r}: linked ancestors are forbidden; use an unlinked confined path")
    resolved = raw.resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError(f"Path {reference!r}: resolves outside the sandbox; use a confined relative path")
    return resolved


def array(value, maximum=32):
    if type(value) is not list or len(value) > maximum:
        raise ValueError("Use a finite JSON array")
    return value


def content(f):
    keys = set(f) & {"utf8", "base64"}
    if len(keys) != 1 or type(f[next(iter(keys))]) is not str:
        raise ValueError("Setup content must be exactly one utf8 or base64 string")
    return f["utf8"].encode() if "utf8" in f else base64.b64decode(f["base64"], validate=True)


def validate(document, product, targets, *, require_entries=True, draft=False, planning=False):
    from codex_harness.core.candidate_delivery import RESERVED_ROOTS
    from codex_harness.core.semantic_runner import launch
    import sys
    if len(json.dumps([document, targets], allow_nan=False).encode()) > 65536:
        raise ValueError("Input exceeds 64KiB")
    if type(targets) is not dict:
        raise ValueError("Declare target bindings")
    for name, binding in targets.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(binding, dict):
            raise ValueError("Target bindings require nonblank IDs and finite JSON objects")
    if draft and document is None:
        return []
    fields(document, ("schema", "scenarios"), ("schema", "scenarios"), "vision.fields.scenarios")
    scenarios = document["scenarios"]
    if (document["schema"] != "seldon/scenarios-v1" or not isinstance(scenarios, list)
            or not scenarios and not planning):
        raise ValueError("Use seldon/scenarios-v1 with a scenarios array")
    ids = set()
    for index, s in enumerate(scenarios):
        label = f"vision.fields.scenarios.scenarios[{index}]"
        keys = "id requirementRefs contractRefs setup actions observe verdict".split()
        fields(s, [*keys, "evidenceTier", "validationMessage", "testRef", "rationale"], keys, label)
        if type(s["id"]) is not str or not s["id"] or s["id"] in ids or s["verdict"] != "all":
            raise ValueError(f"{label}: use unique id and verdict=all; allowed keys {keys}; use harness_upsert_node")
        ids.add(s["id"])
        for key in ("requirementRefs", "contractRefs"):
            if not isinstance(s[key], list) or any(
                type(r) is not str or not r for r in s[key]
            ):
                raise ValueError("Declare requirement and contract references")
        s.pop("evidenceTier", None)
        s.pop("validationMessage", None)
        fields(s["setup"], ("dirs", "files", "snapshots"), label=f"{label}.setup")
        for f in array(s["setup"].get("files", [])):
            fields(f, ("path", "utf8", "base64"), ("path",), f"{label}.setup.files")
            content(f)
        for name in [*array(s["setup"].get("dirs", [])), *(f["path"] for f in s["setup"].get("files", []))]:
            if not isinstance(name, str) or not name.startswith("{data}/") or any(p.casefold() in RESERVED_ROOTS for p in Path(name[7:]).parts[:1]):
                raise ValueError(f"Setup path {name!r}: write only to run-owned DATA using {{data}}/relative-path, never product or reserved roots")
            path(product, name)
        snapshots = set()
        for f in array(s["setup"].get("snapshots", [])):
            fields(f, ("id", "path"), ("id", "path"), f"{label}.setup.snapshots")
            path(product, f["path"])
            if type(f["id"]) is not str or f["id"] in snapshots:
                raise ValueError("Use unique snapshot ids")
            snapshots.add(f["id"])
        try:
            if not s["requirementRefs"]:
                raise ValueError("Link requirementRefs to their owning nodes")
            actions, web_actions, http_actions = set(), set(), set()
            for a in array(s["actions"], 16):
                fields(a, ("id", "cli", "web", "process", "cwd", "http", "file"), ("id",), f"{label}.actions")
                if "file" in a:
                    raise ValueError("File actions need a worker-authored verifier")
                if (
                    type(a["id"]) is not str
                    or a["id"] in actions
                    or len(set(a) & {"cli", "web", "http", "process"}) != 1
                ):
                    raise ValueError("Use unique CLI actions")
                if "web" in a or "http" in a:
                    kind = "web" if "web" in a else "http"
                    fields(a, ("id", kind), ("id", kind), f"{label}.actions")
                    v = a[kind]
                    if kind == "web":
                        web_journey(v, targets, draft=draft, label=f"{label}.actions.web")
                    else:
                        http_request(v, target=True)
                    binding = targets.get(v["target"], {})
                    if kind == "web" and "server" not in binding:
                        launch(dict(entry=binding.get("entry"), argv=[]), product, position="document")
                    if kind == "http" or "server" in binding:
                        if "server" not in binding:
                            raise ValueError("HTTP observation requires target.server with argv, readyPath, readyWithin")
                        server = binding["server"]
                        launch({k: v for k, v in server.items() if k not in ("readyPath", "readyWithin")}, product, position="server")
                        http_request(dict(method="GET", path=server["readyPath"], statusEquals=200))
                        if type(server["readyWithin"]) not in (int, float) or not 0 < server["readyWithin"] <= 10:
                            raise ValueError("Declare server.readyWithin in (0,10] seconds")
                    (web_actions if kind == "web" else http_actions).add(a["id"])
                    actions.add(a["id"])
                    continue
                actions.add(a["id"])
                if "process" in a:
                    launch(a["process"], product, position="process", cwd=a.get("cwd"))
                    continue
                v = a["cli"]
                fields(v, ("target", "operation", "argv", "stdin"), ("target", "operation", "argv"), f"{label}.actions.cli")
                if "stdin" in v:
                    if set(v["stdin"]) == {"holdOpen", "exitWithin"}:
                        if (
                            v["stdin"]["holdOpen"] is not True
                            or type(v["stdin"]["exitWithin"]) is not int
                            or not 1 <= v["stdin"]["exitWithin"] <= 20
                        ):
                            raise ValueError(
                                "Held-open stdin needs holdOpen:true and exitWithin:1..20 seconds"
                            )
                    else:
                        fields(v["stdin"], ("utf8", "base64"), label=f"{label}.actions.cli.stdin")
                        content(v["stdin"])
                if type(v["target"]) is not str or not draft and v["target"] not in targets:
                    raise ValueError("Action references an unknown target")
                if not planning:
                    binding = targets[v["target"]]
                    launch(dict(binding, runtime=sys.executable), product, extra=v["argv"], cwd=a.get("cwd"))
                operation(v)
            if not actions or not array(s["observe"]):
                raise ValueError("Declare product actions and finite observations")
            for o in s["observe"]:
                if isinstance(o, dict) and o.get("action") in http_actions:
                    fields(o, ("action",), ("action",))
                    continue
                if isinstance(o, dict) and o.get("action") in web_actions:
                    fields(o, ("action", *WEB), ("action", *WEB), f"{label}.observe")
                    if any(
                        type(o[k])
                        is not (int if k in ("consoleErrors", "externalRequests") else bool)
                        or o[k] != (0 if k in ("consoleErrors", "externalRequests") else True)
                        for k in WEB
                    ):
                        raise ValueError("Web gates require true or zero error/request counts")
                    continue
                fields(o, ["action", "path", *ACTION, *FILE], label=f"{label}.observe")
                if "action" in o and "path" in o:
                    raise ValueError(
                        "An observation targets one action or one path, not both. "
                        "File observations "
                        "are checked after the scenario's last action; to check files between "
                        "actions, split into separate scenarios."
                    )
                reference = "action" if "action" in o else "path"
                allowed = ACTION if reference == "action" else FILE
                fields(o, [reference, *allowed], (reference,), f"{label}.observe")
                if len(o) < 2 or (reference == "action" and o[reference] not in actions):
                    raise ValueError("Observation needs a known reference and expectations")
                if reference == "path":
                    path(product, o[reference])
                for key, value in o.items():
                    kind = {
                        "exitEquals": int,
                        "elapsedAtMost": int,
                        "stdinOpenAtExit": bool,
                        "stderrNonempty": bool,
                        "fileAbsent": bool,
                    }.get(key, str)
                    if (
                        key not in ("stdoutJsonEquals", "fileJsonEquals")
                        and type(value) is not kind
                    ):
                        raise ValueError("Observation has the wrong JSON value type")
                    if key == "bytesUnchangedFrom" and value not in snapshots:
                        raise ValueError("Observation references an unknown snapshot")
            if any(sum(o.get("action") == a for o in s["observe"]) != 1 for a in web_actions | http_actions):
                raise ValueError("Each web journey requires exactly one complete gate observation")
        except ValueError as error:
            s["evidenceTier"] = "scenario: unvalidated"
            s["validationMessage"] = str(error) + (
                "; pending judgment. Supply worker-authored execution entry/runtime/argv for this test "
                "or an observation adapter; no product failure or proof credit is inferred."
            )
    return scenarios
