# Acceptance product revision scope

The declared product directory owns the acceptance revision. Git contributes
HEAD, tracked files and relevant untracked files only when `--show-toplevel`
resolves to that directory. This includes linked worktrees with a `.git` file.
A plain product or declared subdirectory below a Git ancestor uses its own
filesystem bytes, independent of the ancestor's HEAD and ignore rules. The
existing `.git`, `.codex-harness` and Python/test/tool cache exclusions remain.
A broken product-local Git marker refuses revision observation.

This repairs input scope, not the `tree-bytes-v2` or legacy `git-diff-v1`
serialization. Proper Git roots/worktrees retain their digests and Git ignore
behavior. Plain products previously observed without an ancestor retain their
filesystem digests. Both current and legacy readers use the corrected scope.

Old ancestor-bound records cannot prove which product bytes were accepted.
They become `product-changed`, even if today's bytes happen to be unchanged.
They require fresh scenarios, review and approval; automatic approval carry or
legacy rebind is refused. Original records remain intact. Legitimate legacy
records whose complete product binding still matches retain the existing
scenario rerun and attributable rebind behavior.

Focused real-Git tests establish source behavior only. They are not provider
execution or full-vision acceptance evidence.
