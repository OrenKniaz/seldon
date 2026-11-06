"""Provider-derived packet limits and lossless generated-view compaction."""

PROVIDER_TOKEN_BUDGETS = {"claude": 150_000, "codex": 200_000, "copilot": 100_000}


def check_packet(packet, sections, limit):
    if len(packet) > limit:
        sizes = "; ".join(f"{section.splitlines()[0][:80]}: {len(section)}" for section in sections)
        raise ValueError(
            f"worker packet contract/spec/interfaces exceed {limit} characters: "
            f"total {len(packet)}; sections (characters): {sizes}; "
            f"separators: {2 * (len(sections) - 1)}"
        )


def compact_view(name, text, seen):
    paragraphs = text.split("\n\n")
    for index, paragraph in enumerate(paragraphs):
        if name.endswith(".md") and index and paragraphs[index - 1].startswith("### "):
            key = (paragraphs[index - 1], paragraph.rstrip("\n"))
            if key in seen:
                paragraphs[index] = f"[Identical text: see {seen[key]}.]" + paragraph[len(key[1]) :]
            else:
                seen[key] = f"{name}, paragraph {index + 1}"
    return "\n\n".join(paragraphs)
