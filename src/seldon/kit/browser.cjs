// C14/C26: browser(url, async ({page, shot, errors}) => ..., {viewports, deadline}).
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
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
  let timer;
  try {
    const page = await instance.newPage({viewport: viewports[0]}), errors = [];
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
  } finally { clearTimeout(timer); await instance.close(); }
};
