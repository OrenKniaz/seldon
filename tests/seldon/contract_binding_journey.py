"""Fable 8.5: declared levels, binding-derived consumers and correction progress."""

import json
import sys
import time

from seldon import architecture, plan
from seldon.frontdoor import state as run_state
from seldon.run import Run
from seldon.store import encode
from test_journey import NATIVE, VISION, Scripted, approve, finish, planned


SERVER = '''from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(Path('unit-001/value.txt').read_bytes())
server = HTTPServer(('127.0.0.1', 0), Handler)
print('http://127.0.0.1:' + str(server.server_port), flush=True)
server.serve_forever()
'''
WAVE = '''import subprocess, sys
from urllib.request import urlopen
server = subprocess.Popen([sys.executable, '-B', 'unit-001/server.py'],
                          stdout=subprocess.PIPE, text=True)
try:
    url = server.stdout.readline().strip()
    with urlopen(url) as response:
        assert response.read() == b'value-001'
    print('observed launched server value-001')
finally:
    server.terminate()
    server.wait()
'''
AGREEMENTS = {
    "inventory-query": "The inventory value is UTF-8 value-001 in unit-001/value.txt.",
    "server-command": "Launch Python -B unit-001/server.py; its first stdout line is the URL. "
                      "GET / returns value-001. Stop the child server after the check.",
}


class BindingProvider(Scripted):
    def __init__(self, slot, mode):
        super().__init__(slot, "rule1b", 3)
        self.case, self.corrections = mode, []

    def __call__(self, question):
        kind, facts = question["kind"], question["facts"]
        if kind == "ARCHITECT":
            answer = {"areas": [{"handle": f"area-{i:03}", "responsibility": "Deliver named values",
                "scope": [f"unit-{i:03}/**"], "commitments": list(architecture.commitments(VISION))}
                for i in (1, 2, 3)],
                "contracts": [{"handle": r, "kind": "file-format", "text": text,
                    "provider": "area-001", "consumers": ["area-003"]} for r, text in AGREEMENTS.items()],
                "decisions": [], "waves": [{"goal": "Run the integrated values"}]}
        elif kind == "DETAIL":
            number = int(facts["Area"]["handle"][-3:])
            full = planned(3, "rule1b")
            edits = full["edits"][3*(number-1):3*number]
            unit = {k: v for k, v in full["units"][number-1].items() if k != "area"}
            if number == 1:
                unit["provides"] = list(AGREEMENTS)
            elif number == 2:
                unit["consumes"] = list(AGREEMENTS)
                edits[2]["typedBody"].update(level="unit", requirements=["new:r002", "inventory-query"])
                if self.case in ("corrections", "fixed-point"):
                    if "Merge correction" in facts:
                        self.corrections.append(facts["Merge correction"])
                    turn = len(self.corrections)
                    path = "unassigned/value.txt" if turn == 0 or self.case == "fixed-point" else (
                        "unit-001/value.txt")
                    unit["reads"] = [path]
                    unit["dependencies"] = [path] if turn != 1 or self.case == "fixed-point" else []
                if self.case == "d05":
                    unit["checkPaths"] = ["unit-002/check.py", "unit-002/wave.py"]
                    edits.append({"kind": "check", "targetHandle": "new:launch", "typedBody": {
                        **edits[2]["typedBody"], "level": "wave", "purpose": "Launch the real server",
                        "command": [sys.executable, "-B", "unit-002/wave.py"],
                        "requirements": ["new:r002", "server-command"]}})
            answer = {"verdict": "planned", "edits": edits, "units": [unit],
                      "quality": full["quality"] if number == 1 else []}
        else:
            return super().__call__(question)
        return json.dumps({"answer": {**answer, "why": "Each area chooses the contracts it binds"}})


def rule1b_journey(root, mode):
    """d04 reaches B without a question; d05 launches its server and finishes with green final proof.

    Earlier journeys never cite consumed text from a unit check or bind an unassigned agreement;
    they miss forced wave promotion, consumer refusals and absent WORK text. The actual wave command
    catches reliance on a pre-running service. Two different read corrections reach B, while their
    repeated-error twin asks the user, catching both the old count and an unbounded correction loop.
    """
    began = time.monotonic()
    root.mkdir(parents=True)
    (root / "mode").write_text("rule1b")
    injection = ("if not guide and name == 'unit-001':\n"
                 f"    Path(name, 'server.py').write_text({SERVER!r})\n"
                 "if not guide and name == 'unit-002':\n"
                 f"    Path(name, 'wave.py').write_text({WAVE!r})\n")
    (root / "native.py").write_text(NATIVE.replace('print(json.dumps({"type":"item.completed"',
        injection + 'print(json.dumps({"type":"item.completed"'), encoding="utf-8")
    provider = BindingProvider(root, mode)
    run = Run(root / "product", root / "workers", "Read named values", provider.setup,
              provider, approve, capacity=3)
    try:
        if mode == "fixed-point":
            run.run()
            waiting = run_state(str(run.project))["waitingFor"]
            assert waiting["gate"] == "DETAIL" and "unassigned/value.txt" in str(waiting["subject"])
            assert not run.store.cursor().B
            return
        while run.store.cursor().state != "EXECUTE":
            assert run_state(str(run.project))["status"] not in ("WAITING", "PAUSED")
            run.step()
        assert run.store.cursor().B
        assert {b["gate"] for _, _, b in run.store.records("decision_request")} == {"A", "B"}
        document = run.store.plan(run.store.cursor().revision)
        contracts = plan.select(document, "contract")
        assert all(c["consumers"] == ["u002"] for c in contracts.values())
        local = next(c for c in plan.assigned(document)["u002"].values() if c["purpose"] == "Read value 002")
        assert local["level"] == "unit"
        assert local["requirements"] == plan.select(document, "unit")["u002"]["requirements"]
        if mode == "corrections":
            assert plan.select(document, "unit")["u002"]["dependencies"] == ["u001"]
        if mode == "d05":
            finish(run)
            consumed = [q["facts"]["Contracts consumed"] for _, _, q in run.store.records("question")
                        if q["kind"] == "WORK" and q["facts"].get("Contracts consumed")]
            assert {c["text"] for facts in consumed for c in facts.values()} == set(AGREEMENTS.values())
            wave, = plan.assigned(document)[None]
            final = [run.store.read(r) for r in run.store.cursor().finalObservations]
            observed, = [o for o in final if o["check"] == wave]
            assert observed["exitCode"] == 0 and observed["cleanupVerified"]
            assert observed["commit"] == run.store.cursor().candidate
            assert "observed launched server value-001" in observed["stdout"]
    finally:
        briefs = [q for _, _, q in run.store.records("question") if q["kind"] == "DETAIL"]
        print(f"Rule1b {mode}: largest DETAIL bytes {max(len(encode(q).encode('utf-8')) for q in briefs)}; "
              f"{time.monotonic()-began:.2f}s", flush=True)
        run.close()
