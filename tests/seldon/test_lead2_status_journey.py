"""Fresh Lead chat-to-browser journey through the actual kickoff URL and run database."""

import asyncio
import io
import json
import os
import subprocess
import sys
import zipfile
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import pytest

from seldon import frontdoor
from seldon.lead2 import status_page
from seldon.run import Run

pytestmark = pytest.mark.fast


@pytest.fixture
def status_process(tmp_path, monkeypatch):
    monkeypatch.setenv("SELDON_HOME", str(tmp_path / "home"))
    root = Path(frontdoor.__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", str(root))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    children = []

    def launch(registry):
        # Run the interpreter directly so the fixture owns the listening PID even on Windows.
        child = subprocess.Popen([sys._base_executable, "-m", "seldon.lead2.status_server", str(registry)],
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        children.append(child)
        return child

    def stop():
        child = children[-1]
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)

    monkeypatch.setattr(status_page, "_launch", launch)
    yield stop
    if children:
        stop()


def test_kickoff_page_follows_the_plan_and_keeps_a_replay(tmp_path, monkeypatch, status_process):
    """The same URL shows the revisions through B without exposing a chat mutation route.

    The chat journey alone misses a severed kickoff URL, trace rows on the wrong revision,
    and a waiting label that lingers after the bound B reply.
    """
    product = tmp_path / "door"
    idea = "Keep one shared door count while the door lead takes a break."
    monkeypatch.setenv("SELDON_MODE", "lead2")
    monkeypatch.setattr(frontdoor, "spawn", frontdoor.state)  # Drive the controller in-process.
    app = frontdoor.server(lambda: {})

    def tool(name, **args):
        result = asyncio.run(app.call_tool(name, args))
        assert not result.is_error, result
        return json.loads(result.content[0].text)

    def provider(question):
        answer = {"reason": "Keep handoffs safe"}
        if question["kind"] == "LEAD2-ALIGN":
            answer.update(aligned=True, size="medium", size_reason="Several doors and handoffs",
                          questions=[dict(question="Who can close the shift?", default="Either lead",
                              recommended_means="Either lead can close.", alternative="The current lead",
                              alternative_means="Only the current lead can close.", her_words_quote=" ",
                              bet_why="Either lead seems likely.", would_bet_she_picks_default=True,
                              wrong_costly_why="This changes who can close.",
                              wrong_costly=True),
                              dict(question="Where should the count be shown?", default="At the top",
                              recommended_means="Keep it visible above the controls.", alternative="Below the controls",
                              alternative_means="Show controls before the count.", her_words_quote="",
                              bet_why="No location was chosen.", would_bet_she_picks_default=True,
                              wrong_costly_why="Only presentation changes.",
                              wrong_costly=False)],
                          person=[{"ref": "lead", "role": "Door lead"}],
                          story=[{"ref": "handoff", "story": "Take over the live count"},
                                 {"ref": "export", "story": "Download the count"}],
                          link=[{"source": "brief", "targets": ["handoff", "export"]},
                                {"source": "lead", "targets": ["handoff"]}])
        elif question["kind"] == "LEAD2-STACK":
            answer["stack"] = [{"ref": "local", "decision": "Host on the venue laptop",
                                "reason": "Keep the handoff available on the private Wi-Fi", "words": idea}]
        elif question["kind"] == "LEAD2-SPEC":
            stories = {text: next(line.split("]")[0][1:] for line in
                                  question["facts"]["Current proposal"].splitlines() if text in line)
                       for text in ("Take over the live count", "Download the count")}
            answer.update(
                story=[dict(ref=ref, story=text, core_job=must,
                            core_job_why="The live count is needed every shift." if must else "Counting still works.",
                            nice=False, nice_why="The lead depends on this behavior.")
                       for (text, ref), must in zip(stories.items(), (True, False), strict=True)],
                promise=[{"ref": "count", "title": "Keep the door count", "action": "Take over the door",
                          "result": "See the same count",
                          "not_met_if": "The reported count changes"},
                         {"ref": "download", "title": "Export the count", "action": "Download the CSV",
                          "result": "The CSV contains the count", "not_met_if": "The CSV is missing"}],
                design=[{"ref": "ledger", "decision": "ledger.py", "reason": "One count", "words": idea}],
                check=[{"ref": "verify", "action": "Read the live count", "expected": "Both leads see 15"}],
                walk=[{"ref": "swap", "scenario": "Swap leads while the door is busy",
                       "interruption": "Lose Wi-Fi just before the swap"}],
                link=[{"source": stories["Take over the live count"], "targets": ["count"]},
                      {"source": "count", "targets": ["ledger", "verify", "swap"]},
                      {"source": stories["Download the count"], "targets": ["download"]}],
                build_order=[dict(outcome="Safe handoff", stories=[stories["Take over the live count"]]),
                             dict(outcome="CSV export", stories=[stories["Download the count"]])],
            )
        else:
            answer["findings"] = "The receiving lead sees the original count."
        return json.dumps({"answer": answer})

    launched = tool("start", dream=idea, folder=str(product))
    page = launched["statusPage"]
    home = launched["statusHome"]
    second = frontdoor.server(lambda: {})
    result = asyncio.run(second.call_tool("start", {
        "dream": "# Another venue\n\n# Venue note\n\nI'm Jo, a venue manager. Keep its door count separate.",
        "folder": str(tmp_path / "other"),
    }))
    opened = json.loads(result.content[0].text)
    assert opened["statusHome"] == home
    assert opened["statusPage"] != page

    def read_home():
        with urlopen(home, timeout=5) as response:
            return response.read().decode()

    def sidebar_links(url):
        class Sidebar(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "a" and "plan" in attrs.get("class", "").split():
                    links.append((attrs["href"], attrs["aria-label"], attrs.get("aria-current")))

        links = []
        with urlopen(url, timeout=5) as response:
            Sidebar().feed(response.read().decode())
        return links

    listing = read_home()
    assert "Another venue" in listing and "Keep one shared door count" in listing
    assert "Jo · venue manager" in listing and "Keep its door count separate." in listing
    with urlopen(page, timeout=5) as response:
        assert "Where Seldon is" in response.read().decode()
    endpoint = page + "state.json"
    with pytest.raises(HTTPError) as error:
        urlopen(Request(endpoint, data=b"approve"), timeout=5)
    assert error.value.code == 501
    with pytest.raises(HTTPError) as error:
        urlopen(Request(home, headers={"Host": "other.example"}), timeout=5)
    assert error.value.code == 404

    def read_status():
        with urlopen(endpoint, timeout=5) as response:
            return json.load(response)

    def revision():
        with urlopen(page + "revision", timeout=5) as response:
            return response.read()

    def reply(group="round", option="approve", extra=()):
        decision = tool("state", run=str(product))["waitingFor"]
        tool("answer", run=str(product), request=decision["request"], replyId=decision["gate"],
             selections=[{"groupId": group, "optionId": option}, *extra])

    with closing(Run.reopen(product, provider, lambda _: frontdoor.read_answer(
        frontdoor.side(product) / "answer.json",
    ))) as run:
        with urlopen(page, timeout=5) as response:
            assert 'href="walk"' in response.read().decode()
        with urlopen(urljoin(page, "walk"), timeout=5) as response:
            assert "The independent walk has yet to start." in response.read().decode()
        assert read_status()["idea"]["text"] == idea
        assert read_status()["idea"]["label"] == "Keep one shared door count while the door"
        initial = revision()
        assert revision() == initial
        run.step()
        align = read_status()
        assert revision() != initial
        assert align["frames"][-1]["activity"] == "Waiting on you"
        assert align["trace"][str(align["frames"][-1]["revision"])] == []
        now = read_home().split('id="now"', 1)[1].split('id="earlier"', 1)[0]
        assert now.index("Keep one shared door count") < now.index("Another venue")
        assert "Waiting on you" in now and "Align the idea" in now
        current, other = sidebar_links(page)
        assert current == (f'../{page.rstrip("/").split("/")[-1]}/',
                           "Keep one shared door count while the door · Waiting on you", "page")
        assert other[1:] == ("Another venue · Planning", None)
        linked = urljoin(page, other[0])
        assert linked == opened["statusPage"]
        assert sidebar_links(linked)[1] == (*other[:2], "page")
        assert "Where should the count be shown?" not in [
            g["question"] for g in tool("state", run=str(product))["waitingFor"]["ask"]["groups"]]
        reply(extra=[{"groupId": "q1", "optionId": "o2"}])
        run.step()
        run.step()
        stack = read_status()["frames"][-1]
        assert stack["stage"] == 2 and stack["activity"] == "Waiting on you" and stack["detail"] == "Stack"
        reply()
        run.step()
        run.step()
        run.step()
        run.step()
        offered = read_status()
        frames = offered["frames"]
        assert {0, 1, 3, 4, 5} <= {frame["stage"] for frame in frames}
        assert frames[-1]["activity"] == "Waiting on you" and frames[-1]["detail"] == "Approve the plan at B"
        assert "Keep it visible above the controls." in tool("state", run=str(product))["waitingFor"]["ask"]["proposal"]
        assert offered["trace"]["0"] == []
        assert any(frame["stage"] == 3 and offered["trace"][str(frame["revision"])] for frame in frames)
        row = next(r for r in offered["trace"][str(frames[-1]["revision"])]
                   if r["feature"]["label"] == "Keep the door count")
        assert row["feature"] == {
            "label": "Keep the door count",
            "text": "See the same count\n\nTake over the door\n\nThe reported count changes",
        }
        assert row["stories"][0]["label"] == "Take over the live count"
        assert row["stories"][0]["priority"] == "must"
        assert row["modules"][0]["label"] == "ledger.py"
        walk = row["walks"][0]
        assert walk["text"] == "Swap leads while the door is busy\n\nLose Wi-Fi just before the swap"
        assert row["status"] == "planned"
        reply()
        run.step()
        approved, build = read_status()["frames"][-2:]
        assert approved["activity"] == "Plan approved at B" and approved["stage"] == 5
        assert build["activity"] == "Ready to build" and build["chunk"] == "Safe handoff"
        assert any(label.endswith(" · Building") and active == "page"
                   for _, label, active in sidebar_links(page))
        now, earlier = read_home().split('id="earlier"', 1)
        assert "Keep one shared door count" in now and "Keep one shared door count" not in earlier
        assert 'class="chip build">Building<' in now and "Build · Safe handoff" in now
        assert 'class="seg done" title="You approve"' in now and 'class="seg cur" title="Build"' in now

    (tmp_path / "other").rename(tmp_path / "moved")
    status_process()
    reopened = tool("state", run=str(product))
    assert reopened["statusPage"] == page and reopened["statusHome"] == home
    assert read_status()["frames"][-1] == build
    earlier = read_home().split('id="earlier"', 1)[1]
    assert "Another venue" in earlier and "Missing" in earlier
    assert all("Another venue" not in label for _, label, _ in sidebar_links(page))

    builds, visits, promises = 0, [], {}

    def build_and_walk(question):
        nonlocal builds
        if question["kind"] == "WORK":
            builds += 1
            assert question["execution"]["session"] == ("builder" if builds > 1 else None)
            if builds == 1:
                assert "Keep it visible above the controls." in question["prompt"]
                assert "Only the current lead can close." in question["prompt"]
                hard_checks = question["prompt"].split("Test these yourself", 1)[1].split("Design and stack", 1)[0]
                assert "Lose Wi-Fi just before the swap" in hard_checks and "Both leads see 15" in hard_checks
                for line in question["prompt"].splitlines():
                    ref, _, result = line.partition(": ")
                    if result in ("See the same count", "The CSV contains the count"):
                        promises[result] = ref
                return json.dumps(dict(outcome="partial", acknowledged=True,
                                       note="Waiting for Seldon's walk."))
            if builds == 2:
                assert "Keep going: return done" in question["prompt"]
                live = read_status()
                assert live["timeCost"]["active"] == "build" and live["timeCost"]["activeStartedAt"]
                assert ("Safe handoff", "building") in [tuple(x) for x in live["frames"][-1]["features"]]
                assert "building" in live["frames"][-1]["deliveryTrace"].values()
            if builds == 3:
                batch, = question["facts"]["Repair records"].values()
                finding, = batch["findings"].values()
                assert finding["wrong_or_lost_result"] and "14 places instead of 15" in finding["reason"]
            workspace = Path(question["execution"]["cwd"])
            count = 14 if builds == 2 else 15
            if builds == 4:
                (workspace / "export.csv").write_text("inside\n15\n", encoding="utf-8")
            (workspace / "index.html").write_text(f"<h1>{count} places</h1>", encoding="utf-8")
            (workspace / "ledger.py").write_text(
                "import subprocess, sys\nfrom http.server import HTTPServer, SimpleHTTPRequestHandler\n"
                "subprocess.check_call([sys.executable, '-c', 'import sys; sys.stdout.write(\"Index ready\")'])\n"
                "HTTPServer((sys.argv[1], int(sys.argv[2])), SimpleHTTPRequestHandler).serve_forever()\n",
                encoding="utf-8")
            (workspace / "export.py").write_text("# CSV export entry point\n", encoding="utf-8")
            return json.dumps(dict(outcome="done", acknowledged=True, correction_note="",
                code_map=[dict(promise=promises["See the same count"], modules=["index.html", "ledger.py"], tests=[])] if builds == 3 else [
                    dict(promise=promises["See the same count"], modules=["index.html", "ledger.py"], tests=[]),
                    dict(promise=promises["The CSV contains the count"],
                         modules=["export.py"] + (["export.csv"] if builds == 4 else []), tests=[])]))
        if question["kind"] == "LEAD2-CHUNK":
            return json.dumps(dict(notes=["Take over the count and check whether it starts at 15.",
                                          "The local checks passed."]))
        if question["kind"] == "LEAD2-WALK-STEER":
            return json.dumps(dict(notes=[dict(id=ref, sentence=row["reason"],
                is_problem=row["reason"] == "There is no CSV download.",
                is_problem_why="The main checks already show what worked.", surprise_later=False,
                surprise_later_why="The result is visible immediately.")
                for ref, row in question["facts"]["Observations"].items()]))
        assert question["kind"] == "LEAD2-WALK"
        facts = question["facts"]
        by_priority = {row["priority"]: ref for ref, row in facts["Stories to walk"].items()}
        visits.append(set(by_priority))
        if set(by_priority) == {"should"}:
            assert (Path(question["execution"]["walk"]) / "export.csv").read_text() == "inside\n15\n"
            assert not facts["Notes selected for this fix"]
            return json.dumps(dict(stories=[dict(story=by_priority["should"], outcome="works",
                reason="The CSV contains 15.", screenshot="", defect="", wrong_or_lost_result=False,
                wrong_result_why="The saved count matches the CSV.")], notes=[], guide_path=""))
        evidence = question["execution"]["evidence"]
        workspace = question["execution"]["walk"]
        check = '''
const serve = require('./checks/kit/serve.cjs');
const browser = require('./checks/kit/browser.cjs');
serve([process.argv[1], 'ledger.py', '127.0.0.1', '{port}'], url => browser(url.url,
  async ({page, shot}) => {
    const staff = await page.context().browser().newPage({viewport:{width:1280,height:900}});
    await staff.goto(url.url);
    await page.getByText(/1[45] places/).waitFor();
    await staff.getByText(/1[45] places/).waitFor();
    await shot('jo', page);
    await shot('ben', staff);
  }, {viewports:[{width:390,height:844}], silence:10000}), {preview:true})
  .catch(error => {console.error(error);process.exitCode=1;});
'''
        subprocess.run(["node", "-e", check, sys._base_executable], cwd=workspace, check=True,
                       env={**os.environ, "SELDON_EVIDENCE_DIR": str(Path(evidence) / "screens"),
                            "SELDON_PREVIEW_RECIPE": str(Path(evidence) / "preview-launch.json")})
        for shot in (Path(evidence) / "screens").glob("*.png"):
            shot.rename(Path(evidence) / shot.name)
        if os.name == "nt":
            recipe = Path(evidence) / "preview-launch.json"
            launch = json.loads(recipe.read_text(encoding="utf-8"))
            launch["argv"][0] = "py"
            recipe.write_text(json.dumps(launch), encoding="utf-8")
        story = by_priority["must"]
        broken = "14 places" in (Path(workspace) / "index.html").read_text()
        reason = "The count shows 14 places instead of 15." if broken else "Both doors see 15 places."
        review = list(facts["Review findings"])
        selected = list(facts["Notes selected for this fix"])
        results = [dict(story=story, outcome="works", screenshot="jo-390.png", defect="",
                        reason="Both doors can open the count." if broken else reason,
                        wrong_or_lost_result=False, wrong_result_why="The main screen loaded for both roles.")]
        notes = [dict(kind="untested", stories=[story], reason="Venue Wi-Fi was unavailable.", finding="",
                      wrong_or_lost_result=False, wrong_result_why="No venue result was observed.")]
        if review:
            assert "starts at 15" in facts["Review findings"][review[0]]
            notes += [dict(kind="quality", stories=[story], reason=reason, finding=review[0],
                           wrong_or_lost_result=broken, wrong_result_why=reason),
                      dict(kind="rough", stories=[], reason="The local checks passed.", finding=review[1],
                           wrong_or_lost_result=False, wrong_result_why="This was a test summary.")]
        else:
            ref, = selected
            assert facts["Notes selected for this fix"][ref] == "The count shows 14 places instead of 15."
            assert not broken
            notes.append(dict(kind="rough", stories=[story], reason="The displayed count agrees across both doors.",
                              finding=ref, wrong_or_lost_result=False, wrong_result_why="Both doors saw 15."))
        if optional := by_priority.get("should"):
            assert not (Path(workspace) / "export.csv").exists()
            results.append(dict(story=optional, outcome="broken", reason="There is no CSV download.",
                                screenshot="", defect="missing CSV", wrong_or_lost_result=False,
                                wrong_result_why="The live count is still available."))
        return json.dumps(dict(stories=results, notes=notes, guide_path=""))

    class Provider:
        def invoke(self, question, _started):
            raw = build_and_walk(question)
            return raw, "builder" if question["kind"] == "WORK" else "independent"

    from seldon.decisions import Waiting

    def wait(_):
        raise Waiting()

    with closing(Run.reopen(product, Provider(), wait)) as run:
        assert run.run().state == "C", run.store.records("lead2_fault")
        assert builds == 3 and visits == [{"must", "should"}, {"must"}]
        assert not run.store.records("lead2_chunk_review")
        state = tool("state", run=str(product))
        note = state["releaseNotes"]
        assert read_status()["releaseNotes"] == note
        assert note["works"][0]["text"] == "Both doors see 15 places."
        item = next(row for row in note["items"] if row["label"] == "Not tested here")
        assert item["text"] == "Venue Wi-Fi was unavailable."
        assert state["waitingFor"]["subject"]["release"] == note["ref"]
        assert state["waitingFor"]["ask"]["proposal"].count(item["text"]) == 1
        assert "The local checks passed." not in state["waitingFor"]["ask"]["proposal"]
        assert [o["label"] for o in state["waitingFor"]["ask"]["groups"][0]["options"]] == [
            "Accept as is", "Fix these", "Stop here", "Other"]
        assert read_status()["timeCost"]["phases"]["walk"]["rounds"] == 4
        with urlopen(urljoin(page, "walk"), timeout=5) as reply:
            html = reply.read().decode()
            assert item["text"] in html and "must · Door lead" in html
            assert "Left out of the release notes" in html and "The main checks already show what worked." in html
            assert "The local checks passed." in html
    c = tool("state", run=str(product))
    optional = next(item for item in c["releaseNotes"]["items"] if item["text"] == "There is no CSV download.")
    assert not optional["blocked"]
    tool("answer", run=str(product), request=c["waitingFor"]["request"], replyId="fix-csv",
         words=optional["id"], selections=[{"groupId": "round", "optionId": "revise"}])

    def read_reply(_):
        return frontdoor.read_answer(frontdoor.side(product) / "answer.json")

    with closing(Run.reopen(product, Provider(), read_reply)) as run:
        assert run.run().state == "C", run.store.records("lead2_fault")
        assert builds == 4 and visits[-1] == {"should"}
        assert {row["text"] for row in tool("state", run=str(product))["releaseNotes"]["works"]} == {
            "Both doors see 15 places. Walked on an earlier build and unaffected by this fix.", "The CSV contains 15."}
    browser = Path(frontdoor.__file__).with_name("kit") / "browser.cjs"
    check = '''
const assert = require('node:assert/strict');
const browser = require(process.argv[1]);
browser(process.argv[2], async ({page}) => {
  const image = page.getByRole('img');
  await image.waitFor();
  assert.equal(await image.evaluate(async img => { await img.decode(); return img.naturalWidth; }), 390);
  assert.match(await page.locator('body').textContent(), /Venue Wi-Fi was unavailable/);
  for (const theme of ['light', 'dark']) {
    if (await page.locator('html').getAttribute('data-theme') !== theme)
      await page.getByRole('button', {name: 'Switch to ' + theme + ' theme'}).click();
    assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
    const link = await page.getByRole('link', {name: 'Download a clean copy'}).getAttribute('href');
    const zip = await page.request.get(new URL(link, page.url()).href);
    assert.ok(zip.ok());
    assert.equal((await zip.body()).subarray(0, 2).toString(), 'PK');
    const [opened] = await Promise.all([
      page.waitForResponse(response => response.request().method() === 'POST'),
      page.getByRole('button', {name:'Open this build'}).click()
    ]);
    assert.equal(opened.status(), 303, await opened.text().catch(() => ''));
    const frame = page.frameLocator('iframe[title="Running build"]');
    await frame.getByText('15 places').waitFor();
    const running = await page.getByRole('link', {name:'Open in a new tab'}).getAttribute('href');
    await page.getByRole('button', {name:'Stop preview'}).click();
    await page.getByRole('button', {name:'Open this build'}).waitFor();
    await assert.rejects(fetch(running));
  }
}).catch(error => {console.error(error);process.exitCode=1;});
'''
    subprocess.run(["node", "-e", check, str(browser), urljoin(page, "walk")], check=True)
    request = tool("state", run=str(product))["waitingFor"]["request"]
    tool("answer", run=str(product), request=request, replyId="person-tried-the-build",
         selections=[{"groupId": "round", "optionId": "approve"}])
    with closing(Run.reopen(product, Provider(), lambda _: frontdoor.read_answer(
            frontdoor.side(product) / "answer.json"))) as run:
        assert run.run().state == "ACCEPTED"
    assert tool("state", run=str(product))["outcome"]["acceptedByPerson"] is True
