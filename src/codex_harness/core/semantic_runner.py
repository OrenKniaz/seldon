"""Experimental Python CLI observations; not canonical admission or GUI acceptance.
Windows Jobs contain normal descendants; trusted code, not an OS security sandbox."""

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen, TimeoutExpired

from codex_harness.core.audit_process import run_audit_process
from codex_harness.core.semantic_schema import LIMIT, argv, content, path, validate

ERRORS = (OSError, ValueError, TypeError, LookupError, TimeoutExpired, RecursionError)
MODULE = "codex_harness.core.semantic_runner"


def read(file, limit=LIMIT):
    if file.is_dir():
        entries, pending = [], [file]
        while pending:
            for p in pending.pop().iterdir():
                if p.is_symlink() or p.is_junction():
                    raise ValueError("Directory snapshots cannot observe links/junctions")
                entries.append(p)
                if len(entries) > 128:
                    raise ValueError("Directory snapshot exceeds128 entries")
                if p.is_dir():
                    pending.append(p)
        return {
            p.relative_to(file).as_posix(): None
            if p.is_dir()
            else read(path(file, p.relative_to(file).as_posix()), limit)
            for p in entries
        }
    with file.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("File/output exceeds 16KiB; observation unavailable")
    return value


def capture(cmd, root, cwd=None):
    env = dict.fromkeys("HOME USERPROFILE TEMP TMP PYTHONPATH".split(), str(root))
    if cmd[2] == "-m":
        env["PYTHONSAFEPATH"] = "1"
    env.update((k, os.environ[k]) for k in ("SYSTEMROOT", "WINDIR") if k in os.environ)
    child = Popen(cmd, cwd=cwd or root, env=env, stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
    data = [None, None]

    def drain(stream, index):
        data[index] = stream.read(LIMIT + 1)

    threads = [
        threading.Thread(target=drain, args=(stream, i), daemon=True)
        for i, stream in enumerate((child.stdout, child.stderr))
    ]
    for thread in threads:
        thread.start()
    while child.poll() is None:
        if any(d is not None and len(d) > LIMIT for d in data):
            raise ValueError("Product output exceeds 16KiB")
        time.sleep(0.01)
    for thread in threads:
        thread.join()  # The enclosing action watchdog also bounds pipe drain.
    if any(d is None for d in data):
        raise OSError("Output observation unavailable")
    if any(len(d) > LIMIT for d in data):
        raise ValueError("Product output exceeds 16KiB")
    return dict(exit=child.returncode, stdout=data[0].decode(), stderr=data[1].decode())


def equal(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def execute(s, root, targets, phase, seconds, deadline):
    snapshots, results, markers = {}, {}, []
    for directory in s["setup"].get("dirs", []):
        path(root, directory).mkdir(parents=True, exist_ok=True)
    for f in s["setup"].get("files", []):
        file = path(root, f["path"])
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content(f))
    for f in s["setup"].get("snapshots", []):
        snapshots[f["id"]] = read(path(root, f["path"]))

    def expired():
        print(json.dumps({"state": "ERROR", "reason": "Product action timeout"}), flush=True)
        os._exit(0)  # Outer held Job reaps the entire owned tree, including pipe holders.

    for a in s["actions"]:
        budget = min(seconds, deadline - time.monotonic())
        if budget <= 0:
            raise TimeoutError("Scenario/run deadline; action unperformed")
        guard = threading.Timer(budget, expired)
        guard.start()
        try:
            v = a["cli"]
            binding = targets[v["target"]]
            args = argv(binding["argv"], root) + argv(v["argv"], root)
            launch = (
                ["-m", binding["module"]]
                if "module" in binding
                else [str(path(root, binding["entry"]))]
            )
            result = capture(
                [sys.executable, "-B", *launch, *args],
                root,
                path(root, a["cwd"]) if "cwd" in a else root,
            )
        finally:
            guard.cancel()
        marker = f"SELDON-STUB {v['operation']}"
        if phase == "contracts" and not (
            result["exit"] == 3 and marker in result["stderr"].splitlines()
        ):
            raise ValueError(
                "Missing standard operation stub marker; no Contracts RED credit. "
                "Write SELDON-STUB <operation> for the declared operation to stderr and exit 3 "
                "before argument parsing, importing product modules, or product I/O."
            )
        if "SELDON-STUB " in result["stderr"] + result["stdout"]:
            markers.append(a["id"])
        results[a["id"]] = result
    observations = []
    for o in s["observe"]:
        actual = results[o["action"]] if "action" in o else None
        for key, expected in o.items():
            if key in ("action", "path"):
                continue
            valid = True
            if key in ("exitEquals", "stdoutEquals", "stderrEquals"):
                value = actual[key.removesuffix("Equals")]
                if key in ("stdoutEquals", "stderrEquals"):
                    value = value.replace("\r\n", "\n")
            elif key == "stderrNonempty":
                value = bool(actual["stderr"].strip())
            elif key == "stderrContains":
                value, expected = expected in actual["stderr"], True
            elif key == "fileAbsent":
                value = not path(root, o["path"]).exists()
            else:
                try:
                    raw = (
                        actual["stdout"]
                        if key == "stdoutJsonEquals"
                        else read(path(root, o["path"]))
                    )
                except FileNotFoundError:
                    observations.append(
                        dict(check=key, passed=False, actual=None, reason="product file missing")
                    )
                    continue
                if key == "bytesUnchangedFrom":
                    value, expected = raw == snapshots[expected], True
                elif key == "fileBytesEquals":
                    value = raw.decode()
                else:
                    try:
                        value = json.loads(raw)
                        json.dumps(value, allow_nan=False)
                    except ValueError:
                        value, valid = "invalid JSON output", False
            passed = valid and equal(value, expected)
            reason = "matched" if passed else "observable mismatch"
            observations.append(dict(check=key, passed=passed, actual=value, reason=reason))
    if phase == "validation" and markers:
        raise ValueError("Stub marker remains; final Validation cannot pass")
    state = "PASS" if all(o["passed"] for o in observations) else "FAIL"
    summary = dict(id=s["id"], state=state, reason="observations evaluated")
    return dict(summary, observations=observations, stubActions=markers, actions=results)


def run(
    document, product, targets, phase, *, action_seconds=3, scenario_seconds=15, run_seconds=45
):
    results, scenarios = [], [{"id": None}]
    try:
        scenarios = validate(document, product, targets)
        if phase not in ("contracts", "validation") or os.name != "nt":
            raise ValueError("Use contracts/validation on Windows (held Job required)")
        budgets = (action_seconds, scenario_seconds, run_seconds)
        if (
            not all(type(v) in (int, float) for v in budgets)
            or not 0 < budgets[0] <= budgets[1] <= budgets[2] <= 120
        ):
            raise ValueError("Use positive ordered action/scenario/run budgets <=120s")
        deadline = time.monotonic() + run_seconds
        for s in scenarios:
            end = min(deadline, time.monotonic() + scenario_seconds)
            if end <= time.monotonic():
                raise TimeoutError("Run deadline; scenario unperformed")
            with tempfile.TemporaryDirectory(prefix="seldon-semantic-") as temp:
                root, count = Path(temp), 0
                for base, dirs, files in os.walk(product, followlinks=False):
                    count += len(files) + len(dirs)
                    if count > 128:
                        raise ValueError("Product source exceeds128 entries")
                    for name in [*dirs, *files]:
                        source = path(product, (Path(base) / name).relative_to(product).as_posix())
                        if name in files and source.suffix == ".py":
                            if source.stat().st_size > 262144:
                                raise ValueError("Product source exceeds256KiB per file")
                            destination = root / source.relative_to(Path(product).resolve())
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(source, destination)
                config = root / ".scenario.json"
                config.write_text(
                    json.dumps([s, targets, phase, action_seconds, end]), encoding="utf-8"
                )
                cmd = [sys.executable, "-B", "-m", MODULE, "--worker", str(config)]
                completed = run_audit_process(
                    cmd, cwd=root, timeout=max(0.01, end - time.monotonic())
                )
                control = completed.audit_control
                if (
                    not control.get("quiescenceVerified")
                    or control.get("activeProcessesAfterTeardown") != 0
                ):
                    raise OSError("Owned cleanup uncertain; stop further scenarios")
                if completed.returncode or time.monotonic() > end:
                    raise TimeoutError("Runner failed or scenario deadline expired")
                result = dict(json.loads(completed.stdout), id=s["id"], cleanup=control)
                results.append(result)
                if result["state"] == "ERROR":
                    break
    except ERRORS as error:
        results.append(
            dict(
                id=scenarios[min(len(results), len(scenarios) - 1)]["id"],
                state="ERROR",
                reason=str(error),
                cleanup=getattr(error, "audit_control", {}),
            )
        )
    results.extend(
        dict(id=s["id"], state="ERROR", reason="unperformed after earlier ERROR")
        for s in scenarios[len(results) :]
    )
    return results


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        try:
            s, targets, phase, seconds, deadline = json.loads(read(Path(sys.argv[2]), 131072))
            result = execute(s, Path.cwd(), targets, phase, seconds, deadline)
        except ERRORS as error:
            result = dict(state="ERROR", reason=str(error) or type(error).__name__)
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        for name in ("spec", "product", "targets", "phase"):
            parser.add_argument(name)
        args = parser.parse_args()
        files = (args.spec, args.targets)
        spec, targets = [json.loads(read(Path(p), 65536)) for p in files]
        result = run(spec, Path(args.product), targets, args.phase)
    print(json.dumps(result))
