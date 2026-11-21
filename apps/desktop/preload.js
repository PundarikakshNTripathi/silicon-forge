// Minimal, explicit bridge. The UI needs almost nothing from Node: it identifies the shell and can ask the main process
// to show the native folder dialog (the chosen path is the only thing returned).
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("siliconforgeDesktop", {
  platform: process.platform,
  version: process.versions.electron,
  pickFolder: (defaultPath) => ipcRenderer.invoke("sf:pick-folder", defaultPath),
});
