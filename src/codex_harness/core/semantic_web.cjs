// Trusted adapter: scenario data never becomes JavaScript or launch options.
'use strict';
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const {pathToFileURL, fileURLToPath} = require('node:url');
const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const {journey: j, tools, root} = cfg;
const {chromium} = require(tools.playwright);
const end = Date.now() + cfg.remaining * 1000;
const remaining = () => { const ms = Math.min(cfg.seconds * 1000, end - Date.now());
  if (ms <= 0) throw Error('Web scenario timeout'); return ms; };

// Runs only adapter-owned code in the page. Unmeasurable contrast is advisory.
function inspect() {
  const visible = e => (e.getClientRects().length || [...e.childNodes].some(n => {
    if (n.nodeType !== 3 || !n.textContent.trim()) return false;
    const range = document.createRange(); range.selectNodeContents(n); return range.getClientRects().length;
  })) && [...ancestors(e)].every(n => {
    const s = getComputedStyle(n); return s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0;
  });
  function* ancestors(e) { for (; e; e = e.parentElement) yield e; }
  const all = [...document.querySelectorAll('*')].filter(visible);
  if (all.some(e => e.shadowRoot || /^(IFRAME|OBJECT|EMBED)$/.test(e.tagName)))
    throw Error('Uninspectable embedded/shadow content');
  const headings = all.filter(e => /^H[1-6]$/.test(e.tagName)).map(e => +e.tagName[1]);
  const headingStructure = headings.filter(h => h === 1).length === 1 && headings[0] === 1 &&
    headings.every((h, i) => !i || h <= headings[i - 1] + 1);
  function rgba(s) {
    const m = s.match(/^rgba?\(([^)]+)\)$/);
    if (!m) throw Error('Unresolved text/background colour: ' + s);
    const v = m[1].split(/[, /]+/).map(Number);
    return [v[0] / 255, v[1] / 255, v[2] / 255, v[3] ?? 1];
  }
  const over = (a, b) => { const alpha = a[3] + b[3] * (1 - a[3]);
    return [0, 1, 2].map(i => alpha ? (a[i] * a[3] + b[i] * b[3] * (1 - a[3])) / alpha : 0).concat(alpha); };
  const lum = c => c.slice(0, 3).map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4)
    .reduce((v, x, i) => v + x * [.2126, .7152, .0722][i], 0);
  let textContrastAA = true;
  const advisories = [];
  const decorative = e => [...ancestors(e)].some(n => n.getAttribute('aria-hidden') === 'true');
  function contrast(e, style, pseudo = '') {
    try {
      let fg = rgba(style.color), bg = [0, 0, 0, 0];
      const layers = [...ancestors(e)].map(n => getComputedStyle(n));
      if (pseudo) layers.unshift(style);
      // Effects on an ancestor still apply even behind an opaque child background.
      if (layers.some(s => s.mixBlendMode !== 'normal' || s.filter !== 'none' ||
          s.backdropFilter !== 'none' || s.textShadow !== 'none' || +s.opacity !== 1))
        throw Error('Unresolved blend/filter/shadow/opacity text contrast');
      for (const s of layers) {
        if (s.backgroundImage !== 'none') throw Error('Unresolved image/gradient text contrast');
        const color = rgba(s.backgroundColor);
        fg = over(fg, color); bg = over(bg, color);
        if (bg[3] === 1) break;
      }
      fg = over(fg, [1, 1, 1, 1]); bg = over(bg, [1, 1, 1, 1]);
      const l = [lum(fg), lum(bg)].sort((a, b) => a - b);
      const large = parseFloat(style.fontSize) >= 24 || parseFloat(style.fontSize) >= 18.67 && +style.fontWeight >= 700;
      if ((l[1] + .05) / (l[0] + .05) < (large ? 3 : 4.5)) textContrastAA = false;
    } catch (error) {
      advisories.push({severity: 'ADVISORY', check: 'textContrastAA',
        element: e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + pseudo,
        reason: error.message + '; manual contrast review required'});
    }
  }
  for (const e of all) {
    if (/^(SCRIPT|STYLE|NOSCRIPT|TITLE)$/.test(e.tagName) || decorative(e)) continue;
    for (const pseudo of ['::before', '::after']) {
      const style = getComputedStyle(e, pseudo), content = style.content;
      if (style.display === 'none' || style.visibility === 'hidden' || +style.opacity === 0 ||
          ['none', 'normal', '""', "''"].includes(content)) continue;
      // Punctuation/symbol-only generated content is decorative (e.g. FAQ +/- icons).
      if (/^(["']).*\1$/.test(content) && !/[\p{L}\p{N}]/u.test(content.slice(1, -1))) continue;
      if (/^(["']).*\1$/.test(content)) contrast(e, style, pseudo);
      else advisories.push({severity: 'ADVISORY', check: 'textContrastAA',
        element: e.tagName.toLowerCase() + pseudo,
        reason: 'Unresolved generated content; manual contrast review required'});
    }
    if ([...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) ||
        /^(INPUT|TEXTAREA)$/.test(e.tagName) && e.value) contrast(e, getComputedStyle(e));
  }
  return {noHorizontalOverflow: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0) <= innerWidth,
    textContrastAA, headingStructure, advisories,
    motion: document.getAnimations().some(a => a.playState === 'running' || a.pending),
    uninspectable: all.some(e => /^(CANVAS|VIDEO)$/.test(e.tagName.toUpperCase()) ||
      e.tagName === 'IMG' && /\.(gif|webp|svg)(?:[?#]|$)/i.test(e.currentSrc)) ||
      !!document.querySelector('animate,animateTransform,animateMotion,set') || globalThis.__seldonMotion,
    properties: all.map(e => { const s = getComputedStyle(e), r = e.getBoundingClientRect();
      return [s.transform, s.opacity, s.backgroundPosition, s.color, s.backgroundColor, r.x, r.y, r.width, r.height]; })};
}

(async () => {
  let browser;
  const toolchain = {identity: tools.identity, node: process.version,
    playwright: require(path.join(tools.playwright, 'package.json')).version, chromium: null};
  try {
    browser = await chromium.launch({executablePath: tools.chromium, headless: true, chromiumSandbox: true, timeout: remaining()});
    toolchain.chromium = browser.version();
    const context = await browser.newContext({viewport: j.viewport, reducedMotion: j.reducedMotion,
      offline: true, serviceWorkers: 'block', acceptDownloads: false});
    const requests = [], errors = [], steps = [], artifacts = [], advisoryNotes = new Map();
    let marker = false, anyMarker = false;
    const checks = {noHorizontalOverflow: true, consoleErrors: 0, externalRequests: 0,
      textContrastAA: true, headingStructure: true, reducedMotionRespected: true};
    const allowed = url => { try { const f = fileURLToPath(url), rel = path.relative(root, f);
      return !rel.startsWith('..') && !path.isAbsolute(rel) && !rel.split(path.sep).some(p => p.startsWith('.')) &&
        fs.statSync(f).isFile(); } catch { return false; } };
    context.on('request', r => { if (!allowed(r.url())) requests.push(r.url().slice(0, 160)); });
    await context.routeWebSocket('**/*', ws => { requests.push(ws.url().slice(0, 160)); ws.close(); });
    await context.exposeBinding('__seldonExternal', () => requests.push('serviceWorker registration'));
    await context.addInitScript(() => {
      globalThis.__seldonMotion = false;
      for (const name of ['requestAnimationFrame', 'setInterval', 'setTimeout']) {
        const original = globalThis[name];
        globalThis[name] = function(...args) { globalThis.__seldonMotion = true; return original.apply(this, args); };
      }
      const register = navigator.serviceWorker?.register.bind(navigator.serviceWorker);
      if (register) navigator.serviceWorker.register = (...args) => {
        globalThis.__seldonExternal(); return Promise.reject(Error('Service workers blocked'));
      };
    });
    const page = await context.newPage();
    page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text().slice(0, 160)); });
    page.on('pageerror', err => errors.push(err.message.slice(0, 160)));
    page.on('popup', p => p.close());
    await context.route('**/*', r => allowed(r.request().url()) &&
      (!r.request().isNavigationRequest() || r.request().frame() === page.mainFrame()) ? r.continue() : r.abort());
    const locator = spec => spec.text ? page.getByText(spec.text, {exact: true}) :
      spec.selector ? page.locator('css=' + spec.selector) : page.getByRole(spec.role, {name: spec.name, exact: true});
    async function checkpoint() {
      const a = await page.evaluate(inspect);
      for (const note of a.advisories) advisoryNotes.set(JSON.stringify(note), note);
      for (const k of ['noHorizontalOverflow', 'textContrastAA', 'headingStructure']) checks[k] &&= a[k];
      const body = await page.locator('body').innerText({timeout: remaining()});
      anyMarker ||= (await page.content()).includes('SELDON-STUB ');
      marker ||= body.split(/\r?\n/).some(line => line.trim() === 'SELDON-STUB ' + j.operation);
      if (j.reducedMotion === 'reduce') {
        if (a.uninspectable) throw Error('Uninspectable JS/canvas/image motion under reduce');
        for (let i = 0; i < 2; i++) {
          await page.waitForTimeout(Math.min(120, remaining()));
          const b = await page.evaluate(inspect);
          checks.reducedMotionRespected &&= !a.motion && !b.motion && JSON.stringify(a.properties) === JSON.stringify(b.properties);
          if (b.uninspectable) throw Error('Uninspectable JS/canvas/image motion under reduce');
        }
      }
    }
    for (const step of j.steps) {
      const watchdog = setTimeout(() => {
        process.stdout.write(JSON.stringify({state: 'ERROR', reason: 'Web action timeout'})); process.exit(0);
      }, remaining());
      try {
      page.setDefaultTimeout(remaining()); page.setDefaultNavigationTimeout(remaining());
      let passed = true;
      if (step.op === 'open') await page.goto(pathToFileURL(path.join(root, 'index.html')).href, {waitUntil: 'load'});
      else if (step.op === 'key') await page.keyboard.press(step.key);
      else if (step.op === 'assertFragment') passed = new URL(page.url()).hash === step.fragment;
      else {
        const loc = locator(step.locator), count = await loc.count();
        if (count > 1) passed = false;
        else if (step.op === 'assertVisible') passed = count === 1 && await loc.isVisible();
        else if (step.op === 'assertFocus') passed = count === 1 && await loc.evaluate(e => e === e.ownerDocument.activeElement);
        else if (count === 1 && await loc.isVisible()) await loc.click({timeout: remaining()});
        else passed = false;
      }
      steps.push({check: step.op, passed, actual: passed, reason: passed ? 'rendered state matched' : 'rendered state mismatch'});
      await checkpoint();
      if (!passed) break; // Interactions following a missing positive assertion stay unperformed.
      } finally { clearTimeout(watchdog); }
    }
    if (cfg.phase === 'validation' && anyMarker) throw Error('Stub marker remains; final Validation cannot pass');
    if (cfg.phase === 'contracts' && (!marker || !steps.some(s => s.check === 'assertVisible' && !s.passed)))
      throw Error('Web RED requires observed operation stub marker and failing positive rendered assertion per journey');
    for (const fullPage of [false, true]) {
      try {
        if (await page.evaluate(() => document.documentElement.scrollHeight) > 12000) throw Error('PNG height exceeds bound');
        const bytes = await page.screenshot({fullPage, timeout: remaining()});
        if (bytes.length > 2097152) throw Error('PNG exceeds 2MiB');
        const sha256 = crypto.createHash('sha256').update(bytes).digest('hex');
        const file = path.join(tools.evidence, sha256 + '.png'); fs.writeFileSync(file, bytes);
        artifacts.push({path: file, sha256, viewport: j.viewport, reducedMotion: j.reducedMotion, fullPage});
      } catch (e) { artifacts.push({missing: true, reason: e.message.slice(0, 160), fullPage}); }
    }
    checks.consoleErrors = errors.length; checks.externalRequests = requests.length;
    const result = {state: 'OBSERVED', marker, checks, steps, artifacts, advisories: [...advisoryNotes.values()],
      toolchain, errors: errors.slice(0, 4), requests: requests.slice(0, 4)};
    await browser.close(); browser = null;
    process.stdout.write(JSON.stringify(result));
  } catch (error) {
    process.stdout.write(JSON.stringify({state: 'ERROR', reason: error.message.slice(0, 1500), toolchain}));
  } finally { if (browser) await browser.close(); }
})().catch(error => { process.stderr.write(error.message); process.exitCode = 1; });
