"""Python CLI and local web observations; not canonical admission or GUI acceptance.
Windows Jobs contain normal descendants; trusted code, not an OS security sandbox."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen, TimeoutExpired

from codex_harness.core.audit_process import run_audit_process
from codex_harness.core.semantic_schema import LIMIT, WEB, argv, content, path, validate

ERRORS = (OSError, ValueError, TypeError, LookupError, TimeoutExpired, RecursionError)
MODULE = "codex_harness.core.semantic_runner"
SOURCE_IGNORED = {".git", ".codex-harness", "__pycache__", ".pytest_cache", ".ruff_cache"}


def web_toolchain(config, evidence):
    defaults = dict(
        node="C:/nvm4w/nodejs/node.exe",
        playwright="C:/Users/oren/codex-harness/ui/node_modules/@playwright/test",
        chromium=str(
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "ms-playwright/chromium-1217/chrome-win64/chrome.exe"
        ),
    )
    config = config or {}
    if set(config) - defaults.keys():
        raise ValueError("Unknown web tool configuration")
    locations = {
        k: str(Path(config.get(k) or os.environ.get("SELDON_WEB_" + k.upper()) or v).resolve())
        for k, v in defaults.items()
    }
    locations["adapter"] = str(Path(__file__).with_name("semantic_web.cjs").resolve())
    identities = {}
    for key, location in locations.items():
        file = Path(location) / "package.json" if key == "playwright" else Path(location)
        if not file.is_file():
            raise ValueError(f"Web tool missing: {key}: {file}; configure SELDON_WEB_{key.upper()}")
        with file.open("rb") as stream:
            identities[key] = dict(
                path=str(file), sha256=hashlib.file_digest(stream, "sha256").hexdigest()
            )
    if json.loads(Path(identities["playwright"]["path"]).read_text())["version"] != "1.59.1":
        raise ValueError("Web tool requires Playwright 1.59.1")
    if evidence is False:
        return identities
    locations.update(
        identity=identities,
        evidence=str(Path(evidence or tempfile.mkdtemp(prefix="seldon-web-evidence-")).resolve()),
    )
    Path(locations["evidence"]).mkdir(parents=True, exist_ok=True)
    return locations


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


def capture(cmd, root, cwd=None, payload=None, *, browser=False):
    env = dict.fromkeys("HOME USERPROFILE TEMP TMP PYTHONPATH".split(), str(root))
    if cmd[2] == "-m":
        env["PYTHONSAFEPATH"] = "1"
    env.update((k, os.environ[k]) for k in ("SYSTEMROOT", "WINDIR") if k in os.environ)
    if browser:
        env["DEBUG"] = "pw:browser"
    stdin = DEVNULL if payload is None else PIPE
    child = Popen(cmd, cwd=cwd or root, env=env, stdin=stdin, stdout=PIPE, stderr=PIPE)
    data = [None, None]

    def drain(stream, index):
        data[index] = stream.read(LIMIT + 1)

    threads = [
        threading.Thread(target=drain, args=(stream, i), daemon=True)
        for i, stream in enumerate((child.stdout, child.stderr))
    ]
    for thread in threads:
        thread.start()
    if payload is not None:
        try:
            with child.stdin:
                child.stdin.write(payload)
        except BrokenPipeError:
            pass  # A Contracts stub may exit before consuming input.
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


def execute(s, root, targets, phase, seconds, deadline, web=None):
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
        print(
            json.dumps(dict(state="ERROR", reason="Product action timeout", action=action)),
            flush=True,
        )
        os._exit(0)  # Outer held Job reaps the entire owned tree, including pipe holders.

    for a in s["actions"]:
        budget = min(30 if "web" in a else seconds, deadline - time.monotonic())
        if budget <= 0:
            raise TimeoutError("Scenario/run deadline; action unperformed")
        action = dict(id=a["id"], seconds=budget)
        guard = threading.Timer(budget, expired)
        guard.start()
        try:
            if "web" in a:
                request = root / ".web-request.json"
                request.write_text(
                    json.dumps(
                        dict(
                            journey=a["web"],
                            tools=web,
                            root=str(root),
                            phase=phase,
                            seconds=min(seconds, 10),
                            remaining=budget,
                        )
                    ),
                    encoding="utf-8",
                )
                captured = capture([web["node"], web["adapter"], str(request)], root, browser=True)
                if captured["exit"]:
                    raise ValueError("Web adapter launch failed: " + captured["stderr"][:512])
                result = json.loads(captured["stdout"])
                if result.get("state") == "ERROR":
                    result["reason"] += "\n" + captured["stderr"][-8192:]
                    return dict(
                        state="ERROR", reason=result["reason"], actions={**results, a["id"]: result}
                    )
                results[a["id"]] = result
                if result["marker"]:
                    markers.append(a["id"])
                continue
            v = a["cli"]
            binding = targets[v["target"]]
            cwd = path(root, a["cwd"]) if "cwd" in a else root
            args = argv(binding["argv"], root) + argv(v["argv"], root, cwd)
            launch = (
                ["-m", binding["module"]]
                if "module" in binding
                else [str(path(root, binding["entry"]))]
            )
            command = [sys.executable, "-B", *launch, *args]
            payload = content(v["stdin"]) if "stdin" in v else None
            action.update(
                command=command,
                cwd=str(cwd),
                stdinBytes=len(payload) if payload is not None else None,
            )
            result = capture(command, root, cwd, payload)
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
        if actual is not None and "checks" in actual:
            observations.extend(actual["steps"])
            observations.extend(
                dict(
                    check=k,
                    passed=equal(actual["checks"][k], o[k]),
                    actual=actual["checks"][k],
                    reason="browser observation",
                )
                for k in WEB
            )
            continue
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
    document,
    product,
    targets,
    phase,
    *,
    action_seconds=None,
    scenario_seconds=None,
    run_seconds=None,
    web_config=None,
    evidence_dir=None,
):
    results, scenarios = [], [{"id": None}]
    cleanup = dict(quiescenceVerified=True, activeProcessesAfterTeardown=0, childLaunched=False)
    try:
        scenarios = validate(document, product, targets)
        is_web = any(b.get("kind") == "web" for b in targets.values())
        # Hang guards, not speed targets: cold Windows Python under load exceeded 3s (run 24).
        # Defaults fit inside any caller-supplied outer budget.
        run_seconds = run_seconds if run_seconds is not None else (90 if is_web else 120)
        if scenario_seconds is None:
            scenario_seconds = min(30 if is_web else 60, run_seconds)
        if action_seconds is None:
            action_seconds = min(10 if is_web else 20, scenario_seconds)
        if phase not in ("contracts", "validation") or os.name != "nt":
            raise ValueError("Use contracts/validation on Windows (held Job required)")
        if is_web and all(
            type(v) in (int, float) for v in (action_seconds, scenario_seconds, run_seconds)
        ):
            run_seconds = min(run_seconds, 90)
            scenario_seconds = min(scenario_seconds, 30, run_seconds)
            action_seconds = min(action_seconds, 10, scenario_seconds)
        budgets = (action_seconds, scenario_seconds, run_seconds)
        if (
            not all(type(v) in (int, float) for v in budgets)
            or not 0 < budgets[0] <= budgets[1] <= budgets[2] <= 120
        ):
            raise ValueError("Use positive ordered action/scenario/run budgets <=120s")
        deadline = time.monotonic() + run_seconds
        web = web_toolchain(web_config, evidence_dir) if is_web else None
        for s in scenarios:
            end = min(deadline, time.monotonic() + scenario_seconds)
            if end <= time.monotonic():
                raise TimeoutError("Run deadline; scenario unperformed")
            with ExitStack() as stack:
                temp = tempfile.mkdtemp(prefix="seldon-semantic-")
                root, count = Path(temp), 0
                stack.callback(
                    lambda root=root, control=cleanup: shutil.rmtree(root)
                    if control.get("quiescenceVerified")
                    else None
                )
                for base, dirs, files in os.walk(product, followlinks=False):
                    dirs[:] = [name for name in dirs if name not in SOURCE_IGNORED]
                    files = [name for name in files if name not in SOURCE_IGNORED]
                    count += len(files) + len(dirs)
                    if count > 128:
                        raise ValueError("Product source exceeds128 entries")
                    for name in [*dirs, *files]:
                        raw = Path(base) / name
                        if raw.is_symlink() or raw.is_junction():
                            raise ValueError("Product source cannot contain links/junctions")
                        source = path(product, (Path(base) / name).relative_to(product).as_posix())
                        if name in files and (
                            source.suffix == ".py"
                            or (
                                is_web
                                and source.suffix.lower()
                                in {
                                    ".html",
                                    ".css",
                                    ".js",
                                    ".json",
                                    ".txt",
                                    ".csv",
                                    ".png",
                                    ".jpg",
                                    ".jpeg",
                                    ".svg",
                                    ".webp",
                                    ".ico",
                                    ".woff",
                                    ".woff2",
                                }
                            )
                        ):
                            if source.stat().st_size > 262144:
                                raise ValueError("Product source exceeds256KiB per file")
                            destination = root / source.relative_to(Path(product).resolve())
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(source, destination)
                config = root / ".scenario.json"
                config.write_text(
                    json.dumps([s, targets, phase, action_seconds, end, web]), encoding="utf-8"
                )
                cmd = [sys.executable, "-B", "-m", MODULE, "--worker", str(config)]
                cleanup.clear()  # Launch may create children even if it raises without a receipt.
                completed = run_audit_process(
                    cmd, cwd=root, timeout=max(0.01, end - time.monotonic())
                )
                control = completed.audit_control
                cleanup.update(control)
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
                cleanup=getattr(error, "audit_control", cleanup),
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
            s, targets, phase, seconds, deadline, web = json.loads(read(Path(sys.argv[2]), 131072))
            result = execute(s, Path.cwd(), targets, phase, seconds, deadline, web)
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
