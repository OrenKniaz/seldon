"""Canonical publication writers; composed by the PlanStore transaction owner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from codex_harness.core import waves
from codex_harness.core.spine import (
    Spine,
)
from codex_harness.core.spine_render import render_markdown
from codex_harness.core.test_audit import PROSPECTIVE_LABEL, PROSPECTIVE_WARNING


from codex_harness.core.plan_types import PHASES, PlanStoreError, _json, _now, _nonempty


class PlanPublication:
    def _check_publish(self) -> None:
        from codex_harness.core.approvals import require_approval

        require_approval(self, "A")
        self.require_finding_dispositions()
        if self._open("clarifications"):
            raise PlanStoreError("plan-clarifications-open", "Resolve open clarifications")

    def advance_to(self, target, *, by):
        """Walk canonical order after the controller chooses the target."""
        start, end = PHASES.index(self.phase()), PHASES.index(target)
        for phase in PHASES[start + 1 : end + 1] if end >= start else [target]:
            self.advance(phase, by=by)

    def advance(self, to_phase: str, *, by: str, statement: str | None = None) -> None:
        from codex_harness.core.approvals import require_approval

        _nonempty(by, "by")
        with self._transaction():
            current = self.phase()
            if to_phase not in PHASES:
                raise PlanStoreError("plan-phase-invalid", to_phase)
            index, target = PHASES.index(current), PHASES.index(to_phase)
            if target == index or target > index + 1:
                raise PlanStoreError(
                    "plan-phase-order", "Advance one phase or return to an earlier phase"
                )
            revision = self._revision()
            spine = Spine(self._data())
            if to_phase == "vision-lock" or current == "vision-lock" and target > index:
                from codex_harness.core.planner_vision import expansion_status

                visions = spine.ids("vision")
                status = expansion_status(
                    spine.node(visions[0]) if len(visions) == 1 else {"fields": {}}
                )
                if status["gaps"]:
                    raise PlanStoreError("plan-expansion-required", status["next"])
            if target > index:
                if index > PHASES.index("vision-lock"):
                    require_approval(self, "A")
                if current in ("align", "vision-lock"):
                    if statement is not None:
                        self.approve(current, by=by, statement=statement)
                    require_approval(self, "A")
                if current == "requirements":
                    self._check_coverage(spine, "test")
                if current == "plan":
                    self._manifest(spine)
                if to_phase == "publish":
                    consent = require_approval(self, "B")
                    statement, by = consent["words"], "user"
                    self._check_publish()
                    self._check_evidence(self._manifest(spine)["planRevision"])
                if current == "publish" and not self.publication_current(
                    self._manifest(spine)["planRevision"]
                ):
                    raise PlanStoreError(
                        "plan-publication-required", "Publish the current revision before execution"
                    )
                if current == "publish":
                    self._check_publish()
                    self._check_evidence(self.manifest_revision())
            self.connection.execute("UPDATE meta SET phase=?", (to_phase,))
            wave_lock = None
            if (
                current == "roundtable"
                and to_phase == "publish"
                and isinstance(statement, str)
                and statement.strip()
            ):
                from codex_harness.core.wave_refine import wave_values

                is_proxy = (
                    by.startswith("proxy:") and bool(by[6:].strip()) or by == "opus-user-proxy"
                )
                values = wave_values(Spine(self._data()))
                if values and (by == "user" or is_proxy):
                    owner, details, _ = values
                    wave_lock = dict(
                        digest=waves.product_digest(
                            spine, owner["fields"], [n["fields"] for n in details]
                        ),
                        mapNodeId=owner["id"],
                        waveMapRevision=spine.revision(owner["id"]),
                        spineRevision=spine.plan_revision(),
                        spine=spine.to_dict(),
                        confirmedBy=by,
                        confirmationOrigin="proxy" if is_proxy else "user",
                        isProxy=bool(is_proxy),
                        statement=statement,
                    )
            self._event(
                "phase-advanced",
                {
                    "from": current,
                    "to": to_phase,
                    "by": by,
                    "statement": statement,
                    "plan_revision": revision,
                    **({"waveProductLock": wave_lock} if wave_lock else {}),
                },
            )

    def publish(
        self,
        *,
        by: str,
        export_dir: str | Path,
        proof_level: str = "oracle-verified",
        reason: str | None = None,
    ) -> str:
        """Freeze a content-bound manifest when workspace/template exists.

        With no known template, publish the spine-only revision. Approval gates
        and status.plan_revision continue to describe the editable spine; the
        returned and published_revision identities describe the frozen manifest.
        """
        _nonempty(by, "by")
        if proof_level not in ("oracle-verified", "prospective"):
            raise PlanStoreError("plan-proof-level-invalid", proof_level)
        if proof_level == "prospective" and (not isinstance(reason, str) or not reason.strip()):
            raise PlanStoreError(
                "plan-prospective-reason-required", "Record why no oracle is available"
            )
        with self._transaction():
            from codex_harness.core.approvals import require_approval

            if self.phase() != "publish":
                raise PlanStoreError(
                    "plan-publish-phase-required", "Enter publish before publishing"
                )
            self._check_publish()
            spine = Spine(self._data())
            self._check_coverage(spine, "test")
            self._check_coverage(spine, "bead")
            manifest = self._manifest(spine)
            require_approval(self, "B")
            wave_lock = self.wave_product_lock() if manifest.get("waveScope") else None
            revision = manifest["planRevision"]
            self._check_evidence(revision)
            level = self.proof_level(revision)
            if not spine.semantic and level != proof_level:
                raise PlanStoreError(
                    "plan-proof-level-required",
                    "Prospective publication requires --proof-level prospective "
                    '--reason "why no oracle"; '
                    "oracle-verified publication requires a passing oracle audit.",
                )
            if not spine.semantic and proof_level == "prospective":
                manifest.update(
                    proofLevel="prospective",
                    proofLevelReason=reason.strip(),
                    sourceManifestRevision=revision,
                )
                # Proof choice is publication identity, not editable test identity.
                revision = hashlib.sha256(_json(manifest).encode("utf-8")).hexdigest()
                manifest["planRevision"] = revision
            row = self.connection.execute(
                "SELECT snapshot_json FROM revisions WHERE plan_revision=?", (revision,)
            ).fetchone()
            if row is None:
                snapshot = {
                    **spine.to_dict(),
                    "plan_revision": revision,
                    "published_at": _now(),
                    "by": by,
                    "open_assumptions": self._open("assumptions"),
                    "manifest": manifest,
                    **({"waveProductLock": wave_lock} if wave_lock else {}),
                }
            else:
                snapshot = json.loads(row["snapshot_json"])
                if Spine(snapshot).to_dict() != spine.to_dict():
                    raise PlanStoreError(
                        "plan-revision-conflict", "Revision already published under another name"
                    )
            if row is None:
                self.connection.execute(
                    "INSERT INTO revisions VALUES (?, ?, ?)",
                    (revision, snapshot["published_at"], _json(snapshot)),
                )
                self._event("published", snapshot)
            if self._meta()["published_revision"] != revision:
                self.connection.execute("UPDATE meta SET published_revision=?", (revision,))
                if row is not None:
                    self._event("publication-selected", {"plan_revision": revision, "by": by})
            self._export(snapshot, Path(export_dir))
        return revision

    @staticmethod
    def _export(snapshot: dict, export_dir: Path) -> None:
        revision = snapshot["plan_revision"]
        files = {
            Path("revisions") / f"{revision}.json": _json(snapshot) + "\n",
            Path(revision) / "manifest.json": _json(snapshot["manifest"]) + "\n",
            **{
                Path(revision) / name: text
                for name, text in render_markdown(Spine(snapshot)).items()
            },
        }
        if snapshot["manifest"].get("proofLevel") == "prospective":
            for name in render_markdown(Spine(snapshot)):
                key = Path(revision) / name
                files[key] = PROSPECTIVE_LABEL + "\n" + PROSPECTIVE_WARNING + "\n\n" + files[key]
        # Never overwrite an immutable export. Missing views can be regenerated
        # after an interrupted filesystem write from the same frozen snapshot.
        for relative, content in files.items():
            path = export_dir / relative
            if path.exists() and path.read_text(encoding="utf-8") != content:
                raise PlanStoreError("plan-export-conflict", str(path))
        export_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".plan-export-", dir=export_dir) as temp:
            staged_files = []
            for index, (relative, content) in enumerate(files.items()):
                path = export_dir / relative
                if path.exists():
                    continue
                staged = Path(temp) / str(index)
                staged.write_text(content, encoding="utf-8", newline="\n")
                staged_files.append((staged, path))
            created = []
            try:
                for staged, path in staged_files:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    staged.replace(path)
                    created.append(path)
            except BaseException:
                for path in reversed(created):
                    path.unlink()
                raise

    def _snapshot(self, revision: str) -> dict:
        row = self.connection.execute(
            "SELECT snapshot_json FROM revisions WHERE plan_revision=?", (revision,)
        ).fetchone()
        if row is None:
            raise PlanStoreError("plan-revision-missing", revision)
        return json.loads(row["snapshot_json"])

    def published_manifest(self, revision: str) -> dict:
        return self._snapshot(revision)["manifest"]
