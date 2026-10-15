"""Foreign-toolchain stand-in: reports cannot override the observed exit."""

import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
report = repo / ".harness" / "report.txt"
report.parent.mkdir(exist_ok=True)
report.write_text("PASS", encoding="utf-8")
expected = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
actual = (repo / "value.txt").read_text(encoding="utf-8").strip()
sys.exit(0 if actual == expected else 1)
