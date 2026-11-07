"""Canonical scenario derivation and isolated scenario execution."""
from __future__ import annotations

import copy
import os
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile

from codex_harness.core import spine as plan_spine
from codex_harness.core.acceptance_revision import _metadata_remaining
from codex_harness.core.audit_process import run_audit_process
from codex_harness.core.semantic_runner import run as run_semantic

def derive_scenarios(spine) -> list[dict]:
    fields = spine.node(spine.ids("vision")[0])["fields"]
    if spine.semantic:
        document, _, _ = plan_spine.semantic_spec(spine, draft=True)
        return [
            dict(
                s,
                title=s["id"],
                steps=s["actions"],
                expect=s["observe"],
                executable=s.get("evidenceTier") != "scenario: unvalidated",
                nodeIds=sorted({ref.split("@", 1)[0] for ref in s["requirementRefs"]}),
            )
            for s in document["scenarios"]
        ]
    scope = fields.get("scope_in", [])
    if isinstance(scope, str):
        scope = [item.strip() for item in re.split(r"[,\n]", scope) if item.strip()]
    items = [("first-moment", fields.get("first_user_moment", ""))]
    items.extend((f"scope-{index}", item) for index, item in enumerate(scope, 1))
    explicit = {item["id"]: item for item in fields.get("acceptance", [])}
    for key in spine.ids("acceptance"):
        node = spine.node(key)
        explicit[key] = {"id": key, "title": node["title"], **node["fields"]}
    scenarios = []
    for key, item in items:
        body = item if isinstance(item, dict) else {"title": item}
        scenario = {
            "id": key,
            "title": body.get("title", key),
            "given": "A clean install with no user data",
            "steps": [],
            "expect": {},
        }
        scenario.update(body)
        scenario.update(explicit.pop(key, {}))
        scenarios.append(scenario)
    scenarios.extend(explicit.values())
    return scenarios

def _matches(actual: dict, expect: dict) -> bool:
    if not isinstance(expect, dict) or not expect:
        return False
    if set(expect) - {"exit", "exitCode", "stdout", "stderr", "stdoutRegex", "stderrRegex"}:
        raise ValueError("Unknown acceptance expectation")
    if actual["exit"] != expect.get("exit", expect.get("exitCode", 0)):
        return False
    for stream in ("stdout", "stderr"):
        if stream in expect and expect[stream] not in actual[stream]:
            return False
        if stream + "Regex" in expect and not re.search(expect[stream + "Regex"], actual[stream]):
            return False
    return True

def run_scenarios(
    repo,
    scenarios,
    env=None,
    *,
    on_result=None,
    should_stop=None,
    deadline=None,
    semantic=False,
    judgments=None,
    stop_file=None,
) -> list[dict]:
    """Execute the complete semantic suite, or trusted legacy commands from isolated state.

    Legacy tokens and environment use fresh isolated state; failures retain observations.
    Semantic action admission observes the acceptance owner's stop_file.
    """
    import sys
    repo = Path(repo).resolve()
    results = []
    halt = None
    run_deadline = (
        deadline
        if deadline is not None
        else time.monotonic()
        + sum(60 * len(item.get("steps", item.get("actions", []))) + 1.7 for item in scenarios)
    )
    if isinstance(semantic, dict):
        document, targets = dict(schema="seldon/scenarios-v1", scenarios=scenarios), semantic
        raw_results = run_semantic(
            document,
            repo,
            targets,
            "validation", evidence_dir=repo.parent / "evidence" / "acceptance",
            run_seconds=min(120, _metadata_remaining(run_deadline, should_stop) - 1.7),
            judgments=judgments,
            should_stop=should_stop,
            stop_file=stop_file,
        )
        for scenario, raw in zip(scenarios, raw_results, strict=True):
            if (
                raw["state"] == "ERROR"
                and not raw.get("unperformed")
                and raw.get("cleanup", {}).get("quiescenceVerified") is not True
            ):
                raw["cleanupUncertainty"] = raw["reason"]
            result = dict(
                id=scenario["id"],
                title=scenario["id"],
                steps=[],
                passed=None if raw["state"] == "pending-judgment" else raw["state"] == "PASS",
                state=raw["state"], semantic=raw,
                error=raw["reason"],
            )
            results.append(result)
            if on_result:
                on_result(copy.deepcopy(result))
        return results
    for scenario in scenarios:
        budget = 60 * len(scenario.get("steps", [])) + 1.7
        remaining_run = run_deadline - time.monotonic()
        deadline = min(time.monotonic() + budget, run_deadline)
        result = {
            "id": scenario["id"],
            "title": scenario["title"],
            "steps": [],
            "passed": False,
            "budgetSeconds": budget,
            "deadlineAt": (
                datetime.now(UTC) + timedelta(seconds=min(budget, remaining_run))
            ).isoformat(),
        }
        results.append(result)
        if remaining_run <= 1.7:
            halt = halt or "Whole-run execution deadline exhausted; no additional children admitted"
        if halt or should_stop and should_stop():
            result.update(
                state="unfinished", error=halt or "Acceptance owner stopped; no new child"
            )
            if on_result:
                on_result(copy.deepcopy(result))
            continue
        with TemporaryDirectory(
            prefix="harness-acceptance-", ignore_cleanup_errors=True
        ) as directory:
            root = Path(directory)
            home, data, cwd = (root / name for name in ("home", "data", "work"))
            for path in (home, data, cwd):
                path.mkdir()
            process_env = {**os.environ, **(env or {})}
            for key in list(process_env):
                if key.endswith(("_DATA", "_DATA_DIR", "_DATA_HOME")):
                    process_env[key] = str(data / key.lower())
            process_env.update(
                HOME=str(home),
                USERPROFILE=str(home),
                APPDATA=str(data / "roaming"),
                LOCALAPPDATA=str(data / "local"),
                XDG_DATA_HOME=str(data),
                XDG_CONFIG_HOME=str(home / ".config"),
                XDG_CACHE_HOME=str(home / ".cache"),
                XDG_STATE_HOME=str(data / "state"),
                XDG_RUNTIME_DIR=str(root / "runtime"),
                TMP=str(root),
                TEMP=str(root),
                TMPDIR=str(root),
                PYTHONPATH=str(repo),
                PYTHONNOUSERSITE="1",
                PYTHONDONTWRITEBYTECODE="1",
            )
            process_env.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive) :])
            tokens = {
                "repo": str(repo),
                "home": str(home),
                "data": str(data),
                "python": sys.executable,
            }
            steps = scenario.get("steps", [])
            if not steps:
                result["error"] = "No executable steps supplied for this vision promise"
                if on_result:
                    on_result(copy.deepcopy(result))
                continue
            for index, step in enumerate(steps):
                if should_stop and should_stop():
                    result.update(
                        state="unfinished", error="Acceptance owner stopped; no new child"
                    )
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 1.7:
                    result.update(
                        state="unfinished",
                        error="Scenario execution deadline exhausted; no new child",
                    )
                    break
                command = step if isinstance(step, str) else step["command"]
                for name, value in tokens.items():
                    command = command.replace("{" + name + "}", value)
                expect = (step.get("expect") if isinstance(step, dict) else None) or (
                    scenario.get("expect", {}) if index == len(steps) - 1 else {"exit": 0}
                )
                timed_out = False
                cleanup_uncertainty = ""
                # Files avoid pipe backpressure and inherited-pipe waits. Only bounded
                # output is read into memory, even when the command is very noisy.
                with TemporaryFile() as stdout, TemporaryFile() as stderr:
                    wait_limit = min(60, max(0, deadline - time.monotonic() - 1.7))
                    code, control = None, {}
                    try:
                        completed = run_audit_process(command, cwd=cwd, env=process_env, shell=True,
                            capture_output=False, stdout=stdout, stderr=stderr, timeout=wait_limit,
                            stop_when=lambda: "acceptance-stopped" if should_stop and should_stop() else None)
                        code, control = completed.returncode, completed.audit_control
                    except (OSError, subprocess.SubprocessError) as error:
                        timed_out = isinstance(error, subprocess.TimeoutExpired)
                        control = getattr(error, "audit_control", {})
                        cleanup_uncertainty = str(error) if not control.get("quiescenceVerified") else ""
                    if control.get("quiescenceVerified") is not True:
                        cleanup_uncertainty = cleanup_uncertainty or "Owned Job cleanup unverified"
                    record = {
                        "command": command,
                        "stdout": _bounded_output(stdout),
                        "stderr": _bounded_output(
                            stderr,
                            "\nStep timed out"
                            + ("\n" + cleanup_uncertainty if cleanup_uncertainty else "")
                            if timed_out
                            else "",
                        ),
                        "exit": code,
                        "cleanup": control,
                        "timedOut": timed_out,
                        "timeoutSeconds": wait_limit,
                    }
                    if cleanup_uncertainty:
                        record["cleanupUncertainty"] = cleanup_uncertainty
                        halt = "Prior child cleanup uncertain; no additional children admitted"
                    record["passed"] = (control.get("quiescenceVerified") is True
                                        and not control.get("stopReason") and not timed_out
                                        and _matches(record, expect))
                result["steps"].append(record)
                if not record["passed"]:
                    break
            result["passed"] = len(result["steps"]) == len(steps) and all(
                item["passed"] for item in result["steps"]
            )
        if on_result:
            on_result(copy.deepcopy(result))
    return results

def _bounded_output(stream, suffix="") -> str:
    limit = 64 * 1024
    marker = b"\n[truncated]\n"
    stream.seek(0)
    value = stream.read(limit + 1) + suffix.encode("utf-8")
    if len(value) > limit:
        value = value[: limit - len(marker)] + marker
    # Invalid child bytes are evidence, not whitespace to discard before an
    # exact/regex expectation. Keep visible replacements without crashing the
    # runner; the retained truncation marker likewise remains part of matching.
    text = value.decode("utf-8", errors="replace")
    encoded = text.encode("utf-8")
    if len(encoded) > limit:
        # Replacements may expand bytes. Truncate valid re-encoded text at a
        # character boundary and keep the existing explicit truncation marker.
        text = encoded[: limit - len(marker)].decode("utf-8", errors="ignore") + marker.decode()
    return text
