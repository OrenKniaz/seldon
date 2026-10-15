<!-- Generated from spine.json; document revision dfb58def4b77 -->

# V1: dogfood-watercli (87fa1e13332c)

A tiny local CLI for one person to record water intake and see progress.

User proxy: Opus supplied the assignment answers; Oren did not approve this plan. Local only.

First moment: create water.json once with {"goal_ml":2000,"entries":[]}, run `python -m watercli.cli add 250`, then `today`: 250 ml / 2000 ml (12.5%). Missing files produce clear errors, including on first use; never implicitly initialize or reset.

Scope: add, today, history (default 7 calendar days), goal (default 2000 ml), undo today's last entry; ml/oz, WATER_CLI_DATA, local calendar days. Out: GUI, reminders, sync, multiple users; deferred JSON output, streaks, backup retention. Python 3.11+ stdlib and unittest.

Done feels like a quick add, readable daily percentage and trustworthy persistence, with invalid input or corrupt storage explained without a traceback or lost bytes.

This assignment authorizes local planning using the supplied Opus user-proxy answers. Publication means local fixture export, not a commit, release or execution authorization. No workers are launched; the reference is solely a planner test oracle.

## Decisions

### D_BACKUP: Defer Data-file backup on write (3edcff987c22)

Proposed best-practice uplift. Disposition: deferred by planner pending a proxy answer. Backups help manual recovery, but add retention and disk-use decisions; atomic replacement protects failed writes now.

Parents: V1

### D_CONTRACT: Freeze simple interfaces and storage errors (f41d53b5825b)

Planner detail choices within accepted scope: HALF_UP integer ml; explicit setup file; current goal for history; sequential invocations; atomic replace without backups. No new init command or automatic reset.

Harness validation is an argv PREFIX: core.dispatch._validate constructs [sys.executable, *validation, proofModule]. The terminal -p receives the selected module pattern. Standalone full suite: python -B -m unittest discover -s tests. This prefix must not be run by itself.

Parents: V1

### D_JSON: Defer --json output (356812a40b63)

Proposed best-practice uplift. Disposition: deferred by planner pending a proxy answer. Machine-readable output helps scripting, but introduces an output contract the proxy did not request.

Parents: V1

### D_STACK: Which stack keeps this personal CLI small? (b57a7dc9b5f8)

Decision D_STACK: Which stack keeps this personal CLI small?
Linked to: V1
- Python 3.11+ standard library
  Pros: Matches proxy preference; Decimal, argparse, JSON, tempfile, unittest; no runtime dependencies.
  Cons: Requires installed Python; no third-party command framework.
- Python + Click
  Pros: Convenient command framework.
  Cons: Adds a dependency and violates stdlib-only preference.
Recommendation: Python 3.11+ standard library
Reply with an option name (Python 3.11+ standard library / Python + Click), or explain the change you want.
Decision: choose standard library, per the supplied user-proxy preference; unittest is the requirement proof runner.

Parents: V1

### D_STREAK: Defer Streak count (e62aaf668ee1)

Proposed best-practice uplift. Disposition: deferred by planner pending a proxy answer. Motivation can help habits, but adds calendar semantics beyond intake tracking.

Parents: V1
