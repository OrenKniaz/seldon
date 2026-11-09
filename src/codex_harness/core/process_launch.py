"""Window policy for console subprocesses launched by Seldon."""


def hidden_process_kwargs(creationflags=0):
    """Hide Windows console children while preserving existing creation flags."""
    import os
    import subprocess

    if os.name == "nt":
        return {"creationflags": creationflags | subprocess.CREATE_NO_WINDOW}
    return {}
