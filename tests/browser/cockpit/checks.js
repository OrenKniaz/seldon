"use strict";

// Real saved plan -> HTTP -> shipped UI. No fixture supplies the expected DOM.
const {chromium, expect} = require("C:/Users/oren/codex-harness/ui/node_modules/@playwright/test");
const path = require("path");
const assert = require("assert/strict");
const deadline = Date.now() + 40000;
const watchdog = setInterval(() => {
  if (Date.now() < deadline) return;
  console.error("Cockpit check exceeded 40s, including browser startup/cleanup");
  process.exit(1);
}, 250);

(async () => {
  const browser = await chromium.launch({
    executablePath: path.join(process.env.LOCALAPPDATA,
      "ms-playwright/chromium-1217/chrome-win64/chrome.exe"),
    headless: true
  });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(8000);
    page.setDefaultNavigationTimeout(10000);
    const errors = [];
    const methods = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => methods.push(request.method()));
    await page.goto(process.argv[2]);
    await expect(page.locator("#freshness")).toContainText("Last received:", {timeout: 8000});
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
    // Protect stalled HTTP -> stale snapshot -> usable refresh. An immediate
    // abort cannot catch a pending fetch holding the refresh owner forever.
    let heldRequests = 0;
    let stallStatus = true;
    await page.route("**/api/status", route => {
      if (stallStatus) heldRequests += 1;
      else return route.continue();
    });
    await page.getByRole("button", {name: "Refresh", exact: true}).click();
    await page.locator("#freshness").filter({hasText: "Stale snapshot"}).waitFor();
    assert((await page.locator("#status").innerText()).includes(process.argv[3]));
    assert((await page.locator("#proof").innerText()).includes("Proof freshness unconfirmed"));
    // Catch the observed recovery race: an automatic retry owns a second held
    // request. Manual refresh must replace that owner, not silently drop the click.
    await expect.poll(() => heldRequests, {timeout: 8000}).toBeGreaterThanOrEqual(2);
    // Unroute does not settle paused invocations. End fault injection through
    // the same interceptor; the new request must reach the real saved producer.
    stallStatus = false;
    await page.getByRole("button", {name: "Refresh", exact: true}).click();
    await expect(page.locator("#freshness")).toContainText("Last received:", {timeout: 8000});
    await page.unroute("**/api/status");
    assert.deepEqual(errors, []);
    assert(methods.every(method => method === "GET"));
    console.log("cockpit: canonical revision/blockers/next, receipts, refresh and GET-only browser passed");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(() => clearInterval(watchdog));
