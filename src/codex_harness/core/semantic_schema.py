"""Closed data vocabulary for the experimental Python CLI slice; no proof authority."""

import json
from pathlib import Path

LIMIT = 16384
ACTION = "exitEquals stdoutEquals stdoutJsonEquals stderrNonempty".split()
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


def argv(values, root):
    result = []
    for value in array(values, 64):
        if type(value) is str and not any(c in value for c in ("/", "\\", ":", "\x00", "..")):
            result.append(value)
        else:
            fields(value, ("path",), ("path",))
            result.append(str(path(root, value["path"])))
    return result


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
        fields(binding, ("entry", "argv"), ("entry", "argv"))
        entry = path(product, binding["entry"])
        if entry.suffix != ".py" or (require_entries and not entry.is_file()):
            raise ValueError("Declare an existing product Python entry")
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
        fields(s["setup"], ("files", "snapshots"))
        snapshots = set()
        for f in array(s["setup"].get("files", [])):
            fields(f, ("path", "utf8"), ("path", "utf8"))
            if path(product, f["path"]).suffix.lower() == ".py":
                raise ValueError("Setup is data-only; Python source is unsupported")
            if not f["path"].startswith("data/") or type(f["utf8"]) is not str:
                raise ValueError("Setup writes only UTF-8 data/ fixtures")
        for f in array(s["setup"].get("snapshots", [])):
            fields(f, ("id", "path"), ("id", "path"))
            path(product, f["path"])
            if type(f["id"]) is not str or f["id"] in snapshots:
                raise ValueError("Use unique snapshot ids")
            snapshots.add(f["id"])
        actions = set()
        for a in array(s["actions"], 16):
            fields(a, ("id", "cli", "http", "file"), ("id",))
            if "http" in a or "file" in a:
                raise ValueError(
                    "HTTP/file-only actions unsupported in v1 (unsupported in semantic-v1)"
                )
            if type(a["id"]) is not str or a["id"] in actions or set(a) != {"id", "cli"}:
                raise ValueError("Use unique CLI actions")
            actions.add(a["id"])
            v = a["cli"]
            fields(v, ("target", "operation", "argv"), ("target", "operation", "argv"))
            if type(v["target"]) is not str or v["target"] not in targets:
                raise ValueError("Action references an unknown target")
            if type(v["operation"]) is not str or not v["operation"] or "\n" in v["operation"]:
                raise ValueError("Declare a literal operation marker")
            argv(v["argv"], product)
        if not actions or not array(s["observe"]):
            raise ValueError("Declare product actions and finite observations")
        for o in s["observe"]:
            fields(o, ["action", "path", *ACTION, *FILE])
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
