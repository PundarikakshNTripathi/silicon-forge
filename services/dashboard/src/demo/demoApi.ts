// SPDX-License-Identifier: AGPL-3.0-or-later
// The demo's stand-in for the HTTP API: reads come from the static files that scripts/export_demo_bundle.py wrote from the real
// routes, writes are refused politely. `api()` in ../api.ts calls these when DEMO is on; nothing else knows about it.
import { dataUrl, DEFAULT_RUN } from "./mode";

export class DemoError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

type Notify = (text: string) => void;
let notify: Notify = () => undefined;
/** The shell registers its toast function here, so a refused write can say why without importing the UI into the data layer. */
export const setDemoNotifier = (fn: Notify) => { notify = fn; };

const cache = new Map<string, Promise<unknown>>();
export function loadJson<T>(file: string): Promise<T> {
  let p = cache.get(file) as Promise<T> | undefined;
  if (!p) {
    p = fetch(dataUrl(file)).then((r) => {
      if (!r.ok) throw new DemoError(r.status === 404 ? 404 : 502, `${file}: ${r.status} ${r.statusText}`);
      return r.json() as Promise<T>;
    });
    p.catch(() => cache.delete(file));
    cache.set(file, p);
  }
  return p;
}

/** Every GET the dashboard makes, mapped to its file. A test (demoApi.test.ts) checks this table against the call sites. */
export const READS: [RegExp, (m: RegExpMatchArray, q: URLSearchParams) => string][] = [
  [/^\/runs$/, () => "runs.json"],
  [/^\/runs\/([^/]+)$/, (m) => `run-data/${m[1]}.json`],
  [/^\/runs\/([^/]+)\/events\/all$/, (m) => `run-data/${m[1]}.events.json`],
  [/^\/runs\/([^/]+)\/artifacts$/, (m) => `run-data/${m[1]}.artifacts.json`],
  [/^\/benchmarks$/, () => "benchmarks.json"],
  [/^\/examples$/, () => "examples.json"],
  [/^\/estimate$/, (_m, q) => `estimate/${q.get("profile") ?? "replay"}.json`],
  [/^\/cost\/summary$/, () => "cost.json"],
  [/^\/insights\/agent$/, () => "insights.json"],
  [/^\/providers$/, () => "providers.json"],
  [/^\/models$/, () => "models.json"],
  [/^\/settings$/, () => "settings.json"],
  [/^\/skills$/, () => "skills.json"],
  [/^\/config\/defaults$/, () => "config.json"],
  [/^\/system\/doctor$/, () => "doctor.json"],
];

export function demoRead<T>(path: string): Promise<T> {
  const [route, query = ""] = path.split("?");
  for (const [re, file] of READS) {
    const m = route.match(re);
    if (m) return loadJson<T>(file(m, new URLSearchParams(query)));
  }
  return Promise.reject(new DemoError(404, `The static demo has no data for ${route}.`));
}

interface Pack { name: string; content: string }

/** Writes do nothing in the demo. Each says so in a toast and returns what the real route would, so pages carry on. */
export async function demoWrite<T>(method: string, path: string, body: unknown): Promise<T> {
  const route = path.split("?")[0];
  const say = (text: string) => notify(`Static demo: ${text}`);
  if (method === "POST" && route === "/runs") {
    say("nothing runs here. Opening a recorded run instead.");
    return { run_id: DEFAULT_RUN } as T;
  }
  if (method === "POST" && route === "/skills/retrieve") {
    // a read in spirit (the real route is a POST): keyword overlap over the packs, enough to try the box
    const { query = "", top_k = 2 } = (body ?? {}) as { query?: string; top_k?: number };
    const words = query.toLowerCase().split(/\W+/).filter((w) => w.length > 2);
    const packs = await loadJson<Pack[]>("skills.json");
    const scored = packs.map((p) => ({ name: p.name, content: p.content, s: words.reduce((n, w) => n + (p.content.toLowerCase().split(w).length - 1), 0) }));
    return scored.filter((x) => x.s > 0).sort((a, b) => b.s - a.s).slice(0, top_k).map(({ name, content }) => ({ name, content })) as T;
  }
  if (method === "DELETE" && route.startsWith("/runs/")) { say("the recorded run was not deleted."); return { deleted: false } as T; }
  if (route.endsWith("/cancel")) return { cancelling: false } as T;
  if (route === "/settings") { say("settings are not saved."); return { ...(await loadJson<object>("settings.json")), ...((body ?? {}) as object) } as T; }
  if (route.startsWith("/secrets/")) { say("keys are never stored here."); return { stored: false, deleted: false } as T; }
  if (route.startsWith("/models/")) { say("models are not managed here."); return { job: "demo" } as T; }
  if (route.startsWith("/skills/")) { say("packs are not saved."); return { saved: false } as T; }
  say("this action is not available.");
  throw new DemoError(405, "not available in the static demo");
}
