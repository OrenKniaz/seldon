"""C20/C26: render the independent walk and its recorded artifacts read-only."""

import re
from html import escape
from pathlib import Path
from urllib.parse import quote

from .. import gitwork
from .owner_guide import owner_steps, owner_text
from .store import SpecStore
from .walk_record import current_walk, read_walk, release_notes

LABEL = {"works": ("ok", "Works"), "broken": ("bad", "Broken"),
         "can't-test-here": ("cant", "Couldn’t test here"), "pending": ("cant", "Awaiting proof")}


def _step(row):
    tone, label = LABEL[row["outcome"]]
    mark = {"works": "✓", "broken": "✗ broken"}.get(row["outcome"], label)
    action = escape(row["action"])
    images = step_views(row)
    shot = "".join(_image(view, action) for view in images) or "Screenshot awaiting proof"
    return (
        f'<div class="step {tone}"><div class="shot">{shot}</div><div class="stepbody">'
        f'<b>{action}</b><span class="who">{escape(row["role"])}</span>'
        f'<div class="ea"><span class="exp">Expected: {escape(row["expected"])}</span>'
        f'<span>Saw: {escape(row["actual"])}</span></div>'
        f'<span class="verdict v-{tone}">{mark}</span></div></div>'
    )


def step_views(row):
    if "views" in row:
        return row["views"]
    return [{"role": row["role"], "artifact": row["artifact"]}] if row.get("artifact") else []


def _image(view, action):
    url = f'walk-shot/{quote(view["artifact"])}'
    role = escape(view["role"])
    wide = ' class="desktop-view"' if re.search(r"-(?:[7-9]\d{2}|\d{4,})\.png$", url) else ""
    return (f'<a href="{url}"{wide} target="_blank" rel="noopener">'
            f'<img src="{url}" alt="{role}: {action}"><small>{role}</small></a>')


def render(template, store, name, owner):
    runs = [record for record in store.records("lead2_walk") if "results" in record[2]]
    if runs:
        current = current_walk(store, SpecStore(store, readonly=True).snapshot().revision)
        ref, _, raw = runs[-1]
        run = current or read_walk(store, ref, raw)
        notes = release_notes(store, run)
        body = _release(run, notes, store.cursor().scratch, current is not None)
    else:
        body = ('<div class="hero"><div><h1>The walk</h1>'
                f'<p>{"The independent walk is under way." if store.cursor().state == "L2-WALK" else "The independent walk has yet to start."}</p></div></div>')
    identity = name if owner == "Dream owner" else f'{name} · {owner.split(" · ", 1)[0]}'
    return template.replace("<!-- walk identity -->", escape(identity)).replace("<!-- walk content -->", body)


def _release(run, notes, scratch, current):
    candidate = quote(run["candidate"])
    preview = (f'<form method="post" action="walk-preview/{candidate}">'
               '<button class="btn" type="submit">▶ Open this build</button></form>' if run["launch"] else "")
    body = (f'<div class="hero"><h1>The independent walk</h1><div class="open">{preview}'
            f'<a class="btn2" href="walk-build/{candidate}">Download a clean copy</a></div></div>')
    if not current:
        body += '<p class="limits">These results describe an earlier build.</p>'
    if notes:
        notes = _owner_projection(notes, scratch)
        body += '<section><h2>What works</h2>'
        for row in notes["works"]:
            link = f' <a href="walk-shot/{quote(row["artifact"])}">Screenshot</a>' if row["artifact"] else ""
            body += f'<p>{escape(row["text"])}{link}</p>'
        if notes["items"]:
            body += '<h2>What’s weaker</h2>' + "".join(
                f'<p>{escape(r["id"])} · <b>{escape(r["label"])}</b> {escape(r["text"])}</p>' for r in notes["items"])
        body += '</section>'
        if notes["omitted"]:
            body += ('<details class="limits"><summary>Left out of the release notes</summary><ul>' + "".join(
                f'<li>{escape(row["id"])} · {escape(row["text"])} <small>{escape(row["reason"])}</small></li>'
                for row in notes["omitted"]) + '</ul></details>')
    body += "".join(_step(step) for step in _owner_projection(run["steps"], scratch))
    if run["guide_text"]:
        body += ('<details class="limits guide"><summary>How to try it yourself</summary>'
                 f'{owner_steps(run["guide_text"], scratch)}</details>')
    return body


def _owner_projection(value, scratch):
    if isinstance(value, str):
        return owner_text(value, scratch)
    if isinstance(value, list):
        return [_owner_projection(item, scratch) for item in value]
    if isinstance(value, dict):
        return {key: _owner_projection(item, scratch) for key, item in value.items()}
    return value


def shot(store, artifact):
    if not any(view["artifact"] == artifact for ref, _, run in store.records("lead2_walk")
               for step in read_walk(store, ref, run)["steps"] for view in step_views(step)):
        return None
    root = (Path(store.cursor().scratch) / "walk").resolve()
    path = (root / artifact).resolve()
    return path.read_bytes() if path.is_relative_to(root) and path.is_file() else None


def build(store, candidate):
    if any(run["candidate"] == candidate for _, _, run in store.records("lead2_walk")):
        return gitwork.git(store.root.parent, "archive", "--format=zip", candidate, binary=True)
