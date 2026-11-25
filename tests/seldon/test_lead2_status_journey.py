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
                          person=[{"ref": "lead", "role": "Door lead"}],
                          story=[{"ref": "handoff", "story": "Take over the live count"}],
                          link=[{"source": "brief", "targets": ["handoff"]},
                                {"source": "lead", "targets": ["handoff"]}])
        elif question["kind"] == "LEAD2-STACK":
            answer["stack"] = [{"ref": "local", "decision": "Host on the venue laptop",
                                "reason": "Keep the handoff available on the private Wi-Fi", "words": idea}]
        elif question["kind"] == "LEAD2-SPEC":
            answer.update(
                promise=[{"ref": "count", "title": "Keep the door count", "action": "Take over the door",
                          "result": "See the same count",
                          "not_met_if": "The reported count changes"}],
                design=[{"ref": "ledger", "decision": "ledger.py", "reason": "One count", "words": idea}],
                check=[{"ref": "verify", "action": "Read the live count", "expected": "Both leads see 15"}],
                walk=[{"ref": "swap", "scenario": "Swap leads while the door is busy",
                       "interruption": "Lose Wi-Fi just before the swap"}],
                link=[{"source": "s1", "targets": ["count"]},
                      {"source": "count", "targets": ["ledger", "verify", "swap"]}],
                build_order=[{"outcome": "Safe handoff", "recommend_demo": True}],
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

    def reply(group="round", option="approve"):
        decision = tool("state", run=str(product))["waitingFor"]
        tool("answer", run=str(product), request=decision["request"], replyId=decision["gate"],
             selections=[{"groupId": group, "optionId": option}])

    with closing(Run.reopen(product, provider, lambda _: frontdoor.read_answer(
        frontdoor.side(product) / "answer.json",
    ))) as run:
        with urlopen(page, timeout=5) as response:
            assert 'href="walk"' in response.read().decode()
        with urlopen(urljoin(page, "walk"), timeout=5) as response:
            assert "The independent walk has yet to finish." in response.read().decode()
        assert read_status()["idea"]["text"] == idea
        assert read_status()["idea"]["label"] == "Keep one shared door count while the door"
        initial = revision()
        assert revision() == initial
        run.step()
        reply("technical", "plain")
        run.step()
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
        reply()
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
        assert offered["trace"]["0"] == []
        assert any(frame["stage"] == 3 and offered["trace"][str(frame["revision"])] for frame in frames)
        row, = offered["trace"][str(frames[-1]["revision"])]
        assert row["feature"] == {
            "label": "Keep the door count",
            "text": "See the same count\n\nTake over the door\n\nThe reported count changes",
        }
        assert row["stories"][0]["label"] == "Take over the live count"
        assert row["modules"][0]["label"] == "ledger.py"
        walk = row["walks"][0]
        assert walk["text"] == "Swap leads while the door is busy\n\nLose Wi-Fi just before the swap"
        assert row["status"] == "planned"
        reply()
        run.step()
        approved, build = read_status()["frames"][-2:]
        assert approved["activity"] == "Plan approved at B" and approved["stage"] == 5
        assert build["activity"] == "Ready to build" and build["chunk"] == "chunk 1 of 1"
        assert any(label.endswith(" · Building") and active == "page"
                   for _, label, active in sidebar_links(page))
        now, earlier = read_home().split('id="earlier"', 1)
        assert "Keep one shared door count" in now and "Keep one shared door count" not in earlier
        assert 'class="chip build">Building<' in now and "Build · chunk 1 of 1" in now
        assert 'class="seg done" title="You approve"' in now and 'class="seg cur" title="Build"' in now

    (tmp_path / "other").rename(tmp_path / "moved")
    status_process()
    reopened = tool("state", run=str(product))
    assert reopened["statusPage"] == page and reopened["statusHome"] == home
    assert read_status()["frames"][-1] == build
    earlier = read_home().split('id="earlier"', 1)[1]
    assert "Another venue" in earlier and "Missing" in earlier
    assert all("Another venue" not in label for _, label, _ in sidebar_links(page))

    from seldon.lead2 import SpecStore

    builds = walks = 0
    added_words = "Both doors should see the shared count without asking the lead."

    def build_and_walk(question):
        nonlocal builds, walks
        if question["kind"] == "WORK":
            builds += 1
            workspace = Path(question["execution"]["cwd"])
            (workspace / "index.html").write_text("<h1>15 places</h1>", encoding="utf-8")
            (workspace / "ledger.py").write_text(
                "import sys\nfrom http.server import HTTPServer, SimpleHTTPRequestHandler\n"
                "HTTPServer((sys.argv[1], int(sys.argv[2])), SimpleHTTPRequestHandler).serve_forever()\n",
                encoding="utf-8")
            if builds == 2:
                (workspace / "OWNER WALK.md").write_text(
                    "## Open the count\n\n1. Run `ledger.py` on 127.0.0.1.\n"
                    "2. Read the **places left**.\n", encoding="utf-8")
            graph = SpecStore(run.store).snapshot().graph
            promise = next(ref for ref, node in graph.nodes.items() if node["kind"] == "promise")
            return json.dumps(dict(outcome="done", acknowledged=True,
                                   code_map=[dict(promise=promise, modules=["index.html"], tests=["ledger.py"])]))
        if question["kind"] == "LEAD2-WALK-STEER":
            spec = SpecStore(run.store)
            spec.apply(dict(revision=spec.snapshot().revision, mode="substantive", reason="Record the owner note",
                            edits=[dict(op="add", name="context", node=dict(kind="dream", words=added_words))]),
                       author="owner")
            return json.dumps(dict(direction="Complete the owner guide and verification evidence."))
        walks += 1
        facts = question["facts"]
        evidence = facts["Evidence directory"]
        workspace = question["execution"]["walk"]
        check = '''
const serve = require('./checks/kit/serve.cjs');
const browser = require('./checks/kit/browser.cjs');
serve([process.argv[1], 'ledger.py', '127.0.0.1', '{port}'], url => browser(url.url,
  async ({page, shot}) => {
    const staff = await page.context().browser().newPage({viewport:{width:1280,height:900}});
    await staff.goto(url.url);
    await page.getByText('15 places').waitFor();
    await staff.getByText('15 places').waitFor();
    await shot('jo', page);
    await shot('ben', staff);
  }, {viewports:[{width:390,height:844}], silence:10000}), {preview:true})
  .catch(error => {console.error(error);process.exitCode=1;});
'''
        subprocess.run(["node", "-e", check, sys._base_executable], cwd=workspace, check=True,
                       env={**os.environ, "SELDON_EVIDENCE_DIR": evidence})
        story = next(iter(facts["Stories to walk"]))
        promise = next(iter(facts["Promises to judge"]))
        action = "Compare both door counts"
        if builds == 2:
            spec = SpecStore(run.store)
            head = spec.snapshot()
            words = next(ref for ref, node in head.graph.nodes.items() if node.get("words") == added_words)
            spec.apply(dict(revision=head.revision, mode="substantive", reason="Link the owner note to the story",
                            edits=[dict(op="link", source=words, target=story)]), author="planner")
        return json.dumps(dict(
            steps=[dict(story="" if builds == 2 else story, role=role, action=action,
                        expected="See 15", actual="Saw 15", outcome="works", screenshot=filename)
                   for role, filename in (("Jo", "jo-390.png"), ("Ben", "ben-1280.png"))],
            promises=[dict(promise=promise, outcome="works", reason="Both browsers saw 15")],
            limits=[dict(story=story, reason="Venue Wi-Fi was unavailable.")],
            findings=([dict(story=story, owner="Browser verification",
                            finding=f"The verification failed in {evidence}/walk.cjs.")] if builds == 1 else []),
            guide="works", guide_path="OWNER WALK.md", code_map="works",
            quality="broken" if builds == 1 else "works",
            sample_job=f"Open two browser views in {evidence}."))

    from seldon.decisions import Waiting

    def wait(item):
        if item["gate"] == "Walk decision" and "limits" in item["subject"]:
            return {"request": item["request"], "replyId": "venue-limit",
                    "selections": [{"groupId": "limits", "optionId": "allow"}]}
        raise Waiting()

    with closing(Run.reopen(product, build_and_walk, wait)) as run:
        for _ in range(4):
            run.step()
        with urlopen(urljoin(page, "walk"), timeout=5) as response:
            fixing = response.read().decode()
        assert "✗ Verification failed" in fixing and "Sent to the builder" in fixing
        assert str(run.store.cursor().scratch) not in fixing
        candidate = run.store.cursor().candidate
        with urlopen(urljoin(page, "walk-build/" + candidate), timeout=5) as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                assert "ledger.py" in archive.namelist()
                assert "OWNER WALK.md" not in archive.namelist()
        assert run.run().state == "C"
        assert builds == walks == 2
    browser = Path(frontdoor.__file__).with_name("kit") / "browser.cjs"
    check = '''
const assert = require('node:assert/strict');
const browser = require(process.argv[1]);
browser(process.argv[2], async ({page}) => {
  for (const [role, width] of [['Jo', 390], ['Ben', 1280]]) {
    const image = page.getByRole('img', {name: role + ': Compare both door counts'});
    await image.waitFor();
    assert.equal(await image.evaluate(async img => { await img.decode(); return img.naturalWidth; }), width);
    assert.ok((await page.request.get(new URL(await image.getAttribute('src'), page.url()).href)).ok());
  }
  assert.match(await page.locator('.score').textContent(), /1 couldn’t test here/);
  assert.ok(!(await page.locator('#build').evaluate(details => details.open)));
  await page.getByRole('link', {name:/How to try it yourself/}).click();
  assert.ok(await page.locator('#build').evaluate(details => details.open));
  assert.equal(await page.locator('#build ol li').count(), 2);
  assert.match(await page.locator('#build').textContent(), /places left/);
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
