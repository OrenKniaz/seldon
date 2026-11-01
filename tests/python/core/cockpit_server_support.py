import threading
from contextlib import contextmanager
from http.client import HTTPConnection

from codex_harness.core import status_server


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
            server.shutdown()
            thread.join(timeout=5)


def get(connection, path):
    connection.request("GET", path)
    response = connection.getresponse()
    return response.status, dict(response.getheaders()), response.read()
