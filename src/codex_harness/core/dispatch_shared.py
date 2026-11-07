"""Shared dispatch observation labels; no scheduling or imports facade."""

from pathlib import Path

STATUS_VALIDATED = "plan-validated"
STATUS_VALIDATED_NO_INTEGRATION = "plan-validated (no integration proof declared)"
STATUS_MOCK_ONLY = "source-test-only (mocked provider)"
STATUS_PERMISSION_HELD = "held: permission guard"
STATUS_AUDIT_HELD = "held: denial audit inconclusive"
STATUS_PROVENANCE_HELD = "held: local process provenance"
DENIAL_HELD_STATUSES = {STATUS_PERMISSION_HELD, STATUS_AUDIT_HELD}
DENIAL_CAPABILITIES = {
    "port": "provider-normal-headless-cli",
    "supportsSynchronousDenialInterception": False,
    "supportsPostHocDenialHold": True,
    "canPreventProviderInternalRetry": False,
    "structuredDenialFormats": [
        "claude: system.permission_denied, tool_result_meta.user-rejected, "
        "result.permission_denials",
        "codex: denied command_execution/file_change/mcp_tool_call item status",
    ],
    "otherTelemetry": "unknown; absence of observed denial is not authorization",
}
HARNESS_ROOT = Path(__file__).resolve().parents[3]
