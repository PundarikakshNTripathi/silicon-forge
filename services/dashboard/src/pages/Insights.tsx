import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type RunSummary } from "../api";
import { Bars } from "../charts";
import { Empty, Panel, Prov, Stat } from "../ui";
import { compact, secs, usd } from "../lib";

interface Rollup { total_usd: number; total_tokens: number; runs: number; by_profile: { profile: string; runs: number; usd: number; tokens: number; signoff_passed: number }[] }

export function CostPage() {
  const roll = useQuery({ queryKey: ["cost"], queryFn: () => api<Rollup>("/cost/summary") });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api<RunSummary[]>("/runs") });
  const r = roll.data;
  const top = [...(runs.data ?? [])].sort((a, b) => b.cost_usd - a.cost_usd).slice(0, 8);
  return (
    <>
      <div className="topbar"><span className="title">Cost</span></div>
      <div className="page stack">
        <div className="grid4">
          <Stat label="Total spent" value={usd(r?.total_usd)} sub={`${r?.runs ?? 0} runs`} />
          <Stat label="Tokens used" value={compact(r?.total_tokens ?? 0)} />
          <Stat label="Average per run" value={r && r.runs ? usd(r.total_usd / r.runs) : "-"} />
          <Stat label="Passed signoff" value={`${r?.by_profile.reduce((a, b) => a + b.signoff_passed, 0) ?? 0}`} sub="across all profiles" />
        </div>
        {!r || r.runs === 0 ? (
          <Panel><Empty title="No spending yet" action={<Link to="/" className="btn primary" style={{ textDecoration: "none" }}>Start a run</Link>}>Costs appear here once runs use a paid model. Local and bundled-example runs are free.</Empty></Panel>
        ) : (
          <div className="grid2" style={{ alignItems: "start" }}>
            <Panel title="Spend by model profile"><Bars items={r.by_profile.map((p) => ({ label: p.profile, value: p.usd, sub: `${p.runs} runs` }))} fmt={usd} /></Panel>
            <Panel title="Tokens by model profile"><Bars items={r.by_profile.map((p) => ({ label: p.profile, value: p.tokens }))} fmt={compact} /></Panel>
          </div>
        )}
        <Panel title="Most expensive runs" flush>
          <table className="t"><thead><tr><th>Design</th><th>Model</th><th className="num">Cost</th><th className="num">Tokens</th></tr></thead>
            <tbody>{top.map((t) => <tr key={t.id}><td><Link to={`/runs/${t.id}`}>{t.module ?? t.id}</Link></td><td>{t.profile}</td><td className="num">{usd(t.cost_usd)}</td><td className="num">{compact(t.tokens)}</td></tr>)}
              {top.length === 0 && <tr><td colSpan={4} className="muted">No runs yet.</td></tr>}</tbody></table>
        </Panel>
        <div className="hint">Costs use the price table in <code>configs/models.yaml</code> including prompt-cache discounts. Check those prices against your plan.</div>
      </div>
    </>
  );
}

interface BenchFile { file: string; data: Record<string, any> }

export function EvalsPage() {
  const q = useQuery({ queryKey: ["bench"], queryFn: () => api<BenchFile[]>("/benchmarks") });
  const local = (q.data ?? []).filter((b) => b.file.startsWith("local-"));
  const tele = (q.data ?? []).find((b) => b.file.startsWith("telemetry-"));
  return (
    <>
      <div className="topbar"><span className="title">Evals and benchmarks</span></div>
      <div className="page stack">
        <p className="muted" style={{ maxWidth: 720 }}>
          Every number on this page comes from a result file in <code>benchmarks/results/</code>. Local model runs go through the real pipeline: contract, RTL, proofs and equivalence checking on real tools.
        </p>
        <Panel title="Local models on this machine" flush>
          {local.length === 0 ? <Empty title="No local model benchmarks yet">Run <code>python benchmarks/bench_local_models.py --model models/gguf/&lt;model&gt;.gguf</code> to measure a model.</Empty> : (
            <div style={{ overflowX: "auto" }}>
              <table className="t"><thead><tr><th>Model</th><th>Verified designs</th><th className="num">Speed</th><th className="num">First token</th><th className="num">VRAM</th><th>Results from</th></tr></thead>
                <tbody>{local.map(({ file, data: d }) => {
                  const rows = (d.results ?? []) as Record<string, any>[];
                  const tps = rows.map((r) => r.tokens_per_s_p50).filter(Boolean) as number[];
                  const ttft = rows.map((r) => r.ttft_p50_s).filter(Boolean) as number[];
                  const med = (a: number[]) => (a.length ? [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)] : null);
                  return (
                    <tr key={file}><td><strong>{String(d.model).replace(".gguf", "")}</strong><div className="muted small">{d.gpu}, context {d.ctx}, KV {d.kv_cache}</div></td>
                      <td>{d.verified} of {d.designs}</td>
                      <td className="num">{med(tps) ? `${med(tps)!.toFixed(0)} tok/s` : "-"}</td><td className="num">{med(ttft) ? secs(med(ttft)) : "-"}</td>
                      <td className="num">{d.vram_used_mib_loaded ? `${(d.vram_used_mib_loaded / 1024).toFixed(1)} GB` : "-"}</td>
                      <td><Prov mode={d.backend ?? "simulated"} /></td></tr>);
                })}</tbody></table>
            </div>
          )}
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
