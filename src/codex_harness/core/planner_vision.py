"""One read-only projection of the canonical stored vision, without approval credit."""

import hashlib
import re
from pathlib import PurePosixPath

from codex_harness.core.semantic_schema import array, fields, path


def design_references(vision_fields, workspace, *, allow_missing=False):
    """Check visual material at its owning filesystem boundary; never execute/decode it."""
    refs = array(vision_fields.get("designRefs", []), 16)
    for ref in refs:
        keys = ("path", "type", "sha256", "provenance", "use")
        fields(ref, keys, keys)
        if any(not isinstance(ref[k], str) or not ref[k].strip() for k in keys):
            raise ValueError("designRefs descriptors require nonblank strings")
        name = ref["path"]
        relative = PurePosixPath(name)
        file = path(workspace, name)
        if (
            relative.as_posix() != name
            or ".." in relative.parts
            or any(
                p.is_symlink() or p.is_junction()
                for p in (workspace / relative, *(workspace / p for p in relative.parents))
            )
        ):
            raise ValueError("designRefs require canonical relative paths without reparse points")
        if (file.exists() or not allow_missing) and (
            not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != ref["sha256"]
        ):
            raise ValueError(f"designRefs missing or SHA-256 mismatch: {name}")
    return refs


def taste_fields(fields):
    """Map declared taste into the displayed quality/Done baseline, without consent."""
    result = dict(fields)
    taste = fields.get("taste", {})
    if not isinstance(taste, dict) or not taste:
        return result
    for target, keys in (
        ("quality_bar", ("tone", "references", "brand")),
        ("done_feels_like", ("audience", "requiredSections")),
    ):
        aliases = next(a for _, a in SECTIONS if a[0] == target)
        baseline = next((fields[k] for k in aliases if k in fields), "Not recorded")
        detail = readable({k: taste.get(k, "Not recorded") for k in keys})
        result[target] = f"{readable(baseline)}\nTaste:\n{detail}"
    return result


SECTIONS = (
    ("Vision", ("vision",)),
    ("Users", ("users",)),
    ("First moment", ("first_user_moment", "firstUserMoment")),
    ("Scope in", ("scope_in", "scopeIn")),
    ("Scope out", ("scope_out", "scopeOut")),
    ("Quality", ("quality_bar", "qualityBar", "quality")),
    ("Done", ("done_feels_like", "doneFeelsLike", "done")),
    ("Assumptions", ("assumptions",)),
)

EXPANSION_NEXT = (
    "Expansion required before Vision Lock. Record it with harness_upsert_node on the vision "
    "node: fields.expansion = {mode: product|quality, reason, upgrades: [3–7 x {id, text, kind, "
    "impact, disposition: accept|reject|defer}]} plus nonblank quality_bar and done_feels_like "
    "(harness_schema vision has the rules). Dispositions are proposals; the single Vision Lock "
    "question shows them for the user's accept/reject/defer. There is no separate Expand "
    "confirmation or card tool."
)


def expansion_status(vision: dict, *, downstream_nodes: list[dict] | None = None) -> dict:
    """Assess declared semantics only; dispositions never establish user consent."""
    fields = vision["fields"]
    expansion = fields.get("expansion")
    expansion = expansion if isinstance(expansion, dict) else {}
    gaps, accepted, ids = [], [], set()

    def gap(path, message):
        gaps.append(dict(path=path, message=message))

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    if expansion.get("mode") not in ("product", "quality"):
        gap("expansion.mode", "Choose product or quality.")
    if not text(expansion.get("reason")):
        gap("expansion.reason", "Explain the mode in one line.")
    upgrades = expansion.get("upgrades")
    if not isinstance(upgrades, list) or not 3 <= len(upgrades) <= 7:
        gap("expansion.upgrades", "Propose 3–7 upgrades.")
    for index, upgrade in enumerate(upgrades if isinstance(upgrades, list) else []):
        path = f"expansion.upgrades[{index}]"
        if not isinstance(upgrade, dict):
            gap(path, "Record an upgrade object.")
            continue
        identity = upgrade.get("id")
        if not text(identity) or identity in ids:
            gap(path + ".id", "Use a locally unique stable ID.")
        else:
            ids.add(identity)
            if upgrade.get("disposition") == "accept":
                accepted.append(identity)
        for key in ("text", "kind", "impact"):
            if not text(upgrade.get(key)):
                gap(path + "." + key, "Record nonblank text.")
        if upgrade.get("disposition") not in ("accept", "reject", "defer"):
            gap(path + ".disposition", "Propose accept, reject or defer for confirmation.")
    for heading, aliases in SECTIONS[5:7]:
        value = next((fields[key] for key in aliases if key in fields), None)
        if not (text(value) or isinstance(value, list) and value and all(map(text, value))):
            gap(aliases[0], f"Record the {heading.lower()} baseline.")
    linked = {
        ref
        for node in downstream_nodes or []
        if node["kind"] in ("requirement", "decision", "acceptance")
        for ref in node["fields"].get("expansionRefs", [])
        if isinstance(ref, str)
    }
    return dict(
        gaps=gaps,
        acceptedUpgradeRefs=accepted,
        untracedAcceptedUpgradeRefs=(
            None if downstream_nodes is None else [ref for ref in accepted if ref not in linked]
        ),
        next=EXPANSION_NEXT,
    )


def label(key: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key).replace("_", " ").capitalize()


def readable(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join("- " + readable(item) for item in value) or "None recorded"
    if isinstance(value, dict):
        return "\n\n".join(f"{label(key)}:\n{readable(item)}" for key, item in value.items())
    if value is None:
        return "Not specified"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def vision_projection(vision: dict) -> dict:
    """Consume a Spine node; aliases retain their established first-present precedence.

    Missing fields alone get 'Not recorded'. Empty/null/false values remain recorded
    values, and extra fields retain their complete words rather than disappearing.
    """
    fields = taste_fields(vision["fields"])
    used = {"validation", "requirementFiles", "schema"}
    sections = []
    for heading, aliases in SECTIONS:
        key = next((key for key in aliases if key in fields), None)
        present = key is not None or heading == "Vision"
        value = fields[key] if key else vision["text"] if heading == "Vision" else None
        sections.append({"label": heading, "text": readable(value) if present else "Not recorded"})
        if key:
            used.add(key)
    if "vision" in fields and fields["vision"] != vision["text"]:
        sections.append({"label": "Complete vision details", "text": vision["text"]})
    sections.extend(
        {"label": label(key), "text": readable(value)}
        for key, value in fields.items()
        if key not in used
    )
    return {"title": vision["title"], "sections": sections}


def render_vision(projection: dict) -> str:
    return "\n\n".join(
        [projection["title"]]
        + [f"{section['label']}:\n{section['text']}" for section in projection["sections"]]
    )
