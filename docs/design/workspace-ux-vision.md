# Workspace UX: ten improvements

1. **What's true now.** A server-computed verdict combines acceptance, scenario results, open blockers, deferred gaps and stale evidence; revision/run/review chips retain full identities on hover.
   Why: one readable sentence establishes the evidence boundary before exploring details.
2. **Trace explorer.** Selecting a spine card highlights ancestors and descendants, dims unrelated cards and draws SVG connectors; Escape clears the trace while retaining the inspector.
   Why: the connection between intent and work should be inspectable without losing context.
3. **Run timeline.** Provider-coloured bars use recorded attempt timestamps, outline the critical path and include an integration completion marker; duration-only records use dependency-relative positions.
   Why: concurrency and the longest dependency chain become visible without inventing timing.
4. **Efficiency trend.** Current commands, errors, denied commands, input/output tokens and wall time compare with the preceding run of the same plan; missing comparisons stay grey and explicit.
   Why: run cost and friction belong beside the work, with unknown measurements preserved.
5. **First-use transcript.** The first-moment scenario displays portable command aliases, actual stdout/stderr, per-step results and a command-copy control in a dark terminal card.
   Why: the first user experience is easier to judge as an observable sequence.
6. **Known gaps.** An amber Definition card groups open and deferred findings with severity, selectable node references and recorded reasons in individual disclosures.
   Why: proxy acceptance must leave its remaining limitations visible.
7. **Stepper memory.** Keyboard-operable phase circles show recorded transition time, approval/proxy identity and gate evidence; missing timestamps and approvals remain explicit.
   Why: completed phases need history, rather than an unexplained checkmark.
8. **Page-matched diagrams.** Trace and bead diagrams use CSS-derived Mermaid theme variables; bead status colours, full-screen expansion, zoom and fit support both themes.
   Why: diagrams should read as part of the workspace and remain inspectable at every size.
9. **Find anything.** Slash focuses live search across nodes, current work and findings; results open the relevant tab and inspector, with arrow/Enter and focused-result j/k navigation; ? opens shortcut help.
   Why: large plans need a short path to a specific piece of context without intercepting typed letters.
10. **Quiet polish.** Consistent inline SVG navigation, compact numbers, relative times with absolute titles, initial skeletons, responsive internal scrollers and reduced-motion support finish the read-only page.
    Why: useful detail should preserve calm, accessible scanning and stable polling.

## Evidence limits observed in the replay

- Acceptance build `c43419c` differs from the dispatcher candidate; the verdict uses the acceptance run's recorded revision.
- Integration receipts record completion, but no measured integration duration; the timeline labels its marker accordingly.
- Acceptance scenarios without canonical node references remain unlinked; the inspector reports no upstream links rather than inferring lineage.
- The preceding failed run has missing bead metrics; those deltas say no comparison rather than assuming zero.
- Phase history describes recorded historical transitions, not approval of every subsequent revision.
- Browser screenshots and check output belong in untracked `output/playwright/`.
- Nonblocking server friction: navigating during a status GET can log `ConnectionAbortedError`; successful polls returned 200. Owner: status_server, outside this lane. Exit check: interrupted client requests leave no server traceback.
