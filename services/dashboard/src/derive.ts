// SPDX-License-Identifier: AGPL-3.0-or-later
// Turns the ordered event stream into the view the run page renders (works live and after the fact).
import type { Contract, EventRec, PPA, Witness } from "./api";

export type StageKey = "contract" | "rtl" | "guardrails" | "formal" | "lec" | "pnr" | "signoff";
export type StageStatus = "pending" | "active" | "done" | "fail";

export const STAGES: { key: StageKey; label: string; sub: string }[] = [
  { key: "contract", label: "Contract", sub: "ports, clocks, budgets" },
  { key: "rtl", label: "RTL", sub: "SystemVerilog" },
  { key: "guardrails", label: "Guardrails", sub: "lint + contract" },
  { key: "formal", label: "Formal", sub: "BMC, PDR, cover" },
  { key: "lec", label: "Synthesis", sub: "RTL = netlist" },
  { key: "pnr", label: "Place & route", sub: "OpenROAD" },
  { key: "signoff", label: "Signoff", sub: "timing, DRC, LVS" },
];
export const STAGE_OF: Record<string, StageKey> = {
  ARCHITECT: "contract", RTL_GEN: "rtl", RTL: "rtl", GUARDRAIL_AUDIT: "guardrails", SIMULATION: "guardrails",
  FORMAL_VERIFY: "formal", SYNTHESIS_LEC: "lec", PNR_RL_OPT: "pnr", SIGNOFF: "signoff",
};

export interface Violation { gate: string; rule_id: string; severity: string; message: string; line_number: number }
export interface FormalIter {
  iteration: number; mode: string; task_status: Record<string, string>; elapsed_sec: number; witnesses: Witness[]; passed: boolean;
}
export interface LlmCall {
  seq: number; stage: string; tier: string; provider: string; model: string; cache_hit: boolean; prompt_tokens: number;
  completion_tokens: number; cached_tokens: number; latency_s: number; ttft_s: number | null; tokens_per_s: number | null;
  cost_usd: number; saved_usd: number; system_prompt: string; user_prompt: string; response: string;
}
export interface RtlVersion { label: string; source: string }
export interface SignoffCheck { name: string; passed: boolean; detail: string }

export interface RunView {
  stages: Record<StageKey, { status: StageStatus; loops: number; detail: string }>;
  mode: string; // simulated | native | wsl | docker
  contract: Contract | null;
  rtl: RtlVersion[];
  guardrails: { attempt: number; ok: boolean; violations: Violation[]; detail: string }[];
  sim: { ok: boolean; summary: string; mode: string } | null;
  formal: FormalIter[];
  lec: { ok: boolean; cells: number; mode: string; elapsed: number; logTail: string } | null;
  episodes: PPA[];
  signoff: { ok: boolean; checks: SignoffCheck[]; eco?: { recommendation: string } } | null;
  calls: LlmCall[];
  cost: { usd: number; tokens: number; saved: number; calls: number; cacheHits: number };
  error: string | null;
  cancelled: boolean;
  finished: boolean;
  active: StageKey | null;
  /** when each stage first and last produced an event (seconds since the epoch), for the timeline */
  times: Record<StageKey, { start: number | null; end: number | null }>;
  /** moments a stage sent the design back for repair */
  retries: { stage: StageKey; ts: number }[];
  t0: number | null;
  t1: number | null;
}

const blank = (): RunView["stages"] =>
  Object.fromEntries(STAGES.map((s) => [s.key, { status: "pending", loops: 0, detail: "" }])) as RunView["stages"];

export function deriveView(events: EventRec[]): RunView {
  const v: RunView = {
    stages: blank(), mode: "simulated", contract: null, rtl: [], guardrails: [], sim: null, formal: [], lec: null,
    episodes: [], signoff: null, calls: [], cost: { usd: 0, tokens: 0, saved: 0, calls: 0, cacheHits: 0 },
    error: null, cancelled: false, finished: false, active: null,
    times: Object.fromEntries(STAGES.map((x) => [x.key, { start: null, end: null }])) as RunView["times"], retries: [], t0: null, t1: null,
  };
  let guardAttempt = 0;
  for (const e of events) {
    if (e.stage !== "LLM") { v.t0 = v.t0 == null ? e.ts : Math.min(v.t0, e.ts); v.t1 = v.t1 == null ? e.ts : Math.max(v.t1, e.ts); }
    const key = STAGE_OF[e.stage];
    const p = e.payload ?? {};
    if (e.stage === "INIT" && p.eda_mode) v.mode = p.eda_mode;
    if (e.stage === "LLM") {
      const c = p as Omit<LlmCall, "seq">;
      v.calls.push({ ...c, seq: e.seq });
      v.cost.usd += c.cost_usd ?? 0; v.cost.tokens += (c.prompt_tokens ?? 0) + (c.completion_tokens ?? 0);
      v.cost.saved += c.saved_usd ?? 0; v.cost.calls += 1; v.cost.cacheHits += c.cache_hit ? 1 : 0;
      continue;
    }
    if (e.stage === "FAILED") { v.error = e.detail; v.finished = true; markActive(v, "fail"); continue; }
    if (e.stage === "CANCELLED") { v.cancelled = true; v.finished = true; markActive(v, "fail"); continue; }
    if (!key) continue;
    const st = v.stages[key];
    if (e.stage === "ARCHITECT" && p.contract) v.contract = p.contract as Contract;
    if (e.stage === "RTL") v.rtl.push({ label: p.label, source: p.source });
    if (e.stage === "GUARDRAIL_AUDIT") {
      guardAttempt += 1;
      v.guardrails.push({ attempt: p.attempt ?? guardAttempt, ok: e.status === "PASS", violations: p.violations ?? p.warnings ?? [], detail: e.detail });
    }
    if (e.stage === "SIMULATION") v.sim = { ok: e.status === "PASS", summary: e.detail, mode: p.mode ?? v.mode };
    if (e.stage === "FORMAL_VERIFY" && p.task_status) {
      v.formal.push({ iteration: p.iteration, mode: p.mode, task_status: p.task_status, elapsed_sec: p.elapsed_sec ?? 0,
        witnesses: p.witnesses ?? [], passed: e.status === "PASS" });
    }
    if (e.stage === "SYNTHESIS_LEC") {
      v.lec = { ok: e.status === "PASS", cells: p.cells ?? 0, mode: p.mode ?? v.mode, elapsed: p.elapsed_sec ?? 0, logTail: p.log_tail ?? "" };
    }
    if (e.stage === "PNR_RL_OPT" && e.status === "EPISODE" && p.metrics) v.episodes.push(p.metrics as PPA);
    if (e.stage === "SIGNOFF" && p.checks) v.signoff = { ok: e.status === "PASS", checks: p.checks, eco: p.eco };

    const tm = v.times[key];
    tm.start = tm.start == null ? e.ts : Math.min(tm.start, e.ts); tm.end = tm.end == null ? e.ts : Math.max(tm.end, e.ts);
    if (e.status === "RETRY") { st.loops += 1; v.retries.push({ stage: key, ts: e.ts }); }
    st.detail = e.detail;
    if (e.status === "PASS" || e.status === "INFO" || e.status === "VERSION" || e.status === "EPISODE") {
      st.status = e.status === "EPISODE" || e.status === "VERSION" ? "active" : "done";
    } else if (e.status === "FAIL") st.status = "fail";
    else if (e.status === "WARN") st.status = key === "signoff" ? "fail" : "done";
    else st.status = "active";
    v.active = key;
  }
  // a stage that completed is only "active" while it is the latest unfinished one
  if (v.rtl.length && v.stages.rtl.status === "active" && v.stages.guardrails.status !== "pending") v.stages.rtl.status = "done";
  if (v.stages.pnr.status === "active" && v.stages.signoff.status !== "pending") v.stages.pnr.status = "done";
  if (v.finished || v.signoff) v.active = null;
  return v;
}

function markActive(v: RunView, s: StageStatus) {
  if (v.active) v.stages[v.active].status = s;
}

export function failedProperties(view: RunView): string[] {
  return [...new Set(view.formal.flatMap((f) => f.witnesses.map((w) => w.property_id)))];
}
