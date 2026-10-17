"""Create-once local Popen provenance, not authentication or live-worker proof."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from codex_harness.core.record_io import open_record_read, write_record_json_once

SCHEMA = "seldon/local-process-provenance-v1"
SCHEMA_V2 = "seldon/local-process-provenance-v2"
FILENAME = "process-provenance.json"
FIELDS = (
    "runId",
    "bead",
    "requirementId",
    "planRevision",
    "requirementRefs",
    "specRefs",
    "ownedFiles",
    "provider",
    "model",
    "effort",
    "command",
    "launch",
    "cwd",
    "baseCommit",
    "adapterCommit",
    "executableSha256",
    "processId",
    "startedAt",
    "processObservedAt",
)


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def normalized_owned(files: Sequence[str]) -> list[str]:
    if not isinstance(files, list | tuple) or any(not isinstance(x, str) for x in files):
        raise ValueError("ownedFiles must be a sequence of paths")
    return [str(PurePosixPath(x.replace("\\", "/"))) for x in files]


def identity_from_request(request: Mapping, *, process_id: int, process_observed_at: str) -> dict:
    """Project exact launch originals; PID is supplied by the owned Popen caller."""
    identity = {
        key: copy.deepcopy(request[key])
        for key in FIELDS
        if key not in {"processId", "processObservedAt"}
    }
    if "adapterProvenance" in request:
        source = request["adapterProvenance"]
        if (
            not isinstance(source, dict)
            or set(source) != {"schema", "origin", "distribution", "version", "pythonSourceSha256"}
            or source["schema"] != "seldon/adapter-source-v1"
            or source["origin"] not in {"source-tree", "installed-package"}
            or source["distribution"] != "seldon"
            or (source["version"] is not None and not isinstance(source["version"], str))
            or not isinstance(source["pythonSourceSha256"], str)
            or len(source["pythonSourceSha256"]) != 64
            or any(c not in "0123456789abcdef" for c in source["pythonSourceSha256"])
            or (
                request["adapterCommit"] is not None
                and (not isinstance(request["adapterCommit"], str) or not request["adapterCommit"])
            )
            or (source["origin"] == "installed-package" and request["adapterCommit"] is not None)
        ):
            raise ValueError("adapterProvenance invalid")
        identity["adapterProvenance"] = copy.deepcopy(source)
    elif not isinstance(request["adapterCommit"], str) or not request["adapterCommit"]:
        raise ValueError("adapterCommit unavailable")
    if type(process_id) is not int or process_id <= 0:
        raise ValueError("processId must be a positive owned child PID")
    if not isinstance(process_observed_at, str) or not process_observed_at:
        raise ValueError("processObservedAt unavailable")
    command = request["command"]
    if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
        raise ValueError("command must be a nonempty argv list")
    if request["mode"] != "provider-normal" or not isinstance(request["prompt"], str):
        raise ValueError("provider-normal prompt unavailable")
    if request["launch"]["command"] != command:
        raise ValueError("launch command mismatch")
    if request["launch"]["cli"] != request["provider"]:
        raise ValueError("launch provider mismatch")
    if not isinstance(request["requirementRefs"], list) or (
        request["specRefs"] is not None and not isinstance(request["specRefs"], list)
    ):
        raise ValueError("publication references invalid")
    for key in (
        "runId",
        "bead",
        "requirementId",
        "planRevision",
        "cwd",
        "baseCommit",
        "provider",
        "startedAt",
    ):
        if not isinstance(request[key], str) or not request[key]:
            raise ValueError(f"{key} unavailable")
    for key in ("model", "effort", "executableSha256"):
        if request[key] is not None and not isinstance(request[key], str):
            raise ValueError(f"{key} invalid")
    identity.update(
        ownedFiles=normalized_owned(request["ownedFiles"]),
        cwd=str(Path(request["cwd"]).resolve()),
        processId=process_id,
        processObservedAt=process_observed_at,
        packetUtf8Sha256=hashlib.sha256(request["prompt"].encode("utf-8")).hexdigest(),
        commandSha256=_digest(command),
    )
    return identity


def publish(
    attempt_dir: Path, request: Mapping, *, process_id: int, process_observed_at: str
) -> dict:
    original = {
        "schema": SCHEMA_V2 if "adapterProvenance" in request else SCHEMA,
        "identity": identity_from_request(
            request, process_id=process_id, process_observed_at=process_observed_at
        ),
    }
    original["identity"]["attemptDirectory"] = str(Path(attempt_dir).resolve())
    envelope = {**original, "digest": _digest(original)}
    write_record_json_once(Path(attempt_dir) / FILENAME, envelope)
    return envelope


def _mock_consistent(request, response, record) -> bool:
    mode = request.get("mode")
    return bool(
        mode in {"mock:test-worker", "mock:reference", "mock:noop"}
        and response.get("mode") == mode
        and record.get("attempt", {}).get("mode") == mode
        and request.get("command") == [mode]
        and request.get("launch") == {"command": [mode]}
        and response.get("command") == [mode]
        and response.get("launch") == {"command": [mode]}
        and response.get("isMocked") is True
        and response.get("didRunProvider") is False
        and record.get("isMocked") is True
        and record.get("didRunProvider") is False
    )


def read_and_compare(
    attempt_dir: Path,
    request: Mapping,
    response: Mapping,
    *,
    record: Mapping,
    expected_owned_files: Sequence[str] | None,
) -> dict:
    """Read the fixed selected original; report local consistency, never admission."""
    path = Path(attempt_dir) / FILENAME

    def observation(state, code, digest=None):
        return {"state": state, "code": code, "path": str(path), "digest": digest}

    try:
        if ("adapterProvenance" in request or "adapterProvenance" in response) and (
            ("adapterProvenance" in request) != ("adapterProvenance" in response)
            or request.get("adapterProvenance") != response.get("adapterProvenance")
        ):
            return observation(
                "mismatch", "attempt-provenance-identity-mismatch:result:adapterProvenance"
            )
        if _mock_consistent(request, response, record):
            return observation("source-only", "mock-source-only")
        if not path.resolve().is_relative_to(Path(attempt_dir).resolve()):
            return observation("mismatch", "attempt-provenance-outside-attempt")
        selected = record.get("evidenceDir")
        if (
            selected is not None
            and Path(attempt_dir).resolve() != (Path(selected) / "attempt").resolve()
        ):
            return observation("mismatch", "attempt-provenance-selected-attempt-mismatch")
        with open_record_read(path) as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return observation("mismatch", "attempt-provenance-oversized")
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or envelope.get("schema") not in {SCHEMA, SCHEMA_V2}:
            return observation("unavailable", "attempt-provenance-schema-unavailable")
        schema = envelope["schema"]
        extra_fields = {"adapterProvenance"} if schema == SCHEMA_V2 else set()
        identity = envelope.get("identity")
        if not isinstance(identity, dict) or set(identity) != set(FIELDS) | extra_fields | {
            "packetUtf8Sha256",
            "commandSha256",
            "attemptDirectory",
        }:
            return observation("unavailable", "attempt-provenance-schema-incomplete")
        digest = _digest({"schema": schema, "identity": identity})
        if set(envelope) != {"schema", "identity", "digest"} or envelope.get("digest") != digest:
            return observation("mismatch", "attempt-provenance-digest-mismatch")
        if identity["attemptDirectory"] != str(Path(attempt_dir).resolve()):
            return observation("mismatch", "attempt-provenance-attempt-directory-mismatch", digest)
        for label, value in (("request", request), ("result", response)):
            if ("adapterProvenance" in value) != (schema == SCHEMA_V2):
                return observation(
                    "mismatch",
                    f"attempt-provenance-identity-mismatch:{label}:adapterProvenance",
                    digest,
                )
            projected = identity_from_request(
                value, process_id=value["processId"], process_observed_at=value["processObservedAt"]
            )
            for key in identity:
                if key == "attemptDirectory":
                    continue
                if projected[key] != identity[key]:
                    return observation(
                        "mismatch", f"attempt-provenance-identity-mismatch:{label}:{key}", digest
                    )
            if value.get("processProvenanceDigest") not in (None, digest):
                return observation(
                    "mismatch", f"attempt-provenance-identity-mismatch:{label}:digest", digest
                )
        for key in (
            "runId",
            "bead",
            "planRevision",
            "requirementRefs",
            "specRefs",
            "provider",
            "model",
            "effort",
        ):
            if record.get(key) != identity[key]:
                return observation(
                    "mismatch", f"attempt-provenance-identity-mismatch:record:{key}", digest
                )
        if (
            hashlib.sha256(record["prompt"].encode("utf-8")).hexdigest()
            != identity["packetUtf8Sha256"]
        ):
            return observation(
                "mismatch", "attempt-provenance-identity-mismatch:record:prompt", digest
            )
        attempt = record.get("attempt") or {}
        if ("adapterProvenance" in attempt or "adapterProvenance" in identity) and (
            ("adapterProvenance" in attempt) != ("adapterProvenance" in identity)
            or attempt.get("adapterProvenance") != identity.get("adapterProvenance")
        ):
            return observation(
                "mismatch", "attempt-provenance-identity-mismatch:attempt:adapterProvenance", digest
            )
        if not all(key in attempt for key in ("cwd", "baseCommit")):
            return observation(
                "unavailable", "attempt-provenance-selected-worktree-unavailable", digest
            )
        for key in (*FIELDS, *extra_fields):
            if key in attempt:
                value = str(Path(attempt[key]).resolve()) if key == "cwd" else attempt[key]
                if value != identity[key]:
                    return observation(
                        "mismatch", f"attempt-provenance-identity-mismatch:attempt:{key}", digest
                    )
        if (
            expected_owned_files is not None
            and normalized_owned(expected_owned_files) != identity["ownedFiles"]
        ):
            return observation(
                "mismatch", "attempt-provenance-identity-mismatch:ownedFiles", digest
            )
        if identity["executableSha256"] is None:
            return observation("incomplete", "attempt-provenance-executable-unavailable", digest)
        return observation("consistent", "local-process-provenance-consistent", digest)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return {
            **observation("unavailable", "attempt-provenance-unavailable"),
            "detail": f"{type(error).__name__}: {error}",
        }
