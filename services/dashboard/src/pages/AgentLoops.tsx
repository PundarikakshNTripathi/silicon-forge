// SPDX-License-Identifier: AGPL-3.0-or-later
// AgentBench-SF: our own agent loop against agents built with LangGraph and Pydantic AI, from the committed result files.
import { useMemo, useState } from "react";
import { Empty, Panel } from "../ui";

interface Interval { mean: number; ci95: [number, number] }
interface ArmSummary {
  runs: number; solved_runs: number; task_success: Interval; task_content_ok: Interval; by_task: Record<string, number>;
  safety_violation_runs: number; first_prompt_tokens_median: number | null;
  metrics: {
    latency: { e2e_p50_s: number; e2e_p95_s: number; reasoning_share: number };
    tools: { calls: number; success_rate: number };
    cost: { tokens_per_run: number; tokens_per_solved: number | null };
    recovery: { failures: number; recovered: number; rate: number | null };
  };
}
export interface AgentBenchDoc {
  model: string; trials: number; tasks: string[]; created: number; versions: Record<string, string>; note: string;
  summary: { arms: Record<string, ArmSummary> };
}
interface BenchFile { file: string; data: Record<string, any> }

const NAMES: Record<string, string> = { custom: "SiliconForge loop", custom_prev: "SiliconForge loop (v0.6.0)", langgraph: "LangGraph", pydantic_ai: "Pydantic AI" };
const name = (arm: string) => {
  const [base, flag] = arm.split("+");
  return (NAMES[base] ?? base) + (flag ? " + our guards" : "");
};
const pct = (x: number | null | undefined, d = 0) => (x == null ? "-" : `${(x * 100).toFixed(d)}%`);

/** Arms are told apart by shape and by label, never by colour alone; our loop is the accent, frameworks are ink. */
function Marker({ arm, x, y }: { arm: string; x: number; y: number }) {
  const base = arm.split("+")[0], hollow = arm.includes("+");
  const fill = hollow ? "var(--paper-2)" : base === "custom" ? "var(--accent)" : "var(--ink-2)";
  const stroke = base === "custom" ? "var(--accent)" : "var(--ink-2)";
  if (base === "langgraph") return <rect x={x - 6} y={y - 6} width={12} height={12} fill={fill} stroke={stroke} strokeWidth={2} />;
  if (base === "pydantic_ai") return <polygon points={`${x},${y - 7} ${x + 7},${y + 6} ${x - 7},${y + 6}`} fill={fill} stroke={stroke} strokeWidth={2} />;
  return <circle cx={x} cy={y} r={7} fill={fill} stroke={stroke} strokeWidth={2} />;
}

function SuccessChart({ arms }: { arms: [string, ArmSummary][] }) {
  const W = 760, left = 190, right = 150, rowH = 44, H = 34 + arms.length * rowH;
  const X = (v: number) => left + v * (W - left - right);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" style={{ maxWidth: 860 }} role="img"
      aria-label={"Task success with 95% intervals: " + arms.map(([a, s]) => `${name(a)} ${pct(s.task_success.mean)}`).join(", ")}>
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <g key={t}><line className="grid" x1={X(t)} x2={X(t)} y1={10} y2={H - 26} /><text x={X(t)} y={H - 8} textAnchor="middle">{pct(t)}</text></g>
      ))}
      {arms.map(([arm, s], i) => {
        const y = 28 + i * rowH, [lo, hi] = s.task_success.ci95;
        return (
          <g key={arm}>
            <text x={left - 14} y={y + 4} textAnchor="end" style={{ fill: "var(--ink)", fontSize: 13.5 }}>{name(arm)}</text>
            <line x1={X(lo)} x2={X(hi)} y1={y} y2={y} stroke={arm.startsWith("custom") ? "var(--accent)" : "var(--ink-2)"} strokeWidth={7} opacity={0.28} />
            <Marker arm={arm} x={X(s.task_success.mean)} y={y} />
            {Math.abs(s.task_content_ok.mean - s.task_success.mean) > 1e-9 && (
              <rect x={X(s.task_content_ok.mean) - 4} y={y + 13} width={8} height={8} transform={`rotate(45 ${X(s.task_content_ok.mean)} ${y + 17})`}
                fill="var(--paper-2)" stroke="var(--ink-3)" strokeWidth={1.5}><title>Content-correct, forgiving a missing ANSWER line</title></rect>
            )}
            <text x={W - right + 14} y={y + 4} style={{ fill: "var(--ink)", fontSize: 13 }}>{pct(s.task_success.mean, 1)}  [{pct(lo)}, {pct(hi)}]</text>
          </g>
        );
      })}
    </svg>
  );
}

/** Per-task success: the rate in each cell, shaded by it (the number is always printed, so shade is a hint and not the data). */
function TaskMatrix({ doc, arms }: { doc: AgentBenchDoc; arms: [string, ArmSummary][] }) {
  return (
    <table className="t">
      <thead><tr><th>Task</th>{arms.map(([a]) => <th key={a} className="num">{name(a)}</th>)}</tr></thead>
      <tbody>
        {doc.tasks.map((task) => (
          <tr key={task}>
            <td className="mono">{task}</td>
            {arms.map(([a, s]) => {
              const v = s.by_task[task];
              return <td key={a} className="num" style={{ background: v == null ? undefined : `color-mix(in srgb, var(--accent) ${Math.round(v * 28)}%, transparent)` }}>{v == null ? "-" : pct(v)}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function AgentLoops({ files }: { files: BenchFile[] }) {
  // Newest first, and the newest is what opens: the older public-task suites stay in the list for comparison.
  const docs = useMemo(() => files.filter((f) => f.file.startsWith("agentbench-")).sort((a, b) => String(b.data.created).localeCompare(String(a.data.created))), [files]);
  const [pick, setPick] = useState<string | null>(null);
  const current = docs.find((d) => d.file === pick) ?? docs[0];
  if (!current) {
    return <Panel><Empty title="No agent-loop results yet">Run <code>siliconforge agentbench run --serve &lt;model&gt;.gguf</code>; the result file appears here.</Empty></Panel>;
  }
  const doc = current.data as AgentBenchDoc;
  const arms = Object.entries(doc.summary.arms);
  return (
    <>
      <div className="row wrap">
        <label className="field" style={{ maxWidth: 420 }}><span>Result file</span>
          <select value={current.file} onChange={(e) => setPick(e.target.value)}>{docs.map((d) => <option key={d.file} value={d.file}>{d.file}</option>)}</select></label>
        <span className="muted small" style={{ alignSelf: "end", paddingBottom: 8 }}>{doc.model}, {doc.tasks.length} tasks, {doc.trials} trial(s) each</span>
      </div>
      <Panel layer="formal" title="Task success">
        <SuccessChart arms={arms} />
        <p className="hint">Strict score with a 95% bootstrap interval over tasks. The diamond is the content-correct reading (a labelled secondary). The same model, tools,
          sandbox and step budget for every loop; the intervals are wide with {doc.tasks.length} tasks. See <code>docs/agent-benchmark.md</code> for what this does not show.</p>
      </Panel>
      <Panel layer="signoff" title="Time, tools, recovery, cost" flush>
        <table className="t">
          <thead><tr><th>Loop</th><th className="num">Median time</th><th className="num">95th pct</th><th className="num">Tool success</th><th className="num">Recovered</th><th className="num">Tokens per task</th><th className="num">First prompt</th><th className="num">Safety violations</th></tr></thead>
          <tbody>
            {arms.map(([a, s]) => (
              <tr key={a}>
                <td>{name(a)}</td>
                <td className="num">{s.metrics.latency.e2e_p50_s.toFixed(1)} s</td>
                <td className="num">{s.metrics.latency.e2e_p95_s.toFixed(0)} s</td>
                <td className="num">{pct(s.metrics.tools.success_rate)}</td>
                <td className="num">{s.metrics.recovery.failures ? `${s.metrics.recovery.recovered} of ${s.metrics.recovery.failures}` : "-"}</td>
                <td className="num">{Math.round(s.metrics.cost.tokens_per_run).toLocaleString()}</td>
                <td className="num">{s.first_prompt_tokens_median?.toLocaleString() ?? "-"}</td>
                <td className="num">{s.safety_violation_runs} of {s.runs}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel layer="rtl" title="By task" flush><TaskMatrix doc={doc} arms={arms} /></Panel>
      <p className="hint">Versions: {Object.entries(doc.versions).map(([k, v]) => `${k} ${v}`).join(", ") || "not recorded"}. Cost is tokens and time because the model is local.</p>
    </>
  );
}
