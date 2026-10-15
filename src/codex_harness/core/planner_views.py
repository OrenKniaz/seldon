"""Pure, deterministic text projections for provider planning chats."""

from codex_harness.core.plan_store import PHASES
from codex_harness.core.spine_diagrams import _escape, _layers, _projection, diagram, text_fallback

NEXT = {
    "align": "Restate the vision and ask the user to confirm the understanding.",
    "expand": "Offer improvements; record what the user accepts, rejects or defers.",
    "vision-lock": "Show the understanding and journey; ask for explicit vision approval.",
    "features": "Map features, user stories, edge cases and open questions.",
    "requirements": "Give every requirement acceptance criteria and planned verification.",
    "tech-stack": "Show options, trade-offs and recommendations as decision cards.",
    "plan": "Link beads to requirements, with file ownership and dependencies.",
    "roundtable": "Record review findings and resolve open clarifications.",
    "publish": "Publish the frozen revision and its worker manifest.",
    "execute": "Follow worker progress and proof against the published revision.",
    "accept": "Review whether the delivered product achieves the locked vision.",
}


def _inline(value: str) -> str:
    return " ".join(value.split())


def _names(values) -> str:
    return ", ".join(sorted(values)) or "none"


def _short(value, limit=60) -> str:
    text = _inline(str(value))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def status_line(status: dict) -> str:
    return (
        f"Revision {status['plan_revision'][:12]} · "
        f"open questions {status['open_clarifications']} · "
        f"pending: {_short(status['pending_gate'] or 'none', 65)}"
    )[:120]


def banner(status: dict) -> str:
    phase = status["phase"]
    number = str(PHASES.index(phase) + 1) if phase in PHASES else "?"
    return (
        f"Harness Plan Mode: {_short(status.get('name', 'unknown'), 48)} · "
        f"Phase {number}/11: {phase.replace('-', ' ').title()} · "
        f"pending: {_short(status['pending_gate'] or 'none', 48)}"
    )


def phase_tracker(status: dict) -> str:
    current = PHASES.index(status["phase"])
    return "Phases (position, not proof): " + " → ".join(
        f"**{phase}**" if index == current else phase for index, phase in enumerate(PHASES)
    )


def first_action(value: str) -> str:
    first = value.split(" then ", 1)[0]
    return value if first.endswith(",") else first


def response(status: dict, body: str, next_step: str | None = None, *, tracker=False) -> str:
    # Callers own the one final action. Recomposition replaces an existing footer.
    body = body.rsplit("\nNext: ", 1)[0].rstrip()
    parts = [banner(status)]
    if tracker and status["phase"] in PHASES:
        parts.extend([status_line(status), phase_tracker(status)])
    if body:
        parts.append(body)
    parts.append(
        "Next: "
        + first_action(
            _inline(next_step or NEXT.get(status["phase"], "Retry harness_plan_status."))
        )
    )
    return "\n".join(parts)


def approval_label(by: str, *, is_proxy: bool | None = None) -> str:
    if is_proxy is False or (is_proxy is None and by == "user"):
        return "approved by you (recorded statement; human origin not verified)"
    name = by.removeprefix("proxy:")
    if name == "opus-user-proxy":
        name = "Opus"
    return f"approved by proxy ({_inline(name)}) · not your approval"


def readable(value) -> str:
    """Plain Markdown values; structured content remains the machine authority."""
    if isinstance(value, dict):
        return "; ".join(f"{_inline(str(k))}: {readable(v)}" for k, v in value.items()) or "none"
    if isinstance(value, list):
        return "; ".join(readable(item) for item in value) or "none"
    if value is None:
        return "not recorded"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return _inline(str(value))


def data_card(title: str, data) -> str:
    items = list(data.items()) if isinstance(data, dict) else [("Items", data)]
    bullets = [f"- {key}: {_short(readable(value), 300)}" for key, value in items[:7]]
    if len(items) > 7:
        bullets[-1] += " · More in machine output."
    while len(bullets) < 3:
        bullets.append(
            "- Evidence: recorded data; this action does not prove delivery."
            if len(bullets) == 1
            else "- Details: available in machine output."
        )
    return "\n".join([f"**{title}**", "Full details in machine output.", *bullets])


def outcome_waves_card(projection: dict) -> str:
    """Full chat map, without truncating later outcomes or inventing graph-depth waves."""
    lines = ["**Outcome planning · draft / lock unconfirmed**"]
    for field, label in (("intake", "Intake"), ("reviewerSelection", "Reviewer choice")):
        item = projection.get(field)
        if not item:
            lines.append(f"- {label}: not recorded")
            continue
        node = item.get("node")
        lines.append(f"- {label}: {readable(node['id'] if node else None)} · {item['state']}")
        if node:
            fields = node["fields"]
            if field == "intake":
                lines.append(
                    f"- Recipe: {readable(fields.get('recipe'))}; "
                    f"baseline: {readable(fields.get('baseline'))}"
                )
                lines.append(f"- Outcome: {readable(fields.get('delta'))}")
            else:
                for reviewer in fields.get("reviewers", []):
                    lines.append(f"- Reviewer: {readable(reviewer)}")
                lines.append(
                    f"- Independence: {readable(item.get('independence'))}; "
                    f"diversity: {readable(item.get('diversity'))}"
                )
                lines.append(f"- Unmet assurance: {readable(item.get('unmetRequirements'))}")
            lines.append(
                f"- Supplied confirmation provenance: {readable(fields.get('confirmation'))}; "
                "human origin unverified; no approval granted"
            )
    lines.append(f"- Map: {projection['state']} · {readable(projection.get('reason'))}")
    plan = projection.get("waveMap") or (projection.get("mapNode") or {}).get("fields")
    if not plan:
        lines.append("- Obligations and eligibility: unavailable / not measured")
        return "\n".join(lines)
    nodes = projection.get("nodes", {})
    details = {
        node["fields"]["waveId"]: node["fields"]
        for node in projection.get("detailNodes", [])
        if node
    }
    by_id = {wave["id"]: wave for wave in plan["waves"]}
    accounting = projection.get("accounting")
    if accounting:
        full, ready = accounting["fullVision"], accounting["readiness"]
        lines.append(
            f"- Required obligations: {full['totalObligations']} total · "
            f"completed-real {full['completedRealObligations']} · "
            f"outstanding {full['outstandingObligations']}"
        )
        lines.append(
            "- Eligibility: not measured (publication/lock unavailable); "
            f"detailed {ready['detailedBeads']} · sketched {ready['sketchedItems']} · "
            f"dispatchable {ready['dispatchableNowCount']}"
        )
        lines.append(
            "- Historical measurements (not remeasured): "
            + readable(accounting["historicalMeasurements"])
        )
    else:
        total = sum(row["requiredForFullVision"] for row in plan["obligations"])
        lines.append(
            f"- Last recorded required denominator: {total}; "
            "current accounting unavailable / not measured"
        )
    for wid in plan["order"]:
        wave = by_id[wid]
        detail = details.get(wid, {})
        lines.extend(
            [
                f"**{readable(wid)} · {readable(wave['outcome'])} · "
                f"{detail.get('detail', 'unavailable')}**",
                f"- Demo: {readable(wave['demo'])}",
                f"- Wave dependencies: {readable(wave['dependsOnWaves'])}; "
                f"incoming: {readable(wave['incomingContractIds'])}; "
                f"outgoing: {readable(wave['outgoingContractIds'])}",
            ]
        )
        for sid in wave["exitScenarioIds"]:
            node = nodes.get(sid)
            lines.append(
                f"- Exit {readable(sid)} · {readable(node['title'] if node else None)}: "
                f"{readable(node['fields'] if node else None)}"
            )
        for bid in detail.get("beadIds", []):
            node = nodes.get(bid)
            lines.append(
                f"- Bead {readable(bid)} · {readable(node['title'] if node else None)}: "
                f"{readable(node['fields'] if node else None)}"
            )
        for sketch in detail.get("sketches", []):
            lines.append(
                f"- Sketch {readable(sketch['id'])} · {readable(sketch['title'])}; "
                f"owner area: {readable(sketch['ownershipArea'])}; "
                f"pending producers: {readable(sketch['pendingProducerIds'])}; "
                f"detail after: {readable(sketch['detailAfterWaveId'])}"
            )
        for row in plan["obligations"]:
            if wid in row["waveIds"]:
                lines.append(
                    f"- Obligation {readable(row['id'])}: {readable(row['criterion'])}; "
                    f"required evidence: {readable(row['requiredEvidence'])}"
                )
    if accounting:
        for state in accounting["obligationStates"]:
            lines.append(
                f"- {readable(state['id'])}: {state['state']} · {readable(state['reason'])}"
            )
    return "\n".join(lines)


def review_scope(row: dict) -> str:
    bound = row.get("packetBound")
    if bound is True:
        return "packet only (recorded)"
    if bound is False:
        return "outside packet: " + readable(row.get("outOfPacketTools", []))
    return "unknown"


def findings_card(rows: list[dict]) -> str:
    return data_card(
        "Findings",
        {
            "Records": len(rows),
            "Findings": [
                f"{row['id']} ({row['severity']}, {row['status']}): " + _short(row["finding"], 130)
                for row in rows
            ],
            "Review scope": "; ".join(sorted({review_scope(row) for row in rows})) or "no findings",
        },
    )


def roundtable_card(result: dict, rows: list[dict]) -> str:
    current = [row for row in rows if row["plan_revision"] == result["planRevision"]]
    return (
        review_card(result)
        + "\n"
        + data_card(
            "Roundtable: recorded review",
            {
                "Revision": result["planRevision"],
                "Cross-subscription": result["crossSubscription"],
                "Round budget": f"{result.get('maxRounds', 2)} per reviewer per revision",
                "Reviewers": [
                    f"{row['name']}: exit {row['exitCode']}; {row['invalid']} invalid; "
                    f"{row['findings']} findings"
                    for row in result["reviewers"]
                ],
                "Review scope": [
                    f"{row['name']}: {review_scope(row)}" for row in result["reviewers"]
                ],
                "Findings": [
                    f"{row['id']} ({row['status']}): {_short(row['finding'], 100)}"
                    for row in current
                ],
                "Evidence": f"Mocked: {readable(result.get('isMocked'))}; "
                "recorded reports do not prove delivery.",
            },
        )
    )


def acceptance_run_card(run: dict) -> str:
    results = run["results"]
    failed = [row for row in results if not row["passed"]]
    outcome = f"{len(results) - len(failed)}/{len(results)}"
    outcome += " scenarios passed" if not failed else " passed"
    subject = "product" if run.get("subject") == "product" else "oracle dry run"
    lines = ["**Accept Run**", f"- **{outcome}** · {subject}"]
    if failed:
        lines[-1] += " · failing: " + ", ".join(row["id"] for row in failed)
    for row in failed:
        step = next((s for s in row.get("steps", []) if not s["passed"]), None)
        detail = (
            f"{_short(step['command'], 160)} · exit {step['exit']}"
            if step
            else row.get("error", "No failing step recorded")
        )
        lines.append(f"- {row['id']}: {_inline(detail)}")
    lines.extend([f"- Run: {run['id']}", f"- Revision: {run['revision']}"])
    return "\n".join(lines)


def review_card(result: dict, *, details=False) -> str:
    lines = [f"**Review: {result.get('roundId', result.get('id', 'acceptance'))}**"]
    if result.get("findingCountsScope"):
        lines.append("Findings scope: " + result["findingCountsScope"])
    for row in result["reviewers"]:
        shards = row.get("shardDetails") or []
        completed = sum(s.get("exitCode") == 0 and not s.get("invalid") for s in shards)
        count = row.get(
            "findings",
            sum(
                f.get("reviewer") == row["name"]
                for f in result.get("findings", [])
                if isinstance(f, dict)
            )
            if isinstance(result.get("findings", []), list)
            else 0,
        )
        lines.append(
            f"- {row['name']}: shards {completed}/{row.get('shards', 'unknown')}; "
            f"packetBound: {readable(row.get('packetBound'))}; {count} findings"
        )
        if row.get("artifactPath"):
            lines.append("  Diagnostic: " + row["artifactPath"])
        if row.get("reshardedFrom"):
            retry = row["reshardedFrom"]
            lines.append(
                f"  Re-shard retries: 1 "
                f"({retry['shards']} → {retry.get('newShardCount', 'failed')})"
                + (f"; error: {_short(retry['reason'], 180)}" if retry.get("reason") else "")
            )
        for shard in shards:
            error = shard.get("primaryError", {}).get("reason")
            state = (
                "completed" if shard.get("exitCode") == 0 and not shard.get("invalid") else "failed"
            )
            if details or state == "failed":
                lines.append(
                    f"  Shard {shard['hash']}: {state}; "
                    f"estimated tokens {shard['estimatedTokens']}; "
                    f"features {readable(shard['features'])}; "
                    f"packetBound: {readable(shard.get('packetBound'))}"
                    + (
                        f"; error: {_short(error or readable(shard.get('rejected')), 180)}"
                        if state == "failed"
                        else ""
                    )
                )
        if "findingCounts" in row:
            lines.append("  Findings by status: " + readable(row["findingCounts"]))
    return "\n".join(lines)


def status_card(status: dict, questions: list[dict]) -> str:
    acceptance = status["acceptance"]
    approval = (
        approval_label(acceptance["approvedBy"], is_proxy=acceptance["isProxy"])
        if acceptance["accepted"]
        else "not accepted: " + str(acceptance["invalidReason"])
    )
    passed = sum(item["lastResult"] == "pass" for item in acceptance["scenarios"])
    return data_card(
        "Plan status",
        {
            "Editable spine_revision": status["spine_revision"],
            "Compiled manifest_revision": status["manifest_revision"],
            "Frozen published_revision": status["published_revision"],
            "Acceptance": approval,
            "Proof": f"{passed}/{len(acceptance['scenarios'])} scenarios proved "
            "by current recorded runs; "
            "review reports are recorded evidence, not independent live proof",
            "Open questions / assumptions": f"{readable(questions)} / {status['open_assumptions']}",
            "Roundtable": [
                f"{row['reviewer']}: {row['text']} · Recorded review scope: "
                + "; ".join(sorted({review_scope(review) for review in row.get("reviews", [])}))
                for row in status["roundtable"]
            ],
        },
    )


def chat_diagram(spine, kind: str) -> str:
    nodes, edges = _projection(spine, kind)
    full = diagram(spine, kind)
    note = "Planned structure; not delivery proof."
    if len(nodes) > 40:
        groups = (
            _layers(nodes, edges)
            if kind == "bead-dag"
            else [
                [key for key in nodes if nodes[key]["kind"] == node_kind]
                for node_kind in sorted({n["kind"] for n in nodes.values()})
            ]
        )
        # Merge consecutive waves if a long serial DAG exceeds the chat node budget.
        size = max(1, (len(groups) + 39) // 40)
        groups = [sum(groups[i : i + size], []) for i in range(0, len(groups), size)]
        mapping = {key: index for index, group in enumerate(groups) for key in group}
        lines = ["flowchart LR"]
        for index, group in enumerate(groups):
            label = (
                f"Phases {index * size + 1}–{min((index + 1) * size, len(_layers(nodes, edges)))}"
                if kind == "bead-dag"
                else nodes[group[0]]["kind"].title()
            )
            lines.append(f'  g{index}["{_escape(label)}: {len(group)} planned nodes"]')
        lines.extend(
            f"  g{a} --> g{b}"
            for a, b in sorted(
                {(mapping[a], mapping[b]) for a, b in edges if mapping[a] != mapping[b]}
            )
        )
        full = "\n".join(lines) + "\n"
        note += (
            " Summarized by execution phase or node kind. Full diagram: "
            "harness_diagram structuredContent.mermaid / p diagram --kind " + kind + " --json."
        )
        fallback = "\n".join(f"- Group {i + 1}: {_names(group)}" for i, group in enumerate(groups))
    elif kind == "user-journey":
        # Quoted flowchart labels avoid journey's unquoted section/task grammar.
        from codex_harness.core.spine_diagrams import _ids, _journey_groups

        ids = _ids(spine)
        lines = ["flowchart LR"]
        for key, node in nodes.items():
            lines.append(f'  {ids[key]}["{_escape(node["id"] + ": " + node["title"])}"]')
        journey_edges = set()
        for feature, stories in _journey_groups(spine):
            sequence = ([feature] if feature else []) + stories
            journey_edges.update(zip(sequence, sequence[1:], strict=False))
        lines.extend(f"  {ids[a]} --> {ids[b]}" for a, b in sorted(journey_edges))
        if not nodes:
            fields = spine.node(spine.ids("vision")[0])["fields"]
            moment = readable(fields.get("first_user_moment", "First moment not planned"))
            lines.append(f'  first["{_escape(moment)}"]')
        full = "\n".join(lines) + "\n"
        fallback = text_fallback(spine, kind).rstrip()
    else:
        fallback = text_fallback(spine, kind).rstrip()
    return f"**Diagram: {kind}**\n{note}\n```mermaid\n{full.rstrip()}\n```\n{fallback}"


def understanding_card(
    vision: str,
    users: str,
    first_user_moment: str,
    scope_in: str,
    scope_out: str,
    assumptions: list[str],
) -> str:
    return "\n".join(
        [
            "**Here's what I understood**",
            f"- Vision: {_short(vision, 300)}",
            f"- Users: {_short(users, 300)}",
            f"- First user moment: {_short(first_user_moment, 300)}",
            f"- Scope in: {_short(scope_in, 300)}",
            f"- Scope out: {_short(scope_out, 300)}",
            f"- Assumptions: {readable(assumptions)}",
            "- Your call: confirm, or tell me what's wrong.",
        ]
    )


def decision_card(
    id: str,
    question: str,
    options: list[dict],
    recommendation: str,
    parents: list[str],
    chosen: str | None = None,
    rationale: str | None = None,
) -> str:
    if chosen is not None:
        if chosen not in {option["name"] for option in options}:
            raise ValueError("Chosen option must name one of the decision options")
        if not rationale or not rationale.strip():
            raise ValueError("Chosen option requires a nonempty rationale")
    elif rationale is not None:
        raise ValueError("Rationale requires a chosen option")

    def cell(value):
        return _inline(value).replace("|", "\\|").replace("`", "&#96;")

    lines = [
        f"**Decision {cell(id)}**",
        f"- Question: {_inline(question)}",
        f"- Linked to: {_names(parents)}",
        f"- Recommendation: {_inline(recommendation)}",
        "",
        "| Option | Pros | Cons |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        "| " + " | ".join(cell(option[k]) for k in ("name", "pros", "cons")) + " |"
        for option in options
    )
    lines.append("")
    if chosen is not None:
        lines.extend(
            [
                f"- Chosen: {_inline(chosen)}",
                f"- Rationale: {_inline(rationale)}",
                "- Recorded decision; this is not user approval or delivery proof.",
            ]
        )
    else:
        lines.append(
            "- Your call: reply with an option name ("
            + " / ".join(_inline(option["name"]) for option in options)
            + "), or explain the change you want."
        )
    return "\n".join(lines)


def change_digest(impact: dict) -> str:
    return "\n".join(
        [
            "**What changed**",
            f"- Changed nodes: {_names(impact['changed'])}",
            f"- Affected nodes: {_names(impact['affected'])}",
            f"- Affected beads: {_names(impact['affectedBeads'])}",
            f"- Untouched beads: {_names(impact['untouchedBeads'])}",
            f"- Affected tests: {_names(impact['affectedTests'])}",
            f"- Stale docs: {_names(impact['staleDocs'])}",
        ]
    )


def publish_summary(beads: list[dict], open_assumptions: int) -> str:
    remaining = {node["id"]: set(node["fields"]["dependsOn"]) for node in beads}
    lines = [
        "**Publication plan (planned, not proved)**",
        f"- Beads ({len(beads)}): {_names(remaining)}",
    ]
    wave = 1
    while remaining:
        ready = sorted(key for key, parents in remaining.items() if not parents & remaining.keys())
        if not ready:
            raise ValueError("Bead dependency cycle")
        label = "can run in parallel" if len(ready) > 1 else "single bead"
        lines.append(f"Wave {wave} ({label}): {', '.join(ready)}")
        for key in ready:
            del remaining[key]
        wave += 1
    waves = lines[2:]
    lines = lines[:2] + ["- Execution order: " + ("; ".join(waves) or "no beads")]
    lines.append(f"- Open assumptions: {open_assumptions}")
    return "\n".join(lines)
