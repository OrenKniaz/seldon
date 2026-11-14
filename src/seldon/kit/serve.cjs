// C14/C26: serve(argv, async ({url, port, dataDir, pid}) => ..., {ready, deadline}).
// argv substitutes {port}/{data}; the product also receives PORT and SELDON_DATA_DIR.
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const net = require('node:net'), {spawn, spawnSync} = require('node:child_process');
const {once} = require('node:events'), {setTimeout: sleep} = require('node:timers/promises');
module.exports = async function serve(argv, check, {ready = '/', deadline = 30000} = {}) {
  const socket = net.createServer();
  socket.listen(0, '127.0.0.1'); await once(socket, 'listening');
  const port = socket.address().port; await new Promise(resolve => socket.close(resolve));
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'seldon-serve-'));
  const args = argv.map(arg => arg.replaceAll('{port}', String(port)).replaceAll('{data}', dataDir));
  const child = spawn(args[0], args.slice(1), {detached: process.platform !== 'win32',
    windowsHide: true, stdio: 'inherit',
    env: {...process.env, PORT: String(port), SELDON_DATA_DIR: dataDir}});
  const ended = new Promise(resolve => child.once('close', resolve)), url = `http://127.0.0.1:${port}`;
  let failure; child.on('error', error => { failure = error; });
  try {
    const until = Date.now() + deadline;
    for (;;) {
      if (failure) throw failure;
      if (child.exitCode !== null) throw new Error(`Product exited: ${child.exitCode}`);
      if (Date.now() >= until) throw new Error('Server readiness deadline');
      try {
        const signal = AbortSignal.timeout(Math.max(1, until - Date.now()));
        if ((await fetch(url + ready, {signal})).ok) break;
      }
      catch (error) { if (error.name !== 'TypeError' && error.name !== 'TimeoutError') throw error; }
      await sleep(50);
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
    fs.rmSync(dataDir, {recursive: true, force: true});
  }
};
