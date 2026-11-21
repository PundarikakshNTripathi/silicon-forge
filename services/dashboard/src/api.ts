// Typed client for the SiliconForge API. The desktop app passes a per-launch token as ?token=…

const params = new URLSearchParams(window.location.search);
const fromUrl = params.get("token");
if (fromUrl) {
  try { sessionStorage.setItem("sf-token", fromUrl); } catch { /* storage may be unavailable */ }
}
export const token = (() => {
  try { return fromUrl ?? sessionStorage.getItem("sf-token") ?? ""; } catch { return fromUrl ?? ""; }
})();

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init.headers as Record<string, string>) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`/api${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });

export const withToken = (url: string) => (token ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : url);
export const artifactUrl = (runId: string, path: string) => withToken(`/api/runs/${runId}/artifacts/${path}`);

// ---------- types ----------
export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";

export interface RunSummary {
  id: string; created: number; updated: number; status: RunStatus; stage: string | null; profile: string; backend: string;
  module: string | null; signoff: number; cost_usd: number; tokens: number; duration_s: number | null; error: string | null;
}
export interface PPA {
  wns_ns: number; tns_ns: number; area_um2: number; total_power_mw: number; cell_count: number; drc_violations: number;
  lvs_clean: boolean; runtime_sec: number; knobs: Record<string, string | number>; reward: number; mode: string;
  gds_path: string | null; cached: boolean; corner_wns: Record<string, number>; extra: Record<string, unknown>;
}
export interface Port { name: string; direction: string; width: number; description?: string }
export interface Contract {
  module_name: string; clock_port: string; reset_port: string; reset_active_low: boolean; reset_sync: boolean;
  ports: Port[]; functional_requirements: string[]; target_clock_ns: number; max_area_um2: number; max_power_mw: number;
}
export interface Witness {
  property_id: string; task: string; cycle: number; failing_signal: string; stimulus: Record<string, number | null>;
  summary: string; trace: WaveTrace | null;
}
export interface WaveTrace { timescale: string; end: number; signals: { name: string; width: number; changes: [number, string][] }[] }
export interface EventRec { seq: number; ts: number; stage: string; status: string; detail: string; payload: Record<string, any> }
export interface RunDetail extends RunSummary {
  spec_text: string; request: Record<string, any> | null; state: Record<string, any> | null; cost: Record<string, any> | null;
}
export interface Provider {
  name: string; kind: string; local: boolean; key_env?: string; key_present?: boolean | null; reachable?: boolean;
  installed?: boolean; enabled?: boolean; warning?: string;
}
export interface Profile { name: string; tiers: Record<string, { provider: string; model: string }[]>; providers: string[] }
export interface Doctor {
  platform: string; python: string; host_tools: Record<string, boolean>; gpu: { name?: string; vram_total_mib?: number; vram_used_mib?: number };
  backends: Record<string, { available: boolean; tools: Record<string, boolean>; note?: string; error?: string }>;
  telemetry_engine_built: boolean; llama_cpp: { installed: boolean; tag: string | null; models: string[] };
}
