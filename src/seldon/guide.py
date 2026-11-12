"""C8/C15/I1; R9: execute the guide's declared paths and argv steps from delivery."""

import json
import re
import sys
from pathlib import Path

from . import plan


def checks(root):
    text = (Path(root) / plan.GUIDE_PATH).read_text("utf-8")
    paths = list(dict.fromkeys(re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)))
    blocks = re.findall(r"^```([^\n]*)\n(.*?)^```\s*$", text, re.MULTILINE | re.DOTALL)
    if not paths:
        raise ValueError("The guide must link its project-relative paths")
    for path in paths:
        plan.safe_path(path)
    commands = []
    for language, block in blocks:
        if language.strip() != "argv":
            raise ValueError("Every guide command must be a JSON argv array in an argv fenced block")
        command = json.loads(block)
        if not isinstance(command, list) or not command or any(type(a) is not str or not a for a in command):
            raise ValueError("Guide argv must contain nonempty strings")
        commands.append(command)
    probe = ("import sys; from pathlib import Path; "
             "missing=[p for p in sys.argv[1:] if not Path(p).exists()]; "
             "print('Guide paths:', sys.argv[1:], 'missing:', missing); sys.exit(bool(missing))")
    return {f"tryItGuide:{i:03}": {"command": command, "requirements": [],
                                 "purpose": "Walk the delivered try-it guide"}
            for i, command in enumerate([[sys.executable, "-B", "-c", probe, *paths], *commands])}
