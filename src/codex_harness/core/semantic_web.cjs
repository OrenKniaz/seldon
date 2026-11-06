// Trusted adapter: scenario data never becomes JavaScript or launch options.
'use strict'; const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto'); const {pathToFileURL, fileURLToPath} = require('node:url');
const preview = process.argv[2] === '--preview'; const cfg = preview ? {tools: {playwright: process.argv[3], chromium: process.argv[4], evidence: process.argv[7]}, root: path.resolve(process.argv[5] || '.'), binding: JSON.parse(process.argv[6] || '{}'), seconds: 30, remaining: 30} :
  JSON.parse(fs.readFileSync(process.argv[2], 'utf8')); const {journey: j, tools, root, binding = {}} = cfg;
const end = Date.now() + cfg.remaining * 1000; const remaining = () => { const ms = Math.min(cfg.seconds * 1000, end - Date.now());
  if (ms <= 0) throw Error('Web scenario timeout'); return ms; };
async function openBrowser() {
  let browser;
  for (const browserMode of tools.browserMode === 'single-process' ? ['single-process'] : ['normal', 'single-process']) {
    try {
      browser = await require(tools.playwright).chromium.launch({executablePath: tools.chromium, headless: true, chromiumSandbox: true, timeout: remaining(), args: browserMode === 'normal' ? [] : ['--single-process']});
      const context = await browser.newContext({viewport: j?.viewport, reducedMotion: j?.reducedMotion, offline: !binding.server, serviceWorkers: 'block', acceptDownloads: false});
      const page = await context.newPage(); await page.goto('about:blank', {timeout: remaining()});
      return {browser, context, page, browserMode};
    } catch (error) { error.browserMode = browserMode;
      if (browser) await browser.close();
      if (browserMode !== 'normal' || !/GPU process exited unexpectedly|Target page, context or browser has been closed/i.test(error.message)) throw error;
    }
  }
}
const {spawn} = require('node:child_process'), net = require('node:net'), {isDeepStrictEqual} = require('node:util');
let server, origin, serverEvidence;
async function request(spec, timeout = remaining(), readiness = false) {
  const url = new URL(spec.path, origin);
  if (url.origin !== origin) throw Error('Use a path on the allocated loopback origin');
  const response = await fetch(url, {method: spec.method || 'GET', redirect: 'manual',
    headers: {'Content-Type': 'application/json'}, body: spec.json === undefined ? undefined : JSON.stringify(spec.json), signal: AbortSignal.timeout(Math.max(1, Math.floor(timeout)))});
  if (readiness) { await response.body?.cancel(); return {response: {status: response.status}}; }
  const parts = []; let size = 0;
  for await (const chunk of response.body) { size += chunk.length; if (size > 16384) throw Error('HTTP response exceeds 16KiB; observation unavailable'); parts.push(chunk); }
  const body = Buffer.concat(parts).toString('utf8'); let json;
  try { json = JSON.parse(body); } catch { /* Text responses remain observed text. */ }
  return {request: {...spec, url: url.href}, response: {status: response.status, body, json}};
}
async function startServer() {
  const socket = net.createServer(); await new Promise((resolve, reject) => { socket.once('error', reject); socket.listen(0, '127.0.0.1', resolve); });
  const port = socket.address().port; await new Promise(resolve => socket.close(resolve)); origin = `http://127.0.0.1:${port}`;
  const command = binding.server.argv.map(s => s.replaceAll('{port}', String(port)).replaceAll('{host}', '127.0.0.1'));
  const logPath = path.join(preview ? tools.evidence : root, '.server.log'), log = fs.openSync(logPath, 'w'); let launchError;
  try { server = spawn(command[0], command.slice(1), {cwd: root, env: {...process.env, SELDON_PORT: String(port), SELDON_HOST: '127.0.0.1'}, windowsHide: true, stdio: ['ignore', log, log]}); }
  finally { fs.closeSync(log); }
  server.on('error', e => { launchError = e.message; });
  serverEvidence = {command, port, origin, pid: server.pid, ready: false, stopped: false};
  const until = Math.min(end, Date.now() + binding.server.readyWithin * 1000);
  while (Date.now() < until && server.exitCode === null && !launchError) {
    try { const r = await request({path: binding.server.readyPath}, Math.min(250, until - Date.now()), true);
      if (r.response.status >= 200 && r.response.status < 400) { serverEvidence.ready = true; return; }
    } catch (error) { serverEvidence.lastReadinessError = error.message; }
    await new Promise(resolve => setTimeout(resolve, Math.max(1, Math.min(50, until - Date.now()))));
  }
  throw Error(`Server readiness timeout/unavailable at ${origin}${binding.server.readyPath}; fix server.argv/readyPath (exit=${server.exitCode}, error=${launchError || serverEvidence.lastReadinessError || 'HTTP not ready'}): ${fs.readFileSync(logPath, 'utf8').slice(-1024)}`);
}
async function stopServer() {
  if (!server) return;
  if (server.exitCode === null && server.pid) {
    const exited = new Promise(resolve => server.once('exit', resolve)); server.kill();
    await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 500))]);
  }
  serverEvidence.stopped = server.exitCode !== null || server.signalCode !== null;
}
(async () => {
  let browser, browserMode; const toolchain = {identity: tools.identity, node: process.version,
    playwright: cfg.http ? null : require(path.join(tools.playwright, 'package.json')).version, chromium: null};
  let result;
  try {
    if (binding.server) await startServer();
    const opened = cfg.http ? {} : await openBrowser();
    ({browser, browserMode} = opened); const {context, page} = opened;
    if (preview) {
      await page.goto(origin ? new URL(binding.entry || '/', origin).href : pathToFileURL(path.join(root, 'index.html')).href);
      process.stdout.write(JSON.stringify({browserMode, userAgent: await page.evaluate(() => navigator.userAgent), url: page.url(), server: serverEvidence}) + '\n');
      return;
    }
    toolchain.chromium = browser?.version(); const requests = [], errors = [], steps = [], artifacts = [];
    const checks = {noHorizontalOverflow: true, consoleErrors: 0, externalRequests: 0, textContrastAA: null, headingStructure: null, reducedMotionRespected: null};
    const allowed = url => { if (origin) return new URL(url.replace(/^ws/, 'http')).origin === origin; try { const f = fileURLToPath(url), rel = path.relative(root, f);
      return !rel.startsWith('..') && !path.isAbsolute(rel) && !rel.split(path.sep).some(p => p.startsWith('.')) && fs.statSync(f).isFile(); } catch { return false; } };
    if (context) { context.on('request', r => { if (!allowed(r.url())) requests.push(r.url().slice(0, 160)); });
    await context.routeWebSocket('**/*', ws => { if (origin && allowed(ws.url())) ws.connectToServer(); else { requests.push(ws.url().slice(0, 160)); ws.close(); } });
    page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text().slice(0, 160)); });
    page.on('pageerror', err => errors.push(err.message.slice(0, 160)));
    page.on('popup', p => p.close()); await context.route('**/*', r => allowed(r.request().url()) &&
      (!r.request().isNavigationRequest() || r.request().frame() === page.mainFrame()) ? r.continue() : r.abort());
    }
    const locator = spec => spec.text ? page.getByText(spec.text, {exact: true}) :
      spec.selector ? page.locator('css=' + spec.selector) : page.getByRole(spec.role, {name: spec.name, exact: true});
    const http = [];
    for (const step of (cfg.http ? [{op: 'request', request: cfg.http}] : j.steps)) {
      page?.setDefaultTimeout(remaining()); page?.setDefaultNavigationTimeout(remaining()); let passed = true;
      if (step.op === 'request') {
        const observed = await request(step.request); http.push(observed);
        passed = observed.response.status === step.request.statusEquals &&
          (!Object.hasOwn(step.request, 'jsonEquals') || isDeepStrictEqual(observed.response.json, step.request.jsonEquals));
      }
      else if (step.op === 'open') await page.goto(origin ? new URL(cfg.entry, origin).href : pathToFileURL(path.join(root, cfg.entry)).href, {waitUntil: 'load'});
      else if (step.op === 'key') await page.keyboard.press(step.key);
      else if (step.op === 'assertFragment') passed = new URL(page.url()).hash === step.fragment;
      else {
        const loc = locator(step.locator), count = await loc.count();
        if (count > 1) passed = false;
        else if (step.op === 'assertVisible' || step.op === 'assertHidden') {
          try { await loc.waitFor({state: step.op === 'assertVisible' ? 'visible' : 'hidden', timeout: Math.min(1000, remaining())}); }
          catch (error) { if (error.name !== 'TimeoutError') throw error; passed = false; }
        }
        else if (step.op === 'fill' && count === 1) await loc.fill(step.value, {timeout: remaining()});
        else if (step.op === 'focus' && count === 1) await loc.focus({timeout: remaining()});
        else if (step.op === 'assertFocus') passed = count === 1 && await loc.evaluate(e => e === e.ownerDocument.activeElement);
        else if (count === 1 && await loc.isVisible()) await loc.click({timeout: remaining()});
        else passed = false;
      }
      steps.push({check: step.op, passed, actual: passed, reason: passed ? 'rendered state matched' : 'rendered state mismatch'}); if (page) checks.noHorizontalOverflow &&= await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= innerWidth);
      if (!passed) break;
    }
    // One bounded final rendered snapshot complements action observations and PNGs for 1j.
    const rendered = page ? await page.evaluate(() => ({text: document.body.innerText.slice(0,4096), textOmitted: document.body.innerText.length > 4096, styles: [...document.querySelectorAll('body *')].filter(e => e.getClientRects().length).slice(0,16).map(e => {const s = getComputedStyle(e); return {tag:e.tagName, color:s.color, background:s.backgroundColor, backgroundImage:s.backgroundImage, fontSize:s.fontSize, animations:e.getAnimations().length};})})) : null;
    for (const fullPage of (page ? [false, true] : [])) {
      try {
        if (await page.evaluate(() => document.documentElement.scrollHeight) > 12000) throw Error('PNG height exceeds bound');
        const bytes = await page.screenshot({fullPage, timeout: remaining()});
        if (bytes.length > 2097152) throw Error('PNG exceeds 2MiB');
        const sha256 = crypto.createHash('sha256').update(bytes).digest('hex'); const file = path.join(tools.evidence, sha256 + '.png'); fs.writeFileSync(file, bytes);
        artifacts.push({path: file, sha256, viewport: j.viewport, reducedMotion: j.reducedMotion, fullPage});
      } catch (e) { artifacts.push({missing: true, reason: e.message.slice(0, 160), fullPage}); }
    }
    checks.consoleErrors = errors.length; checks.externalRequests = requests.length;
    result = {state: 'OBSERVED', browserMode, checks: cfg.http ? {} : checks, steps, artifacts, rendered,
      toolchain, url: page?.url(), http, server: serverEvidence, errors: errors.slice(0, 4), requests: requests.slice(0, 4)};
  } catch (error) {
    result = {state: 'ERROR', reason: error.message.slice(0, 1500), browserMode: error.browserMode ?? browserMode, toolchain, server: serverEvidence};
  } finally { try { if (browser) await browser.close(); } finally { await stopServer(); } }
  process.stdout.write(JSON.stringify(result));
})().catch(error => { process.stderr.write(error.message); process.exitCode = 1; });
