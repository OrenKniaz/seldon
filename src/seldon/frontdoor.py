"""C20/C25/C23: relay chat words and derive state; detached controllers own execution."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from . import decisions, providers
from .decisions import Waiting
from .run import TERMINAL, Run
from .store import Store


RELAY = ("Show the question and options as written, recommendation first, using your own ask-user tool "
         "when you have one; relay the picked label or the user's own words through answer; "
         "never pick for the user.")


class Refusal(ValueError):
    """A caller can fix this with the named next action."""


def side(run):
    return Path(str(Path(run).resolve()) + ".seldon")


@contextmanager
def opened(run):
    project = Path(run).resolve()
    if not (project / ".seldon/state.sqlite3").is_file():
        raise Refusal("Unknown folder; start a run first")
    store = Store(project)
    try:
        yield store
    finally:
        store.close()


def running(store):
    try:
        store.claim_controller().close()
        return False
    except ValueError:
        return True


def state(run: str):
    """Show canonical progress, the displayed request, and where the next words go."""
    with opened(run) as store:
        cursor = store.cursor()
        active = running(store)
        waiting = ({"request": cursor.decision, **store.read(cursor.decision)}
                   if cursor.decision and cursor.state not in TERMINAL else None)
        if waiting:
            ask = waiting.get("ask")
            waiting = {key: waiting[key] for key in ("request", "gate", "subject")}
            if ask:
                waiting["ask"] = {"question": ask["question"], "options": [
                    {"label": option["label"], "means": option["means"]} for option in ask["options"]
                ] + [{"label": "Other", "means": "Your own words"}]}
        outcomes = store.records("outcome")
        next_step = ("Use start with a new folder for your next dream." if cursor.state in TERMINAL else
                     f"{RELAY} Use answer with request {cursor.decision}." if waiting else
                     "Use pause to pause this run." if active else "Use resume to continue this run.")
        status = ("ACCEPTED" if cursor.state in ("ACCEPTED", "SCRIPTED_C") else
                  "STOPPED" if cursor.state == "STOPPED" else
                  "RUNNING" if waiting and active else "WAITING" if waiting else
                  "PAUSED" if cursor.failure else "RUNNING")
        return {"status": status, "reason": cursor.failure, "run": str(Path(run).resolve()), "state": cursor.state,
                "controller": "running" if active else "idle", "waitingFor": waiting,
                "units": {ref: {key: getattr(row, key) for key in ("state", "waiting", "failure")}
                          for ref, row in store.units().items()},
                "outcome": outcomes[-1][2] if outcomes else None, "next": next_step}


def spawn(run):
    run = str(Path(run).resolve())
    # A hidden console, not DETACHED_PROCESS: a console-less venv launcher makes Windows open a visible
    # console for the real interpreter and every console child (T-177).
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
             if os.name == "nt" else 0)
    with (side(run) / "controller.log").open("ab") as log:
        options = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
        command = [sys.executable, "-m", "seldon.frontdoor", "drive", run]
        try:
            child = subprocess.Popen(command, **{**options, "creationflags": flags | 0x01000000}
                                     if os.name == "nt" else {**options, "start_new_session": True})
        except OSError as error:
            if os.name != "nt" or error.winerror != 5:
                raise
            child = subprocess.Popen(command, **options)
        (side(run) / "controller.json").write_text(json.dumps({"pid": child.pid}), encoding="utf-8")
        deadline = time.monotonic() + 2
        while child.poll() is None and time.monotonic() < deadline:
            if state(run)["controller"] == "running":
                break
            time.sleep(.02)
        if child.poll() not in (None, 0):
            raise Refusal("Controller failed; inspect " + str(side(run) / "controller.log"))
    return state(run)


def pause(run: str):
    """End the controller and wait for the existing lease to become available."""
    with opened(run) as store:
        if running(store):
            pid = json.loads((side(run) / "controller.json").read_text("utf-8"))["pid"]
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 10
        while running(store):
            if time.monotonic() >= deadline:
                raise Refusal("Controller still owns the run; wait for it to exit, then use pause")
            time.sleep(.02)
    return state(run)


def drive(run):
    root = side(run)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(root / "temp")
    os.environ["SELDON_EVIDENCE_DIR"] = str(root / "evidence")
    with opened(run) as store:
        setup = store.read(store.cursor().kickoff)["setup"]
    provider = providers.Provider(root / "calls", setup)

    def user(request):
        answer = root / "answer.json"
        if answer.exists():
            response = json.loads(answer.read_text("utf-8"))
            if response["request"] == request["request"]:
                # Keep the carrier until the decision commits and a different request is shown.
                return response["words"]
            answer.unlink()
        raise Waiting()

    deadline = time.monotonic() + 2
    while True:
        try:
            loop = Run.reopen(Path(run), provider, user)
            break
        except ValueError as error:
            if not str(error).startswith("Another controller") or time.monotonic() >= deadline:
                raise
            time.sleep(.02)
    try:
        loop.run()
    finally:
        loop.close()


def server(setup=providers.setup_facts):
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    app = MCPServer("seldon", instructions=(
        "Relay the user's dream through start. " + RELAY +
        " Show state and its next sentence; all decisions happen in this chat."))
    def tool(fn):
        @wraps(fn)
        def relay(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Refusal as error:
                raise ToolError(str(error)) from error
        return app.tool()(relay)

    tool(state)
    tool(pause)

    @tool
    def start(dream: str, folder: str):
        """Start a new run from the user's dream in a new product folder."""
        project = Path(folder).resolve()
        if project.exists():
            raise Refusal("Folder exists; choose a new folder")
        root = side(project)
        for name in ("calls", "evidence", "temp"):
            (root / name).mkdir(parents=True, exist_ok=True)
        facts = {**setup(), "scriptedUser": False, "userOrigin": "user's words relayed by the provider chat",
                 "evidenceDirectory": str(root / "evidence")}
        Run(project, root / "workers", dream, facts, None, None).close()
        return spawn(project)

    def available(run):
        value = state(run)
        if value["state"] in TERMINAL:
            raise Refusal("Run is " + value["state"] + "; use start with a new folder")
        if value["controller"] == "running":
            raise Refusal("Controller is running; use pause first")
        return value

    @tool
    def answer(run: str, request: str, words: str):
        """Relay the picked label or the user's own words verbatim; never pick for the user."""
        value = state(run)
        if value["state"] in TERMINAL:
            raise Refusal("Run is " + value["state"] + "; use start with a new folder")
        waiting = value["waitingFor"]
        if not waiting:
            raise Refusal("No request is waiting; use resume")
        if request != waiting["request"]:
            raise Refusal("Use answer request " + waiting["request"])
        target = side(run) / "answer.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"request": request, "words": words}), encoding="utf-8")
        temporary.replace(target)
        return state(run) if value["controller"] == "running" else spawn(run)

    @tool
    def resume(run: str):
        """Continue an idle run; a displayed request needs answer with the user's words."""
        pause(run)
        value = available(run)
        if value["waitingFor"] and not (side(run) / "answer.json").exists():
            raise Refusal("Use answer request " + value["waitingFor"]["request"])
        return spawn(run)

    @tool
    def stop(run: str):
        """Stop the controller and record the user's stop through the existing lifecycle."""
        pause(run)
        available(run)
        with opened(run) as store, store.claim_controller():
            decisions.stop(store, "Stopped by the user in chat")
        return state(run)

    return app


def main():
    parser = argparse.ArgumentParser(description="Seldon chat front door")
    commands = parser.add_subparsers(dest="command", required=True, metavar="{mcp,status}")
    commands.add_parser("mcp", help="serve chat tools over stdio")
    commands.add_parser("status", help="show run state").add_argument("run")
    commands.add_parser("drive").add_argument("run")
    args = parser.parse_args()
    if args.command == "mcp":
        server().run()
    elif args.command == "status":
        print(json.dumps(state(args.run), ensure_ascii=False))
    else:
        drive(args.run)


if __name__ == "__main__":
    main()
