"""Dispatch a plan through parallel provider attempts and serial integration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codex_harness.core.attempt_control import request_cancel
from codex_harness.core.dispatch import STATUS_MOCK_ONLY, STATUS_VALIDATED, run_plan


def _mapping(value: str) -> tuple[str, str]:
    bead, separator, setting = value.partition("=")
    if not separator or not bead or not setting:
        raise argparse.ArgumentTypeError("expected BEAD=value")
    return bead, setting


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["cancel"]:
        cancel = argparse.ArgumentParser(
            description="Request cancellation of a selected live attempt."
        )
        cancel.add_argument("--run", type=Path, required=True)
        cancel.add_argument("--bead", required=True)
        cancel.add_argument("--started-at")
        cancel.add_argument("--reason", default="operator cancellation")
        selected = cancel.parse_args(argv[1:])
        result = request_cancel(
            selected.run, selected.bead, started_at=selected.started_at, reason=selected.reason
        )
        print(json.dumps({"status": "cancellation-requested", **result}, indent=2))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    for name in ("provider", "model", "effort", "executable"):
        parser.add_argument(f"--{name}", type=_mapping, action="append", default=[])
    parser.add_argument("--mock-provider", choices=("reference", "noop"))
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--relaunch-ambiguous", action="store_true")
    parser.add_argument(
        "--retry-blocked", help="On resume, retry blocked bead IDs (comma separated), or all"
    )
    args = parser.parse_args(argv)
    record = run_plan(
        args.plan,
        work_root=args.work_root,
        provider_for=dict(args.provider),
        model_for=dict(args.model),
        effort_for=dict(args.effort),
        executable_for=dict(args.executable),
        mock_provider=args.mock_provider,
        max_parallel=args.max_parallel,
        timeout=args.timeout,
        resume_run_dir=args.resume,
        relaunch_ambiguous=args.relaunch_ambiguous,
        retry_blocked=args.retry_blocked,
    )
    print(
        json.dumps(
            {key: record[key] for key in ("runId", "plan", "status", "isMocked", "claimSummary")},
            indent=2,
        )
    )
    if args.resume:
        print(
            json.dumps(
                {
                    **record["resumeSummary"],
                    "planUpdate": (record.get("planUpdate") or {}).get("commit"),
                },
                indent=2,
            )
        )
    for bead_id, bead in record["beads"].items():
        telemetry = (bead.get("attempt") or {}).get("efficiency", {})
        print(
            f"Efficiency {bead_id}: "
            + ", ".join(f"{key}={json.dumps(value)}" for key, value in telemetry.items())
            if telemetry
            else f"Efficiency {bead_id}: unavailable (no observed attempt)",
            file=sys.stderr,
        )
    return 0 if record["status"] in {STATUS_VALIDATED, STATUS_MOCK_ONLY} else 1


if __name__ == "__main__":
    raise SystemExit(main())
