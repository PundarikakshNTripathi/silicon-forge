// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type RunSummary } from "../api";
import { Bars } from "../charts";
import { Empty, Panel, Stat } from "../ui";
import { useState } from "react";
import { compact, usd } from "../lib";

interface AgentMetrics {
  runs: number; solved: number;
  latency: { e2e_p50_s: number | null; e2e_p95_s: number | null; reasoning_share: number | null; action_share: number | null; overhead_share: number | null };
  tools: { calls: number; success_rate: number | null; failures_by_class: Record<string, number>; policy_blocks: number };
  cost: { tokens_per_run: number; usd_per_solved: number | null };
  recovery: { failures: number; recovered: number; rate: number | null };
}
const pctOf = (x: number | null | undefined, d = 0) => (x == null ? "-" : `${(x * 100).toFixed(d)}%`);

/** Where an agent turn's time goes, how often its tools work, and whether it recovers: measured from recorded spans (docs/telemetry.md). */
function AgentInsights() {
  const [hours, setHours] = useState(24 * 7);
  const q = useQuery({ queryKey: ["agent-insights", hours], queryFn: () => api<{ spans: number; metrics: AgentMetrics }>(`/insights/agent?hours=${hours}`) });
  const m = q.data?.metrics;
  const split = m && m.runs > 0 && m.latency ? [["Waiting on the model", m.latency.reasoning_share], ["Running tools", m.latency.action_share], ["Everything else", m.latency.overhead_share]] as const : [];
  return (
    <Panel layer="formal" title="Workspace agent" actions={
      <select value={hours} onChange={(e) => setHours(Number(e.target.value))} aria-label="Time window" style={{ width: "auto" }}>
        <option value={24}>last 24 hours</option><option value={24 * 7}>last 7 days</option><option value={24 * 30}>last 30 days</option>
      </select>}>
      {!m || m.runs === 0 ? (
        <Empty title="No agent turns recorded in this window">Metrics appear once you use the Workspace agent: time split, tool success, recovery and tokens per solved task. Nothing leaves this machine.</Empty>
      ) : (
        <div className="stack">
          <div className="grid4">
            <Stat label="Agent turns" value={`${m.runs}`} sub={`${m.solved} ended with an answer`} />
            <Stat label="Median turn" value={m.latency.e2e_p50_s == null ? "-" : `${m.latency.e2e_p50_s.toFixed(1)} s`} sub={`95th percentile ${m.latency.e2e_p95_s == null ? "-" : m.latency.e2e_p95_s.toFixed(0) + " s"}`} />
            <Stat label="Tool success" value={pctOf(m.tools.success_rate)} sub={`${m.tools.calls} calls, ${m.tools.policy_blocks} refused by policy`} />
            <Stat label="Recovered after a failure" value={m.recovery.failures ? `${m.recovery.recovered} of ${m.recovery.failures}` : "-"} sub={m.recovery.rate == null ? undefined : pctOf(m.recovery.rate)} />
          </div>
          <div>
            <div className="small muted" style={{ fontWeight: 700, marginBottom: 6 }}>Where the time goes</div>
            <div className="splitbar" role="img" aria-label={split.map(([k, v]) => `${k} ${pctOf(v)}`).join(", ")}>
              {split.map(([k, v], n) => <div key={k} className={`seg s${n}`} style={{ flexGrow: Math.max(v ?? 0, 0.004) }} title={`${k}: ${pctOf(v, 1)}`} />)}
            </div>
            <div className="row wrap small muted" style={{ gap: 18, marginTop: 6 }}>{split.map(([k, v], n) => <span key={k}><i className={`key s${n}`} /> {k} {pctOf(v, 1)}</span>)}</div>
          </div>
          {Object.keys(m.tools.failures_by_class).length > 0 && (
            <Bars items={Object.entries(m.tools.failures_by_class).map(([k, v]) => ({ label: k.replaceAll("_", " "), value: v }))} fmt={(v: number) => `${v}`} />
          )}
          <div className="hint">{m.cost.usd_per_solved != null ? `About ${usd(m.cost.usd_per_solved)} per solved turn; ` : ""}{Math.round(m.cost.tokens_per_run).toLocaleString()} tokens per turn on average.</div>
        </div>
      )}
    </Panel>
  );
}

interface Rollup { total_usd: number; total_tokens: number; runs: number; by_profile: { profile: string; runs: number; usd: number; tokens: number; signoff_passed: number }[] }

export function CostPage() {
  const roll = useQuery({ queryKey: ["cost"], queryFn: () => api<Rollup>("/cost/summary") });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api<RunSummary[]>("/runs") });
  const r = roll.data;
  const top = [...(runs.data ?? [])].sort((a, b) => b.cost_usd - a.cost_usd).slice(0, 8);
  return (
    <>
      <div className="topbar"><span className="title">Insights</span></div>
      <div className="page stack">
        <div className="grid4">
          <Stat label="Total spent" value={usd(r?.total_usd)} sub={`${r?.runs ?? 0} runs`} />
          <Stat label="Tokens used" value={compact(r?.total_tokens ?? 0)} />
          <Stat label="Average per run" value={r && r.runs ? usd(r.total_usd / r.runs) : "-"} />
          <Stat label="Passed signoff" value={`${r?.by_profile.reduce((a, b) => a + b.signoff_passed, 0) ?? 0}`} sub="across all profiles" />
        </div>
        {!r || r.runs === 0 ? (
          <Panel><Empty title="No spending yet" action={<Link to="/new" className="btn primary" style={{ textDecoration: "none" }}>Start a run</Link>}>Costs appear here once runs use a paid model. Local and bundled-example runs are free.</Empty></Panel>
        ) : (
          <div className="grid2" style={{ alignItems: "start" }}>
            <Panel title="Spend by model profile"><Bars items={r.by_profile.map((p) => ({ label: p.profile, value: p.usd, sub: `${p.runs} runs` }))} fmt={usd} /></Panel>
            <Panel title="Tokens by model profile"><Bars items={r.by_profile.map((p) => ({ label: p.profile, value: p.tokens }))} fmt={compact} /></Panel>
          </div>
        )}
        <AgentInsights />
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
