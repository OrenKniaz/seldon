"""Closed data vocabulary for Python CLI and local web observations; no proof authority."""

import base64
import json
import os
import sys
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


def web_journey(v, targets, *, draft=False):
    fields(
        v,
        ("target", "operation", "viewport", "reducedMotion", "steps"),
        ("target", "operation", "viewport", "reducedMotion", "steps"),
    )
    if (
        type(v["target"]) is not str
        or (v["target"] in targets or not draft)
        and targets.get(v["target"], {}).get("kind") != "web"
    ):
        raise ValueError("Web journey requires a web target")
    fields(v["viewport"], ("width", "height"), ("width", "height"))
    if tuple(v["viewport"][k] for k in ("width", "height")) not in ((375, 812), (1440, 900)) or any(
        type(v["viewport"][k]) is not int for k in ("width", "height")
    ):
        raise ValueError("Web viewports are 375x812 and 1440x900")
    if v["reducedMotion"] not in ("reduce", "no-preference"):
        raise ValueError("Declare a reducedMotion preference")
    steps = array(v["steps"])
    if not steps or steps[0] != {"op": "open"}:
        raise ValueError("Journey must start with open")
    for index, step in enumerate(steps):
        fields(step, ("op", "locator", "key", "fragment"), ("op",))
        op = step["op"]
        operand = {
            "open": None,
            "assertVisible": "locator",
            "assertHidden": "locator",
            "focus": "locator",
            "click": "locator",
            "key": "key",
            "assertFocus": "locator",
            "assertFragment": "fragment",
        }
        if op not in operand or set(step) != {"op", *([operand[op]] if operand[op] else [])}:
            raise ValueError("Unknown web step or operands")
        if op == "open" and index:
            raise ValueError("Open occurs once at the start")
        if "locator" in step:
            loc = step["locator"]
            fields(loc, ("text", "selector", "role", "name"))
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

    if not any(isinstance(step, dict) and step.get("op") == "assertVisible" for step in steps):
        raise ValueError("Web RED requires a positive assertVisible observation in every journey")


def fields(value, allowed, required=()):
    if not isinstance(value, dict):
        raise ValueError(f"Unknown/missing fields; expected an object with {sorted(allowed)}")
    unknown, missing = set(value) - set(allowed), set(required) - value.keys()
    if unknown or missing:
        raise ValueError(
            f"Unknown/missing fields: unknown {sorted(unknown)}, missing {sorted(missing)}; "
            f"allowed {sorted(allowed)}. See harness_schema kind=vision semanticScenarios."
        )


def path(root, name):
    if type(name) is not str or not name or "\\" in name or ":" in name:
        raise ValueError("Use a relative sandbox path")
    if name.startswith("/") or any(p.endswith((".", " ")) for p in name.split("/")):
        raise ValueError("Use canonical sandbox paths without trailing dots/spaces or traversal")
    resolved = (Path(root) / name).resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError("Path resolves outside the product sandbox")
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


def argv(values, root, cwd=None):
    result = []
    for value in array(values, 64):
        if type(value) is str:
            if "\x00" in value:
                raise ValueError("Literal argv cannot contain NUL")
            result.append(value)
        else:
            fields(value, ("path", "relative"), ("path",))
            if "relative" in value and value["relative"] is not True:
                raise ValueError("relative must be true when present")
            resolved = path(root, value["path"])
            result.append(
                os.path.relpath(resolved, root if cwd is None else cwd).replace("\\", "/")
                if value.get("relative")
                else str(resolved)
            )
    return result


def target_entry(binding):
    if "entry" in binding:
        return binding["entry"]
    module = binding["module"]
    if type(module) is not str or not all(part.isidentifier() for part in module.split(".")):
        raise ValueError("Module must name a dotted Python package")
    if module.split(".")[0] in sys.stdlib_module_names:
        raise ValueError("Module targets must not name standard-library packages")
    return module.replace(".", "/") + "/__main__.py"


def validate(document, product, targets, *, require_entries=True, draft=False):
    if len(json.dumps([document, targets], allow_nan=False).encode()) > 65536:
        raise ValueError("Input exceeds 64KiB")
    if type(targets) is not dict:
        raise ValueError("Declare target bindings")
    coverage = {}
    for binding in targets.values():
        if isinstance(binding, dict) and binding.get("kind") == "web":
            fields(binding, ("kind", "entry"), ("kind", "entry"))
            if binding["entry"] != "index.html" or (
                require_entries and not path(product, binding["entry"]).is_file()
            ):
                raise ValueError("Web targets require index.html")
            continue
        fields(binding, ("entry", "module", "argv"), ("argv",))
        if len(set(binding) & {"entry", "module"}) != 1:
            raise ValueError("Declare exactly one entry or module target")
        entry = path(product, target_entry(binding))
        if entry.suffix != ".py" or (require_entries and not entry.is_file()):
            raise ValueError(
                "Declare an existing product Python entry or module package __main__.py"
            )
        if "module" in binding and require_entries:
            count = len(binding["module"].split("."))
            if not all((p / "__init__.py").is_file() for p in list(entry.parents)[:count]):
                raise ValueError("Module package paths require owned __init__.py files")
        argv(binding["argv"], product)
    if draft and document is None:
        return []
    fields(document, ("schema", "scenarios"), ("schema", "scenarios"))
    scenarios = array(document["scenarios"], 16)
    if document["schema"] != "seldon/scenarios-v1" or not scenarios:
        raise ValueError("Use seldon/scenarios-v1 with 1..16 scenarios")
    ids = set()
    for s in scenarios:
        keys = "id requirementRefs contractRefs setup actions observe verdict".split()
        fields(s, keys, keys)
        if type(s["id"]) is not str or not s["id"] or s["id"] in ids or s["verdict"] != "all":
            raise ValueError("Use unique ids and verdict all")
        ids.add(s["id"])
        for key in ("requirementRefs", "contractRefs"):
            if not array(s[key]) or any(type(r) is not str or not r for r in s[key]):
                raise ValueError("Declare requirement and contract references")
        fields(s["setup"], ("dirs", "files", "snapshots"))
        for directory in array(s["setup"].get("dirs", [])):
            resolved = path(product, directory)
            if (
                directory != "data" and not directory.startswith("data/")
            ) or resolved.suffix.lower() == ".py":
                raise ValueError("Setup creates only data/ directories, not Python source paths")
        snapshots = set()
        for f in array(s["setup"].get("files", [])):
            fields(f, ("path", "utf8", "base64"), ("path",))
            if path(product, f["path"]).suffix.lower() == ".py":
                raise ValueError("Setup is data-only; Python source is unsupported")
            if any(b.get("kind") == "web" for b in targets.values()):
                if path(product, f["path"]).suffix.lower() != ".json":
                    raise ValueError(
                        "Web setup writes only finite JSON data, never browser content"
                    )
                json.dumps(json.loads(content(f)), allow_nan=False)
            if not f["path"].startswith("data/"):
                raise ValueError("Setup writes only data/ fixtures")
            content(f)
        for f in array(s["setup"].get("snapshots", [])):
            fields(f, ("id", "path"), ("id", "path"))
            path(product, f["path"])
            if type(f["id"]) is not str or f["id"] in snapshots:
                raise ValueError("Use unique snapshot ids")
            snapshots.add(f["id"])
        actions, web_actions = set(), set()
        for a in array(s["actions"], 16):
            fields(a, ("id", "cli", "web", "cwd", "http", "file"), ("id",))
            if "http" in a or "file" in a:
                raise ValueError(
                    "HTTP/file-only actions unsupported in v1 (unsupported in semantic-v1)"
                )
            if type(a["id"]) is not str or a["id"] in actions or len(set(a) & {"cli", "web"}) != 1:
                raise ValueError("Use unique CLI actions")
            if "web" in a:
                fields(a, ("id", "web"), ("id", "web"))
                web_journey(a["web"], targets, draft=draft)
                v = a["web"]
                if type(v["operation"]) is not str or not v["operation"] or "\n" in v["operation"]:
                    raise ValueError("Declare a literal operation marker")
                coverage.setdefault((v["target"], v["operation"]), set()).add(
                    (v["viewport"]["width"], v["reducedMotion"])
                )
                actions.add(a["id"])
                web_actions.add(a["id"])
                continue
            if "cwd" in a:
                cwd = path(product, a["cwd"])
                created = [path(product, d) for d in s["setup"].get("dirs", [])]
                created += [path(product, f["path"]).parent for f in s["setup"].get("files", [])]
                if not any(cwd == p or cwd in p.parents for p in created):
                    raise ValueError("Action cwd must be a directory created by setup")
            actions.add(a["id"])
            v = a["cli"]
            fields(v, ("target", "operation", "argv", "stdin"), ("target", "operation", "argv"))
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
                    fields(v["stdin"], ("utf8", "base64"))
                    content(v["stdin"])
            if type(v["target"]) is not str or not draft and v["target"] not in targets:
                raise ValueError("Action references an unknown target")
            if targets.get(v["target"], {}).get("kind") == "web":
                raise ValueError("CLI action requires a Python target")
            if type(v["operation"]) is not str or not v["operation"] or "\n" in v["operation"]:
                raise ValueError("Declare a literal operation marker")
            argv(v["argv"], product, path(product, a["cwd"]) if "cwd" in a else product)
        if not actions or not array(s["observe"]):
            raise ValueError("Declare product actions and finite observations")
        for o in s["observe"]:
            if isinstance(o, dict) and o.get("action") in web_actions:
                fields(o, ("action", *WEB), ("action", *WEB))
                if any(
                    type(o[k]) is not (int if k in ("consoleErrors", "externalRequests") else bool)
                    or o[k] != (0 if k in ("consoleErrors", "externalRequests") else True)
                    for k in WEB
                ):
                    raise ValueError("Web gates require true or zero error/request counts")
                continue
            fields(o, ["action", "path", *ACTION, *FILE])
            if "action" in o and "path" in o:
                raise ValueError(
                    "An observation targets one action or one path, not both. File observations "
                    "are checked after the scenario's last action; to check files between "
                    "actions, split into separate scenarios."
                )
            reference = "action" if "action" in o else "path"
            allowed = ACTION if reference == "action" else FILE
            fields(o, [reference, *allowed], (reference,))
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
                if key not in ("stdoutJsonEquals", "fileJsonEquals") and type(value) is not kind:
                    raise ValueError("Observation has the wrong JSON value type")
                if key == "bytesUnchangedFrom" and value not in snapshots:
                    raise ValueError("Observation references an unknown snapshot")
        if any(sum(o.get("action") == a for o in s["observe"]) != 1 for a in web_actions):
            raise ValueError("Each web journey requires exactly one complete gate observation")
    if not draft and any(
        c != {(w, m) for w in (375, 1440) for m in ("reduce", "no-preference")}
        for c in coverage.values()
    ):
        raise ValueError("Each web operation requires both viewports and motion preferences")
    return scenarios
