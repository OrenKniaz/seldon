"""C20: register chats with the independent, loopback-only status server."""

import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener


def registry_path():
    home = Path(os.environ.get("SELDON_HOME", Path.home() / ".seldon"))
    return home.resolve() / "lead2-status.sqlite3"


def _register(project, registry):
    if not registry.is_file():
        return None
    with closing(sqlite3.connect(registry, timeout=1)) as db:
        try:
            port, key = db.execute("SELECT port, secret FROM server").fetchone()
        except (TypeError, sqlite3.OperationalError):
            return None  # First start has yet to publish its address.
    base = f"http://127.0.0.1:{port}"
    request = Request(f"{base}/register", os.fsencode(project), {"X-Seldon-Key": key})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=1) as response:
            run = int(response.read())
    except OSError:
        return None
    home = f"{base}/{key}/"
    return {"statusHome": home, "statusPage": f"{home}{run}/"}


def registered_links(project):
    registry = registry_path()
    if not registry.is_file():
        return {}
    with closing(sqlite3.connect(registry, timeout=1)) as db:
        server = db.execute("SELECT port, secret FROM server").fetchone()
        row = db.execute("SELECT id FROM run WHERE folder = ?", (str(project.resolve()),)).fetchone()
    if not server or not row:
        return {}
    port, key = server
    return {"statusPage": f"http://127.0.0.1:{port}/{key}/{row[0]}/"}


def _launch(registry):
    args = ["-B", "-m", "seldon.lead2.status_server", str(registry)]
    streams = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.name != "nt":
        subprocess.Popen([sys.executable, *args], start_new_session=True, **streams)
        return
    # A chat's Windows Job ends with its transport. The local process service owns the listener.
    interpreter = Path(sys._base_executable).with_name("pythonw.exe")
    command = subprocess.list2cmdline([str(interpreter), *args])
    source = str(Path(__file__).resolve().parents[2])
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "$ErrorActionPreference='Stop'; "
         "$process = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
         "@{CommandLine=$env:SELDON_STATUS_COMMAND; CurrentDirectory=$env:SELDON_STATUS_SOURCE}; "
         "exit $process.ReturnValue"],
        env={**os.environ, "SELDON_STATUS_COMMAND": command, "SELDON_STATUS_SOURCE": source},
        creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=5, **streams,
    )


def links(project):
    registry = registry_path()
    if result := _register(project, registry):
        return result
    unavailable = {"statusError": "Could not open the Seldon status server. Ask for state to try again."}
    try:
        _launch(registry)
    except (OSError, subprocess.SubprocessError):
        return unavailable
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if result := _register(project, registry):
            return result
        time.sleep(.05)
    return unavailable
