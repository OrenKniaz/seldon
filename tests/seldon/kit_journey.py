"""C14/C26: admitted mechanics support workers proving real products through C."""

import json
import re
from pathlib import Path

from seldon import proof
from test_journey import finish, prepare

PRODUCT = r'''
const http = require('node:http'), {spawn} = require('node:child_process'), fs = require('node:fs');
const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {stdio: 'ignore'});
fs.writeFileSync(process.env.SELDON_DATA_DIR + '/value', 'value-001');
http.createServer((req, res) => {
  res.setHeader('Content-Type', req.url === '/api' ? 'application/json' : 'text/html');
  res.end(req.url === '/api' ? JSON.stringify({value: fs.readFileSync(
    process.env.SELDON_DATA_DIR + '/value', 'utf8'), child: child.pid}) :
    '<button onclick="this.textContent=Number(this.textContent)+1">0</button>');
}).listen(Number(process.env.PORT), '127.0.0.1');
'''

CHECK = r'''
const assert = require('node:assert/strict'), fs = require('node:fs');
const serve = require('../checks/kit/serve.cjs'), browser = require('../checks/kit/browser.cjs');
(async () => {
  let state, descendant, opened;
  const run = () => serve([process.execPath, 'unit-001/server.cjs'], async server => {
    state = server;
    const response = await (await fetch(server.url + '/api')).json();
    descendant = response.child;
    assert.equal(response.value, 'value-001');
    if (MODE === 'browser') {
      await browser(server.url, async ({page, shot, errors}) => {
        opened = page;
        await page.getByRole('button', {name: '0', exact: true}).click();
        assert.equal(await page.getByRole('button').textContent(), '1');
        await page.evaluate(() => console.error('captured product console'));
        await shot('clicked');
        assert.deepEqual(errors, ['captured product console']);
        console.log('real Edge interaction and screenshots');
      }, {viewports: [{width: 1280, height: 800}, {width: 390, height: 844}]});
      assert.equal(opened.isClosed(), true);
    } else throw new Error('worker assertion failed');
  });
  if (MODE === 'server') await assert.rejects(run(), /worker assertion failed/);
  else await run();
  assert.equal(fs.existsSync(state.dataDir), false);
  for (const pid of [state.pid, descendant]) assert.throws(() => process.kill(pid, 0), {code: 'ESRCH'});
  await assert.rejects(fetch(state.url), TypeError);
  console.log('real server value-001; parent, descendant, port and data cleaned');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''


def kit_journey(slot, surface):
    """Catch missing baseline kit, machine-bound delivery and leaked browser/server trees.

    Existing value-file journeys never import delivered helpers or start real Edge and a server.
    A JSON-fenced guide must supply actual execution to PRODUCT; argv-only journeys miss this trace bug.
    The broken twin protects real failure routing instead of accepting path existence.
    """
    run, provider = prepare(slot, 'kit', 1)
    original = provider.__class__.__call__

    class KitProvider(provider.__class__):
        def __call__(self, question):
            answer = json.loads(original(self, question))
            if question['kind'] == 'DETAIL' and answer['answer'].get('verdict') == 'planned':
                for unit in answer['answer']['units']:
                    unit['checkPaths'].append('unit-001/check.cjs')
            return json.dumps(answer)

    provider.__class__ = KitProvider
    invoke = provider.invoke
    judged = []

    def require_walkthrough(question, started):
        if question['kind'] == 'PRODUCT' and question['targets'] == ['tryItGuide']:
            facts = question['facts']
            observations = facts['Final observations']
            assert len(observations) == 1
            observation = observations[0]
            assert observation['command'] == ['python', '-S', '-B', 'unit-001/check.py']
            assert observation['exitCode'] == 0 and observation['ranOnDeliveredRevision']
            assert observation['executionStarted'] and observation['cleanupVerified']
            assert 'parent, descendant, port and data cleaned' in observation['stdout']
            assert facts['Check artifacts']
            assert 'guide:paths' not in facts['Checks']
            if surface == 'browser':
                assert len(question['images']) == 2
            judged.append(question)
        return invoke(question, started)

    provider.invoke = require_walkthrough
    native = slot / 'native.py'
    source = CHECK.replace('MODE', json.dumps('server' if surface == 'broken-guide' else surface))
    wrapper = ("import os, subprocess, sys\nfrom pathlib import Path\n"
               "result = subprocess.run(['node', 'unit-001/check.cjs'], capture_output=True, text=True)\n"
               "print(result.stdout, end=''); print(result.stderr, end='', file=sys.stderr)\n"
               "result.check_returncode()\n"
               "Path(os.environ['SELDON_EVIDENCE_DIR'], 'walkthrough.txt').write_text(result.stdout)\n"
               "print('observed value-001')\n")
    worker = ('    Path(name, "server.cjs").write_text(' + repr(PRODUCT) + ')\n'
              '    Path(name, "check.cjs").write_text(' + repr(source) + ')\n'
              '    source = ' + repr(wrapper) + '\n')
    native.write_text(native.read_text('utf-8').replace('```argv', '```json').replace(
        '    path.write_text("Try it yourself',
        '    command = ["python", "-S", "-B", "unit-001/check.py"]\n'
        '    path.write_text("Try it yourself').replace(
        'command = [sys.executable,', 'command = ["python",').replace(
        '    Path(name, "check.py").write_text(source)',
        worker + '    Path(name, "check.py").write_text(source)'), encoding='utf-8')
    if surface == 'broken-guide':
        native.write_text(native.read_text().replace(
            'command = ["python", "-S", "-B", "unit-001/check.py"]',
            "command = ['python', '-B', '-c', \"raise AssertionError('broken guide walkthrough')\"]"))
    try:
        if surface == 'broken-guide':
            while run.store.cursor().state != 'WAVE-VERIFY':
                run.step()
            assert run.step() == 'EXECUTE'
            gaps = [b for _, _, b in run.store.records('unit_fixup')]
            assert gaps and 'broken guide walkthrough' in str(gaps)
            assert not judged and not run.store.cursor().publication
            return
        cursor, rows, units = finish(run)
        assert judged
        assert units['u001'].recoveries == 0
        observations = [b for _, kind, b in rows if kind == 'observation' and b['check'] == 'n003']
        red = [b for b in observations if b['purpose'] == 'T']
        assert red and all(b['exitCode'] != 0 and 'Product exited' in b['stderr'] for b in red)
        green = [b for b in observations if b['purpose'] in ('P', 'final')]
        assert green and not proof.failures(run.store, [r for r, kind, b in rows
            if kind == 'observation' and b in green])
        assert all('parent, descendant, port and data cleaned' in b['stdout'] for b in green)
        if surface == 'browser':
            assert all(sum(Path(ref).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
                           for ref in b['evidenceFiles']) == 2 for b in green)
        assert cursor.finalCommit == cursor.candidate and not cursor.failure
        delivered = [p for p in run.project.rglob('*') if p.is_file()
                     and '.git' not in p.parts and '.seldon' not in p.parts]
        for path in delivered:
            assert not re.search(rb'(?<![A-Za-z])[A-Za-z]:[\\/]|/(?:Users|home|usr|opt)/',
                                 path.read_bytes()), path
        written = sum(p.stat().st_size for p in (run.project / 'unit-001').glob('check.*'))
        print(f'{surface} worker-written check bytes: before=unmeasured '
              f'(no retained equivalent); after={written}')
    finally:
        run.close()
