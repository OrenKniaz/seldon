"""Pure native audit cards; canonical results and persisted evidence stay intact."""

import json


def _groups(findings):
    groups = {}
    for index, finding in enumerate(findings, 1):
        # Only the known requirement/bead reference is presentation-duplicate.
        # Unknown fields, severity, output and artifact differences stay distinct.
        key = json.dumps(
            {name: value for name, value in finding.items() if name != "bead"},
            sort_keys=True,
            ensure_ascii=False,
        )
        if key not in groups:
            groups[key] = (finding, [], [])
        reference = str(finding.get("bead", "not recorded"))
        if reference not in groups[key][1]:
            groups[key][1].append(reference)
        groups[key][2].append(index)
    return list(groups.values())


def _details(finding, references, indexes, section):
    lines = [
        f"- {finding.get('severity', 'unknown')}: {finding.get('code', 'unknown')} "
        f"— {finding.get('module', 'module not recorded')}",
        "  References: " + ", ".join(references),
    ]
    output = finding.get("output", finding.get("outputPreview"))
    if output is not None:
        text = str(output)
        preview = "\n".join(text.splitlines()[:20])[:2000]
        lines.extend(["```text", preview, "```"])
        if preview != text:
            lines.append(
                "Full diagnostic retained in structured audit result "
                + section
                + " #"
                + ", #".join(str(index) for index in indexes)
                + "."
            )
    if finding.get("artifactPath"):
        lines.append("Full output: " + str(finding["artifactPath"]))
    extras = {
        key: value
        for key, value in finding.items()
        if key not in {"code", "severity", "module", "bead", "output", "artifactPath"}
        and not (key == "outputPreview" and "output" not in finding)
    }
    if extras:
        lines.append("Additional recorded details: " + json.dumps(extras, ensure_ascii=False))
    return lines


def render_native_audit_card(result: dict) -> str:
    """Group exact diagnostics; full output stays in the unchanged structured result."""
    findings = result.get("findings", [])
    groups = _groups(findings)
    if result.get("evidenceTier") == "audit: unrecognized interface":
        lines = ["audit: unrecognized interface — lower evidence tier; planning may proceed."]
    elif result["ok"]:
        label = "prospective audit" if result.get("proofLevel") == "prospective" else "audit"
        lines = [f"The {label} passed for this revision."]
    else:
        lines = [
            f"The audit did not pass: {len(findings)} findings grouped into "
            f"{len(groups)} distinct diagnostics."
        ]
    if result.get("proofLevel"):
        lines.append("Proof level: " + str(result["proofLevel"]))
    if result.get("baseline"):
        lines.append("Baseline: " + str(result["baseline"]))
    if result.get("warning"):
        lines.append(str(result["warning"]))
    if result.get("integrationChecked") is False:
        lines.append(
            "Integration not checked: " + str(result.get("integrationReason", "not recorded"))
        )
    for finding, references, indexes in groups:
        lines.extend(_details(finding, references, indexes, "findings"))
    expected = _groups(result.get("expectedFailures", []))
    if expected:
        lines.append("Expected failures (retained separately):")
        for finding, references, indexes in expected:
            lines.extend(_details(finding, references, indexes, "expectedFailures"))
    if result["ok"]:
        lines.append(
            "Next: Check the current planning gate; this audit alone does not establish "
            "product or integration acceptance. Execution RED→GREEN still decides."
        )
    else:
        lines.append(
            "Next: Inspect the complete diagnostics and structured audit receipt before "
            "choosing a repair; rerun harness_audit after an explicit correction."
        )
    return "\n".join(lines) + "\n"
