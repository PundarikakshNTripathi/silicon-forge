// SPDX-License-Identifier: AGPL-3.0-or-later
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type RunSummary } from "../api";
import { Bars } from "../charts";
import { Empty, Panel, Stat } from "../ui";
import { compact, usd } from "../lib";

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
