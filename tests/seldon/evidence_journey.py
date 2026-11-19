"""Real retained products drive candidate-bound evidence through the strict folded judge."""

import json
import os
import sqlite3
import sys
from contextlib import ExitStack, closing
from pathlib import Path
from threading import Event, Thread

from seldon import features, gitwork, schema
from seldon.providers import render_question
from seldon.run import Run
from test_journey import Scripted, approve, strict_schema


WALKTHROUGH = r"""
const assert = require('node:assert/strict'), fs = require('node:fs'), os = require('node:os');
const path = require('node:path'), {spawn} = require('node:child_process');
const {once} = require('node:events'), {setTimeout: sleep} = require('node:timers/promises');
const browser = require('../../checks/kit/browser.cjs'), serve = require('../../checks/kit/serve.cjs');
const python = PYTHON;

async function shelf(url) {
  await browser(url, async ({page}) => {
    await page.getByRole('heading', {name: 'Your pantry', exact: true}).waitFor();
    await page.waitForLoadState('networkidle');
    await page.reload();
    await page.getByRole('heading', {name: 'Your pantry', exact: true}).waitFor();
    await page.waitForLoadState('networkidle');
  });
}

async function quiet(url, dataDir) {
  await browser(url, async ({page}) => {
    const settings = JSON.parse((await page.locator('#settings').inputValue()));
    await page.locator('#settings').fill(JSON.stringify({...settings, samples: 400000}));
    const started = page.waitForResponse(r => r.url().endsWith('/api/runs'));
    await page.getByRole('button', {name: /Start run/}).click();
    const start = await (await started).json();
    const sessionUrl = url + '/api/sessions/' + start.id;
    const read = async () => {
      const {status, processed_samples} = await (await fetch(sessionUrl)).json();
      return {status, processed_samples};
    };
    const whileRunning = await read();
    assert.equal(whileRunning.status, 'running');
    // Hold the actual saved session across Stop, recreating the recorded Windows reader/writer overlap.
    const reader = spawn(python, ['-B', '-c',
      'import sys; f=open(sys.argv[1], "rb"); print("open", flush=True); sys.stdin.read()',
      path.join(dataDir, start.id + '.json')], {stdio: ['pipe', 'pipe', 'inherit']});
    const exited = once(reader, 'close');
    try {
      await once(reader.stdout, 'data');
      const stopped = page.waitForResponse(r => r.url().endsWith('/stop'));
      await page.getByRole('button', {name: /Stop run/}).click();
      const stop = (await stopped).status();
      // The unfinished replacement is the recorded fault's completion signal. Read state through the app.
      const unfinished = path.join(dataDir, start.id + '.tmp');
      for (let i = 0; i < 400 && !fs.existsSync(unfinished); ++i) await sleep(50);
      assert.ok(fs.existsSync(unfinished), 'expected the saved-session replacement to be blocked');
      const after = await read();
      if (RECORD_LIFECYCLE) fs.writeFileSync(path.join(process.env.SELDON_EVIDENCE_DIR, 'operation-lifecycle.json'),
        JSON.stringify([{operation: 'Stop run', requirements: REQUIREMENTS,
          documentation: 'docs/try-it.md', observed: true,
          start: {status: start.status, processed_samples: start.processed_samples}, whileRunning, stop, after}]));
    } finally {
      reader.stdin.end();
      await exited;
    }
  }, {deadline: 60000});
}

async function walk() {
  const shelfLife = PRODUCT === 'shelf';
  const db = path.join(os.tmpdir(), 'trial.db');
  await serve([python, '-B', 'server.py', '--port', '{port}',
    shelfLife ? '--db' : '--data', shelfLife ? db : '{data}'], async ({url, dataDir}) => {
    if (shelfLife) await shelf(url);
    else await quiet(url, dataDir);
  });
  if (shelfLife) {
    // A real SQLite reader in the journey survives the server and walkthrough processes.
    fs.writeFileSync(db + '.probe', '');
    for (let i = 0; i < 100 && !fs.existsSync(db + '.held'); ++i) await sleep(20);
    assert.ok(fs.existsSync(db + '.held'), 'SQLite reader did not acquire its connection');
  }
}
walk().catch(error => { console.error(error); process.exitCode = 1; });
"""


class EvidenceReplay(Scripted):
    def __init__(self, slot, product):
        super().__init__(slot)
        self.product = product
        recordings = Path("C:/seldon/tests/m1")
        self.repository = recordings / {
            "quiet": "qbu-dbc5db9-s01", "shelf": "shelflife-main-d140427-w01"}[product] / "product"
        revision = {"quiet": "82fb2ce", "shelf": "9447ee9"}[product]
        paths = gitwork.paths(self.repository, revision)
        self.sources = gitwork.blobs(self.repository, revision, [
            name for name in paths if not name.startswith(("checks/", "docs/try-it/"))])
        self.scope = [path for path in self.sources if path != "docs/try-it.md"]
        self.story = {"quiet": "Start and stop a bench run and read the saved state.",
                      "shelf": "Open the pantry and read the current inventory."}[product]
        self.packet = None

    def invoke(self, q, started):
        strict_schema(q["schema"])
        kind, facts = q["kind"], q["facts"]
        if kind == "WORK":
            root = Path(q["execution"]["cwd"])
            for name, content in self.sources.items():
                target = root / name
                target.parent.mkdir(exist_ok=True, parents=True)
                target.write_bytes(content)
            path = "docs/try-it/evidence.cjs"
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            requirements = list(facts["Requirements"])
            (root / path).write_text(WALKTHROUGH.replace("PYTHON", json.dumps(sys.executable))
                .replace("PRODUCT", json.dumps(self.product)).replace("REQUIREMENTS", json.dumps(requirements))
                .replace("RECORD_LIFECYCLE", json.dumps("Operation lifecycle walkthrough" in facts)),
                encoding="utf-8")
            answer = {"outcome": "done", "summary": "Deliver the recorded product and observe its walkthrough.",
                "checkPaths": [path], "checks": [{"purpose": "Exercise the documented page",
                "command": ["node", path], "requirements": requirements,
                "expectedEvidence": "The documented page can be exercised."}], "discoveries": [],
                "why": "The walkthrough runs the recorded product and observes its own interface."}
        elif kind == "ALIGN":
            answer = q["examples"][0]["answer"]
            vision = answer["vision"]
            vision.update(size="tiny", understanding=self.story)
            vision["stories"][0].update(handle="experience", feature="Local product", story=self.story,
                                        outcome=self.story, sources=["kickoff"])
        elif kind == "ARCHITECT":
            goals = list(facts["Commitment handles"])
            answer = {"areas": [{"handle": "local", "responsibility": self.story, "scope": self.scope,
                "commitments": goals}], "waves": [{"goal": self.story, "commitments": goals}],
                "contracts": [], "decisions": [], "why": "One owner delivers the local experience."}
        elif kind == "DETAIL":
            answer = {"verdict": "planned", "edits": [
                {"kind": "feature", "targetHandle": "experience", "typedBody": {
                    "story": self.story, "acceptance": self.story, "commitments": ["scopeIn:experience"]}},
                {"kind": "requirement", "targetHandle": "observe", "typedBody": {
                    "features": ["experience"], "acceptance": self.story}}],
                "units": [{"scope": self.scope, "requirements": ["observe"], "dependencies": [],
                           "instructions": self.story}], "why": "The walkthrough exercises the page."}
        elif kind == "JUDGE":
            self.packet = q
            findings = {subject: {"verdict": "uncertain", "neededFacts": ["Semantic review"],
                "why": "This stub establishes packet delivery."} for subject in q["targets"]}
            if self.product == "quiet" and "codeQuality" in findings:
                owner = next(iter(facts["Checks"])).split(":", 1)[0]
                findings["codeQuality"] = {"verdict": "gap", "gaps": [{"target": "codeQuality",
                    "owners": [owner], "severity": "must-fix",
                    "correction": "Separate session persistence from Stop cleanup in Store.run."}],
                    "why": "One line couples terminal persistence and removal of the active-run event."}
            answer = {"findings": findings, "why": "This stub establishes packet delivery and owner routing."}
        else:
            return super().invoke(q, started)
        response = {"answer": answer}
        schema.validate(response, q["schema"])
        return json.dumps(response), None


def hold_sqlite(workers, stop):
    with ExitStack() as connections:
        held = set()
        while not stop.wait(0.02):
            for marker in workers.glob("check-*/temp/trial.db.probe"):
                if marker not in held:
                    db = marker.with_suffix("")
                    connection = connections.enter_context(closing(sqlite3.connect(db)))
                    connection.execute("SELECT name FROM sqlite_master").fetchall()
                    db.with_suffix(".db.held").touch()
                    held.add(marker)


def evidence_journey(slot, monkeypatch, product):
    assert os.name == "nt"
    monkeypatch.setattr(features, "planning_setup", lambda: {"sizeFirst": True, "split": "auto"})
    enabled = features.enabled
    monkeypatch.setattr(features, "enabled", lambda name: name == "judgeEvidence" or enabled(name))
    provider = EvidenceReplay(slot, product)

    def user(item):
        if item["gate"] == "A" and "groups" in item["ask"]:
            return {"request": item["request"], "replyId": "align-done",
                    "selections": [{"groupId": "round", "optionId": "done"}]}
        return approve(item)

    workers = slot / "workers"
    run = Run(slot / "product", workers, provider.story, provider.setup, provider, user, capacity=1)
    stop = Event()
    reader = Thread(target=hold_sqlite, args=(workers, stop))
    if product == "shelf":
        reader.start()
    try:
        for _ in range(15):
            run.step()
            if provider.packet:
                break
        q = provider.packet
        assert q, run.store.cursor()
        current, = (row for row in q["facts"]["Observations"] if row["purpose"] == "final")
        assert current["commit"] == run.store.cursor().candidate and current["exitCode"] == 0
        if product == "quiet" and "codeQuality" in q["targets"]:
            from seldon import fixups

            owner, = run.store.units()
            assert run.store.cursor().state == "EXECUTE"
            assert "Separate session persistence" in fixups.active(run.store, owner)["gaps"][0]["correction"]
        for reviewed in (False, True):
            stdin, _ = render_question(q, prompts2=reviewed)
            if product == "quiet":
                assert "operationLifecycle" in current, "The passing final check lost the Stop lifecycle evidence."
                operation, = current["operationLifecycle"]
                assert operation["whileRunning"] == operation["after"] == {"status": "running",
                                                                            "processed_samples": 0}
                assert operation["stop"] == 200 and set(operation["requirements"]) == set(current["requirements"])
                assert "codeQuality" in q["targets"] and "compressed one-liners" in stdin
                assert "self.save(sess); self.events.pop" in q["facts"]["Product files"]["quiet_bench/__init__.py"]
            else:
                assert "browserVisits" in current, "The passing final check lost browser visit evidence."
                first, second = current["browserVisits"]
                assert first["url"] == second["url"]
                assert any(row["status"] == 404 and row["url"].endswith("/favicon.ico")
                           for row in first["failedRequests"]), first
                assert any(row["level"] == "error" and "404" in row["text"] for row in first["console"]), first
                assert {"root": "temp", "path": "trial.db", "windowsError": 32, "locked": True} in (
                    current["fileCleanup"]["blockedFiles"])
                assert current["operationLifecycle"][0]["observed"] is False
                assert "/favicon.ico" in stdin and '"locked": true' in stdin
    finally:
        stop.set()
        if product == "shelf":
            reader.join(timeout=5)
        run.close()
