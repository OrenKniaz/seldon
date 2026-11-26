"""C20/C25/C23: chat words, request binding and mid-work continuity over the MCP wire."""

import json
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from seldon import decisions, frontdoor
from seldon.frontdoor import opened, server, side
from seldon.store import RunRow, Store

from test_journey import NATIVE, Scripted


FAKE = '''
import json, runpy, sys
from pathlib import Path
sys.path[:0] = SOURCE_PATHS
from test_journey import Scripted, strict_schema
slot = Path(sys.argv[1])
schema = Path(sys.argv[sys.argv.index('--output-schema') + 1])
question = json.loads(schema.with_name('question.json').read_text('utf-8'))
strict_schema(question['schema'])
if question['kind'] == 'WORK':
    runpy.run_path(str(slot / 'native.py'), run_name='__main__')
else:
    if question['kind'] == 'DECIDE':
        words = question['facts']["User's words"]
        choice = ('Other' if words.startswith('Revise') else
                  'Stop' if words.startswith('No') else question['targets'][0])
        answer = json.dumps({'answer': {'choice': choice, 'why': words}})
    else:
        answer = Scripted(slot, 'kill-work', 2)(question)
    print(json.dumps({'type':'item.completed',
                      'item':{'type':'agent_message','text':answer}}), flush=True)
    print(json.dumps({'type':'turn.completed'}), flush=True)
'''


@pytest.mark.fast
def test_chat_controls_and_continuity_golden(tmp_path, scalar_align, capsys):
    """Words reach DECIDE, stale A cannot answer B, and pause preserves completed work.

    Catches a chat server driving transitions or losing work on disconnect; the golden journey
    calls Run directly and cannot catch MCP serialization, detached process or installed CLI defects.
    """
    began = time.monotonic()
    slots = iter(tmp_path / name for name in ('continue', 'stop', 'decline'))

    def scripted():
        slot = next(slots)
        slot.mkdir()
        (slot / 'mode').write_text('kill-work')
        (slot / 'native.py').write_text(NATIVE, encoding='utf-8')
        paths = [str(Path(__file__).resolve().parents[2] / 'src'), str(Path(__file__).resolve().parent)]
        (slot / 'fake.py').write_text(FAKE.replace('SOURCE_PATHS', repr(paths)), encoding='utf-8')
        setup = Scripted(slot, 'kill-work', 2).setup
        setup['codexCommand'] = [sys.executable, '-B', str(slot / 'fake.py'), str(slot)]
        setup['scriptedUser'] = False  # This fixture relays a person's choices over MCP.
        setup.pop('userOrigin', None)
        return setup

    async def journey():
        app = server(setup=scripted)
        low = app._lowlevel_server
        async with (create_client_server_memory_streams() as (client, service),
                    anyio.create_task_group() as tasks):
            tasks.start_soon(low.run, *service, low.create_initialization_options())
            async with ClientSession(*client) as session:
                await session.initialize()
                assert {tool.name for tool in (await session.list_tools()).tools} == {
                    'start', 'state', 'answer', 'pause', 'resume', 'stop'}

                async def call(tool, error=None, **arguments):
                    result = await session.call_tool(tool, arguments)
                    if error:
                        assert result.is_error and error in str(result.content), result
                        return
                    assert not result.is_error, result
                    return json.loads(result.content[0].text)

                async def wait(run, expected, previous=None):
                    with anyio.fail_after(55):
                        while True:
                            value = await call('state', run=run)
                            assert value['state'] != 'STOPPED', value
                            target = ((value['waitingFor'] or {}).get('gate')
                                      if expected in ('A', 'B', 'CHECK-IN', 'C') else value['state'])
                            if (value['controller'] == 'idle' and target == expected
                                    and (expected == 'ACCEPTED' or value['waitingFor'] is not None)
                                    and (previous is None or value['waitingFor']['request'] != previous)):
                                return value
                            await anyio.sleep(.05)

                run = str(tmp_path / 'product')
                await call('state', error='start a run first', run=run)
                await call('start', dream='Read two named values', folder=run)
                await call('start', error='new folder', dream='Read values', folder=run)
                a = await wait(run, 'A')
                assert a['waitingFor']['ask']['options'][0]['label'] == 'Build this'
                old = a['waitingFor']['request']
                await call('answer', run=run, request=old, words='Revise the dream: name the exact value')
                a = await wait(run, 'A', previous=old)
                current = a['waitingFor']['request']
                assert current != old
                await call('answer', error=current, run=run, request=old, words='Yes to the old vision')
                await call('resume', error='answer request ' + current, run=run)
                await call('answer', run=run, request=current, words='Yes, this is what I want')
                b = await wait(run, 'B')
                await call('answer', run=run, request=b['waitingFor']['request'], words='Build this plan')
                slot = tmp_path / 'continue'
                # The marker follows the first unit's integration, which shares the parallel gate.
                with anyio.fail_after(90):
                    while not (slot / 'kill-ready').exists():
                        await anyio.sleep(.05)
                pid = int((slot / 'kill-ready').read_text())
                # Keep the native handle across pause: a reused pid must not make death look successful.
                import ctypes
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                handle = kernel.OpenProcess(0x100000, False, pid)
                assert handle
                try:
                    paused = await call('pause', run=run)
                    assert paused['controller'] == 'idle' and paused['waitingFor'] is None
                    assert paused['units']['1.area-1.1']['state'] == 'DONE'
                    assert paused['units']['1.area-1.2']['state'] != 'DONE'
                    assert kernel.WaitForSingleObject(handle, 5000) == 0, 'Worker survived pause'
                finally:
                    kernel.CloseHandle(handle)
                (slot / 'resume').write_text('Continue the same run')
                await call('resume', run=run)
                for gate in ('CHECK-IN', 'C'):
                    value = await wait(run, gate)
                    await call('answer', run=run, request=value['waitingFor']['request'],
                               words='Yes, I accept what you showed me')
                accepted = await wait(run, 'ACCEPTED')
                assert accepted['outcome']['accepted'] and not accepted['outcome']['scriptedUser']
                assert (slot / 'turn-unit-001').read_text() == '1'

                stopped_run = str(tmp_path / 'stopped')
                await call('start', dream='Read values', folder=stopped_run)
                value = await wait(stopped_run, 'A')
                stopped = await call('stop', run=stopped_run)
                assert stopped['state'] == 'STOPPED'
                assert stopped['outcome']['reason'] == 'Stopped by the user in chat'
                await call('answer', error='start', run=stopped_run,
                           request=value['waitingFor']['request'], words='I accept')
                await call('resume', error='start', run=stopped_run)
                output = await anyio.run_process([sys.executable, '-B', '-m', 'seldon.frontdoor',
                                                  'status', stopped_run])
                assert json.loads(output.stdout) == stopped

                declined_run = str(tmp_path / 'declined')
                await call('start', dream='Read values', folder=declined_run)
                value = await wait(declined_run, 'A')
                await call('answer', run=declined_run, request=value['waitingFor']['request'],
                           words='No, not this')
                with anyio.fail_after(10):
                    while True:
                        value = await call('state', run=declined_run)
                        if value['state'] == 'STOPPED' and value['controller'] == 'idle':
                            break
                        await anyio.sleep(.05)
                reason = value['outcome']['reason']
                assert value['state'] == 'STOPPED' and 'No, not this' in reason
            tasks.cancel_scope.cancel()

    anyio.run(journey)
    with capsys.disabled():
        print(f'Front door journey: {time.monotonic() - began:.2f}s', flush=True)


@pytest.mark.fast
def test_recorded_quiet_bench_round_chat_journey(tmp_path, monkeypatch):
    """Independent choices survive a reconnect and a partial answer consumes one delivery.

    A scalar journey cannot catch a picker flattening two groups into one label or replaying
    an old carrier while the same round still waits. The loop's receipt writer is a contract twin.
    """
    run = tmp_path / "bench"
    run.mkdir()
    saved = side(run)
    saved.mkdir()
    store = Store(run)
    kickoff = store.record("kickoff", {"setup": {}})
    store.cursor(RunRow(kickoff=kickoff, state="A", scratch=str(tmp_path),
                        candidate="fixture", waveStart="fixture"))

    # Abridged recorded ALIGN and qb2/b01.log r10; split the recorded tradeoffs for the twin.
    cli = {"label": "CLI-first bench", "means": "Complete the simulation, safety controls and "
           "session workflows through the command line before adding the web page."}
    first = {"label": "Start with Wave 1 only", "means": "Agree to the thinnest saved, visible fixed-filter "
             "run now and decide later whether to build the remaining waves."}
    full = {"label": "Full three-wave bench", "means": "Agree to all three waves and review each working "
            "increment before proceeding to the next."}
    vision = {"alternatives": [first, cli, full], "size": "large",
              "why": "Numerical signal processing, persistent sessions, asynchronous stopping, page and CLI."}
    decisions.request(store, "A", {"vision": vision})
    old = store.cursor().decision
    delta = "Report stopped and limited runs truthfully, including how many samples were processed."
    ask = {"delta": delta, "groups": [
        {"id": "surface", "question": "Which interface should come first?", "default": "page",
         "options": [{"id": "page", "label": "Local page first", "means": "Open the bench in the browser"},
                     {"id": "cli", **cli}]},
        {"id": "waves", "question": "How much should we agree to now?", "default": "full",
         "options": [{"id": "full", **full}, {"id": "first", **first}]},
        {"id": "size", "question": "Override the proposed large size?", "default": "large",
         "options": [{"id": "large", "label": "Large", "means": vision["why"]},
                     {"id": "medium", "label": "Medium", "means": "Use a smaller split"}]},
        {"id": "round", "question": "Are alignment and expansion done?", "default": "revise",
         "options": [{"id": "revise", "label": "Keep shaping this", "means": "Review the next proposal"},
                     {"id": "done", "label": "This is it", "means": "Accept the displayed defaults"}]}]}

    delivered = []

    class LoopContract:
        def __init__(self, project, provider, user):
            self.store = Store(project)
            self.user = user

        def run(self):
            with self.store.transaction():
                ref = self.store.cursor().decision
                request = self.store.read(ref)
                try:
                    reply = self.user({"request": ref, **request})
                except decisions.Waiting:
                    return
                delivered.append(reply)
                if len(delivered) == 1:
                    return  # Interruption before the loop commits the receipt.
                self.store.record("user_words", {**reply, "origin": request["origin"]})
                if "words" in reply:
                    self.store.cursor(replace(self.store.cursor(), decision=None))
                    decisions.request(self.store, "B", {"vision": vision})  # r170 is the scalar B control.

        def close(self):
            self.store.close()

    monkeypatch.setattr(frontdoor.Run, "reopen", LoopContract)
    monkeypatch.setattr(frontdoor, "spawn", lambda run: (frontdoor.drive(run), frontdoor.state(run))[1])
    for key in ("TEMP", "TMP", "TMPDIR", "SELDON_EVIDENCE_DIR"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setenv("SELDON_OFF", "growAlign")

    @asynccontextmanager
    async def chat():
        low = server()._lowlevel_server
        async with (create_client_server_memory_streams() as (client, service),
                    anyio.create_task_group() as tasks):
            tasks.start_soon(low.run, *service, low.create_initialization_options())
            async with ClientSession(*client) as session:
                await session.initialize()

                async def call(tool, error=None, **arguments):
                    result = await session.call_tool(tool, {"run": str(run), **arguments})
                    if error:
                        assert result.is_error and error in str(result.content), result
                        return
                    assert not result.is_error, result
                    return json.loads(result.content[0].text)

                yield call
            tasks.cancel_scope.cancel()

    async def journey():
        async with chat() as call:
            scalar = (await call("state"))["waitingFor"]["ask"]
            assert [o["label"] for o in scalar["options"]] == [
                "Build this", first["label"], cli["label"], full["label"], "Other"]
            store.cursor(replace(store.cursor(), decision=None))
            decisions.request(store, "A", {"vision": vision, "ask": ask})
            current = store.cursor().decision
            grouped = (await call("state"))["waitingFor"]["ask"]
            assert grouped["delta"] == delta
            assert [group["default"] for group in grouped["groups"]] == ["page", "full", "large", "revise"]
            assert all(group["options"][-1]["label"] == "Other" for group in grouped["groups"])
            await call("answer", error=current, request=old, words="Build this")
            await call("answer", error="with a replyId", request=current, words="This is it")
            # Counterfactual owner reply: the recorded simulator said only "Build this".
            selections = [{"groupId": "surface", "optionId": "cli"},
                          {"groupId": "waves", "optionId": "other",
                           "text": "  Show me Wave 1, then we decide.\n"}]
            batch = {"request": current, "replyId": "chat-message-1", "selections": selections}
            await call("answer", **batch)
        async with chat() as call:
            await call("answer", error="use resume", request=current, replyId="chat-message-2",
                       words="This is it")
            waiting = (await call("resume"))["waitingFor"]
            assert waiting["request"] == current
            await call("resume", error=f"answer request {current}")
            await call("answer", **batch)  # Redelivery is not a new turn.
            assert delivered == [batch, batch]  # Same identity delivered before and after interruption.
            # The loop projects the durable reply into the next pending display.
            _, _, receipt = store.records("user_words")[-1]
            remaining = [group for group in ask["groups"] if group["id"] in ("size", "round")]
            store.cursor(replace(store.cursor(), decision=None))
            decisions.request(store, "A", {"vision": vision, "ask": {
                **ask, "groups": remaining, "retained": receipt["selections"]}})
            waiting = (await call("state"))["waitingFor"]
            assert waiting["ask"]["retained"] == selections
            assert [group["id"] for group in waiting["ask"]["groups"]] == ["size", "round"]
            await call("answer", error=waiting["request"], **batch)
            # Another counterfactual chat turn, relayed as exact prose.
            words = "  Keep the size as proposed.\nThis is it. "
            next_display = (await call("answer", request=waiting["request"], replyId="chat-message-2",
                                       words=words))["waitingFor"]
            assert delivered[-1]["words"] == words
            assert next_display["ask"]["options"][0]["label"] == "Build this plan"
            await call("answer", error=next_display["request"], **batch)
            await call("answer", error="with the picked label", request=next_display["request"],
                       replyId="chat-message-3", selections=selections)

    try:
        anyio.run(journey)
    finally:
        store.close()


@pytest.mark.fast
@pytest.mark.parametrize('fault', ['live', 'restart'])
def test_answer_file_continuity_journey(tmp_path, scalar_align, fault):
    """An answer preserves live work and survives a controller loss before its decision commits.

    Catches SIGTERM-on-answer and destructive reads of the answer carrier. The existing chat journey
    answers only idle gates and intentionally pauses its worker, so neither fault is visible there.
    The framed request is the R17 producer handoff fixture; ordinary gates use decisions.request.
    """
    slot = tmp_path / 'provider'
    slot.mkdir()
    (slot / 'mode').write_text('kill-work')
    native = NATIVE.replace('time.sleep(30)',
        "while not (slot/'resume').exists(): time.sleep(.02)")
    (slot / 'native.py').write_text(native, encoding='utf-8')
    paths = [str(Path(__file__).resolve().parents[2] / 'src'), str(Path(__file__).resolve().parent)]
    fake = FAKE.replace('SOURCE_PATHS', repr(paths)).replace(
        "choice = ('Other'", "if words == 'Keep my answer through restart' and not (slot/'release').exists():\n"
        "            (slot/'deciding').write_text('answer read, not committed')\n"
        "            import time\n"
        "            while not (slot/'release').exists(): time.sleep(.02)\n"
        "        choice = ('Other'")
    (slot / 'fake.py').write_text(fake, encoding='utf-8')
    setup = Scripted(slot, 'kill-work', 2).setup
    setup['codexCommand'] = [sys.executable, '-B', str(slot / 'fake.py'), str(slot)]
    run = str(tmp_path / 'product')

    async def journey():
        low = server(setup=lambda: setup)._lowlevel_server
        async with (create_client_server_memory_streams() as (client, service),
                    anyio.create_task_group() as tasks):
            tasks.start_soon(low.run, *service, low.create_initialization_options())
            async with ClientSession(*client) as session:
                await session.initialize()

                async def call(tool, **arguments):
                    result = await session.call_tool(tool, {'run': run, **arguments})
                    assert not result.is_error, result
                    return json.loads(result.content[0].text)

                async def wait(gate):
                    with anyio.fail_after(90):
                        while True:
                            value = await call('state')
                            if value['controller'] == 'idle' and value['waitingFor'] \
                                    and value['waitingFor']['gate'] == gate:
                                return value['waitingFor']['request']
                            await anyio.sleep(.02)

                try:
                    result = await session.call_tool('start', {'dream': 'Read two named values', 'folder': run})
                    assert not result.is_error, result
                    a = await wait('A')
                    words = 'Keep my answer through restart' if fault == 'restart' else 'Build this'
                    await call('answer', request=a, words=words)
                    if fault == 'restart':
                        with anyio.fail_after(30):
                            while not (slot / 'deciding').exists():
                                await anyio.sleep(.02)
                        assert json.loads((side(run) / 'answer.json').read_text()) == {
                            'request': a, 'words': words}
                        await call('pause')
                        (slot / 'release').write_text('Resume the interrupted decision')
                        await call('resume')
                    b = await wait('B')
                    # The next request discards the old carrier; replay must not approve B as well.
                    assert not (side(run) / 'answer.json').exists()
                    with opened(run) as store:
                        approvals = [row for _, _, row in store.records('approval') if row['request'] == a]
                        assert len(approvals) == 1
                    await call('answer', request=b, words='Build this plan')
                    with anyio.fail_after(90):
                        while not (slot / 'kill-ready').exists():
                            await anyio.sleep(.02)
                    if fault == 'live':
                        # Publish the sibling producer's framed request while the real worker is held.
                        ask = {'question': 'How should we continue?', 'options': [
                            {'label': 'Continue this work', 'means': 'Keep the accepted scope', 'effect': 'direct'},
                            {'label': 'Change direction', 'means': 'Explain a new approach', 'effect': 'direct'}]}
                        with opened(run) as store, store.transaction():
                            request = store.record('decision_request', {'gate': 'EXECUTE',
                                'subject': {'step': 'EXECUTE'}, 'origin': 'user', 'ask': ask})
                            store.cursor(replace(store.cursor(), decision=request))
                        shown = await call('state')
                        assert shown['status'] == 'RUNNING' and shown['units']['1.area-1.2']['state'] == 'WORKING'
                        assert shown['waitingFor']['ask'] == {'question': ask['question'], 'options': [
                            {'label': 'Continue this work', 'means': 'Keep the accepted scope'},
                            {'label': 'Change direction', 'means': 'Explain a new approach'},
                            {'label': 'Other', 'means': 'Your own words'}]}
                        assert 'never pick for the user' in shown['next']
                        controller = (side(run) / 'controller.json').read_text()
                        stale = await session.call_tool('answer', {'run': run, 'request': a, 'words': 'Old answer'})
                        assert stale.is_error and request in str(stale.content)
                        await call('answer', request=request, words='Continue this work')
                        assert (side(run) / 'controller.json').read_text() == controller
                        assert (await call('state'))['controller'] == 'running'
                    (slot / 'resume').write_text('Finish in the original worker session')
                    for gate in ('CHECK-IN', 'C'):
                        request = await wait(gate)
                        await call('answer', request=request, words='Yes, I accept')
                    with anyio.fail_after(40):
                        accepted = await call('state')
                        while accepted['controller'] != 'idle' or accepted['status'] != 'ACCEPTED':
                            await anyio.sleep(.02)
                            accepted = await call('state')
                    assert accepted['units']['1.area-1.2']['state'] == 'DONE'
                    assert (Path(run) / 'unit-002/value.txt').read_text() == 'value-002'
                    with opened(run) as store:
                        assert not any(row['event'] == 'reopen' and row['unit'] == '1.area-1.2'
                                       for _, _, row in store.records('transition'))
                        if fault == 'live':
                            assert any(row['words'] == 'Continue this work'
                                       for _, _, row in store.records('user_words'))
                finally:
                    await call('pause')
            tasks.cancel_scope.cancel()

    anyio.run(journey)
