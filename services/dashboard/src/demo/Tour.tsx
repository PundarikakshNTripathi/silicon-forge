// SPDX-License-Identifier: AGPL-3.0-or-later
// The demo's top bar: what it is, and four stops that are each worth about fifteen seconds.
import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { X } from "lucide-react";

export const STOPS = [
  { id: "replay", title: "Agent replay and the approval gate", short: "Replay and approval", to: "/workspace" },
  { id: "agentbench", title: "AgentBench: our loop against LangGraph and Pydantic AI", short: "AgentBench matrix", to: "/evals?tab=agents" },
  { id: "evals", title: "Evaluation and verification telemetry", short: "Evaluation and telemetry", to: "/evals" },
  { id: "note", title: "Engineering note", short: "Engineering note", to: "/note" },
] as const;

const KEY = "sf-demo-tour-dismissed";
const read = () => { try { return sessionStorage.getItem(KEY) === "1"; } catch { return false; } };

export default function Tour() {
  const [hidden, setHidden] = useState(read);
  const loc = useLocation();
  const here = (to: string) => {
    const [p, q] = to.split("?");
    return loc.pathname === p && (q ? loc.search.includes(q) : !loc.search.includes("tab=agents"));
  };
  return (
    <div className="demo-top">
      <div className="demo-banner" role="status">Static demo: recorded results from the repository. Nothing runs live here.</div>
      {!hidden && (
        <nav className="tour" aria-label="Guided tour">
          <span className="tour-label small">60-second tour</span>
          <ol>
            {STOPS.map((s, i) => (
              <li key={s.id}><NavLink to={s.to} className={here(s.to) ? "on" : ""} end><span className="tour-n">{i + 1}</span>{s.short}</NavLink></li>
            ))}
          </ol>
          <button className="btn quiet sm" onClick={() => { try { sessionStorage.setItem(KEY, "1"); } catch { /* fine */ } setHidden(true); }} aria-label="Dismiss the tour"><X size={14} /></button>
        </nav>
      )}
    </div>
  );
}
