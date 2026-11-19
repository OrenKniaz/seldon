"""C1/C20: keep provider and Git work off the person's desktop."""

import os


def environment(extra=None):
    return {**os.environ, **(extra or {}), "GIT_EDITOR": "true", "GIT_SEQUENCE_EDITOR": "true",
            "GIT_MERGE_AUTOEDIT": "no", "GIT_TERMINAL_PROMPT": "0",
            "BROWSER": "cmd /c exit 0 %s" if os.name == "nt" else "true %s"}


WRITE_RULE = ("The person is using this computer, so never open files or pages in desktop apps "
              "(start, Invoke-Item, explorer, notepad, or a desktop browser); check pages with the "
              "headless kit in checks/kit/browser.cjs and save screenshots as evidence.")
