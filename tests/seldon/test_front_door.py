"""C20/C25/C23: chat words, request binding and mid-work continuity over the MCP wire."""

import json
import sys
import time
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from seldon.frontdoor import server

from test_journey import NATIVE, Scripted


FAKE = '''
import json, runpy, sys
from pathlib import Path
sys.path[:0] = SOURCE_PATHS
from test_journey import Scripted
slot = Path(sys.argv[1])
schema = Path(sys.argv[sys.argv.index('--output-schema') + 1])
question = json.loads(schema.with_name('question.json').read_text('utf-8'))
if question['kind'] == 'WORK':
    runpy.run_path(str(slot / 'native.py'), run_name='__main__')
else:
    if question['kind'] == 'DECIDE':
        words = question['facts']["User's words"]
        choice = ('revise' if words.startswith('Revise') else
                  'decline' if words.startswith('No') else 'approve')
        answer = json.dumps({'answer': {'choice': choice, 'why': words}})
    else:
        answer = Scripted(slot, 'kill-work', 2)(question)
    print(json.dumps({'type':'item.completed',
                      'item':{'type':'agent_message','text':answer}}), flush=True)
    print(json.dumps({'type':'turn.completed'}), flush=True)
'''


@pytest.mark.fast
def test_chat_controls_and_continuity(tmp_path, capsys):
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

                async def wait(run, expected):
                    with anyio.fail_after(55):
                        while True:
                            value = await call('state', run=run)
                            assert value['state'] != 'STOPPED', value
                            if value['controller'] == 'idle' and value['state'] == expected:
                                return value
                            await anyio.sleep(.05)

                run = str(tmp_path / 'product')
                await call('state', error='start a run first', run=run)
                await call('start', dream='Read two named values', folder=run)
                await call('start', error='new folder', dream='Read values', folder=run)
                a = await wait(run, 'A')
                old = a['waitingFor']['request']
                await call('answer', run=run, request=old, words='Revise the dream: name the exact value')
                a = await wait(run, 'A')
                current = a['waitingFor']['request']
                assert current != old
                await call('answer', error=current, run=run, request=old, words='Yes to the old vision')
                await call('resume', error='answer request ' + current, run=run)
                await call('answer', run=run, request=current, words='Yes, this is what I want')
                b = await wait(run, 'B')
                await call('answer', run=run, request=b['waitingFor']['request'], words='Build this plan')
                slot = tmp_path / 'continue'
                with anyio.fail_after(40):
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
                    assert paused['units']['u001']['state'] == 'DONE'
                    assert paused['units']['u002']['state'] != 'DONE'
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
