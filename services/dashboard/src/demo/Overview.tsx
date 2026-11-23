// SPDX-License-Identifier: AGPL-3.0-or-later
// Landing page of the static demo: the four things worth a minute, each with a fact computed from the committed result files.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { STOPS } from "./Tour";

interface Bench { file: string; data: Record<string, any> }

export default function Overview() {
  const q = useQuery({ queryKey: ["bench"], queryFn: () => api<Bench[]>("/benchmarks") });
  const files = q.data ?? [];
  const ab = files.find((f) => f.file.startsWith("agentbench-") && f.file.includes("-run1-"))?.data;
  const facts: Record<string, string> = {
    replay: "A recorded agent session. It pauses at the first edit and waits for your decision.",
    agentbench: ab ? `${ab.arms.length} agent setups, ${ab.tasks.length} tasks, ${ab.trials} trials each, repeated three times; one local ${ab.model} model throughout.` : "",
    evals: files.length ? `${files.length} committed result files: VerilogEval, full-flow runs, formal and equivalence results, PPA search, telemetry.` : "",
    note: "Written by the author.",
  };
  return (
    <>
      <div className="topbar"><span className="title">SiliconForge: static demo</span></div>
      <div className="page stack">
        <p className="muted" style={{ maxWidth: 700, margin: 0 }}>
          SiliconForge turns a hardware spec into verified RTL, formal proofs and a layout, using open EDA tools. Tool verdicts decide; language models
          propose. This page is a static copy of its dashboard: everything is a recording of this repository's own results, and nothing runs live.
        </p>
        <ol className="tour-cards">
          {STOPS.map((s, i) => (
            <li key={s.id}>
              <Link to={s.to} className="tour-card">
                <span className="tour-n">{i + 1}</span>
                <span><b>{s.title}</b><span className="muted small">{facts[s.id]}</span></span>
              </Link>
            </li>
          ))}
        </ol>
        <p className="muted small" style={{ maxWidth: 700, margin: 0 }}>
          The two real pipeline runs under <Link to="/runs">Runs</Link> were made on the WSL backend with Yosys, SymbiYosys and OpenROAD; each carries a provenance stamp.
        </p>
      </div>
    </>
  );
}
