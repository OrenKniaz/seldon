"""C20/C26: render the independent walk and its recorded artifacts read-only."""

import re
from html import escape
from pathlib import Path
from urllib.parse import quote

from .. import gitwork
from .owner_guide import owner_steps, owner_text
from .store import SpecStore
from .walk_record import ASSESSMENTS, current_walk, read_walk, release_notes

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


def _story(story, steps, results, feedback, sent, failures):
    states = {row["outcome"] for row in steps} | {results.get(p, "pending") for p in story["promises"]}
    if failures:
        states.add("broken")
    state = next((key for key in ("broken", "pending", "can't-test-here") if key in states),
                 "works" if states else "pending")
    tone, label = LABEL[state]
    css = {"works": "pass", "broken": "fail"}.get(state, "")
    roles = "".join(f'<span class="role">{escape(role.partition(":")[0].replace(", ", " · ", 1))}</span>'
                    for role in story["roles"])
    notes = "".join(f'<div class="ux"><b>UX feedback:</b> {escape(note)}</div>' for note in feedback)
    observed = {f"{row['action']}: expected {row['expected']}; saw {row['actual']}"
                for row in steps if row["outcome"] == "broken"}
    checks = "".join(f'<div class="fix"><b>✗ Verification failed</b> {escape(finding)}</div>'
                     for finding in failures if finding not in observed)
    fix = ('<div class="fix"><b>Sent to the builder.</b> Seldon will re-walk this story after the fix.</div>'
           if sent and state == "broken" else "")
    body = (
        f'<section class="story {css}"><div class="shead"><div><h2>{escape(story["title"])}</h2>'
        f'<small>{escape(story["story"]) if story["story"] != story["title"] else ""}</small>'
        f'<div class="roles">{roles}</div></div><span class="verdict v-{tone}">{label}</span></div>'
        f'<div class="steps">{"".join(map(_step, steps))}</div>{checks}{fix}{notes}</section>'
    )
    return state, body


def _contents(run, sent, name, scratch, current, limits):
    results = {row["promise"]: row["outcome"] for row in run["promises"]}
    findings = run["open_defects"]
    cards = [_story(story, [s for s in run["steps"] if s["story"] == ref], results,
                    [f["note"] for f in run["feedback"] if f["story"] == ref],
                    sent and (not sent.get("stories") or ref in sent["stories"] or
                              set(story["promises"]).intersection(sent.get("promises", []))),
                    [f["finding"] for f in findings if f["story"] == ref and f["owner"] != "planner"])
             for ref, story in run["stories"].items()]
    counts = [sum(state == key for state, _ in cards) for key in LABEL]
    counts[2] = len(limits)
    roles = list(dict.fromkeys(view["role"] for step in run["steps"] for view in step_views(step)))
    actors = f' as {", ".join(roles[:-1])} and {roles[-1]}' if len(roles) > 1 else (
        f" as {roles[0]}" if roles else "")
    title = f"Seldon walked {name}{actors}."
    broken = " broken → sent to the builder" if sent else " broken"
    labels = (" story works" if counts[0] == 1 else " stories work", broken,
              " couldn’t test here", " awaiting proof")
    pills = "".join(f'<span class="pill {tone}">{count}{label}</span>'
                    for (tone, _), count, label in zip(LABEL.values(), counts, labels, strict=False) if count)
    items = "".join(f"<li>{escape(limit)}</li>" for limit in limits)
    box = (f'<div class="limits"><b>Couldn’t test here · Venue limits</b><ul>{items}</ul></div>'
           if items else "")
    preview = (f'<form method="post" action="walk-preview/{quote(run["candidate"])}">'
               '<button class="btn" type="submit">▶ Open this build</button></form>'
               if run.get("launch") else "")
    guide_link = ('<a class="btn2" href="#build" onclick="document.getElementById(\'build\').open=true">'
                  'How to try it yourself · fresh-copy guide</a>' if run["guide_text"] else "")
    download = f'<a class="btn2" href="walk-build/{quote(run["candidate"])}">Download a clean copy</a>'
    launch = f'<div class="open">{preview}{guide_link}{download}</div>'
    guide = ('<details class="limits guide" id="build"><summary>How to try it yourself</summary>'
             f'{owner_steps(run["guide_text"], scratch)}</details>' if run["guide_text"] else "")
    overview = "".join(f'<div class="limits verification"><b>✗ Verification failed</b> {escape(f["finding"])}'
                       f'{" <b>Sent to the builder.</b>" if sent else ""}</div>'
                       for f in findings if not f["story"] and f["owner"] != "planner")
    guide_worked = run["verdicts"]["guide"]["outcome"] == "works"
    intro = ("A fresh tester opened the real app from a clean copy. Here is what happened, step by step."
             if guide_worked else "Here is what the independent tester observed.")
    hero = (f'<div class="hero"><div><h1>{escape(title)}</h1><p>{intro}</p>'
            f'<div class="score">{pills}</div></div>{launch}</div>')
    stale = ('<p class="limits">These results describe an earlier build.</p>' if not current else "")
    checks = "".join(f'<p><b>{label}:</b> {escape(assessment["reason"])}</p>'
                     for key, label in ASSESSMENTS.items() if (assessment := run["verdicts"][key])["reason"]
                     and assessment["outcome"] == "works")
    unassigned = "".join(_step(row) for row in run["steps"] if not row["story"] and row["action"])
    pending = (f'<section class="story"><h2>Observations awaiting a story</h2>{unassigned}</section>'
               if unassigned else "")
    return hero + stale + "".join(card for _, card in cards) + pending + overview + box + checks + guide


def render(template, store, name, owner):
    runs = store.records("lead2_walk")
    if runs:
        current = current_walk(store, SpecStore(store, readonly=True).snapshot().revision)
        ref, _, raw = runs[-1]
        run = current or read_walk(store, ref, raw)
        if "results" in run:
            body = _release(run, release_notes(store, current) if current else None,
                            store.cursor().scratch, current is not None)
            return template.replace("<!-- walk identity -->", escape(name)).replace("<!-- walk content -->", body)
        run["open_defects"] = [row for row in run.get("issues", []) if row.get("ref") in run.get("defects", {})]
        sent = next((row for _, _, row in reversed(store.records("lead2_walk_repair"))
                     if row["target"] == run["target"]), None)
        cursor = store.cursor()
        scratch = cursor.scratch
        unverified = [store.read(ref)["reason"] for ref in run.get("limits", []) if isinstance(ref, str)]
        body = _contents(_owner_projection(run, scratch), sent, name, scratch, current, unverified)
        if "verdicts" not in raw:
            answer = store.read(run["source"])["answer"]
            body += "".join(f'<p><b>{label}:</b> {escape(owner_text(answer[key], scratch))}</p>'
                            for key, label in ASSESSMENTS.items() if answer.get(key))

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
        body += '<section><h2>What works</h2>'
        for row in notes["works"]:
            link = f' <a href="walk-shot/{quote(row["artifact"])}">Screenshot</a>' if row["artifact"] else ""
            body += f'<p>{escape(row["text"])}{link}</p>'
        if notes["items"]:
            body += '<h2>What’s weaker</h2>' + "".join(
                f'<p>{escape(r["id"])} · <b>{escape(r["label"])}</b> {escape(r["text"])}</p>' for r in notes["items"])
        body += '</section>'
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
