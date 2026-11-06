"""CLI, offline/served browser and HTTP observations; not admission or GUI acceptance.
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
from codex_harness.core.record_io import write_record_json
from codex_harness.core.semantic_schema import LIMIT, argv, content, path, validate

ERRORS = (OSError, ValueError, TypeError, LookupError, TimeoutExpired, RecursionError)
MODULE = "codex_harness.core.semantic_runner"
SOURCE_IGNORED = {".git", ".codex-harness", "__pycache__", ".pytest_cache", ".ruff_cache"}


class ObservationUnavailable(ValueError):
    """A capture bound omits evidence; it is not a product verdict."""


def web_toolchain(config, evidence, *, browser=True):
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
        if not browser and key in ("playwright", "chromium"):
            continue
        file = Path(location) / "package.json" if key == "playwright" else Path(location)
        if not file.is_file():
            raise ValueError(f"Web tool missing: {key}: {file}; configure SELDON_WEB_{key.upper()}")
        with file.open("rb") as stream:
            identities[key] = dict(
                path=str(file), sha256=hashlib.file_digest(stream, "sha256").hexdigest()
            )
    if browser and json.loads(Path(identities["playwright"]["path"]).read_text())["version"] != "1.59.1":
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
                    raise ObservationUnavailable("Directory snapshot omitted after 128 entries")
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
        raise ObservationUnavailable("File snapshot omitted after 16KiB")
    return value


def capture(cmd, root, cwd=None, payload=None, *, browser=False, hold_open=False):
    env = dict(os.environ)
    env.update(dict.fromkeys("HOME USERPROFILE TEMP TMP PYTHONPATH".split(), str(root)))
    if cmd[1:3] == ["-B", "-m"]:
        env["PYTHONSAFEPATH"] = "1"
    env.update((k, os.environ[k]) for k in ("SYSTEMROOT", "WINDIR") if k in os.environ)
    if browser:
        env["DEBUG"] = "pw:browser"
    started = time.monotonic()
    stdin = PIPE if payload is not None or hold_open else DEVNULL
    child = Popen(cmd, cwd=cwd or root, env=env, stdin=stdin, stdout=PIPE, stderr=PIPE)
    data = [None, None]
    omitted = [0, 0]

    def drain(stream, index):
        kept = bytearray()
        while chunk := stream.read(4096):
            remaining = max(0, LIMIT - len(kept))
            kept.extend(chunk[:remaining])
            omitted[index] += max(0, len(chunk) - remaining)
        data[index] = bytes(kept)

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
            pass  # A process may exit before consuming input.
    while child.poll() is None:
        time.sleep(0.01)
    elapsed = time.monotonic() - started
    stdin_open = hold_open and not child.stdin.closed
    if hold_open:
        child.stdin.close()
    for thread in threads:
        thread.join()  # The enclosing action watchdog also bounds pipe drain.
    if any(d is None for d in data):
        raise OSError("Output observation unavailable")
    return dict(
        exit=child.returncode,
        stdout=data[0].decode("utf-8", errors="replace"),
        stderr=data[1].decode("utf-8", errors="replace"),
        omittedBytes=dict(stdout=omitted[0], stderr=omitted[1]),
        stdinOpenAtExit=stdin_open,
        elapsedSeconds=elapsed,
    )


def equal(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def execute(s, root, targets, seconds, deadline, web=None, judgments=None, stop_file=None):
    snapshots, results = {}, {}
    for directory in s["setup"].get("dirs", []):
        path(root, directory).mkdir(parents=True, exist_ok=True)
    for f in s["setup"].get("files", []):
        file = path(root, f["path"])
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content(f))
    for f in s["setup"].get("snapshots", []):
        try:
            snapshots[f["id"]] = read(path(root, f["path"]))
        except ObservationUnavailable:
            snapshots[f["id"]] = None

    def expired():
        print(
            json.dumps(dict(state="ERROR", reason="Product action timeout", action=action)),
            flush=True,
        )
        os._exit(0)  # Outer held Job reaps the entire owned tree, including pipe holders.

    for a in s["actions"]:
        if stop_file and Path(stop_file).exists():
            raise ValueError("Acceptance owner stopped; no new action child")
        budget = min(30 if "web" in a or "http" in a else seconds, deadline - time.monotonic())
        if budget <= 0:
            raise TimeoutError("Scenario/run deadline; action unperformed")
        if "cli" in a and a["cli"].get("stdin", {}).get("holdOpen"):
            budget = min(budget, a["cli"]["stdin"]["exitWithin"])
        action = dict(id=a["id"], seconds=budget)
        guard = threading.Timer(budget, expired)
        guard.start()
        try:
            if "web" in a or "http" in a:
                v = a.get("web", a.get("http"))
                binding = dict(targets[v["target"]])
                if "server" in binding:
                    binding["server"] = dict(binding["server"], argv=argv(binding["server"]["argv"], root))
                request = root / ".web-request.json"
                request.write_text(
                    json.dumps(
                        dict(
                            journey=a.get("web"), http=a.get("http"), binding=binding,
                            tools=web,
                            root=str(root),
                            entry=binding.get("entry", "/"),
                            seconds=min(seconds, 10),
                            remaining=budget,
                        )
                    ),
                    encoding="utf-8",
                )
                captured = capture([web["node"], web["adapter"], str(request)], root, browser=True)
                if captured["exit"]:
                    raise ValueError("Web adapter launch failed: " + captured["stderr"][:512])
                if captured["omittedBytes"]["stdout"]:
                    raise ObservationUnavailable("Browser/HTTP evidence exceeded capture bound; shorten the journey or response")
                result = json.loads(captured["stdout"])
                if result.get("launchDiagnostic"):
                    result["launchDiagnostic"]["stderr"] = captured["stderr"]
                    return dict(state="pending-judgment", reason="browser unavailable", unperformed=True,
                                evidenceTier="browser: UNVERIFIED", launchDiagnostic=result["launchDiagnostic"],
                                actions={**results, a["id"]: result})
                if result.get("state") == "ERROR":
                    result["reason"] += "\n" + captured["stderr"][-8192:]
                    return dict(
                        state="ERROR", reason=result["reason"], actions={**results, a["id"]: result}
                    )
                results[a["id"]] = result
                continue
            cwd = path(root, a["cwd"]) if "cwd" in a else root
            if "process" in a:
                v = a["process"]
                command = argv(v["argv"], root, cwd)
            else:
                v = a["cli"]
                binding = targets[v["target"]]
                args = argv(binding["argv"], root) + argv(v["argv"], root, cwd)
                launch = (["-m", binding["module"]] if "module" in binding
                          else [str(path(root, binding["entry"]))])
                command = [sys.executable, "-B", *launch, *args]
            hold_open = v.get("stdin", {}).get("holdOpen", False)
            payload = content(v["stdin"]) if "stdin" in v and not hold_open else None
            action.update(
                command=command,
                cwd=str(cwd),
                stdinBytes=len(payload) if payload is not None else None,
            )
            result = capture(command, root, cwd, payload, hold_open=hold_open)
            result.update(action)
        finally:
            guard.cancel()
        results[a["id"]] = result
    observations = []
    for o in s["observe"]:
        actual = results[o["action"]] if "action" in o else None
        if actual is not None and "checks" in actual:
            observations.extend(actual["steps"])
            for k in o.keys() - {"action"}:
                value = actual["checks"][k]
                judgment = (judgments or {}).get(k, {}) if value is None else {}
                execution = (judgments or {}).get("execution", {})
                if value is None and not judgment and execution.get("meaning", {}).get("qualitativeChecks", {}).get(k) is True:
                    judgment = execution
                passed = (True if judgment.get("positiveCredit") else False if judgment.get("concreteGap") else None) if value is None else equal(value, o[k])
                observations.append(dict(check=k, passed=passed, actual=value, judgment=judgment or None,
                                         reason="pending-judgment" if value is None else "browser observation"))
            observations.append(
                dict(
                    check="screenshots",
                    passed=None if any(a.get("missing") for a in actual["artifacts"]) else True,
                    reason="bounded screenshot capture; omissions need judgment",
                )
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
            elif key == "stdoutEndsWith":
                value, expected = actual["stdout"].endswith(expected), True
            elif key == "stdoutContains":
                value, expected = expected in actual["stdout"], True
            elif key == "stdinOpenAtExit":
                value = actual["stdinOpenAtExit"]
            elif key == "elapsedAtMost":
                value, expected = actual["elapsedSeconds"] <= expected, True
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
                except ObservationUnavailable as error:
                    observations.append(dict(check=key, passed=None, actual=None, reason=str(error)))
                    continue
                if key == "bytesUnchangedFrom":
                    if snapshots[expected] is None:
                        observations.append(dict(check=key, passed=None, actual=None,
                                                 reason="Baseline snapshot omitted"))
                        continue
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
            if actual and (
                key.startswith("stdout") and actual.get("omittedBytes", {}).get("stdout")
                or key.startswith("stderr") and actual.get("omittedBytes", {}).get("stderr")
            ):
                passed = None
            reason = "matched" if passed else "observable mismatch"
            observations.append(dict(check=key, passed=passed, actual=value, reason=reason))
    state = ("FAIL" if any(o["passed"] is False for o in observations) else
             "pending-judgment" if not observations or any(o["passed"] is None for o in observations)
             else "PASS")
    summary = dict(id=s["id"], state=state, reason="observations evaluated")
    return dict(summary, observations=observations, actions=results)


def _cleanup(root, control):
    if not control.get("quiescenceVerified"):
        return
    # Browser profile sharing can outlast verified Job teardown on Windows.
    for attempt in range(3):
        try:
            shutil.rmtree(root)
            return
        except PermissionError as error:
            if getattr(error, "winerror", None) != 32 or attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))


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
    judgments=None,
    should_stop=None,
    stop_file=None,
    progress_path=None,
):
    results, scenarios = [], [{"id": None}]
    cleanup = dict(quiescenceVerified=True, activeProcessesAfterTeardown=0, childLaunched=False)
    try:
        scenarios = validate(document, product, targets)
        is_web = any("web" in a or "http" in a for s in scenarios
                     if s.get("evidenceTier") != "scenario: unvalidated" for a in s["actions"])
        # Hang guards, not speed targets: cold Windows Python under load exceeded 3s (run 24).
        # Defaults fit inside any caller-supplied outer budget.
        run_seconds = run_seconds if run_seconds is not None else (90 if is_web else 120)
        if scenario_seconds is None:
            scenario_seconds = min(30 if is_web else 60, run_seconds)
        if action_seconds is None:
            action_seconds = min(10 if is_web else 20, scenario_seconds)
        if phase not in ("contracts", "validation") or os.name != "nt":
            raise ValueError("Use contracts/validation on Windows (held Job required)")
        budgets = (action_seconds, scenario_seconds, run_seconds)
        if is_web and all(type(v) in (int, float) for v in budgets):
            budgets = tuple(min(v, cap) for v, cap in zip(budgets, (10, 30, 90), strict=True))
        action_seconds, scenario_seconds, run_seconds = budgets
        if (
            not all(type(v) in (int, float) for v in budgets)
            or not 0 < budgets[0] <= budgets[1] <= budgets[2] <= 120
        ):
            raise ValueError("Use positive ordered action/scenario/run budgets <=120s")
        deadline = time.monotonic() + run_seconds
        web = web_toolchain(web_config, evidence_dir, browser=any("web" in a for s in scenarios for a in s["actions"])) if is_web else None
        for s in scenarios:
            if should_stop and should_stop():
                raise ValueError("Acceptance owner stopped; no new scenario child")
            end = min(deadline, time.monotonic() + scenario_seconds)
            if end <= time.monotonic():
                raise TimeoutError("Run deadline; scenario unperformed")
            if s.get("evidenceTier") == "scenario: unvalidated":
                results.append(dict(id=s["id"], state="pending-judgment", unperformed=True,
                                    reason=s.get("validationMessage", "Worker executable needed")))
                continue
            with ExitStack() as stack:
                temp = tempfile.mkdtemp(prefix="seldon-semantic-")
                root = Path(temp)
                stack.callback(_cleanup, root, cleanup)
                for base, dirs, files in os.walk(product, followlinks=False):
                    dirs[:] = [name for name in dirs if name not in SOURCE_IGNORED]
                    files = [name for name in files if name not in SOURCE_IGNORED]
                    for name in [*dirs, *files]:
                        if time.monotonic() >= end:
                            raise TimeoutError("Snapshot deadline; observation unperformed")
                        raw = Path(base) / name
                        if raw.is_symlink() or raw.is_junction():
                            raise ValueError("Product source cannot contain links/junctions")
                        source = path(product, (Path(base) / name).relative_to(product).as_posix())
                        if name in dirs:
                            (root / source.relative_to(Path(product).resolve())).mkdir(parents=True, exist_ok=True)
                        if name in files:
                            destination = root / source.relative_to(Path(product).resolve())
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(source, destination)
                config = root / ".scenario.json"
                config.write_text(
                    json.dumps(
                        [
                            s,
                            targets,
                            action_seconds,
                            end,
                            web,
                            judgments,
                            str(stop_file) if stop_file else None,
                        ]
                    ),
                    encoding="utf-8",
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
                if result.get("launchDiagnostic"):
                    results.extend(dict(result, id=later["id"], actions={}) for later in scenarios[len(results):])
                    break  # One failed launch is diagnostic, never retry authority.
                if progress_path is not None:
                    write_record_json(progress_path, dict(scenario=s["id"], completed=len(results)))
    except ERRORS as error:
        results.append(
            dict(
                id=scenarios[min(len(results), len(scenarios) - 1)]["id"],
                state="ERROR",
                reason=f"timeout: {error}" if isinstance(error, TimeoutExpired) else str(error),
                cleanup=getattr(error, "audit_control", cleanup),
            )
        )
    results.extend(
        dict(id=s["id"], state="ERROR", reason="unperformed after earlier ERROR", unperformed=True)
        for s in scenarios[len(results) :]
    )
    return results


if __name__ == "__main__":
    if sys.argv[1:2] == ["--preview"]:
        selection = Path(sys.argv[2] if len(sys.argv) > 2 else ".").resolve()
        spec = json.loads((selection if selection.is_file() else selection / "SCENARIOS.json").read_text(encoding="utf-8"))
        spec["scenarios"]["scenarios"] = [s for s in spec["scenarios"]["scenarios"] if len(sys.argv) <= 3 or sys.argv[3] == s["id"] or any(a.get("web", a.get("http", {})).get("target") == sys.argv[3] for a in s["actions"])]
        result = run(spec["scenarios"], selection.parent if selection.is_file() else selection, spec["targets"], "validation", evidence_dir=(selection.parent if selection.is_file() else selection).parent / "evidence" / "preview")
        result = result[0] if len(result) == 1 else result
    elif sys.argv[1:2] == ["--worker"]:
        try:
            s, targets, seconds, deadline, web, judgments, stop_file = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
            result = execute(
                s, Path.cwd(), targets, seconds, deadline, web, judgments, stop_file
            )
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
