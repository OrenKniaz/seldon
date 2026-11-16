"""C14/C26: admitted mechanics support workers proving real products through C."""

import json
import os
import re
import sys
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
    Declared guide checks must supply final execution and source to PRODUCT.
    Value-only journeys miss server lifecycle failures; the broken twin protects owner repair.
    """
    run, provider = prepare(slot, 'kit', 1)
    invoke = provider.invoke
    judged = []

    def require_walkthrough(question, started):
        if question['kind'] == 'PRODUCT' and question['targets'] == ['tryItGuide']:
            facts = question['facts']
            observations = facts['Final observations']
            assert len(observations) == 1
            observation = observations[0]
            assert observation['command'] == [sys.executable, '-B', 'docs/try-it/check.py']
            assert observation['exitCode'] == 0 and observation['ranOnDeliveredRevision']
            assert observation['executionStarted'] and observation['cleanupVerified']
            assert 'parent, descendant, port and data cleaned' in observation['stdout']
            assert facts['Check artifacts']
            assert set(facts['Checks']) == {'guide:check:1'}
            assert facts['Walkthrough check sources']['docs/try-it/check.py'].replace('\r\n', '\n') == guide_source
            assert facts['Guide file']['text'].replace('\r\n', '\n') == guide_text.strip()
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
    native.write_text(native.read_text('utf-8').replace(
        "    Path(name, 'check.py').write_text(source)",
        worker + "    Path(name, 'check.py').write_text(source)"), encoding='utf-8')
    native.write_text(native.read_text().replace(
        "answer['checkPaths'] = [name+'/check.py']",
        "answer['checkPaths'] = [name+'/check.py',name+'/check.cjs','checks/kit/serve.cjs',"
        "'checks/kit/browser.cjs']").replace(
        "'command':[sys.executable,'-B',name+'/check.py']",
        "'command':[sys.executable,'-B','-m',name+'.check']"))
    guide_text = "Start the server, read the API value-001, then stop the server.\n"
    if surface == 'server':
        guide_text += '```json\n["python", "unit-001/server.py"]\n```\nLeave it running while using the product.\n'
        server = ("from http.server import HTTPServer, SimpleHTTPRequestHandler\n"
                  "HTTPServer(('127.0.0.1', 0), SimpleHTTPRequestHandler).serve_forever()\n")
        native.write_text(native.read_text().replace(
            '    Path(name, "server.cjs").write_text(',
            '    Path(name, "server.py").write_text(' + repr(server) + ')\n'
            '    Path(name, "server.cjs").write_text('))
    guide_source = wrapper
    if surface == 'broken-guide':
        guide_source = ("import os\nfrom pathlib import Path\n"
                        "assert not Path(os.environ['SELDON_TEST_GUIDE_FAULT']).exists(), "
                        "'broken guide walkthrough'\n") + wrapper
    text = native.read_text()
    start = text.index("    Path(facts['Guide path']).write_text(")
    end = text.index("    answer['checkPaths']", start)
    native.write_text(text[:start] + "    Path(facts['Guide path']).write_text(" + repr(guide_text) + ")\n"
        + "    Path('docs/try-it/check.py').write_text(" + repr(guide_source) + ")\n" + text[end:])
    try:
        if surface == 'broken-guide':
            while run.store.cursor().state != 'WAVE-VERIFY':
                run.step()
            Path(os.environ['SELDON_TEST_GUIDE_FAULT']).touch()
            assert run.step() == 'EXECUTE'
            gaps = [b for _, _, b in run.store.records('unit_fixup')]
            assert len(gaps) == 1 and gaps[0]['owner'] == 'guide'
            assert 'broken guide walkthrough' in str(gaps)
            assert gaps[0]['gaps'][0]['subject'] == 'guide:check:1'
            assert not judged and not run.store.cursor().publication
            return
        cursor, rows, units = finish(run)
        assert judged
        assert units['u001'].recoveries == 0
        assert not any(b['gate'] == 'UNIT' for _, _, b in run.store.records('decision_request'))
        guide = [b for _, _, b in run.store.records('observation') if b['unit'] == 'guide']
        assert guide and all(b['exitCode'] == 0 for b in guide if b['purpose'] in ('P', 'final'))
        assert any(b['purpose'] == 'final' for b in guide)
        assert units['guide'].recoveries == 0
        assert not any(b['owner'] == 'guide' for _, _, b in run.store.records('unit_fixup'))
        observations = [b for _, kind, b in rows if kind == 'observation' and b['check'] == 'u001:check:1']
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
