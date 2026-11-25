"""C20: index and render the shared status home from run summaries."""

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from time import time

from ..store import Store, encode
from .status_read import HOME_STAGES, HomeSummary, last_activity, summary

PAGE_SIZE = 20
TONES = {"Waiting on you": "wait", "Building": "build", "Fixing": "fix", "Done": "done",
         "Stopped": "stop", "Missing": "stop", "Paused": "stop"}


def status_template(name):
    folder = Path(__file__).parent
    theme = folder.joinpath("status_theme.js").read_text("utf-8")
    template = folder.joinpath(name).read_text("utf-8")
    return template.replace("<!-- theme script -->", f"<script>{theme}</script>")


def read_summary(project):
    when = last_activity(project / ".seldon/state.sqlite3")
    with closing(Store(project, readonly=True)) as store:
        return summary(store, when)


def _time(when, now):
    if when is None:
        return ""
    local = datetime.fromtimestamp(when).astimezone()
    minutes = int(max(now - when, 0) // 60)
    if minutes < 1:
        relative = "Just now"
    elif minutes < 60:
        relative = f"{minutes} min ago"
    elif minutes < 1440:
        relative = f"{minutes // 60} h ago"
    else:
        relative = f"{minutes // 1440} d ago"
    exact = local.strftime("%d %b %Y, %H:%M:%S")
    return f'<time datetime="{local.isoformat(timespec="seconds")}" title="{exact}">{relative}</time>'


def _card(run, plan, now):
    tag = "article" if plan.chip == "Missing" else "a"
    href = f' href="{run}/"' if tag == "a" else ""
    tone = TONES.get(plan.chip, "")
    waiting = tone == "wait"
    pulse = '<span class="pulse" aria-hidden="true"></span>' if waiting else ""
    completed = plan.stage + (plan.chip == "Done")
    current = plan.stage if plan.chip != "Missing" else -1
    marker = "bad" if tone == "fix" else "cur"
    segments = "".join(
        f'<span class="seg {"done" if i < completed else marker if i == current else ""}" '
        f'title="{escape(stage)}"></span>'
        for i, stage in enumerate(HOME_STAGES)
    )
    return (f'<li><{tag} class="card{" waiting" if waiting else ""}"{href}>'
            f'<div class="top"><h3 class="title">{escape(plan.name)}</h3>'
            f'<span class="chip {tone}">{pulse}{escape(plan.chip)}</span></div>'
            f'<p class="idea" title="{escape(plan.idea)}">{escape(plan.idea)}</p>'
            f'<div class="strip" aria-hidden="true">{segments}</div>'
            f'<div class="stage"><b>{escape(plan.label)}</b></div>'
            f'<div class="owner"><span>{escape(plan.owner)}</span>{_time(plan.when, now)}</div></{tag}></li>')


class Home:
    def __init__(self, db):
        self.db = db
        self.template = Template(status_template("status_home.html"))
        db.executescript("""
            CREATE TABLE IF NOT EXISTS home (run INTEGER PRIMARY KEY, earlier INTEGER NOT NULL,
                                              activity REAL NOT NULL, summary TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS home_sections ON home (earlier, activity DESC, run DESC);
            CREATE INDEX IF NOT EXISTS home_recent ON home (activity DESC, run DESC);
        """)
        # Index registrations made before the home had sections. Keep their IDs even if the folder is gone.
        with db:
            for run, folder, name, owner in db.execute(
                "SELECT id, folder, name, owner FROM run LEFT JOIN home ON home.run = run.id "
                "WHERE home.run IS NULL"
            ):
                empty = HomeSummary(name, owner, "", -1, "", "Missing", None)
                self.refresh(run, folder, empty, new=True)

    def save(self, run, plan):
        self.db.execute("INSERT OR REPLACE INTO home VALUES (?, ?, ?, ?)",
                        (run, plan.earlier, plan.when or 0, encode(asdict(plan))))

    def refresh(self, run, folder, saved, *, new=False):
        try:
            plan = read_summary(Path(folder))
        except FileNotFoundError:
            plan = replace(saved, chip="Missing")
        except (OSError, sqlite3.Error, TypeError, ValueError):
            plan = saved
        if new or plan != saved:
            self.save(run, plan)
        return plan

    def sidebar(self, current):
        rows = self.db.execute("""
            SELECT home.run, folder, summary FROM home JOIN run ON run.id = home.run
            ORDER BY activity DESC, home.run DESC LIMIT 5
        """)
        links = []
        for run, folder, body in rows:
            plan = self._saved_summary(folder, body)
            active = ' aria-current="page"' if run == current else ""
            css = "plan active" if active else "plan"
            name, state = escape(plan.name), escape(plan.chip)
            label = f"{name} · {state}"
            content = (f'<span class="pdot {TONES.get(plan.chip, "")}" aria-hidden="true"></span>'
                       f'<span><span class="plan-name">{name}</span>'
                       f'<span class="plan-sub">{state}</span></span>')
            if plan.chip == "Missing":
                links.append(f'<div class="{css}" title="{label}">{content}</div>')
            else:
                links.append(f'<a class="{css}" href="../{run}/" title="{label}" aria-label="{label}"'
                             f'{active}>{content}</a>')
        return "".join(links)

    @staticmethod
    def _saved_summary(folder, body):
        plan = HomeSummary(**json.loads(body))
        return plan if (Path(folder) / ".seldon/state.sqlite3").is_file() else replace(plan, chip="Missing")

    def render(self, page):
        active = []
        with self.db:
            rows = self.db.execute(
                "SELECT home.run, folder, summary FROM home JOIN run ON run.id = home.run WHERE earlier = 0"
            ).fetchall()
            for run, folder, body in rows:
                plan = self.refresh(run, folder, HomeSummary(**json.loads(body)))
                if not plan.earlier:
                    active.append((run, plan))
        active.sort(key=lambda entry: (entry[1].chip != "Waiting on you", -(entry[1].when or 0), -entry[0]))
        earlier = self.db.execute("""
            SELECT home.run, folder, summary FROM home JOIN run ON run.id = home.run WHERE earlier = 1
            ORDER BY activity DESC, home.run DESC LIMIT ? OFFSET ?
        """, (PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)).fetchall()
        now = time()
        history = (_card(run, self._saved_summary(folder, body), now)
                   for run, folder, body in earlier[:PAGE_SIZE])
        prev = f'<a href="?page={page - 1}#earlier" rel="prev">← Previous</a>' if page > 1 else ""
        more = (f'<a href="?page={page + 1}#earlier" rel="next">Show {PAGE_SIZE} more</a>'
                if len(earlier) > PAGE_SIZE else "")
        if active or earlier or page > 1:
            content = ('<section id="now" aria-labelledby="now-heading"><h2 id="now-heading">Now</h2>'
                       + self._grid(_card(run, plan, now) for run, plan in active)
                       + '</section><section id="earlier" aria-labelledby="earlier-heading">'
                         '<h2 id="earlier-heading">Earlier</h2>' + self._grid(history)
                       + (f'<nav class="more" aria-label="Earlier plans">{prev}{more}</nav>'
                          if prev or more else "") + "</section>")
        else:
            content = ('<div class="empty"><h2>No plans yet.</h2>'
                       '<p>Start one in chat: tell Seldon your idea.</p></div>')
        waiting = sum(plan.chip == "Waiting on you" for _, plan in active)
        pulse = '<span class="pulse" aria-hidden="true"></span>' if waiting else ""
        badge = f'<span class="need{" quiet" if not waiting else ""}">{pulse}{waiting} waiting on you</span>'
        return self.template.substitute(content=content, waiting=badge).encode()

    @staticmethod
    def _grid(cards):
        content = "".join(cards)
        return f'<ul class="grid">{content}</ul>' if content else '<p class="quiet">No plans here.</p>'
