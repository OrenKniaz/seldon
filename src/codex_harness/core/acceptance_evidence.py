"""Canonical read-only candidate source and finding evidence for acceptance packets."""
import hashlib
import json
from pathlib import Path, PurePosixPath
from codex_harness.core import review_findings, spine as plan_spine
from codex_harness.core.approvals import fingerprint
from codex_harness.core.execution_assessments import selected_execution_support
from codex_harness.core.planner_candidate import judgments_for
from codex_harness.core.spine import compile_manifest, requirement_file_sources


SOURCE_SUFFIXES = frozenset(
    ".py .js .mjs .cjs .ts .tsx .jsx .go .rs .c .h .cpp .hpp .cs .java .kt "
    ".swift .rb .php .sh .ps1 .sql .html .css .vue .svelte".split()
)


def candidate_evidence(store, repo, run, *, execution_assessment_ids=None):
    """Read explicit manifest-owned source only; never follow imports or scan the repo."""
    template = store.workspace / "template"
    manifest = compile_manifest(store.spine(), template if template.is_dir() else None)
    owners = {}
    for bead_id, bead in manifest["beads"].items():
        for name in bead["ownedFiles"]:
            owners.setdefault(name, []).append(bead_id)
    implementation = []
    for name in sorted(owners):
        relative = PurePosixPath(name.replace("\\", "/"))
        record = {"path": name, "owners": sorted(owners[name])}
        if (
            any(part.startswith(".") for part in relative.parts)
            or relative.suffix.lower() not in SOURCE_SUFFIXES
        ):
            record["status"] = "not-source; contents not read"
        elif (Path(repo) / relative).is_dir():
            record["status"] = "directory; contents not read"
        else:
            sources = requirement_file_sources(repo, [name])
            if not sources:
                record["status"] = "missing"
            else:
                raw = next(iter(sources.values())).read_bytes()
                record["sha256"] = hashlib.sha256(raw).hexdigest()
                try:
                    record.update(status="included", source=raw.decode("utf-8"))
                except UnicodeDecodeError:
                    record["status"] = "non-UTF-8 source; body not included"
        implementation.append(record)

    def finding_evidence(rows):
        closed = [row for row in rows if row["state"] == "closed"]
        return {
            "active": [row for row in rows if row["state"] != "closed"],
            "closed": [
                {
                    "id": row["id"],
                    "status": row["status"],
                    "sha256": fingerprint(row),
                }
                for row in closed
            ],
            "closedCounts": {
                status: sum(row["status"] == status for row in closed)
                for status in ("resolved", "rejected")
            },
            "closedHashAlgorithm": "sha256-json-sort_keys-ensure_ascii_false-compact-utf8",
        }

    result = {
        "runId": run["id"],
        "manifestRevision": manifest["planRevision"],
        "productRevision": run["revision"],
        "revisionAlgorithm": run.get("revisionAlgorithm"),
        "implementation": implementation,
        "scenarioOwners": plan_spine.scenario_nodes(store.spine()),
        "requiredEvidence": [
            dict(
                scenarioId=row["id"],
                candidateRevision=run["revision"],
                resultRef=f"scenarioResults.results[{index}]",
            )
            for index, row in enumerate(run["results"])
        ],
        "planningFindings": finding_evidence(review_findings.findings(store, expand=True)),
        "acceptanceFindings": finding_evidence(store.acceptance_findings()),
        "openClarifications": [
            {
                "id": row["id"],
                "question": row["question"],
                "node_ids": json.loads(row["node_ids_json"]),
                "status": row["status"],
            }
            for row in store._open("clarifications")
        ],
    }
    if run.get("webToolchain"):

        result["requiredJudgments"] = {
            "visualQuality": "Observe the actual page against the locked quality bar and Done"
        }
        result["judgments"] = judgments_for(store, repo)
    if execution_assessment_ids is not None:
        result["executionSupport"] = selected_execution_support(
            store, execution_assessment_ids, repo=repo
        )
    return result

