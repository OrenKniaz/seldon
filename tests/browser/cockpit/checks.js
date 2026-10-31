"use strict";

// Real saved plan -> HTTP -> shipped UI. No fixture supplies the expected DOM.
const {chromium} = require("C:/Users/oren/codex-harness/ui/node_modules/@playwright/test");
const path = require("path");
const assert = require("assert/strict");

(async () => {
  const browser = await chromium.launch({
    executablePath: path.join(process.env.LOCALAPPDATA,
      "ms-playwright/chromium-1217/chrome-win64/chrome.exe"),
    headless: true
  });
  try {
    const page = await browser.newPage();
    const errors = [];
    const methods = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => methods.push(request.method()));
    await page.goto(process.argv[2]);
    await page.locator("#freshness").filter({hasText: "Last received:"}).waitFor();
    assert((await page.locator("#status").innerText()).includes(process.argv[3]));
    assert.equal(await page.locator("#next").innerText(), `Next: ${process.argv[4]}`);
    assert((await page.locator("#runs-title").innerText()).includes("providers"));
    assert((await page.locator("#runs").innerText()).includes("Matches current publication"));
    assert((await page.locator("#runs").innerText()).includes("codex"));
    assert((await page.locator("#plan").innerText()).includes("Owned files:"));
    assert((await page.locator("main").innerText()).includes("Usage: unknown"));
    await page.getByText("Canonical validated receipts and acceptance", {exact: true}).click();
    assert((await page.locator("#proof pre").innerText()).includes('"accepted": true'));
    assert((await page.locator("#plan").innerText()).includes("V1"));
    await page.route("**/api/status", route => route.abort());
    await page.getByRole("button", {name: "Refresh", exact: true}).click();
    await page.locator("#freshness").filter({hasText: "Stale snapshot"}).waitFor();
    assert((await page.locator("#status").innerText()).includes(process.argv[3]));
    assert((await page.locator("#proof").innerText()).includes("Proof freshness unconfirmed"));
    await page.unroute("**/api/status");
    await page.getByRole("button", {name: "Refresh", exact: true}).click();
    await page.locator("#freshness").filter({hasText: "Last received:"}).waitFor();
    assert.deepEqual(errors, []);
    assert(methods.every(method => method === "GET"));
    console.log("cockpit: canonical revision/blockers/next, receipts, refresh and GET-only browser passed");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
