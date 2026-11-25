"""Render the candidate's owner instructions without exposing test workspace paths."""

import re
from html import escape
from itertools import groupby


def owner_text(text, scratch):
    if not scratch:
        return text
    root = re.escape(str(scratch).rstrip("/\\").replace("\\", "/")).replace("/", r"[/\\]")
    return re.sub(root + r"""(?:[/\\][^\s`'"<>),;]+)*""", "the temporary test folder", text,
                  flags=re.IGNORECASE)


def owner_steps(text, scratch):
    text = owner_text(text, scratch)
    text = re.split(r"(?im)^#{1,6}\s+(?:developer|automated) (?:checks|tests)\s*$", text, maxsplit=1)[0]
    blocks = re.split(r"(?ms)^\s*```[^\n]*\n(.*?)^\s*```\s*$", text)
    html = []
    for index, block in enumerate(blocks):
        if index % 2:
            html.append(f"<pre><code>{escape(block.rstrip())}</code></pre>")
        else:
            html.extend(_formatted(block))
    return "".join(html)


def _inline(text):
    return re.sub(r"`([^`]+)`|\*\*([^*]+)\*\*",
                  lambda m: f"<code>{m[1]}</code>" if m[1] else f"<strong>{m[2]}</strong>", escape(text))


def _formatted(block):
    def classify(line):
        if line.startswith("#"):
            return "h3", line.lstrip("#").strip(), 1
        if item := re.match(r"^(\d+[.)]|[-*])\s+(.+)", line):
            return ("ol", item[2], int(item[1][:-1])) if item[1][0].isdigit() else ("ul", item[2], 1)
        return ("p" if line else ""), line, 1

    lines = [classify(line.strip()) for line in block.splitlines()]
    for tag, group in groupby(lines, key=lambda row: row[0]):
        if not tag:
            continue
        rows = list(group)
        items = [_inline(text) for _, text, _ in rows]
        if tag in ("ol", "ul"):
            start = f' start="{rows[0][2]}"' if tag == "ol" and rows[0][2] != 1 else ""
            yield f"<{tag}{start}>" + "".join(f"<li>{item}</li>" for item in items) + f"</{tag}>"
        else:
            yield f"<{tag}>" + " ".join(items) + f"</{tag}>"
