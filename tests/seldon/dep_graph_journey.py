"""T-157: contract-only planning overlaps work; actual file readers still wait."""

import json
import sys
from collections import Counter

from seldon import plan
from test_architect import AreaProvider, start
from test_journey import finish


BACKEND = '''import json
from pathlib import Path
def save(path, rows):
    Path(path).write_text(json.dumps(rows))
def load(path):
    return json.loads(Path(path).read_text())
'''
UI = '''KEY = 'Enter'
def submit(key, text, importer):
    if key == KEY:
        return importer(text)
    return []
'''
IMPORTER = '''import csv, io
def parse(text):
    rows = csv.DictReader(io.StringIO(text))
    if rows.fieldnames != ['name']:
        raise ValueError('Expected name column')
    return list(rows)
'''
OBLIGATIONS = ('fresh folder', 'keyboard', 'offline', 'restart', 'errors')
WAVE = '''import json, runpy, subprocess, sys
from pathlib import Path
backend = runpy.run_path('unit-001/backend.py')
ui = runpy.run_path('unit-002/ui.py')
parse = runpy.run_path('unit-003/importer.py')['parse']
folder = Path('fresh-import')
assert not folder.exists()
folder.mkdir()
print('fresh folder')
assert ui['submit']('Escape', 'name\\napple\\n', parse) == []
rows = ui['submit']('Enter', 'name\\napple\\n', parse)
assert rows == [{'name': 'apple'}]
print('keyboard')
backend['save'](folder / 'items.json', rows)
assert backend['load'](folder / 'items.json') == rows
print('offline')
output = subprocess.check_output([sys.executable, '-B', '-c',
    "import json, runpy; print(json.dumps(runpy.run_path('unit-001/backend.py')['load']"
    "('fresh-import/items.json')))"])
assert json.loads(output) == rows
print('restart')
try:
    ui['submit']('Enter', 'wrong\\napple\\n', parse)
except ValueError as error:
    assert str(error) == 'Expected name column'
else:
    raise AssertionError('Bad CSV was accepted')
print('errors')
'''


def dep_graph_journey(slot, reads_ui):
    class Provider(AreaProvider):
        def areas(self):
            return [{"handle": f"area-{i:03}", "responsibility": role,
                     "scope": [f"unit-{i:03}/**"],
                     "commitments": [*[f"scopeIn:{n}" for n in range(1, 6)], "quality:1", "quality:2"]}
                    for i, role in enumerate(('backend', 'UI', 'importer', 'delivery'), 1)]

        def __call__(self, question):
            answer = json.loads(super().__call__(question))
            body, facts = answer['answer'], question['facts']
            if question['kind'] == 'ALIGN':
                body['scopeIn'] = list(OBLIGATIONS)
            elif question['kind'] == 'ARCHITECT':
                body['contracts'] = [{"handle": f"link-{i}", "kind": "api",
                    "text": 'submit(Enter, CSV, parse) -> name rows; save/load JSON rows',
                    "provider": f"area-{i:03}", "consumers": ['area-003']} for i in (1, 2)]
            elif question['kind'] == 'DETAIL':
                unit, = body['units']
                unit['reads'] = []
                unit['dependencies'] = []
                number = int(facts['Area']['handle'][-3:])
                if number == 3:
                    # h02's old prerequisites were only for authoring the integrated scenario.
                    unit['dependencies'] = ['unit-001/backend.py', 'unit-002/ui.py']
                    unit['checkPaths'] = ['unit-003/*.py']
                    if reads_ui:
                        unit['reads'] = ['unit-002/ui.py']
                    body['edits'].append({'kind': 'check', 'targetHandle': 'import-flow', 'typedBody': {
                        'purpose': 'Import CSV through API and UI: ' + ', '.join(OBLIGATIONS),
                        'level': 'wave', 'command': [sys.executable, '-B', 'unit-003/wave.py'],
                        'requiredEnvironment': ['Python'], 'expectedEvidence': ', '.join(OBLIGATIONS),
                        'requirements': ['new:r003', 'link-1', 'link-2']}})
                elif number == 4:
                    unit['dependencies'] = ['unit-001/backend.py', 'unit-002/ui.py', 'unit-003/importer.py']
            return json.dumps(answer)

    run, base = start(slot, 4, 'sizing')
    provider = Provider(base.slot, 4, 'sizing')
    run.provider = provider
    native = slot / 'native.py'
    text = native.read_text('utf-8')
    # All independent first-wave workers meet before producing a patch. A false prerequisite
    # prevents this rendezvous; the normal provider deadline is the hang guard.
    injection = (
        "if not guide:\n"
        f"    first = {('unit-001', 'unit-002') if reads_ui else ('unit-001', 'unit-002', 'unit-003')!r}\n"
        "    if name in first:\n"
        "        (slot / ('started-' + name)).touch()\n"
        "        while not all((slot / ('started-' + n)).exists() for n in first):\n"
        "            time.sleep(0.01)\n"
        f"    sources = {{'unit-001/backend.py': {BACKEND!r}, 'unit-002/ui.py': {UI!r},\n"
        f"               'unit-003/importer.py': {IMPORTER!r}, 'unit-003/wave.py': {WAVE!r}}}\n"
        "    for path, source in sources.items():\n"
        "        if path.startswith(name + '/'):\n"
        "            Path(path).write_text(source)\n")
    if reads_ui:
        injection += (
            "    if name == 'unit-003':\n"
            "        key = __import__('runpy').run_path('unit-002/ui.py')['KEY']\n"
            "        with Path(name, 'check.py').open('a') as check:\n"
            "            check.write('assert ' + repr(key) + " + repr(" == 'Enter'\n") + ")\n")
    # Avoid the generic area's worker reading any consumed contract implementation.
    text = text.replace('if facts.get("Contracts consumed"):', 'if False:')
    native.write_text(text.replace('print(json.dumps({"type":"item.completed"',
        injection + 'print(json.dumps({"type":"item.completed"'), encoding='utf-8')
    try:
        while run.store.cursor().state != 'EXECUTE':
            run.step()
        document = run.store.plan(run.store.cursor().revision)
        units = plan.select(document, 'unit')
        assert units['u003']['dependencies'] == (['u002'] if reads_ui else [])
        assert units['u004']['dependencies'] == []
        finish(run)
        wave, = plan.assigned(document)[None]
        assert plan.owner(document, wave) == 'u003'
        final = [run.store.read(r) for r in run.store.cursor().finalObservations]
        observed, = [o for o in final if o['check'] == wave]
        assert observed['exitCode'] == 0
        accepted = run.store.read(run.store.cursor().vision)['answer']['scopeIn']
        assert Counter(observed['stdout'].splitlines()) == Counter(accepted)
        launches = {u: json.loads((slot / ('launch-docs.jsonl' if u == 'guide' else
                    f'launch-unit-{u[-3:]}.jsonl')).read_text().splitlines()[0]) for u in units}
        ended = {b['unit']: b['finishedAt'] for _, _, b in run.store.records('writer_end')}
        minutes = {u: (ended[u] - launch['start']) / 60
                   for u, launch in launches.items()}
        def chain(ref):
            previous = [chain(d) for d in units[ref]['dependencies']]
            path, duration = max(previous, key=lambda item: item[1], default=([], 0))
            return [*path, ref], duration + minutes.get(ref, 0)
        path, duration = max((chain(u) for u in launches), key=lambda item: item[1])
        print(f"depGraph reads_ui={reads_ui}: longest dependency chain {' -> '.join(path)}, "
              f"{duration:.3f} stub WORK minutes", flush=True)
    finally:
        run.close()
