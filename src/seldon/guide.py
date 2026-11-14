"""C8/C15/I1; R9: walk the delivered guide in one disposable observation."""

import json
import posixpath
import re
import sys
from pathlib import Path, PurePosixPath

from . import plan


def checks(root):
    text = (Path(root) / plan.GUIDE_PATH).read_text("utf-8")
    paths = list(dict.fromkeys(re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)))
    blocks = re.findall(r"^```([^\n]*)\n(.*?)^```\s*$", text, re.MULTILINE | re.DOTALL)
    if not paths:
        raise ValueError("The guide must link paths relative to the guide file")
    paths = [p for p in paths if not re.match(r"[a-zA-Z][\w+.-]*:|//|#", p)]
    paths = [plan.safe_path(posixpath.normpath(posixpath.join(
        str(PurePosixPath(plan.GUIDE_PATH).parent), path))) for path in paths]
    for path in paths:
        (Path(root) / path).resolve().relative_to(Path(root).resolve())
    commands = []
    for language, block in blocks:
        if language.strip() != "argv":
            continue
        command = json.loads(block)
        if not isinstance(command, list) or not command or any(type(a) is not str or not a for a in command):
            raise ValueError("Guide argv must contain nonempty strings")
        commands.append(command)
    probe = ("import json, subprocess, sys; from pathlib import Path; "
             "[subprocess.run(c, check=True) for c in json.loads(sys.argv[1])]; "
             "missing=[p for p in sys.argv[2:] if not Path(p).exists()]; "
             "print('Guide paths:', sys.argv[2:], 'missing:', missing); sys.exit(bool(missing))")
    return {"tryItGuide:000": {"command": [sys.executable, "-B", "-c", probe, json.dumps(commands), *paths],
                               "requirements": [], "purpose": "Walk the delivered try-it guide"}}
