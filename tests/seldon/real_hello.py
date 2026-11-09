"""C1/C20/C26: lead-operated real-provider development run, never owner acceptance."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from seldon.loop import Loop
from seldon.providers import Codex, setup_facts

DREAM = ('Build a tiny web page with one Play button. When I click Play, the page shows '
         'the text "Hello, world!". A single static HTML file is enough. '
         'No server, no accounts, no dependencies.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slot", type=Path, help="new slot folder under C:/seldon/tests/core1")
    parser.add_argument("--dry-run", action="store_true", help="prepare ALIGN; never launch Codex")
    args = parser.parse_args()
    slot = args.slot.resolve()
    if not slot.is_relative_to(Path("C:/seldon/tests/core1").resolve()):
        parser.error("slot must be under C:/seldon/tests/core1")
    slot.mkdir(parents=True)
    try:
        return run(slot, args.dry_run)
    finally:
        # A diagnostic must never replace the delivery result or its exception.
        try:
            from run_report import main as report_run
            report_run([str(slot)])
        except Exception as error:
            print(f"Dev-run report failed: {error}", file=sys.stderr)


def run(slot, dry_run):
    temp = slot / "temp"
    temp.mkdir()
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(temp)
    evidence = slot / "browser-evidence"
    evidence.mkdir()
    os.environ["SELDON_EVIDENCE_DIR"] = str(evidence)
    setup = setup_facts()
    setup["evidenceDirectory"] = str(evidence)
    (slot / "setup.json").write_text(json.dumps(setup, indent=2), encoding="utf-8")
    provider = Codex(slot / "calls", setup)

    def yes(request):
        print(json.dumps({"scriptedDecision": request}, ensure_ascii=False), flush=True)
        return "yes"

    loop = Loop(slot / "product", slot / "workers", DREAM, setup, provider, yes)
    try:
        if dry_run:
            from seldon.questions import build
            kickoff = loop.store.cursor()["kickoff"]
            question = build("ALIGN", {kickoff: {"sourceRecord": kickoff, "revision": None,
                             "boundedContent": loop.store.read(kickoff)}}, {})
            folder, invocation = provider.prepare(question)
            print(invocation["commandLine"] + ' < "' + str(folder / "prompt.txt") + '"')
            print(json.dumps(question["schema"], indent=2))
            return 0
        cursor = loop.run()
        outcome = loop.store.records("outcome")[-1][2]
        print(json.dumps({"state": cursor["state"], "scriptedUser": True,
                          "ownerAccepted": False, "outcome": outcome}, indent=2), flush=True)
        return 0 if cursor["state"] == "ACCEPTED" else 1
    finally:
        loop.store.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
