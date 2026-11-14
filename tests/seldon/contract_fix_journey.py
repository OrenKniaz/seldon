"""Lane G: canonical planning -> WORK discovery -> correction -> handoff -> check-in/C."""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.request import urlopen

from seldon import plan
from seldon.frontdoor import state
from test_journey import finish, prepare


def contract_fix_journey(slot, mode):
    """Catch recovery charged to the consumer and stale provider briefs; old fixups never amend text.

    Pending catches needless provider rework; user catches a silent commitment change. The f01 twin
    exercises a real items HTTP response whose server date was unavailable under the old agreement.
    Scripted judgments prove routing, not model judgment or real UI acceptance.
    """
    old = 'GET /items returns items; expiry uses the server calendar date.'
    new = 'GET /items returns items and today, the server calendar date in ISO format.'
    need = 'Expiry uses server today, but items-http does not expose it; the browser date can differ.'
    run, provider = prepare(slot, 'ask', 2)
    run.capacity = 1  # Deterministic not-started/DONE provider states, without sleeps.

    def contracts(answer):
        answer['edits'].append({'kind': 'contract', 'targetHandle': 'new:items-http', 'typedBody': {
            'kind': 'api', 'text': old, 'provider': 1, 'consumers': [2]}})
        answer['units'][0]['provides'] = ['new:items-http']
        answer['units'][1]['consumes'] = ['new:items-http']
        answer['units'][0 if mode == 'pending' else 1]['dependencies'] = [2 if mode == 'pending' else 1]
        answer['units'][0 if mode == 'pending' else 1]['reads'] = [
            'unit-002/value.txt' if mode == 'pending' else 'unit-001/value.txt']

    provider.plan_fault = contracts
    invoke = provider.invoke
    briefs, planner = [], []

    def answer(question, started):
        if question['kind'] == 'CONTRACT':
            planner.append(question)
            assert need in str(question['facts']["Worker's need"])
            consumed = question['facts']['Contracts consumed']
            handle, = consumed
            assert consumed[handle]['text'] == old
            assert consumed[handle]['Provider responsibility'] == 'Deliver value 001'
            payload = ({'verdict': 'user', 'why': 'Exposing this date changes an accepted commitment.'}
                       if mode == 'user' else {'verdict': 'corrected', 'changes': [
                           {'contract': handle, 'text': new}], 'decision': 'Expose the server date to consumers.',
                           'why': 'Makes the existing expiry promise implementable without changing scope.'})
            return json.dumps({'answer': payload}), None
        if question['kind'] == 'WORK':
            briefs.append(question)
        return invoke(question, started)

    provider.invoke = answer
    server = '''from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import date
import json
class Items(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = {'items': []}
        if EXPOSE_DATE:
            payload['today'] = date.today().isoformat()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())
    def log_message(self, *args):
        pass
server = HTTPServer(('127.0.0.1', 0), Items)
print(server.server_port, flush=True)
server.serve_forever()
'''
    native = slot / 'native.py'
    injection = (
        "if not guide:\n"
        f"    if name == {'unit-001' if mode == 'pending' else 'unit-002'!r}:\n"
        f"        assert Path({'unit-002/value.txt' if mode == 'pending' else 'unit-001/value.txt'!r})"
        f".read_text() == {'value-002' if mode == 'pending' else 'value-001'!r}\n"
        "    agreements = facts.get('Contracts provided', facts.get('Contracts consumed', {}))\n"
        "    current = next(iter(agreements.values()))['text']\n"
        "    Path(name, 'agreement.txt').write_text(current)\n"
        "    if name == 'unit-001':\n"
        f"        Path(name, 'server.py').write_text('EXPOSE_DATE = ' + repr(current == {new!r}) + '\\n' + {server!r})\n"
        "    if name == 'unit-002' and turn == 1:\n"
        "        answer['answer'].update(outcome='blocked', discoveries=[{\n"
        f"            'owner': next(iter(agreements)), 'correction': {need!r}}}])\n")
    native.write_text(native.read_text('utf-8').replace('print(json.dumps({"type":"item.completed"',
        injection + 'print(json.dumps({"type":"item.completed"'), encoding='utf-8')
    try:
        while run.store.cursor().state != 'EXECUTE':
            run.step()
        approved = run.store.cursor().revision
        original = run.store.plan(approved)
        if mode == 'user':
            run.run()
            shown = state(str(run.project))
            assert shown['status'] == 'WAITING' and shown['waitingFor']['gate'] == 'UNIT'
            assert need in str(shown['waitingFor']['subject'])
            assert 'accepted commitment' in shown['waitingFor']['subject']['recommendation']
            assert len(planner) == 1 and not run.store.records('contract_revision')
            assert run.store.cursor().revision == approved
            assert run.store.unit('u002').recoveries == 0
            assert not run.store.records('unit_fixup') and not run.store.records('outcome')
            return
        # Re-enter EXECUTE after correction: B must still bind the unchanged commitments.
        run.step()
        assert run.store.unit('u002').recoveries == 0
        finish(run)
        assert len(planner) == 1
        assert not run.store.records('unit_failure')
        revised = run.store.plan(run.store.cursor().revision)
        handles = plan.select(revised, 'contract')
        assert handles.keys() == plan.select(original, 'contract').keys()
        assert next(iter(handles.values()))['text'] == new
        checkin, = [b for _, _, b in run.store.records('decision_request') if b['gate'] == 'CHECK-IN']
        assert checkin['subject']['contractChanges'][0]['decision'] == 'Expose the server date to consumers.'
        assert checkin['subject']['contractChanges'][0]['changes'][0]['text'] == new
        fixups = [b for _, _, b in run.store.records('unit_fixup')]
        assert [b['owner'] for b in fixups] == ([] if mode == 'pending' else ['u001'])
        for number in (1, 2):
            assert (run.project / f'unit-{number:03}/agreement.txt').read_text() == new
        provided = [b for b in briefs if b['facts']['Write scope'] == ['unit-001/**']]
        assert len(provided) == (1 if mode == 'pending' else 2)
        assert next(iter(provided[-1]['facts']['Contracts provided'].values()))['text'] == new
        assert 'override' in provided[-1]['facts']['Contract authority']
        if mode == 'f01':
            child = subprocess.Popen([sys.executable, '-B', str(run.project / 'unit-001/server.py')],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                port = int(child.stdout.readline())
                with urlopen(f'http://127.0.0.1:{port}/items') as response:
                    payload = json.load(response)
                assert payload['today'] == date.today().isoformat() and payload['items'] == []
            finally:
                child.terminate()
                child.communicate()
    finally:
        run.close()
