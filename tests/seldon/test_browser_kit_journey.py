"""C14/C26: distinguish a failed browser check from a passing local user path."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

CHECK = r"""
const assert = require('node:assert/strict');
const http = require('node:http');
const {once} = require('node:events');
const browser = require('./src/seldon/kit/browser.cjs');

async function journey() {
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', 'text/html');
    response.end(request.url === '/broken'
      ? `<script>throw new Error('Editor failed to initialize')</script>`
      : `<button onclick="this.textContent='Saved'">Save</button>`);
  }).listen(0, '127.0.0.1');
  try {
    await once(server, 'listening');
    const url = `http://127.0.0.1:${server.address().port}/settings`;
    let instance;
    const run = (check, options) => browser(url, async ({page}) => {
      instance = page.context().browser();
      return check(page);
    }, options);

    assert.equal(await run(async page => {
      await page.getByRole('button', {name: 'Save'}).click();
      return page.getByRole('button').textContent();
    }), 'Saved');
    assert.equal(instance.isConnected(), false);

    await assert.rejects(run(page => page.getByRole('button', {name: 'Publish'}).click({timeout: 500})),
      error => error.name === 'TimeoutError' && error.message.includes('locator.click')
        && error.message.includes('Publish'));
    assert.equal(instance.isConnected(), false);

    const broken = new URL('/broken', url).href;
    await assert.rejects(run(async page => {
      await page.goto(broken);
      await page.getByRole('button', {name: 'Publish'}).click();
    }, {deadline: 10_000}), error => error.message.includes('Browser deadline') && error.message.includes(broken)
      && error.message.includes('check callback') && error.message.includes('Editor failed to initialize'));
    assert.equal(instance.isConnected(), false);

    const active = await run(async page => {
      const context = await page.context().browser().newContext();
      const staff = await context.newPage();
      await staff.goto(url);
      await staff.evaluate(() => { window.progress = setInterval(() => console.log('Count updated'), 250); });
      await new Promise(resolve => setTimeout(resolve, 5700));
      await staff.evaluate(() => clearInterval(window.progress));
      return staff.getByRole('button', {name: 'Save'}).textContent();
    }, {deadline: 5000, silence: 5500});
    assert.equal(active, 'Save');
    await assert.rejects(run(page => page.getByRole('button', {name: 'Publish'}).click(),
      {deadline: 50, silence: 5500}), error => error.message.includes('Browser silent for 5500 ms'));
    assert.equal(instance.isConnected(), false);
  } finally {
    server.close();
  }
}

journey().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.fast
def test_browser_kit_reports_the_failed_path_and_closes_edge(tmp_path):
    """The kit golden passes its UI check and misses a deadline masking a missing control."""
    stderr_path = tmp_path / "browser-check.stderr"
    with stderr_path.open("wb") as stderr:
        result = subprocess.run(
            ["node", "-e", CHECK], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=stderr, timeout=120
        )
    assert result.returncode == 0, stderr_path.read_text(encoding="utf-8", errors="replace")
