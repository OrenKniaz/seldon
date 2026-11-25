// C14/C26: serve(argv, async ({url, port, dataDir, pid}) => ..., {ready, deadline}).
// argv substitutes {port}/{data}; the product also receives PORT and SELDON_DATA_DIR.
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {randomUUID} = require('node:crypto');
const net = require('node:net'), {spawn, spawnSync} = require('node:child_process');
const {once} = require('node:events'), {setTimeout: sleep} = require('node:timers/promises');
async function serve(argv, check, {ready = '/', deadline = 30000, preview = false} = {}) {
  const silence = Number(process.env.SELDON_WALK_SILENCE_MS);
  const evidence = process.env.SELDON_EVIDENCE_DIR;
  const socket = net.createServer();
  socket.listen(0, '127.0.0.1'); await once(socket, 'listening');
  const port = socket.address().port; await new Promise(resolve => socket.close(resolve));
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'seldon-serve-'));
  const args = argv.map(arg => arg.replaceAll('{port}', String(port)).replaceAll('{data}', dataDir));
  const child = spawn(args[0], args.slice(1), {detached: process.platform !== 'win32',
    windowsHide: true, stdio: silence ? ['ignore', 'pipe', 'pipe'] : ['ignore', 'inherit', 'inherit'],
    env: {...process.env, HOST: '127.0.0.1', PORT: String(port), SELDON_DATA_DIR: dataDir}});
  const ended = new Promise(resolve => child.once('close', resolve)), url = `http://127.0.0.1:${port}`;
  let failure; child.on('error', error => { failure = error; });
  try {
    let until = Date.now() + (silence || deadline);
    if (silence) for (const stream of [child.stdout, child.stderr]) stream.on('data', data => {
      until = Date.now() + silence;
      process.stderr.write(data);
      if (process.env.SELDON_WALK_ACTIVITY) fs.writeFileSync(process.env.SELDON_WALK_ACTIVITY, '');
    });
    for (;;) {
      if (failure) throw failure;
      if (child.exitCode !== null) throw new Error(`Product exited: ${child.exitCode}`);
      if (Date.now() >= until) throw new Error(silence ? 'App startup went quiet' : 'Server readiness deadline');
      try {
        const signal = AbortSignal.timeout(Math.min(500, Math.max(1, until - Date.now())));
        const response = await fetch(url + ready, {signal});
        if (response.ok) break;
        await response.body?.cancel();
      }
      catch (error) { if (error.name !== 'TypeError' && error.name !== 'TimeoutError') throw error; }
      await sleep(50);
    }
    if (preview && evidence) {
      fs.mkdirSync(evidence, {recursive: true});
      fs.writeFileSync(path.join(evidence, 'preview-launch.json'), JSON.stringify({argv, ready}));
    }
    return await check({url, port, dataDir, pid: child.pid});
  } finally {
    if (child.pid) {
      if (process.platform === 'win32') {
        const killed = spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {windowsHide: true});
        if (killed.status !== 0 && child.exitCode === null) throw new Error('Server tree cleanup failed');
      } else {
        try { process.kill(-child.pid, 'SIGKILL'); }
        catch (error) { if (error.code !== 'ESRCH') throw error; }
      }
      await ended;
      try { process.kill(child.pid, 0); throw new Error('Server still alive'); }
      catch (error) { if (error.code !== 'ESRCH') throw error; }
    }
    const cleanup = {directory: path.basename(dataDir), removed: true};
    try { fs.rmSync(dataDir, {recursive: true, force: true}); }
    catch (error) {
      if (!evidence || !error.code) throw error;
      cleanup.removed = false;
      cleanup.error = error.code;
    }
    if (evidence) {
      fs.writeFileSync(path.join(evidence, `server-cleanup-${randomUUID()}.json`), JSON.stringify([cleanup]));
    }
  }
}

module.exports = serve;

if (require.main === module) {
  const [launch, status] = process.argv.slice(2);
  const {argv, ready} = JSON.parse(launch);
  const closed = once(process.stdin, 'end');
  process.stdin.resume();
  serve(argv, async ({url}) => {
    fs.writeFileSync(status, url);
    await closed;
  }, {ready}).catch(error => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
