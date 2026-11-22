// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Plus, Search, Trash2 } from "lucide-react";
import { api, del, type RunStatus, type RunSummary } from "../api";
import { Empty, Panel, Prov, StatusBadge } from "../ui";
import { ago, secs, usd } from "../lib";

export default function Runs() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["runs"], queryFn: () => api<RunSummary[]>("/runs"),
    refetchInterval: (query) => (query.state.data?.some((r) => r.status === "running" || r.status === "queued") ? 2000 : 10_000),
  });
  const remove = useMutation({ mutationFn: (id: string) => del(`/runs/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }) });
  const [text, setText] = useState("");
  const [status, setStatus] = useState<"all" | RunStatus>("all");
  const rows = useMemo(
    () => (q.data ?? []).filter((r) => (status === "all" || r.status === status) &&
      `${r.module ?? ""} ${r.id} ${r.profile} ${r.backend}`.toLowerCase().includes(text.toLowerCase())),
    [q.data, text, status],
  );

  return (
    <>
      <div className="topbar">
        <span className="title">Runs</span>
        <span className="grow" />
        <Link to="/new" className="btn primary" style={{ textDecoration: "none" }}><Plus size={15} /> New run</Link>
      </div>
      <div className="page stack">
        <div className="row wrap">
          <div style={{ position: "relative", width: 280 }}>
            <Search size={14} style={{ position: "absolute", left: 9, top: 10, color: "var(--ink-3)" }} />
            <input type="text" style={{ paddingLeft: 28 }} placeholder="Search by design, model or id" value={text} onChange={(e) => setText(e.target.value)} aria-label="Search runs" />
          </div>
          <select style={{ width: 160 }} value={status} onChange={(e) => setStatus(e.target.value as typeof status)} aria-label="Filter by status">
            {["all", "running", "succeeded", "failed", "cancelled", "interrupted"].map((s) => <option key={s} value={s}>{s === "all" ? "All statuses" : s[0].toUpperCase() + s.slice(1)}</option>)}
          </select>
        </div>
        <Panel flush>
          {rows.length === 0 ? (
            <Empty title={q.data?.length ? "No runs match" : "No runs yet"} action={!q.data?.length && <Link to="/new" className="btn primary" style={{ textDecoration: "none" }}>Start your first run</Link>}>
              {q.data?.length ? "Try a different search or status." : "Pick a bundled example and watch the agent prove it."}
            </Empty>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="t">
                <thead><tr><th>Design</th><th>Status</th><th>Results from</th><th>Model</th><th>Stage</th><th className="num">Cost</th><th className="num">Time</th><th>Started</th><th /></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className="link" onClick={() => nav(`/runs/${r.id}`)}>
                      <td><strong>{r.module ?? "Waiting for contract…"}</strong><div className="muted small mono">{r.id}</div></td>
                      <td><StatusBadge status={r.status} />{r.status === "succeeded" && r.signoff ? <span className="badge ok" style={{ marginLeft: 6 }}>Signoff closed</span> : null}</td>
                      <td><Prov mode={r.backend} /></td>
                      <td>{r.profile}</td>
                      <td className="muted">{r.stage?.replaceAll("_", " ").toLowerCase()}</td>
                      <td className="num">{usd(r.cost_usd)}</td>
                      <td className="num">{secs(r.duration_s)}</td>
                      <td className="muted nowrap">{ago(r.created)}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        {r.status !== "running" && r.status !== "queued" && (
                          <button className="btn quiet sm" aria-label={`Delete ${r.id}`} onClick={() => confirm("Delete this run and its history?") && remove.mutate(r.id)}><Trash2 size={14} /></button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
