// SPDX-License-Identifier: AGPL-3.0-or-later
// Drive the browser-hosted terminal with real keystrokes and capture frames. Usage: node rec.cjs PORT OUT_DIR "<request text>"
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");
const [port, out, request] = [process.argv[2], process.argv[3], process.argv[4]];
fs.mkdirSync(out, { recursive: true });
const exe = ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find((p) => fs.existsSync(p));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ executablePath: exe, headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 680 } });
  const page = await ctx.newPage();
  await page.goto("file:///" + path.resolve(__dirname, "page.html").replace(/\\/g, "/") + `?port=${port}`);
  await page.click("#t");
  const text = () => page.evaluate(() => window.screenText());
  const waitFor = async (re, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (re.test(await text())) return true; await sleep(300); } return false; };

  // capture loop: a PNG every ~120 ms with its wall-clock time, so the GIF keeps the real timing
  const frames = []; let capturing = true; const T0 = Date.now(); let n = 0;
  const cap = (async () => {
    while (capturing) {
      const t = Date.now() - T0; n += 1;
      const file = `f${String(n).padStart(5, "0")}.png`;
      try { await page.screenshot({ path: path.join(out, file) }); frames.push({ file, t }); } catch (e) { /* page closing */ }
      await sleep(60);
    }
  })();

  await sleep(500);
  console.log("splash seen:", await waitFor(/SiliconForge|spec/i, 8000));
  await sleep(4200); // the splash runs to its end
  console.log("ui ready:", await waitFor(/Describe a change|\/help/, 20000));
  await sleep(1500);
  await page.keyboard.type(request, { delay: 55 });
  await sleep(800);
  await page.keyboard.press("Enter");
  let approvals = 0, last = Date.now();
  const t0 = Date.now();
  while (Date.now() - t0 < 170000) {
    const s = await text();
    if (/y allow/i.test(s) && /n decline/i.test(s)) {
      await sleep(2500);
      await page.keyboard.press("y");
      approvals += 1; last = Date.now();
      await sleep(2000);
      continue;
    }
    if (Date.now() - last > 35000) break;
    if (approvals === 0 && Date.now() - t0 > 100000) break;
    await sleep(600);
  }
  console.log("approvals:", approvals);
  await sleep(5000);
  capturing = false;
  await cap;
  fs.writeFileSync(path.join(out, "frames.json"), JSON.stringify(frames));
  fs.writeFileSync(path.join(out, "final.txt"), await text());
  await ctx.close();
  await browser.close();
  console.log("frames:", frames.length, "seconds:", Math.round((Date.now() - T0) / 1000));
})();
