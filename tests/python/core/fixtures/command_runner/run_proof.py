"""Foreign-toolchain stand-in that intentionally writes an untrustworthy report."""

import json
import sys
from pathlib import Path

Path("report.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
sys.exit(0 if Path(sys.argv[1]).read_text(encoding="utf-8").strip() == "pass" else 1)
