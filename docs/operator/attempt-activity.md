# Read-only attempt activity

`observe_attempt_activity(attempt_dir, *, now=None, no_progress_seconds=300)`
reads the existing canonical request/result and output-file metadata. It creates
no files or planning state, queries no PID/process list, launches nothing, and performs no recovery.
The status API confines these reads to the run's canonical evidence root. JSON
records are bounded to 1 MiB; oversized, changing, malformed or unavailable
records are unobservable. No prompts, stream contents or credentials are returned.

Request/result reads participate in the frozen `record_io.open_record_read`
contract; canonical and flat run JSON reads use `read_record_json`. Participating
Windows readers and writers serialize before opening using a session-local
named mutex with a five-second bound. POSIX retains ordinary atomic-replacement
behavior. No ACL changes, permission bypass, I/O retry or fallback is added;
permission refusals/timeouts remain unknown/unavailable. Nonparticipating readers
can still interfere with publication. This does not make the entire multi-record
status snapshot atomic or establish live process/cleanup proof.

Each run bead gains `activity`: `state`, `recordedStatus`, nullable `isTerminal`,
`processLiveness`, nullable `lastObservedActivityAt` and `secondsSinceActivity`,
`noProgressThresholdSeconds`, nullable `stuck`, and `reason`.

- A valid launching/running request with output-file metadata and no result can
  yield a file-inactivity interval. `stuck` means only that this interval reached
  the threshold. Empty output files provide an initial creation/activity time;
  byte timestamps do not establish semantic progress. Missing result leaves
  `state` and `processLiveness` unknown, including after a controller crash.
- A matching terminal result (`cancelled`, `exited`, `timed-out`, `launch-failed`,
  `observation-failed`) takes precedence over lagging run status and sets
  `isTerminal=true`, `stuck=false`. It does not prove descendant cleanup or safe
  stop: process liveness stays unknown even when a receipt records termination.
  Matching compares the present canonical `runId`, `bead`, `cwd`, `baseCommit`,
  `requirementId`, `planRevision` and `startedAt` fields. A field present on only
  one side, or unequal values, makes the result ambiguous rather than terminal.
- Missing/partial/malformed records, missing streams, mismatched identities,
  future clocks and inaccessible evidence retain null observations. Holds and
  ambiguous launches remain held/ambiguous; neither a quiet interval nor a
  terminal receipt authorizes relaunch, cancellation, integration or acceptance.

Run `activity` and grouped `agents`/`currentAgents[].activity` expose
`stuckCount`, `unknownCount`, `terminalCount`, `lastObservedActivityAt`,
`maxSecondsSinceActivity` and the threshold. `stuckCount` counts only measurable
flags and is null when none are measurable, as is `terminalCount`;
`unknownCount` counts unmeasurable
flags, not unknown process liveness. Original status/efficiency fields remain;
agent `active` counts recorded running beads except those with terminal receipts,
and is not a verified live-process count. No worker/model availability or product
completion is inferred. The default threshold is advisory, never a new gate.
