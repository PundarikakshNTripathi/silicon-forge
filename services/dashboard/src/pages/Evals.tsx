// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Empty, Panel, Prov } from "../ui";
import { compact, secs } from "../lib";

interface BenchFile { file: string; data: Record<string, any> }
interface Sample { passed: boolean; compiled: boolean; completion_tokens: number; prompt_tokens: number; calls: number }
interface PerProblem { id: string; n: number; c: number; samples: Sample[] }
interface EvalResult {
  file: string; model: string; arm: string; armSettings: Record<string, unknown>; k: number; mean: number; lo: number; hi: number;
  problems: number; compile: number | null; calls: number; tokens: number; wall: number; runner: string; n: number; tag: string;
  thinking?: string; perProblem: PerProblem[]; excluded: number; selected: number; seed: number; created: string;
}

/** Unbiased pass@k from c passes in n samples (Chen et al. 2021). */
function passAtK(n: number, c: number, k: number): number {
  if (n - c < k) return 1;
  let p = 1;
  for (let i = n - c + 1; i <= n; i++) p *= 1 - k / i;
  return 1 - p;
}

/** Small seeded generator so the same two results always give the same interval. */
function mulberry32(a: number) {
  return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

function pairedDifference(a: EvalResult, b: EvalResult) {
  const pb = new Map(b.perProblem.filter((p) => p.n >= b.k).map((p) => [p.id, passAtK(p.n, p.c, b.k)]));
  const diffs: number[] = [];
  for (const p of a.perProblem) if (p.n >= a.k && pb.has(p.id)) diffs.push(passAtK(p.n, p.c, a.k) - pb.get(p.id)!);
  if (!diffs.length) return null;
  const rnd = mulberry32(0);
  const means: number[] = [];
  for (let r = 0; r < 4000; r++) { let s = 0; for (let i = 0; i < diffs.length; i++) s += diffs[Math.floor(rnd() * diffs.length)]; means.push(s / diffs.length); }
  means.sort((x, y) => x - y);
  const mean = diffs.reduce((x, y) => x + y, 0) / diffs.length;
  return { mean, lo: means[Math.floor(0.025 * means.length)], hi: means[Math.floor(0.975 * means.length)], n: diffs.length };
}

function parse(files: BenchFile[]): EvalResult[] {
  const out: EvalResult[] = [];
  for (const { file, data: d } of files) {
    if (d.schema !== "siliconforge.eval/1") continue;
    const key = Object.keys(d).find((k) => k.startsWith("pass_at_"));
    if (!key) continue;
    const agg = d[key];
    out.push({
      file, model: String(d.model), arm: String(d.arm?.arm ?? "?"), armSettings: d.arm ?? {}, k: agg.k, mean: agg.mean, lo: agg.ci95[0], hi: agg.ci95[1],
      problems: agg.problems, compile: d.compile_rate ?? null, calls: d.tokens?.calls ?? 0, tokens: (d.tokens?.prompt ?? 0) + (d.tokens?.completion ?? 0),
      wall: d.wall_s ?? 0, runner: d.runner ?? "unknown", n: d.n ?? 1, tag: file.replace(/^eval-.*?-(\d{4}-\d{2}-\d{2})\.json$/, "$1"), thinking: d.thinking,
      perProblem: d.per_problem ?? [], excluded: Object.keys(d.dataset?.excluded ?? {}).length, selected: d.selection?.count ?? agg.problems,
      seed: d.selection?.seed ?? 0, created: d.created ?? "",
    });
  }
  return out;
}

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

/** 0-100% track with the confidence interval as a line and the estimate as a tick. */
function IntervalBar({ r }: { r: EvalResult }) {
  const W = 220, x = (v: number) => 4 + v * (W - 8);
  return (
    <svg width={W} height={22} role="img" aria-label={`pass@${r.k} ${pct(r.mean)}, 95% interval ${pct(r.lo)} to ${pct(r.hi)}`}>
      <line x1={4} x2={W - 4} y1={11} y2={11} stroke="var(--rule)" strokeWidth={1} />
      {[0, 0.25, 0.5, 0.75, 1].map((t) => <line key={t} x1={x(t)} x2={x(t)} y1={8} y2={14} stroke="var(--rule-strong)" strokeWidth={1} />)}
      <line x1={x(r.lo)} x2={x(r.hi)} y1={11} y2={11} stroke="var(--ink)" strokeWidth={5} strokeLinecap="butt" opacity={0.28} />
      <line x1={x(r.mean)} x2={x(r.mean)} y1={3} y2={19} stroke="var(--ink)" strokeWidth={2.5} />
    </svg>
  );
}

/** One cell per problem: solid = passed, hollow = failed, hatched = did not compile. */
function ProblemStrip({ r }: { r: EvalResult }) {
  return (
    <div className="pstrip" role="img" aria-label={`${r.perProblem.filter((p) => p.c > 0).length} of ${r.perProblem.length} problems passed`}>
      {r.perProblem.map((p) => {
        const none = p.samples.every((s) => !s.compiled);
        const cls = p.c === p.n ? "pass" : p.c > 0 ? "part" : none ? "nocomp" : "fail";
        return <span key={p.id} className={`pcell ${cls}`} title={`${p.id}: ${p.c} of ${p.n} passed${none ? " (did not compile)" : ""}`} />;
      })}
    </div>
  );
}

export function EvalsPage() {
  const q = useQuery({ queryKey: ["bench"], queryFn: () => api<BenchFile[]>("/benchmarks") });
  const files = q.data ?? [];
  const results = useMemo(() => parse(files), [files]);
  const local = files.filter((b) => b.file.startsWith("local-"));
  const tele = files.find((b) => b.file.startsWith("telemetry-"));
  const [open, setOpen] = useState<string | null>(null);
  const [pick, setPick] = useState<string[]>([]);
  const byFile = new Map(results.map((r) => [r.file, r]));
  const models = [...new Set(results.map((r) => r.model))].sort();
  const toggle = (f: string) => setPick((p) => (p.includes(f) ? p.filter((x) => x !== f) : [...p.slice(-1), f]));
  const diff = pick.length === 2 ? pairedDifference(byFile.get(pick[0])!, byFile.get(pick[1])!) : null;

  return (
    <>
      <div className="topbar"><span className="title">Evals and benchmarks</span></div>
      <div className="page stack">
        <p className="muted" style={{ maxWidth: 740 }}>
          Every number here comes from a result file in <code>benchmarks/results/</code>. An interval is a 95% bootstrap over problems; if two intervals
          overlap, the data do not separate the two setups. Method and limits are in <code>docs/evals.md</code>.
        </p>

        <Panel title="Model quality on VerilogEval (spec to RTL)" flush>
          {results.length === 0 ? (
            <Empty title="No evaluation results yet">
              Run <code>siliconforge evals fetch verilogeval</code>, then <code>siliconforge evals run verilogeval --serve &lt;model&gt;.gguf --arm single</code>.
            </Empty>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="t">
                <thead><tr><th style={{ width: 28 }} aria-label="Select to compare" /><th>Model and setup</th><th>pass@k with 95% interval</th><th className="num">Compiles</th><th className="num">Problems</th><th className="num">Calls</th><th className="num">Tokens</th><th className="num">Wall</th><th>Tools</th></tr></thead>
                <tbody>
                  {models.flatMap((m) => results.filter((r) => r.model === m).map((r) => (
                    <FragmentRow key={r.file} r={r} open={open === r.file} picked={pick.includes(r.file)} onToggle={() => toggle(r.file)} onOpen={() => setOpen(open === r.file ? null : r.file)} />
                  )))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        {pick.length === 2 && (
          <Panel title="Paired comparison">
            {diff ? (
              <div>
                <div><strong>A</strong> {byFile.get(pick[0])!.model}, {byFile.get(pick[0])!.arm} <span className="muted">minus</span> <strong>B</strong> {byFile.get(pick[1])!.model}, {byFile.get(pick[1])!.arm}</div>
                <div style={{ marginTop: 8, fontSize: 20 }} className="mono">{diff.mean >= 0 ? "+" : ""}{(diff.mean * 100).toFixed(1)} points <span className="muted" style={{ fontSize: 14 }}>95% interval {(diff.lo * 100).toFixed(1)} to {(diff.hi * 100).toFixed(1)} over {diff.n} shared problems</span></div>
                <div className="muted" style={{ marginTop: 6 }}>{diff.lo > 0 || diff.hi < 0 ? "The interval excludes zero: on these problems the two setups differ." : "The interval contains zero: the data do not separate the two setups."}</div>
              </div>
            ) : <div className="muted">The two results share no problems.</div>}
          </Panel>
        )}

        <Panel title="Local models through the full pipeline" flush>
          {local.length === 0 ? <Empty title="No pipeline benchmarks yet">Run <code>python benchmarks/bench_local_models.py --model models/gguf/&lt;model&gt;.gguf</code>.</Empty> : (
            <div style={{ overflowX: "auto" }}>
              <table className="t"><thead><tr><th>Model and setup</th><th>Verified designs</th><th>Where each design ended</th><th className="num">Speed</th><th className="num">First token</th><th className="num">VRAM</th><th>Tools</th></tr></thead>
                <tbody>{local.map(({ file, data: d }) => {
                  const rows = (d.results ?? []) as Record<string, any>[];
                  const med = (a: number[]) => (a.length ? [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)] : null);
                  const tps = med(rows.map((r) => r.tokens_per_s_p50).filter(Boolean));
                  const ttft = med(rows.map((r) => r.ttft_p50_s).filter(Boolean));
                  const ov = d.pipeline_overrides && Object.keys(d.pipeline_overrides).length ? Object.entries(d.pipeline_overrides).map(([k, v]) => `${k}=${v}`).join(", ") : "";
                  const tag = (file.match(/-(baseline|debugctx-irgrounding|debugctx|ir-only|full|gpu)-\d{4}/) ?? [])[1];
                  return (
                    <tr key={file}>
                      <td><strong>{String(d.model).replace(".gguf", "")}</strong><div className="muted small">{tag ?? "run"}{ov ? `, ${ov}` : ""}; context {d.ctx}, KV {d.kv_cache}</div></td>
                      <td>{d.verified} of {d.designs}</td>
                      <td><div className="ends">{rows.map((r) => <span key={r.design} className={`end ${r.verified ? "ok" : "no"}`} title={`${r.design}: ${r.verified ? "passed LEC" : r.error ?? r.stage}`}>{r.design}</span>)}</div></td>
                      <td className="num">{tps ? `${tps.toFixed(0)} tok/s` : "-"}</td><td className="num">{ttft ? secs(ttft) : "-"}</td>
                      <td className="num">{d.vram_used_mib_loaded ? `${(d.vram_used_mib_loaded / 1024).toFixed(1)} GB` : "-"}</td>
                      <td><Prov mode={d.backend ?? "simulated"} /></td>
                    </tr>);
                })}</tbody></table>
            </div>
          )}
          <div className="body small muted">One run per row. With four designs and one run each, a single design flipping moves a row by 25 points, so read these as leads and use the evals above for comparisons.</div>
        </Panel>

        {tele && (
          <Panel title="C++ telemetry engine versus Python" flush>
            <table className="t"><thead><tr><th>Operation</th><th className="num">C++ (spawned per call)</th><th className="num">Python (in-process)</th></tr></thead>
              <tbody>
                <tr><td>Parse tool logs</td><td className="num">{tele.data.cpp_subprocess_parse_logs.median_ms} ms</td><td className="num">{tele.data.python_inprocess_parse_logs.median_ms} ms</td></tr>
                <tr><td>Lint RTL</td><td className="num">{tele.data.cpp_subprocess_lint_rtl.median_ms} ms</td><td className="num">{tele.data.python_inprocess_lint_rtl.median_ms} ms</td></tr>
              </tbody></table>
            <div className="body small muted">{tele.data.note}</div>
          </Panel>
        )}
      </div>
    </>
  );
}

function FragmentRow({ r, open, picked, onToggle, onOpen }: { r: EvalResult; open: boolean; picked: boolean; onToggle: () => void; onOpen: () => void }) {
  const setup = [r.arm, r.thinking === "on" ? "thinking on" : "", r.n > 1 ? `${r.n} samples` : ""].filter(Boolean).join(", ");
  return (
    <>
      <tr className={picked ? "picked" : ""}>
        <td><input type="checkbox" checked={picked} onChange={onToggle} aria-label={`Compare ${r.model} ${r.arm}`} /></td>
        <td>
          <button className="linklike" onClick={onOpen} aria-expanded={open}><strong>{r.model}</strong></button>
          <div className="muted small">{setup}; subset {r.selected} (seed {r.seed}){r.excluded ? `; ${r.excluded} problem excluded` : ""}</div>
        </td>
        <td><div style={{ display: "flex", alignItems: "center", gap: 10 }}><IntervalBar r={r} /><span className="mono">{pct(r.mean)}</span><span className="muted small mono">[{pct(r.lo)}, {pct(r.hi)}]</span></div></td>
        <td className="num">{r.compile == null ? "-" : pct(r.compile)}</td>
        <td className="num">{r.problems}</td><td className="num">{r.calls}</td><td className="num">{compact(r.tokens)}</td><td className="num">{secs(r.wall)}</td>
        <td><Prov mode={r.runner} /></td>
      </tr>
      {open && <tr><td /><td colSpan={8}><ProblemStrip r={r} /><div className="muted small" style={{ marginTop: 6 }}>Solid: passed. Hollow: failed. Hatched: did not compile. Hover a cell for the problem.</div></td></tr>}
    </>
  );
}
