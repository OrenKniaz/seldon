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
                                                       deadline = 30000} = {}) {
  const links = path.join(process.env.LOCALAPPDATA || os.homedir(), 'ms-playwright', '.links');
  const roots = ['playwright', 'playwright-core'].flatMap(name =>
    require.resolve.paths(name).map(root => path.join(root, name)));
  if (fs.existsSync(links)) roots.push(...fs.readdirSync(links).sort().map(name =>
    fs.readFileSync(path.join(links, name), 'utf8').trim()));
  const root = roots.find(root => fs.existsSync(path.join(root, 'package.json')));
  if (!root) throw new Error('Install Playwright locally before running browser checks');
  const instance = await require(root).chromium.launch(
    {channel: 'msedge', headless: true, timeout: deadline});
  let timer, visits;
  try {
    const page = await instance.newPage({viewport: viewports[0]}), errors = [];
    visits = await observe(page);
    page.setDefaultTimeout(deadline);
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    const shot = async name => {
      const folder = process.env.SELDON_EVIDENCE_DIR;
      fs.mkdirSync(folder, {recursive: true});
      for (const viewport of viewports) {
        await page.setViewportSize(viewport);
        await page.screenshot({path: path.join(folder, `${name}-${viewport.width}.png`), fullPage: true});
      }
      await page.setViewportSize(viewports[0]);
    };
    return await Promise.race([(async () => { await page.goto(url); return check({page, shot, errors}); })(),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('Browser deadline')), deadline); })]);
  } finally {
    clearTimeout(timer);
    try { await instance.close(); }
    finally {
      const folder = process.env.SELDON_EVIDENCE_DIR;
      if (folder && visits?.length) {
        fs.mkdirSync(folder, {recursive: true});
        fs.writeFileSync(path.join(folder, `browser-visits-${randomUUID()}.json`), JSON.stringify(visits));
      }
    }
  }
};
