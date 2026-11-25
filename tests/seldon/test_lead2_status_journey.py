"""Fresh Lead chat-to-browser journey through the actual kickoff URL and run database."""

import asyncio
import json
import os
import subprocess
import sys
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
