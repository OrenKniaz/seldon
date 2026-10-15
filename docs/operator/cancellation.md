# Selected-attempt cancellation

Request cancellation from the existing run evidence:

```text
run_plan cancel --run <run-directory> --bead <bead-id> [--started-at <request-startedAt>]
```

The command writes `cancel-request.json` beside the selected attempt request.
Its target binds run, bead, request startedAt, cwd, base commit, requirement and
the observed process ID. The caller cannot supply a PID. Stale targets, terminal
results and evidence paths escaping the run are refused. A request is not proof
of a stop, and no controller after restart may kill the recorded PID.

Only the launching controller consumes the request and calls its own live
`Popen.kill()` once. `cancel-observation.json` records disposition. Normal
completion observed before cancellation wins; a late request cannot rewrite a
completed result. A stop denial or unsupported stop gets no alternate method.

Terminal attempt results use `status: cancelled`, existing exit/finishedAt fields
and separate cleanup observations. `controllerProcessExited` reports whether
that child's exit was observed. Descendant cleanup remains `UNVERIFIED` with
`descendantsStopped: null`; this operation makes no graceful/tree-wide claim.
If the stop failed, finishedAt is the controller's observation time, not a claim
that the child stopped. The recorded exit code may remain null.

Cancelled work cannot integrate or release dependents. Ordinary resume retains
it. Once controller exit is observed, use the existing same-run resume command
with `--retry-blocked <bead-id>` to explicitly retry into a new attempt/worktree.
Other validated beads remain reusable. Descendant uncertainty stays recorded;
no global process query or reconstructed-PID cleanup is provided.

Permission-denial and inconclusive-audit holds remain independent and sticky.
Cancellation preserves raw telemetry and its hold even though the terminal
attempt status remains cancelled. Targeted retry cannot clear those holds.
Local Python process tests and injected builders establish source mechanics;
they do not establish actual-provider or full-vision acceptance.

Mutable JSON readers and writers use `record_io.open_record_read`,
`read_record_json` and `write_record_json`. On Windows, participating operations
serialize by canonical record path using a session-local named mutex, with a
five-second wait before opening the file. Writers then publish by atomic
replacement. Readers must close promptly. There is no persistent lock file,
service, settings change, ACL change or retry of a refused file operation.
Timeouts and access failures propagate. Nonparticipating readers and processes
in other Windows sessions are outside this sharing contract; refusal remains
possible and is not retried. POSIX keeps ordinary atomic replacement semantics.

A failure publishing the owned child's PID after launch triggers one stop on
that live `Popen`. The result retains the publication error, actual exit
observation and unknown descendant cleanup. The failed request publication is
not retried during finalization; canonical and requirement result records are
still attempted. If cancellation observation publication fails after a stop,
the in-memory stop latch prevents a second stop or alternate method.
An interrupted attempt with an owned-stop observation requires targeted retry
and an observed controller exit just as cancellation does. Retry and ambiguous
relaunch flags cannot clear an unobserved exit; later natural process exit does
not rewrite the saved observation.
Malformed or unsupported cached control observations produce an attempt-control
observation failure through the same live-handle cleanup path. Their original
bytes remain unchanged. They do not establish cancellation or permission denial,
and a valid cached stop observation cannot trigger another stop.
