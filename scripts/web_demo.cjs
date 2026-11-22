// SPDX-License-Identifier: AGPL-3.0-or-later
// Record the web cockpit for the README: pick an example, start a REAL run (replayed model, real Yosys/SymbiYosys/OpenROAD in WSL), watch
// it, then walk every section. Frames and a frames.json (file, hold in ms, caption) are written for scripts/web_gif.py.
//
//   cd <a folder with `npm i playwright-core`>; NODE_PATH=$PWD/node_modules node <repo>/scripts/web_demo.cjs http://localhost:5181 OUT_DIR
//
// Needs a running API with a clean data directory (so the run list starts empty and the first screen is "New run"), the dashboard
// (vite dev server or `siliconforge serve`), and Chrome or Edge. Nothing is mocked: every frame is what the app rendered.
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");

const base = process.argv[2] || "http://localhost:5173";
const out = process.argv[3] || "frames";
fs.mkdirSync(out, { recursive: true });
const browsers = ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium"];
const executablePath = browsers.find((p) => fs.existsSync(p));

const frames = [];
let n = 0;
async function shot(page, caption, ms) {
  n += 1;
  const file = `f${String(n).padStart(3, "0")}.png`;
  await page.screenshot({ path: path.join(out, file) });
  frames.push({ file, ms, caption });
}

(async () => {
  const browser = await chromium.launch({ executablePath, headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 })).newPage();
  await page.goto(`${base}/new?theme=light`);
  await page.waitForSelector("text=Describe the chip block you want");
  await page.waitForTimeout(700);
  await shot(page, "New run: describe the block, or pick an example", 1500);

  await page.getByRole("button", { name: "up_down_counter" }).click();
  await page.waitForTimeout(500);
  await shot(page, "The spec is the only input", 1300);
  await page.getByText("WSL2").first().click();
  await page.waitForTimeout(400);
  await shot(page, "Real tools: Yosys, SymbiYosys, OpenROAD in WSL2", 1500);

  await page.getByRole("button", { name: /Start run/ }).click();
  await page.waitForURL(/\/runs\//);
  for (let i = 0; i < 200; i += 1) {  // watch it run: the flow strip fills as each stage works (a real place and route takes a minute or two)
    await page.waitForTimeout(700);
    const done = (await page.getByText("Passed", { exact: true }).count()) > 0;
    if (i % 3 === 0 || done) await shot(page, "The run, live: each stage in its own layer colour", done ? 1700 : 280);
    if (done) break;
  }
  await page.waitForTimeout(500);
  await shot(page, "Signoff closed: results against the contract's limits", 2200);

  for (const [name, caption] of [["Design", "Design: the contract and every RTL revision"], ["Verification", "Verification: counterexample, repair, proof"],
    ["Performance", "Performance: area and timing across layout episodes"], ["Layout & files", "Layout: the real GDSII, zoomable"],
    ["Agent log", "Every model call, with tokens and cost"]]) {
    await page.getByRole("tab", { name: new RegExp(name) }).click();
    await page.waitForTimeout(700);
    await page.evaluate(() => { const m = document.querySelector(".main"); if (m) m.scrollTo(0, 540); });  // bring the tab's content into view
    await page.waitForTimeout(250);
    await shot(page, caption, 1700);
  }
  await page.goto(`${base}/runs?theme=light`);
  await page.waitForTimeout(900);
  await shot(page, "Runs: every run, its evidence and its cost", 1500);
  await page.goto(`${base}/evals?tab=agents&theme=light`);
  await page.waitForTimeout(1200);
  await shot(page, "Evals: models and agent loops, with intervals", 2000);
  await page.goto(`${base}/cost?theme=light`);
  await page.waitForTimeout(900);
  await shot(page, "Insights: spend, and where the agent's time goes", 1500);
  await page.goto(`${base}/workspace?theme=light`);
  await page.waitForTimeout(900);
  await shot(page, "Agent: work in any folder, approve every edit", 1500);
  await page.goto(`${base}${new URL(page.url()).pathname === "/workspace" ? "/runs" : "/runs"}?theme=dark`);
  await page.waitForTimeout(900);
  await page.getByRole("row").nth(1).click();
  await page.waitForTimeout(1200);
  await shot(page, "And a dark theme", 2200);

  fs.writeFileSync(path.join(out, "frames.json"), JSON.stringify(frames, null, 1));
  await browser.close();
  console.log(`${frames.length} frames in ${out}`);
})().catch((e) => { console.error(e); process.exit(1); });
