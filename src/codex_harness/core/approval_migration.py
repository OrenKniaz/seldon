"""One-way, transactional import of retained V1 originals; no V1 consent resolver."""

import hashlib
import json

from codex_harness.core.record_io import read_record_json


def _review_digest(original):
    return hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()


def _declared_actor(request, response):
    from codex_harness.core.planner_confirmations import CHAT, NATIVE, _chat_evidence

    channel = request.get("channel", NATIVE)
    if response.get("provenance") != channel:
        return None
    if channel == NATIVE:
        return "native-client-declared-user"
    if channel != CHAT:
        return None
    try:
        saved = response["chat"]
        action, content, evidence = _chat_evidence(
            request["request_id"],
            request.get(
                "response_revision",
                request.get("plan_revision", request.get("scope", {}).get("publicationRevision")),
            ),
            saved["quoted_message"],
            saved["quote_sha256"],
            saved["actor_label"],
        )
    except (KeyError, TypeError, ValueError):
        return None
    return (
        saved["actor_label"]
        if (
            saved == evidence
            and response.get("action") == action
            and response.get("content") == content
        )
        else None
    )


def _planning_review_originals(store):
    """Check retained originals against their admission-time budget in event order."""
    root = store.path.parent / "planning-review-budget"
    registrations = store.connection.execute(
        "SELECT payload_json FROM events WHERE kind='planning-review-budget-registered' ORDER BY id"
    ).fetchall()
    claims = store.connection.execute(
        "SELECT id, payload_json FROM events WHERE kind='planning-review-start-claimed' ORDER BY id"
    ).fetchall()
    amendments = store.connection.execute(
        "SELECT id,payload_json FROM events WHERE kind='planning-review-budget-amended' ORDER BY id"
    ).fetchall()
    if not (registrations or claims or amendments or root.exists()):
        return None, []
    try:
        if len(registrations) != 1 or root.is_symlink():
            raise ValueError("registration required")
        original = json.loads(registrations[0][0])
        if (
            not isinstance(original, dict)
            or set(original) != {"schema", "workspace", "campaign", "maxStarts"}
            or original["schema"] != "seldon/planning-review-budget-v1"
            or original["workspace"] != str(store.workspace)
            or not isinstance(original["campaign"], str)
            or not original["campaign"].strip()
            or type(original["maxStarts"]) is not int
            or not 1 <= original["maxStarts"] <= 10
            or read_record_json(root / "registration.json") != original
        ):
            raise ValueError("registration changed")
        digests = [(0, _review_digest(original))]
        for event_id, payload in amendments:
            amendment = json.loads(payload)
            if amendment["previousDigest"] != _review_digest(original):
                raise ValueError("amendment changed")
            original = {**original, "maxStarts": amendment["maxStarts"]}
            digests.append((event_id, _review_digest(original)))
        records = [json.loads(row[1]) for row in claims]
        if len(records) > original["maxStarts"]:
            raise ValueError("claims exceed original cap")
        expected = {f"{ordinal:08d}.json" for ordinal in range(1, len(records) + 1)}
        directory = root / "claims"
        if directory.is_symlink() or {path.name for path in directory.iterdir()} != expected:
            raise ValueError("claim originals missing or orphaned")
        for ordinal, record in enumerate(records, 1):
            if (
                not isinstance(record, dict)
                or set(record) != {"registrationDigest", "ordinal", "reviewer", "pid"}
                or record["registrationDigest"]
                != next(
                    digest for event, digest in reversed(digests) if event < claims[ordinal - 1][0]
                )
                or type(record["ordinal"]) is not int
                or record["ordinal"] != ordinal
                or not isinstance(record["reviewer"], dict)
                or type(record["pid"]) is not int
                or read_record_json(directory / f"{ordinal:08d}.json") != record
            ):
                raise ValueError("claim original changed")
        return original, records
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise ValueError("planning-review-budget-invalid-original") from error


def migrate(store):
    from codex_harness.core import approvals
    from codex_harness.core.planner_jobs import _digest, _inside

    if approvals.records(store, "approval-v2-migrated"):
        if not store.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage_ledger'"
        ).fetchone():
            raise ValueError("usage-ledger-missing-after-conversion: preserve state and diagnose")
        return
    store.connection.execute(
        "CREATE TABLE IF NOT EXISTS usage_ledger (id INTEGER PRIMARY KEY, "
        "kind TEXT NOT NULL, source TEXT UNIQUE NOT NULL, payload_json TEXT NOT NULL)"
    )
    original, claims = _planning_review_originals(store)
    has_reviews = store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='review_runs'"
    ).fetchone()
    if (
        original is None
        and has_reviews
        and store.connection.execute(
            "SELECT 1 FROM review_runs WHERE didRunProvider=1 AND isMocked=0 LIMIT 1"
        ).fetchone()
    ):
        raise ValueError("usage-migration-missing-review-claims: preserve originals and diagnose")
    if (
        has_reviews
        and len(claims)
        < store.connection.execute(
            "SELECT count(*) FROM review_runs WHERE didRunProvider=1 AND isMocked=0"
        ).fetchone()[0]
    ):
        raise ValueError("usage-migration-inconsistent-review-claims")
    for claim in claims:
        _import(store, "review", "v1-review:" + str(claim["ordinal"]), claim)
    jobs = store.path.parent / "worker-jobs"
    for directory in jobs.iterdir() if jobs.is_dir() else []:
        _inside(store.workspace, directory)
        claims_dir = directory / "provider-call-budget"
        if not claims_dir.exists():
            continue
        request = read_record_json(directory / "request.json")
        if request["requestDigest"] != _digest(
            {k: v for k, v in request.items() if k != "requestDigest"}
        ):
            raise ValueError("usage-migration-worker-original-invalid")
        paths = sorted(claims_dir.glob("*.json"))
        cap = request["options"]["callCap"]
        if (
            type(cap) is not int
            or len(paths) > cap
            or {p.name for p in claims_dir.iterdir()}
            != {f"{i:08d}.json" for i in range(1, len(paths) + 1)}
        ):
            raise ValueError("usage-migration-worker-claims-inconsistent")
        for ordinal, path in enumerate(paths, 1):
            claim = read_record_json(path)
            if (
                path.stem != f"{ordinal:08d}"
                or type(claim["ordinal"]) is not int
                or claim["ordinal"] != ordinal
                or claim["requestDigest"] != request["requestDigest"]
            ):
                raise ValueError("usage-migration-worker-claim-invalid")
            _import(store, "worker", f"v1-worker:{directory.name}:{ordinal}", claim)
    # V1 combined cards displayed a complete canonical snapshot and lock effect.
    # Excerpt-only native cards cannot establish the complete V2 scope.
    responses = approvals.records(store, "confirmation-responded")
    for kind, gates in (
        ("A", {"align", "vision-lock", "align+vision-lock"}),
        ("B", {"plan-execution"}),
    ):
        request = next(
            (r for r in approvals.records(store, "confirmation-requested") if r["gate"] in gates),
            None,
        )
        response = next(
            (r for r in responses if request and r["request_id"] == request["request_id"]), {}
        )
        actor = _declared_actor(request, response) if request else None
        payload = request.get("subject") if request else None
        if request and payload is None and kind == "A" and "\nControls:" in request["card"]:
            card = request["card"]
            raw = card.split("\nControls:", 1)[1].split("\n", 1)[1]
            try:
                snapshot, _ = json.JSONDecoder().raw_decode(raw)
                visions = [n for n in snapshot["nodes"] if n["kind"] == "vision"]
            except (ValueError, KeyError, TypeError):
                visions = []
            expected_digest = hashlib.sha256(card.encode("utf-8")).hexdigest()
            if (
                len(visions) == 1
                and request.get("workspace") == str(store.workspace)
                and request.get("card_digest") == expected_digest
                and response.get("card_digest") == expected_digest
                and approvals.EFFECTS["A"] in card
                and request["gate"] == "align+vision-lock"
                and all(
                    store.connection.execute(
                        "SELECT 1 FROM approvals WHERE id=? AND gate=? AND plan_revision=? "
                        'AND "by"=? AND statement=?',
                        (
                            response.get("approval_ids", {}).get(gate),
                            gate,
                            request["plan_revision"],
                            response.get("provenance"),
                            response.get("content", {}).get("statement"),
                        ),
                    ).fetchone()
                    for gate in ("align", "vision-lock")
                )
                and approvals.intent(visions[0]) == approvals.subject(store, "A")["scope"]
            ):
                payload = approvals.subject(store, "A")
        if (
            payload
            and actor is not None
            and response.get("state") == "approved"
            and response.get("action") == "accept"
            and response.get("content", {}).get("approve") is True
            and response["content"].get("conditions") == ""
            and response.get("provenance") == request.get("channel", "mcp-client-elicitation")
            and (
                request.get("subject") is None
                or request["card"] == approvals.render(payload, request.get("diff", []))
            )
            and payload == approvals.subject(store, kind, request.get("proposedAction"))
        ):
            approvals.record_approval(
                store,
                {**request, "subject": payload},
                words=response["content"]["statement"],
                actor=actor,
                channel=response["provenance"],
                affirmative=True,
                state="accept",
            )
    for consumed in approvals.records(store, "worker-execution-consumed"):
        request = next(
            (
                r
                for r in approvals.records(store, "worker-execution-requested")
                if r["request_id"] == consumed["request_id"]
            ),
            {},
        )
        response = next(
            (
                r
                for r in approvals.records(store, "worker-execution-responded")
                if r["request_id"] == consumed["request_id"]
            ),
            None,
        )
        if (
            not response
            or request.get("scope") != consumed["scope"]
            or response["state"] != "approved"
            or response["scope"] != consumed["scope"]
            or response.get("action") != "accept"
            or response.get("content", {}).get("approve") is not True
            or response["content"].get("conditions") != ""
            or not isinstance(response["content"].get("statement"), str)
            or not response["content"]["statement"].strip()
            or response.get("provenance") != request.get("channel", "mcp-client-elicitation")
            or _declared_actor(request, response) is None
        ):
            raise ValueError("usage-migration-admission-original-invalid")
        store._event(
            "worker-job-admitted",
            dict(
                id=consumed["request_id"],
                jobId=consumed["jobId"],
                scope=consumed["scope"],
                channel=response["provenance"],
            ),
        )
    store._event("approval-v2-migrated", dict(reviewClaims=len(claims), legacyBudget=original))


def _import(store, kind, source, payload):
    store.connection.execute(
        "INSERT INTO usage_ledger (kind, source, payload_json) VALUES (?, ?, ?)",
        (kind, source, json.dumps(payload, sort_keys=True)),
    )
