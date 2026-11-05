import queue
import threading
import time
from contextlib import contextmanager
from http.client import HTTPConnection

from codex_harness.core import status_server


def bounded(action, label):
    """A stalled response or shutdown cannot own the golden beyond its wall deadline."""
    replies = queue.Queue()
    def run():
        try:
            replies.put((True, action()))
        except Exception as error:
            replies.put((False, error))
    threading.Thread(target=run, daemon=True).start()
    end = time.time() + 5
    while time.time() < end:
        try:
            ok, value = replies.get(timeout=.25)
        except queue.Empty:
            continue
        if not ok:
            raise value
        return value
    raise TimeoutError(f"Cockpit {label} exceeded 5s wall-clock deadline")


@contextmanager
def running(tmp_path, workspace=None, runs_root=None):
    with status_server.create_server(
        workspace=workspace or tmp_path / "missing-workspace",
        runs_root=runs_root or tmp_path / "missing-runs",
        port=0,
    ) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            yield connection
        finally:
            connection.close()
            bounded(server.shutdown, "HTTP shutdown")
            thread.join(timeout=1)
            assert not thread.is_alive(), "Cockpit HTTP thread failed to terminate"


def get(connection, path):
    def readback():
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    return bounded(readback, "HTTP fetch/readback")
