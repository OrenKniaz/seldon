// C14/C26: browser(url, async ({page, shot, errors}) => ..., {viewports, deadline}).
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const {randomUUID} = require('node:crypto');

async function observe(page) {
  const visits = [];
  const requests = new WeakMap();
  let visit;
  page.on('request', request => {
    if (request.isNavigationRequest() && request.frame() === page.mainFrame() && !request.redirectedFrom()) {
      visit = {url: request.url(), console: [], failedRequests: []};
      visits.push(visit);
    }
    if (visit) requests.set(request, visit);
  });
  page.on('console', message => {
    if (visit && ['error', 'warning'].includes(message.type())) {
      visit.console.push({level: message.type(), text: message.text(), ...message.location()});
    }
  });
  page.on('pageerror', error => visit?.console.push({level: 'pageerror', text: error.message}));
  page.on('response', response => {
    if (response.status() >= 400) {
      requests.get(response.request())?.failedRequests.push({url: response.url(),
        method: response.request().method(), status: response.status()});
    }
  });
  page.on('requestfailed', request => {
    requests.get(request)?.failedRequests.push({url: request.url(), method: request.method(),
      error: request.failure()?.errorText});
  });
  // Chromium's browser-side resource errors (including implicit icons) can bypass Playwright's page events.
  const session = await page.context().newCDPSession(page);
  session.on('Log.entryAdded', ({entry}) => {
    if (!visit || entry.source !== 'network' || !['error', 'warning'].includes(entry.level)) return;
    visit.console.push({level: entry.level, text: entry.text, url: entry.url});
    const status = Number(entry.text.match(/status of (\d{3})\b/)?.[1]);
    if (status >= 400 && !visit.failedRequests.some(row => row.url === entry.url && row.status === status)) {
      visit.failedRequests.push({url: entry.url, status});
    }
  });
  await session.send('Log.enable');
  return visits;
}

module.exports = async function browser(url, check, {viewports = [{width: 1280, height: 800}],
                                                       deadline = 30000, silence} = {}) {
  silence = Number(process.env.SELDON_WALK_SILENCE_MS) || silence;
  const links = path.join(process.env.LOCALAPPDATA || os.homedir(), 'ms-playwright', '.links');
  const roots = ['playwright', 'playwright-core'].flatMap(name =>
    require.resolve.paths(name).map(root => path.join(root, name)));
  if (fs.existsSync(links)) roots.push(...fs.readdirSync(links).sort().map(name =>
    fs.readFileSync(path.join(links, name), 'utf8').trim()));
  const root = roots.find(root => fs.existsSync(path.join(root, 'package.json')));
  if (!root) throw new Error('Install Playwright locally before running browser checks');
  let instance, timer, failed = false, finished = false, rejectQuiet, lastSignal = 0;
  let operation = 'starting the browser', page;
  let visits = [];
  const errors = [], tracked = new WeakSet();
  const stalled = new Promise((_, reject) => { rejectQuiet = reject; });
  const signal = () => {
    if (!silence || finished) return;
    clearTimeout(timer);
    timer = setTimeout(() => {
      const pageError = visits.at(-1)?.console.findLast(entry => entry.level === 'pageerror');
      const detail = pageError ? `; last page error: ${pageError.text}` : '';
      rejectQuiet(new Error(`Browser silent for ${silence} ms while ${operation}; page: ${page?.url() || url}${detail}`));
    }, silence);
    if (process.env.SELDON_WALK_ACTIVITY && Date.now() - lastSignal >= 250) {
      lastSignal = Date.now();
      fs.writeFileSync(process.env.SELDON_WALK_ACTIVITY, '');
    }
  };
  try {
    signal();
    const launch = require(root).chromium.launch({channel: 'msedge', headless: true,
      timeout: silence || deadline});
    if (silence) launch.then(opened => { if (failed) return opened.close(); }).catch(() => {});
    instance = silence ? await Promise.race([launch, stalled]) : await launch;
    const track = view => {
      if (tracked.has(view)) return;
      tracked.add(view);
      view.setDefaultTimeout(silence ? 0 : deadline);
      view.setDefaultNavigationTimeout(silence ? 0 : deadline);
      for (const event of ['request', 'response', 'requestfailed', 'framenavigated', 'console',
        'pageerror', 'dialog', 'download', 'domcontentloaded', 'load']) view.on(event, signal);
      view.on('pageerror', error => errors.push(error.message));
      view.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    };
    // Playwright exposes extra roles as separate contexts. Track every page as soon as it opens.
    if (silence) {
      const create = instance.newContext.bind(instance);
      instance.newContext = async (...args) => {
        const context = await create(...args);
        context.on('page', track);
        return context;
      };
    }
    const proof = async () => {
      page = await instance.newPage({viewport: viewports[0]});
      track(page);
      visits = await observe(page);
      signal();
      operation = `navigating to ${url}`;
      await page.goto(url);
      operation = 'running the check callback';
      return check({page, shot, errors});
    };
    const shot = async (name, view = page) => {
      const folder = process.env.SELDON_EVIDENCE_DIR;
      fs.mkdirSync(folder, {recursive: true});
      const original = view.viewportSize();
      for (const viewport of silence && original ? [original] : viewports) {
        await view.setViewportSize(viewport);
        await view.screenshot({path: path.join(folder, `${name}-${viewport.width}.png`), fullPage: !silence});
        signal();
      }
      if (original) await view.setViewportSize(original);
    };
    if (silence) return await Promise.race([proof(), stalled]);
    const deadlineReached = new Promise((_, reject) => {
      timer = setTimeout(() => {
        const pageError = visits.at(-1)?.console.findLast(entry => entry.level === 'pageerror');
        const detail = pageError ? `; last page error: ${pageError.text}` : '';
        const state = `page: ${page?.url() || url}${detail}`;
        reject(new Error(`Browser deadline after ${deadline} ms while ${operation}; ${state}`));
      }, deadline);
    });
    return await Promise.race([proof(), deadlineReached]);
  } catch (error) {
    failed = true;
    throw error;
  } finally {
    finished = true;
    clearTimeout(timer);
    try {
      await (instance?.close() || Promise.resolve()).finally(() => {
        const folder = process.env.SELDON_EVIDENCE_DIR;
        if (folder && visits.length) {
          fs.mkdirSync(folder, {recursive: true});
          fs.writeFileSync(path.join(folder, `browser-visits-${randomUUID()}.json`), JSON.stringify(visits));
        }
      });
    } catch (error) {
      if (!failed) throw error;
      console.error('Browser cleanup or evidence write failed:', error);
    }
  }
};
