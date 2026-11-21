// SPDX-License-Identifier: AGPL-3.0-or-later
// SiliconForge desktop shell.
// Starts the Python backend as a supervised sidecar on a random localhost port with a per-launch token,
// then shows the web UI (served by that backend) in a native window.
"use strict";

const { app, BrowserWindow, Menu, Tray, shell, dialog, nativeImage, ipcMain } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

const SMOKE = process.argv.includes("--smoke-test");
const PROTOCOL = "siliconforge";
const REPO_ROOT = path.resolve(__dirname, "..", "..");

let win = null;
let tray = null;
let backend = null;
let port = 0;
let token = "";
let quitting = false;
let restarts = 0;
let logStream = null;

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  if (logStream) logStream.write(line);
  if (!app.isPackaged || SMOKE) process.stdout.write(line);
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port: p } = srv.address();
      srv.close(() => resolve(p));
    });
  });
}

function healthy(p) {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port: p, path: "/api/health", timeout: 1500 }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => { req.destroy(); resolve(false); });
  });
}

async function waitHealthy(p, ms = 60000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await healthy(p)) return true;
    await new Promise((r) => setTimeout(r, 300));
  }
  return false;
}

/** Packaged: the PyInstaller bundle in resources/backend. Development: the repo's virtualenv (or $SILICONFORGE_PYTHON). */
function backendCommand(dataDir) {
  const env = { ...process.env, SILICONFORGE_API_TOKEN: token, SILICONFORGE_HOME: dataDir, PYTHONIOENCODING: "utf-8" };
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, "backend", process.platform === "win32" ? "siliconforge-backend.exe" : "siliconforge-backend");
    return { cmd: exe, args: ["--host", "127.0.0.1", "--port", String(port)], cwd: path.dirname(exe), env };
  }
  const venvPy = process.platform === "win32" ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe") : path.join(REPO_ROOT, ".venv", "bin", "python");
  const py = process.env.SILICONFORGE_PYTHON || (fs.existsSync(venvPy) ? venvPy : "python");
  return { cmd: py, args: ["-m", "services.orchestrator.backend_entry", "--host", "127.0.0.1", "--port", String(port)], cwd: REPO_ROOT, env };
}

async function startBackend() {
  port = await freePort();
  token = crypto.randomBytes(24).toString("hex");
  const dataDir = path.join(app.getPath("userData"), "data");
  fs.mkdirSync(dataDir, { recursive: true });
  const { cmd, args, cwd, env } = backendCommand(dataDir);
  log(`starting backend: ${cmd} ${args.join(" ")}`);
  backend = spawn(cmd, args, { cwd, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  backend.stdout.on("data", (d) => log(`[backend] ${String(d).trimEnd()}`));
  backend.stderr.on("data", (d) => log(`[backend] ${String(d).trimEnd()}`));
  backend.on("exit", async (code) => {
    log(`backend exited with code ${code}`);
    backend = null;
    if (quitting) return;
    if (restarts < 3) {
      restarts += 1;
      log(`restarting backend (attempt ${restarts})`);
      await startBackend();
      if (win && (await waitHealthy(port))) win.loadURL(appUrl());
    } else if (win) {
      win.loadURL(`data:text/html,${encodeURIComponent(errorPage("The SiliconForge backend stopped repeatedly. See the log for details."))}`);
    }
  });
  return waitHealthy(port);
}

const appUrl = (route = "") => `http://127.0.0.1:${port}/${route.replace(/^\//, "")}?token=${token}`;

function errorPage(text) {
  const logPath = path.join(app.getPath("userData"), "siliconforge.log");
  return `<body style="font-family:system-ui;padding:40px;color:#14202e"><h2>SiliconForge could not start</h2><p>${text}</p><p>Log: <code>${logPath}</code></p></body>`;
}

// The native folder dialog for "choose a working directory". Only the chosen path crosses back to the UI.
ipcMain.handle("sf:pick-folder", async (_event, defaultPath) => {
  const res = await dialog.showOpenDialog(win, {
    title: "Choose a workspace folder",
    defaultPath: typeof defaultPath === "string" && defaultPath ? defaultPath : undefined,
    properties: ["openDirectory", "createDirectory"],
  });
  return res.canceled || res.filePaths.length === 0 ? null : res.filePaths[0];
});

function createWindow() {
  win = new BrowserWindow({
    width: 1440, height: 920, minWidth: 900, minHeight: 600, show: false, title: "SiliconForge",
    backgroundColor: "#edf0f2", icon: iconPath(),
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.once("ready-to-show", () => win.show());
  // external links open in the default browser, never inside the app window
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith(`http://127.0.0.1:${port}`)) { e.preventDefault(); shell.openExternal(url); }
  });
  win.on("close", (e) => {
    if (!quitting && tray && process.platform !== "darwin") { e.preventDefault(); win.hide(); }
  });
  return win;
}

function iconPath() {
  const p = path.join(__dirname, "build", "icon.png");
  return fs.existsSync(p) ? p : undefined;
}

function createTray() {
  const p = iconPath();
  if (!p) return;
  tray = new Tray(nativeImage.createFromPath(p).resize({ width: 18, height: 18 }));
  tray.setToolTip("SiliconForge");
  const show = () => { if (!win) createWindow(); win.show(); win.focus(); };
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open SiliconForge", click: show },
    { label: "Open data folder", click: () => shell.openPath(path.join(app.getPath("userData"), "data")) },
    { type: "separator" },
    { label: "Quit", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("click", show);
}

function buildMenu() {
  const route = (r) => () => win && win.loadURL(appUrl(r));
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: "File", submenu: [
      { label: "New run", accelerator: "CmdOrCtrl+N", click: route("") },
      { label: "Runs", accelerator: "CmdOrCtrl+R", click: route("runs") },
      { type: "separator" },
      { label: "Open data folder", click: () => shell.openPath(path.join(app.getPath("userData"), "data")) },
      { role: "quit" },
    ] },
    { label: "View", submenu: [{ role: "reload" }, { role: "togglefullscreen" }, { role: "zoomIn" }, { role: "zoomOut" }, { role: "resetZoom" }, { role: "toggleDevTools" }] },
    { label: "Go", submenu: [
      { label: "Models", click: route("models") }, { label: "Cost", click: route("cost") },
      { label: "Evals", click: route("evals") }, { label: "Toolchain and settings", click: route("system") },
    ] },
    { label: "Help", submenu: [{ label: "Documentation", click: () => shell.openExternal("https://github.com/PundarikakshNTripathi/silicon-forge#readme") }] },
  ]));
}

/** siliconforge://runs/<id> opens that run. */
function handleDeepLink(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== `${PROTOCOL}:`) return;
    const route = `${u.hostname}${u.pathname}`.replace(/\/$/, "");
    if (win) { win.loadURL(appUrl(route)); win.show(); win.focus(); }
  } catch (e) { log(`bad deep link: ${e}`); }
}

if (!SMOKE && !app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", (_e, argv) => {
    if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); }
    const link = argv.find((a) => a.startsWith(`${PROTOCOL}://`));
    if (link) handleDeepLink(link);
  });
  app.on("open-url", (e, url) => { e.preventDefault(); handleDeepLink(url); });

  app.whenReady().then(async () => {
    logStream = fs.createWriteStream(path.join(app.getPath("userData"), "siliconforge.log"), { flags: "a" });
    if (process.defaultApp) app.setAsDefaultProtocolClient(PROTOCOL, process.execPath, [path.resolve(process.argv[1] || "")]);
    else app.setAsDefaultProtocolClient(PROTOCOL);

    const ok = await startBackend();
    createWindow();
    if (!ok) {
      win.loadURL(`data:text/html,${encodeURIComponent(errorPage("The backend did not become ready in time."))}`);
      if (SMOKE) { log("SMOKE FAIL: backend not healthy"); app.exit(1); }
      return;
    }
    buildMenu();
    createTray();
    await win.loadURL(appUrl());

    if (SMOKE) {
      // renderer-side check: the UI bundle loaded and can reach the authenticated API
      const result = await win.webContents.executeJavaScript(
        "fetch('/api/system/doctor?probe=false', {headers: {Authorization: 'Bearer ' + sessionStorage.getItem('sf-token')}}).then(r => r.ok)",
        true,
      );
      const title = await win.webContents.executeJavaScript("document.title");
      log(result && title === "SiliconForge" ? "SMOKE OK" : `SMOKE FAIL: api=${result} title=${title}`);
      quitting = true;
      if (backend) backend.kill();
      app.exit(result && title === "SiliconForge" ? 0 : 1);
      return;
    }
    if (app.isPackaged) {
      try { require("electron-updater").autoUpdater.checkForUpdatesAndNotify(); } catch (e) { log(`updater unavailable: ${e}`); }
    }
    const link = process.argv.find((a) => a.startsWith(`${PROTOCOL}://`));
    if (link) handleDeepLink(link);
  }).catch((e) => { dialog.showErrorBox("SiliconForge", String(e)); app.exit(1); });

  app.on("before-quit", () => { quitting = true; if (backend) backend.kill(); });
  process.on("exit", () => { if (backend) backend.kill(); });
  app.on("window-all-closed", () => { if (!tray && process.platform !== "darwin") app.quit(); });
  app.on("activate", () => { if (!win) createWindow(); });
}
