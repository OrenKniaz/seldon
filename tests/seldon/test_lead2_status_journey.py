"""Fresh Lead chat-to-browser journey through the actual kickoff URL and run database."""

import asyncio
import json
from contextlib import closing
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from seldon import frontdoor
from seldon.run import Run

pytestmark = pytest.mark.fast


def test_kickoff_page_follows_the_plan_and_keeps_a_replay(tmp_path, monkeypatch):
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
    with urlopen(page, timeout=5) as response:
        assert "Where Seldon is" in response.read().decode()
    endpoint = page + "state.json"
    with pytest.raises(HTTPError) as error:
        urlopen(Request(endpoint, data=b"approve"), timeout=5)
    assert error.value.code == 501

    def read_status():
        with urlopen(endpoint, timeout=5) as response:
            return json.load(response)

    def reply(group="round", option="approve"):
        decision = tool("state", run=str(product))["waitingFor"]
        tool("answer", run=str(product), request=decision["request"], replyId=decision["gate"],
             selections=[{"groupId": group, "optionId": option}])

    with closing(Run.reopen(product, provider, lambda _: frontdoor.read_answer(
        frontdoor.side(product) / "answer.json",
    ))) as run:
        assert read_status()["idea"]["text"] == idea
        assert read_status()["idea"]["label"] == "Keep one shared door count while the door"
        run.step()
        reply("technical", "plain")
        run.step()
        run.step()
        align = read_status()
        assert align["frames"][-1]["activity"] == "Waiting on you"
        assert align["trace"][str(align["frames"][-1]["revision"])] == []
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
        assert read_status()["frames"][-1]["activity"] == "Plan approved at B"
