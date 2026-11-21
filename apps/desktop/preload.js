// Minimal, explicit bridge. The UI needs nothing from Node, so only identify the shell.
"use strict";
const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("siliconforgeDesktop", { platform: process.platform, version: process.versions.electron });
