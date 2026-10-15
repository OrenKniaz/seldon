# Language-neutral verification runners

Status: integrated with manifest compilation, dispatch, packets and test audit.
Owner: canonical Python verification consumers; reviewer: the assigning driver.
Acceptance: legacy packet byte parity and compile/audit/mock-dispatch command proofs.
No real-provider or release certification is claimed by mock validation.

## Manifest contract

`validation` remains the legacy Python argv prefix. An optional root `verification`
object selects the runner and is passed through from the spine vision fields.
Without it, legacy manifests and worker packets retain their existing bytes.
Existing argv lists resolve to `unittest`.
An explicit unittest object is `{"runner":"unittest","argv":["-B","-m",
"unittest","discover","-s","tests","-p"]}`; argv is the existing Python prefix.
A command object is:

```json
{"runner":"command","argv":["npm","test","--","{proof}"],"cwd":".",
 "pass":{"exit":0},"testDir":"tests","proofGlob":"*.test.ts"}
```

`cwd` defaults to repository root; `pass` defaults to `{"exit":0}`.
Only exit zero passes, with no report input.
Command `testDir` and `proofGlob` are required. Paths are repository-relative;
testDir cannot be root, absolute or escaping. cwd may be root or a child.
proofGlob is a filename pattern, matching files directly in testDir.
The only template slot is one standalone `{proof}` argv element.
Command proofModule names a concrete repository-relative file, such as
`tests/widget.test.ts`. An optional `singleTest` argv template is shown as a
worker hint; without it command packets do not invent single-test commands.
The built-in unittest runner declares the existing single-test hints.
The executor runs at cwd; argv_for receives a repo-relative proof and renders
the proof argument relative to cwd so child-directory toolchains address it correctly.

## Prospective command audits and TypeScript

Command runners may optionally declare `listArgv`, `assertPatterns` and
`importPatterns`. All three are required for `audit --prospective`; a refusal
names each missing field. They are unnecessary for an oracle audit. The entire
contract, including these fields, contributes to the plan revision.

This Node 25 contract uses only built-in TypeScript stripping and Node tests;
no npm packages or installation are needed:

```json
{
  "runner": "command",
  "argv": ["node", "--test", "--test-reporter=tap", "{proof}"],
  "listArgv": ["node", "--test", "--test-reporter=tap", "--test-skip-pattern=.*", "{proof}"],
  "assertPatterns": ["\\bassert(\\.\\w+)?\\("],
  "importPatterns": ["from\\s+['\"]([^'\"]+)['\"]", "require\\(['\"]([^'\"]+)['\"]\\)"],
  "cwd": ".",
  "testDir": "tests",
  "proofGlob": "*.test.ts"
}
```

Verified with Node 25.8.0: `--test-skip-pattern=.*` loads the proof module and its
imports without running test bodies. The TAP reporter emits the file identifier
even though its tests are filtered out. Load/syntax errors still fail collection.
Ordinary `argv` then runs each bead's proof on a disposable copy of the stubs;
each must exit nonzero. Launch failures, timeouts and terminated processes do not
count as red proofs. Do not use `--test-only` as listing: it can run marked tests.

`listArgv` accepts zero or one standalone `{proof}` slot. With the slot, Harness
lists each protected or declared proof separately. Without it, Harness runs the
listing once and requires all proof files in that output (for example, replace
the slot with `tests/*.test.ts` for Node's built-in glob handling). Stdout must
contain one proof-file identifier per line or TAP `ok N - <proof-file>` result
lines. Identifiers may be repository-relative or cwd-relative; path separators
are normalized. Missing files, nonzero listing exit, or TAP `not ok`/nonzero fail
counts block collection. Merely mentioning a path in an error is insufficient.

Each bead proof must match at least one assertion regex and import a file owned
by that bead. Import regexes must capture the specifier in group 1. Relative
specifiers resolve against the proof's directory, checking the exact file,
`.ts`, `.js`, `index.ts`, `index.js`, and `.js` specifiers pointing to `.ts` source.
These are declared regex checks, not language parsing or assertion dataflow
analysis. Requirements must still trace to protected test nodes, as for unittest.
Prospective audits do not establish test satisfiability. Worker packets carry
the published prospective warning from the canonical rendered PLAN.md; oracle
packets retain their existing bytes. A subsequent passing oracle audit upgrades
the recorded plan proof level to oracle-verified.

The real fixture at `tests/python/core/fixtures/ts_runner/` contains separate
sum/format beads, throwing TypeScript stubs, protected Node proofs and reference
implementations. Its tests exercise the CLI prospective audit, both red proofs,
rejection of vacuous/unowned/load-failing proofs, and recorded oracle upgrade.

## Derived proof and trust

Harness derives the protected test directory from unittest `-s` (default `tests`)
or command testDir. Bead/integration proofModule remains the canonical selector.
For unittest, argv is exactly `[sys.executable, *validation, proofModule]`.
For command, Harness selects a concrete repo-relative file under testDir that
matches proofGlob, then inserts that path into argv without shell formatting.
`Runner.proof_paths(repo)` lists existing proof files: declared bead/integration
patterns for unittest (fallback `test*.py`), or proofGlob for command, sorted.
Unittest argv_for accepts a filename pattern; command argv_for accepts a concrete
repo-relative path and rejects patterns and paths outside the protected directory.
Discovery rejects resolved test/proof paths escaping their owning root.

Harness never trusts worker summaries, status JSON, exit claims or result files.
The controller launches the runner against the exact candidate and observes exit.
Nonzero exit, failed launch, timeout or interruption cannot pass. A report saying
pass while exit is 1 is a failure. Exit zero proves the runner check, not worker
completion, acceptance of the full product or real-provider execution.
An optional future machine-readable report must be produced by that invocation
in a fresh Harness-controlled temp directory, with a controller-chosen path and
no preexisting report. It may add diagnostic/required check evidence, never turn
a failing exit into pass. Protect its producer/config; temp location alone does
not make worker-controlled content authoritative. Report parsing is not prototyped.

## Integrity and execution

Bind the complete protected test directory, selected proof, test configuration,
runner scripts and toolchain lockfiles into candidate/verification identity.
The entire verification object, including singleTest, is part of planRevision;
changing these invalidates prior evidence. Workers cannot own protected proofs
or verification driver/configuration; product source may remain worker-owned.
Pass argv lists with shell=False, except for the bounded Windows batch command
line below; no shell snippets or user-provided string interpolation.
Resolve command executables with shutil.which on a controller-controlled PATH.
Windows shims follow the bounded launch policy below.
Executable identity/version must be captured by the future executor.
Environment isolation belongs to that executor: create an allowlisted environment,
exclude provider credentials and inherited runtime injection options, pin PATH and
toolchain locations, and use a fresh temp/home/cache policy per invocation.
These environment and identity policies are design requirements, not implemented
by this resolver. It does not create a sandbox or launch processes.

## Windows batch shims

Resolve executables with `shutil.which`. A resolved `.cmd` or `.bat` (case
insensitive) on Windows runs as the command line string
`cmd.exe /d /s /c "<subprocess.list2cmdline([resolved, *args])>"`,
still with shell=False. The outer quotes let `/s` strip exactly the first and
last quote while preserving inner quoted shim paths and arguments with spaces;
passing a string avoids Python re-escaping those inner quotes as an argv element.
Non-shim executables continue to use argv lists.
Compilation rejects cmd metacharacters (`& | < > ^ % ! "`, CR and LF) in
batch argv, listArgv, cwd, testDir, proofGlob, declared proof selectors and singleTest.
The rendered argv is checked again before launch, including the resolved path.
Quotes in the resolved shim path are rejected as well.
Shell expressions and environment expansion are not supported.

## Consumer integration

Plan compilation/loading validates the object and hashes its full runner contract
and protected inputs. Graph protection uses the derived directory even when
requirementFiles omits it. Keep one canonical resolver, not adapter-specific logic.
Explicit runner packets present exact argv/cwd and concrete proof paths; Python
AST test snippets remain unittest-specific. Command packets must not invent a
Python proof bridge.
Audit executes the same runner for baseline, revised tests and available oracle;
missing tools/reports/timeouts are explicit nonpasses, never worker summaries.
Integration executes against the candidate checkout, captures observed exit and
identity, and binds evidence to that candidate and plan revision.
Acceptance consumes controller-produced evidence for the exact revision and
checks the required integration proof; it cannot infer pass from worker prose.
Dispatch baseline, bead, integration and revalidation share runner argv and
exit-only verdicts. Verification identity covers the whole testDir plus existing
requirementFiles and the explicit runner object. Put verification scripts,
configuration and lockfiles under testDir or in requirementFiles so they are
protected and bound to that identity. Audit overlays testDir even when omitted
from requirementFiles. Launch errors and timeouts produce failed evidence.

## Migration and open questions

Existing manifests and byte-identical unittest commands need no migration.
The command fixture proves compile, audit and mock-reference dispatch, including
a forged PASS report with nonzero exit and Windows batch execution.
Offline toolchains must be preprovisioned from pinned caches; verification never
installs packages or downloads dependencies. Missing tools are explicit blockers.
Timeout budget and process-tree termination need an owning executor contract;
preserve the current 120-second policy until a deliberate plan-bound change.
Nested proof patterns, report schemas and zero-test
success detection need agreement before integration; do not add speculative adapters.
