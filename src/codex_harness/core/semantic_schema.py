"""Closed data vocabulary for the experimental Python CLI slice; no proof authority."""

import base64
import json
import os
import sys
from pathlib import Path

LIMIT = 16384
ACTION = (
    "exitEquals stdoutEquals stdoutJsonEquals stderrNonempty stderrEquals stderrContains".split()
)
FILE = "fileJsonEquals fileBytesEquals fileAbsent bytesUnchangedFrom".split()


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
        if type(value) is str and not any(c in value for c in ("/", "\\", ":", "\x00", "..")):
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


def validate(document, product, targets, *, require_entries=True):
    if len(json.dumps([document, targets], allow_nan=False).encode()) > 65536:
        raise ValueError("Input exceeds 64KiB")
    fields(document, ("schema", "scenarios"), ("schema", "scenarios"))
    scenarios = array(document["scenarios"], 16)
    if document["schema"] != "seldon/scenarios-v1" or not scenarios:
        raise ValueError("Use seldon/scenarios-v1 with 1..16 scenarios")
    if type(targets) is not dict:
        raise ValueError("Declare target bindings")
    for binding in targets.values():
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
            if not f["path"].startswith("data/"):
                raise ValueError("Setup writes only data/ fixtures")
            content(f)
        for f in array(s["setup"].get("snapshots", [])):
            fields(f, ("id", "path"), ("id", "path"))
            path(product, f["path"])
            if type(f["id"]) is not str or f["id"] in snapshots:
                raise ValueError("Use unique snapshot ids")
            snapshots.add(f["id"])
        actions = set()
        for a in array(s["actions"], 16):
            fields(a, ("id", "cli", "cwd", "http", "file"), ("id",))
            if "http" in a or "file" in a:
                raise ValueError(
                    "HTTP/file-only actions unsupported in v1 (unsupported in semantic-v1)"
                )
            if type(a["id"]) is not str or a["id"] in actions or not {"id", "cli"} <= set(a):
                raise ValueError("Use unique CLI actions")
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
                fields(v["stdin"], ("utf8", "base64"))
                content(v["stdin"])
            if type(v["target"]) is not str or v["target"] not in targets:
                raise ValueError("Action references an unknown target")
            if type(v["operation"]) is not str or not v["operation"] or "\n" in v["operation"]:
                raise ValueError("Declare a literal operation marker")
            argv(v["argv"], product, path(product, a["cwd"]) if "cwd" in a else product)
        if not actions or not array(s["observe"]):
            raise ValueError("Declare product actions and finite observations")
        for o in s["observe"]:
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
                kind = {"exitEquals": int, "stderrNonempty": bool, "fileAbsent": bool}.get(key, str)
                if key not in ("stdoutJsonEquals", "fileJsonEquals") and type(value) is not kind:
                    raise ValueError("Observation has the wrong JSON value type")
                if key == "bytesUnchangedFrom" and value not in snapshots:
                    raise ValueError("Observation references an unknown snapshot")
    return scenarios
