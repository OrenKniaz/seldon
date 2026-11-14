"""T-156: integrated browser observations route repair, never trace coverage alone."""

import json
import shutil
import time
from pathlib import Path

from seldon import questions
from test_journey import finish, prepare


def gap_owner_journey(slot, case):
    """Catch g04 amplification, lost RED/screenshots and shared-text ping-pong through C.

    Existing shared-flow fixtures stop before actual FLOW repair; check-gap fixtures bypass the
    semantic correction owner. The two-owner twin protects an explicitly shared correction.
    The literal twin requires the existing CONTRACT route to settle a provider's disputed text.
    """
    began = time.monotonic()
    run, provider = prepare(slot, 'gap-owner', 3)
    node = shutil.which('node')
    package = Path('C:/Users/oren/codex-harness/ui/node_modules/.pnpm/playwright-core@1.59.1/node_modules/playwright-core')
    edge = Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    assert node and package.is_dir() and edge.is_file(), 'The installed browser runtime is required'
    old = 'The UI shows inventory; empty-state wording is not yet agreed.'
    agreed = 'An empty inventory shows exactly: No inventory yet.'

    def layout(answer):
        # One integrated flow crosses all three owners; only the UI is broken in the g04 twin.
        for e in answer['edits']:
            if e['kind'] == 'requirement':
                e['typedBody']['features'] = ['new:f001']
        answer['edits'] = [e for e in answer['edits'] if e['targetHandle'] not in ('new:f002', 'new:f003')]
        answer['edits'].append({'kind': 'contract', 'targetHandle': 'new:inventory', 'typedBody': {
            'kind': 'ui-structure', 'text': old, 'provider': 2, 'consumers': [3]}})
        answer['units'][1]['provides'] = ['new:inventory']
        answer['edits'].append({'kind': 'contract', 'targetHandle': 'new:data', 'typedBody': {
            'kind': 'file-format', 'text': 'The backend supplies the inventory status.', 'provider': 1, 'consumers': [3]}})
        answer['units'][0]['provides'] = ['new:data']
        answer['units'][2].update(consumes=['new:inventory', 'new:data'],
                                dependencies=[1, 2], reads=['unit-001/status.txt', 'unit-002/index.html'],
                                checkPaths=['unit-003/*.py', 'unit-003/*.cjs'])
        answer['edits'].append({'kind': 'check', 'targetHandle': 'new:flow', 'typedBody': {
            'purpose': 'Observe actual inventory and its empty-state message in Edge', 'level': 'wave',
            'command': [node, 'unit-003/flow.cjs'], 'requiredEnvironment': ['Node', 'Edge'],
            'expectedEvidence': 'Integrated inventory screenshot and rendered empty-state text',
            'requirements': ['new:r002', 'new:r003']}})

    provider.plan_fault = layout
    browser_check = f"""const {{chromium}} = require({str(package)!r});
const fs = require('node:fs'), path = require('node:path');
const {{pathToFileURL}} = require('node:url');
(async () => {{
  const browser = await chromium.launch({{executablePath:{str(edge)!r},headless:true}});
  try {{
    const page = await browser.newPage();
    await page.goto(pathToFileURL(path.resolve('unit-002/index.html')).href);
    const result = {{items:await page.locator('li').count(),
      message:await page.locator('[role=status]').innerText(),
      backend:fs.readFileSync('unit-001/status.txt','utf8')}};
    await page.screenshot({{path:path.join(process.env.SELDON_EVIDENCE_DIR,'inventory.png')}});
    console.log('INVENTORY '+JSON.stringify(result));
  }} finally {{ await browser.close(); }}
}})().catch(e => {{console.error(e);process.exitCode=1;}});
"""
    injection = (
        "if not guide and name == 'unit-001':\n"
        f"    Path(name, 'status.txt').write_text('stale' if {case == 'two'!r} and turn == 1 else 'ready')\n"
        "if not guide and name == 'unit-002':\n"
        "    agreements = facts.get('Contracts provided', {})\n"
        f"    corrected = any(c['text'] == {agreed!r} for c in agreements.values())\n"
        f"    literal = {case == 'literal'!r}\n"
        "    fixed = corrected if literal else turn > 1\n"
        "    message = ('No inventory yet.' if corrected else 'Nothing here') if literal else ('' if fixed else 'No items')\n"
        "    Path(name, 'index.html').write_text('<!doctype html><h1>Inventory</h1>' +\n"
        "        ('' if literal else '<ul><li>Milk</li></ul>') + '<p role=status>' + message + '</p>')\n"
        "    if turn > 1 and not literal:\n"
        "        with Path(name, 'check.py').open('a') as check:\n"
        "            check.write(\"assert 'No items' not in Path('unit-002/index.html').read_text()\\n\")\n"
        f"    if literal and turn > 1 and not corrected:\n"
        "        answer['answer'].update(outcome='blocked', discoveries=[{\n"
        "            'owner': next(iter(agreements)), 'correction': 'Choose the shared empty-state text once.'}])\n"
        "if not guide and name == 'unit-003':\n"
        f"    Path(name, 'flow.cjs').write_text({browser_check!r})\n")
    native = slot / 'native.py'
    native.write_text(native.read_text('utf-8').replace('print(json.dumps({"type":"item.completed"',
        injection + 'print(json.dumps({"type":"item.completed"'), encoding='utf-8')
    invoke = provider.invoke
    repairs, contract_calls = [], []
    baseline_red = {}

    def answer(question, started):
        kind, facts = question['kind'], question['facts']
        if kind == 'FLOW':
            rows = [o for o in facts['Final observations'] if 'INVENTORY ' in o['stdout']]
            observed = json.loads(rows[0]['stdout'].split('INVENTORY ')[1])
            broken = observed['message'] != ('No inventory yet.' if case == 'literal' else '')
            if broken:
                owner = next(r for r, c in facts['Crossed contracts'].items() if c['text'] == old) \
                    if case == 'literal' else 'u002'
                return json.dumps({'answer': {'verdict': 'gap', 'assessments':
                    questions.assessments(question['targets'], final=True),
                    'gaps': [{'target': question['targets'][1], 'owners':
                        ['u001', 'u002'] if case == 'two' else [owner],
                        'correction': 'Repair the observed inventory empty state and stale backend if named.'}],
                    'why': 'The integrated screenshot exposes the contradictory empty state'}}), None
        if kind == 'CONTRACT':
            contract_calls.append(question)
            handle, = facts['Contracts consumed']
            return json.dumps({'answer': {'verdict': 'corrected', 'changes': [
                {'contract': handle, 'text': agreed}], 'decision': 'Choose one empty-state message.',
                'why': 'Resolves shared wording within the accepted inventory experience'}}), None
        if kind == 'WORK' and 'Scoped corrections' in facts:
            repairs.append(question)
            assert question['images'] and all(Path(p).is_file() for p in question['images'])
            assert any('INVENTORY ' in o['stdout'] for o in facts['Failing observations'])
        if kind == 'JUDGE' and 'Scoped corrections' in facts:
            owned = facts['Scoped corrections']['owner']
            # Original failed checks keep their actual commit; fresh regression RED is also visible.
            observations = facts['Observations']
            for commit in baseline_red[owned]:
                assert any(o['commit'] == commit and o['exitCode'] != 0 for o in observations)
            assert facts['Failing observations'] and question['images']
            if owned == 'u002' and case != 'literal':
                assert any(o['purpose'] == 'T' and 'No items' in o['stderr'] for o in observations)
        raw, thread = invoke(question, started)
        if kind == 'DETAIL':
            payload = json.loads(raw)
            for e in payload['answer'].get('edits', []):
                if e['kind'] == 'check' and e['typedBody']['purpose'].startswith('Observe actual inventory'):
                    e['typedBody']['requirements'] += [r for r, c in facts['Contracts'].items()
                                                       if c['text'] == 'The backend supplies the inventory status.']
            raw = json.dumps(payload)
        return raw, thread

    provider.invoke = answer
    try:
        while run.store.cursor().state != 'FLOW-JUDGE':
            assert not run.store.cursor().decision, run.store.records('unit_failure')
            run.step()
        before = run.store.units()
        baseline_red = {u: {run.store.read(r)['commit'] for r in row.observations
                           if run.store.read(r)['purpose'] in ('T', 'wave-start')
                           and run.store.read(r)['exitCode'] != 0} for u, row in before.items()}
        assert baseline_red['u002']
        assert run.step() == 'EXECUTE'
        expected = {'u001', 'u002'} if case == 'two' else {'u002'}
        assert {u for u in before if run.store.unit(u).state == 'REWORK'} == expected
        finish(run)
        assignments = [b for _, _, b in run.store.records('unit_fixup')]
        assert {b['owner'] for b in assignments} == expected and len(assignments) == len(expected)
        for u in before.keys() - expected:
            assert run.store.unit(u).P == before[u].P
            directory = 'docs' if u == 'guide' else 'unit-' + u[1:]
            assert len((slot / f'launch-{directory}.jsonl').read_text().splitlines()) == 1
        assert not run.store.records('unit_failure')
        assert len(contract_calls) == (1 if case == 'literal' else 0)
        final = [run.store.read(r) for r in run.store.cursor().finalObservations]
        result = json.loads(next(o['stdout'].split('INVENTORY ')[1] for o in final if 'INVENTORY ' in o['stdout']))
        assert result == {'items': 0 if case == 'literal' else 1,
                          'message': 'No inventory yet.' if case == 'literal' else '', 'backend': 'ready'}
        assert all(o['commit'] == run.store.cursor().candidate and o['exitCode'] == 0 for o in final)
        assert repairs
        rounds = len({b['base'] for b in assignments})
        print(f'gapOwner {case}: repair rounds={rounds}; seconds={time.monotonic()-began:.3f}', flush=True)
    finally:
        run.close()
