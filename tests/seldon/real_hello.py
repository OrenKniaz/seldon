"""C1/C20/C26: lead-operated real-provider development run, never owner acceptance."""

import argparse
import json
import os
import sys
import hashlib
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from seldon.run import Run
from seldon.providers import Codex, setup_facts

DREAM = ('Build a tiny web page with one Play button. When I click Play, the page shows '
         'the text "Hello, world!". A single static HTML file is enough. '
         'No server, no accounts, no dependencies.')


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slot", type=Path, help="new slot folder under C:/seldon/tests/m1")
    parser.add_argument("--dry-run", action="store_true", help="prepare ALIGN; never launch Codex")
    parser.add_argument("--dream-file", type=Path, help="dream text instead of hello-world")
    parser.add_argument("--reopen", action="store_true", help="resume this slot's canonical run")
    parser.add_argument("--runs", type=Path, help="override RUNS.md for an offline diagnostic")
    args = parser.parse_args()
    slot = args.slot.resolve()
    if not slot.is_relative_to(Path("C:/seldon/tests/m1").resolve()):
        parser.error("slot must be under C:/seldon/tests/m1")
    if not args.reopen:
        slot.mkdir(parents=True)
    try:
        dream = args.dream_file.read_text("utf-8").strip() if args.dream_file else DREAM
        return run(slot, args.dry_run, dream, args.reopen)
    finally:
        # A diagnostic must never replace the delivery result or its exception.
        try:
            from run_report import main as report_run
            report_run([str(slot), *(["--runs", str(args.runs)] if args.runs else [])])
        except Exception as error:
            print(f"Dev-run report failed: {error}", file=sys.stderr)


def run(slot, dry_run, dream=DREAM, reopen=False):
    temp = slot / "temp"
    temp.mkdir(exist_ok=reopen)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(temp)
    evidence = slot / "browser-evidence"
    evidence.mkdir(exist_ok=reopen)
    os.environ["SELDON_EVIDENCE_DIR"] = str(evidence)
    setup = json.loads((slot / "setup.json").read_text("utf-8")) if reopen else setup_facts()
    source = Path(__file__).resolve().parents[2]
    if not reopen:
        setup["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source,
                                                  text=True).strip()
        setup["runtimeSource"] = hashlib.sha256(b"\0".join(
            p.name.encode() + b"\0" + p.read_bytes() for p in sorted((source / "src/seldon").glob("*.py"))
        )).hexdigest()
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "src/seldon"], cwd=source).returncode:
            setup["commit"] += "+worktree:" + setup["runtimeSource"][:12]
        setup["questionContract"] = hashlib.sha256(b"".join((source / "src/seldon" / name).read_bytes()
            for name in ("questions.py", "schema.py"))).hexdigest()
        setup["evidenceDirectory"] = str(evidence)
        (slot / "setup.json").write_text(json.dumps(setup, indent=2), encoding="utf-8")
    provider = Codex(slot / "calls", setup)

    def yes(request):
        print(json.dumps({"scriptedDecision": request}, ensure_ascii=False), flush=True)
        return {"request": request["request"], "subject": request["subject"], "choice": "approve"}

    loop = (Run.reopen(slot / "product", provider, yes) if reopen else
            Run(slot / "product", slot / "workers", dream, setup, provider, yes))
    try:
        if dry_run:
            from seldon.questions import build
            question = build("ALIGN", {"Dream": dream, "Runtime": setup})
            folder, invocation = provider.prepare(question)
            print(invocation["commandLine"] + ' < "' + str(folder / "prompt.txt") + '"')
            print(json.dumps(question["schema"], indent=2))
            return 0
        cursor = loop.run()
        # A run never fails (R15): it ends with an outcome or waits on one question; print whichever it is.
        from seldon.frontdoor import state
        outcomes = loop.store.records("outcome")
        print(json.dumps({"state": "WAITING" if cursor.decision else cursor.state, "scriptedUser": True,
                          "ownerAccepted": False, "outcome": outcomes[-1][2] if outcomes else None,
                          "waitingFor": state(str(loop.project)).get("waitingFor")}, indent=2), flush=True)
        return 0 if cursor.state == "SCRIPTED_C" else 1
    finally:
        loop.close()


if __name__ == "__main__":
    raise SystemExit(main())
