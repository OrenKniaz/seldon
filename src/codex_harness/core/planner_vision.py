"""One read-only projection of the canonical stored vision, without approval credit."""

import re

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
    fields = vision["fields"]
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
