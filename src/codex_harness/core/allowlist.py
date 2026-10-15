"""Explicit external dependencies and legacy trust-spine import bans."""

# Standalone core has no legacy package dependencies, eager or deferred.
ALLOWED_EXTERNAL_MODULES: frozenset[str] = frozenset()

# Bare names match fragments; dotted names match exactly, except trailing-underscore
# entries, which match module prefixes.
FORBIDDEN: frozenset[str] = frozenset(
    {
        "filesystem_authority",
        "effective_worker_authority",
        "owned_process",
        "windows_launch_backend",
        "native_sandbox_provider",
        "worker_provider_port",
        "codex_harness.sessions.linux_",
        "codex_harness.execution.linux_",
        "codex_harness.state.store",
        "codex_harness.state.worker_process_repository",
        "codex_harness.operator.api",
        "codex_harness.execution.worker_lifecycle",
    }
)
