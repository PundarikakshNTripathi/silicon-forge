// SPDX-License-Identifier: AGPL-3.0-or-later
// Static demo mode: a build with VITE_DEMO_MODE=true serves recorded data from /demo-data and needs no backend (docs/demo.md).
// With the flag unset every branch that reads DEMO is dead code and the normal app is unchanged.
const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};

export const DEMO = env.VITE_DEMO_MODE === "true";
const base = env.BASE_URL ?? "/";
export const BASE = base.endsWith("/") ? base : `${base}/`;
export const dataUrl = (path: string) => `${BASE}demo-data/${path}`;
export const assetUrl = (name: string) => `${BASE}demo-assets/${name}`;

/** The run a visitor is taken to when they try to start one: a real, finished run recorded with real tools. */
export const DEFAULT_RUN = "demo-up_down_counter";
