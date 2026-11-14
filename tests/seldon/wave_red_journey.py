"""Fable 9.2: owned wave baselines reach the unit judge without premature GREEN."""

import json
import sys
from pathlib import Path

from seldon import gitwork, plan, questions, units
from seldon.providers import ROLES
from seldon.store import RunRow, Store, UnitRow
from test_journey import VISION, planned


def wave_red_journey(root, preserving):
    """A delivery unit reaches DONE without recovery using its observable baseline.

    Replays f04's wave check with a short-lived helper twin (taskkill is denied in this sandbox).
    The later-wave twin preserves an existing product. Local-only journeys miss both cases.
    """
    root.mkdir()
    project, scratch = root / 'product', root / 'scratch'
    project.mkdir()
    scratch.mkdir()
    base = gitwork.init(project)
    helper = ("import subprocess, sys\n"
              "child = subprocess.run([sys.executable, '-B', '-c', \"print('helper ready')\"],\n"
              "                       capture_output=True, text=True, check=True)\n"
              "assert child.stdout.strip() == 'helper ready'\nprint('helper ready')\n")
    if preserving:
        document = plan.create(VISION, planned(1, 'machinery'))
        ref = 'u001'
        files = {'unit-001/check.py': helper,
                 'unit-001/wave.py': "from pathlib import Path\n"
                     "assert Path('unit-001/value.txt').read_text() == 'value-001'\n"
                     "print('observed preserved value-001')\n",
                 'unit-001/value.txt': 'value-001'}
        (project / 'unit-001').mkdir()
        (project / 'unit-001/value.txt').write_text('value-001')
        gitwork.git(project, 'add', '--all')
        base = gitwork.commit(project, gitwork.git(project, 'write-tree'), base)
        gitwork.checkout(project, base)
    else:
        fixture = json.loads(Path(__file__).with_name('recorded_f04_delivery.json').read_text('utf-8'))
        document, files, ref = fixture['document'], fixture['files'], 'u004'
        files['checks/delivery/check_helpers.py'] = helper
        document['nodes']['n027']['body'].update(
            command=[sys.executable, '-B', 'checks/delivery/check_helpers.py'],
            expectedEvidence='A real short-lived subprocess produces its readiness output and exits.')

    def provider(question):
        if question['kind'] == 'WORK':
            for name, text in files.items():
                path = Path(question['execution']['cwd']) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8')
            answer = {'outcome': 'done', 'summary': 'Authored delivery checks and product',
                      'discoveries': [], 'why': 'Ready for independent proof'}
        else:
            facts = question['facts']
            assessments, gaps = questions.assessments(question['targets']), []
            for assessment in assessments:
                target = assessment['target']
                if target not in facts['Requirements']:
                    continue
                baseline = [o for o in facts['Observations'] if o['purpose'] in ('T', 'wave-start')
                            and o['check'] in facts['Checks'] and target in o['requirements']]
                red = any(o['exitCode'] not in (0, None) for o in baseline)
                preserved = preserving and any(o['purpose'] == 'wave-start' and o['exitCode'] == 0
                                               for o in baseline)
                assessment.update(baselineDemonstrated=red or preserved,
                                  why='Observed product RED' if red else 'Observed preservation baseline')
                if not assessment['baselineDemonstrated']:
                    gaps.append({'target': target, 'correction': 'Need observable product RED'})
            answer = {'verdict': 'gap' if gaps else 'satisfied', 'gaps': gaps,
                      'assessments': assessments, 'why': 'Judge interprets observed baselines'}
        return json.dumps({'answer': answer})

    store = Store(project)
    kickoff = store.record('kickoff', {'setup': {'providers': ROLES}})
    store.cursor(RunRow(state='EXECUTE', kickoff=kickoff, scratch=str(scratch),
                        candidate=base, waveStart=base, revision=store.revision(document)))
    store.unit(ref, UnitRow(state='READY'))
    store.close()
    units.run_unit(Store(project), provider, ref)
    store = Store(project)
    try:
        unit = store.unit(ref)
        assert unit.state == 'DONE', store.records('unit_failure')
        assert unit.recoveries == 0 and not store.records('unit_failure')
        wave = {r for r, c in plan.assigned(document)[None].items() if plan.owner(document, r) == ref}
        observed = [b for _, _, b in store.records('observation') if b['check'] in wave]
        assert observed and all(o['purpose'] == 'wave-start' for o in observed)
        assert all((o['exitCode'] == 0) == preserving for o in observed)
        if not preserving:
            assert all('FileNotFoundError' in o['stderr'] for o in observed)
        verdict = store.read(unit.judgment)['answer']
        assert verdict['verdict'] == 'satisfied'
        if preserving:
            assert any('preservation' in a['why'] for a in verdict['assessments'])
        print(f'Extra owned wave-start runs ({"preserving" if preserving else "f04"}): {len(observed)}',
              flush=True)
    finally:
        store.close()
