// SPDX-License-Identifier: AGPL-3.0-or-later
// Replay of a recorded agent session with a real approval checkpoint. The recording comes from
// services/orchestrator/demo/record_trace.py: the agent, tools, lint engine and approval code are the real ones, the model is a script.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronsRight, Pause, Play, RotateCcw, StepForward, Undo2 } from "lucide-react";
import { api, artifactUrl, type EventRec, type PPA, type RunDetail } from "../api";
import { Badge, DiffView, Panel, Prov, Skeleton } from "../ui";
import { DEFAULT_RUN, assetUrl } from "./mode";
import { loadJson } from "./demoApi";

export interface TraceEvent {
  i: number; kind: string; text: string; tool?: string; args?: Record<string, any>; context?: string;
  permission?: string; decision?: "approved" | "declined"; summary?: string;
}
export interface Trace {
  schema: string; task: string; steps_before_checkpoint: number;
  provenance: { model: string; real: string[]; scripted: string[]; context_chars: number; siliconforge_version: string; example: string };
  events_before: TraceEvent[]; checkpoint: { permission: string; tool: string; args: Record<string, any>; summary: string };
  approve: TraceEvent[]; revise: TraceEvent[];
}
type Branch = "approve" | "revise";

/** Which stage of the work an event belongs to; drives the highlight strip and nothing else. */
export const STAGES = [
  { key: "spec", label: "Spec and tools" },
  { key: "rtl", label: "RTL" },
  { key: "guardrail", label: "Guardrail" },
  { key: "context", label: "Context" },
  { key: "approval", label: "Approval" },
] as const;
export type StageKey = (typeof STAGES)[number]["key"];

export function stageOf(e: TraceEvent): StageKey | null {
  if (e.kind === "approval_request" || e.kind === "approval") return "approval";
  if (e.kind === "tool_call" || e.kind === "tool_result") {
    if (e.tool === "lint_rtl") return "guardrail";
    if (e.tool === "write_file" || e.tool === "edit_file") return "rtl";
    return "spec";
  }
  return null;
}

const CONTEXT_NOTE: Record<string, string> = {
  summaries: "Older tool results are now one-line summaries; the newest stay whole.",
  digest: "Earlier turns are replaced by a short digest built by code, not by a model.",
  "digest+shrunk": "Earlier turns are a digest and every old tool result is a one-line summary, to stay inside the context budget.",
};

function parse(text: string): any {
  try { return JSON.parse(text); } catch { return null; }
}

function Finding({ f }: { f: { rule: string; severity: string; line: number; message: string } }) {
  return (
    <div className="rp-finding">
      <Badge kind={f.severity === "ERROR" ? "bad" : "warn"}>{f.severity.toLowerCase()}</Badge>
      <b className="mono">{f.rule}</b>
      <span className="muted small">line {f.line}</span>
      <span>{f.message}</span>
    </div>
  );
}

function ToolCall({ e }: { e: TraceEvent }) {
  const a = e.args ?? {};
  const hint = e.tool === "write_file" ? `${a.path} · ${(a.content as string).split("\n").length - 1} lines`
    : e.tool === "edit_file" ? `${a.path}` : (a.path ?? "");
  return (
    <details className="rp-call" open={e.tool === "edit_file"}>
      <summary><span className="rp-tool mono">{e.tool}</span><span className="muted small">{hint}</span></summary>
      {e.tool === "edit_file" && <DiffView before={String(a.old)} after={String(a.new)} />}
      {e.tool === "write_file" && <pre className="code mono rp-code" tabIndex={0}>{String(a.content)}</pre>}
    </details>
  );
}

function ToolResult({ e }: { e: TraceEvent }) {
  const j = parse(e.text);
  if (e.tool === "lint_rtl" && j) {
    return (
      <div className="rp-result" data-stage="guardrail">
        <div className="rp-result-head"><b>Static lint</b>{j.passed ? <Badge kind="ok">clean</Badge> : <Badge kind="bad">rejected</Badge>}</div>
        {j.findings.length === 0 && <div className="muted small">No findings.</div>}
        {j.findings.map((f: any, k: number) => <Finding key={k} f={f} />)}
      </div>
    );
  }
  if (e.tool === "read_file") { // the recorded text is clipped at 600 characters, so it may not parse; the head always has the facts
    const m = e.text.match(/"path": "([^"]+)", "total_lines": (\d+)/);
    if (m) return <div className="rp-result"><div className="rp-result-head"><b>Read {m[1]}</b><span className="muted small">{m[2]} lines returned to the model</span></div></div>;
  }
  if (e.text.startsWith("denied:")) {
    return <div className="rp-result"><div className="rp-result-head"><b>Not run</b><Badge kind="warn">declined by you</Badge></div></div>;
  }
  if (j && (j.bytes != null || j.replaced != null)) {
    return <div className="rp-result"><div className="rp-result-head"><b>{j.path}</b><span className="muted small">{j.bytes != null ? `${j.bytes} bytes, ${j.lines} lines written` : `${j.replaced} snippet replaced`}</span></div></div>;
  }
  return <pre className="rp-result mono small">{e.text}</pre>;
}

function EventRow({ e, prev }: { e: TraceEvent; prev: string }) {
  const note = e.kind === "tool_call" && e.context && e.context !== prev && e.context !== "full" ? CONTEXT_NOTE[e.context] : null;
  return (
    <>
      {note && <div className="rp-context" role="note"><ChevronsRight size={14} /><span><b>Context compaction ({e.context}).</b> {note}</span></div>}
      {e.kind === "user" && <div className="rp-user"><span className="rp-who">You</span><p>{e.text}</p></div>}
      {e.kind === "assistant" && <div className="rp-say"><span className="rp-who">Scripted model</span><p>{e.text}</p></div>}
      {e.kind === "tool_call" && <ToolCall e={e} />}
      {e.kind === "tool_result" && <ToolResult e={e} />}
      {e.kind === "approval_request" && (
        <div className="rp-approval" data-decision={e.decision}>
          <Check size={15} /><span><b>{e.decision === "approved" ? "You approved" : "You declined"}</b> <span className="mono">{e.tool}</span> ({e.permission} permission)</span>
        </div>
      )}
      {e.kind === "done" && <div className="rp-done">Turn finished.</div>}
    </>
  );
}

function RecordedRun() {
  const run = useQuery({ queryKey: ["run", DEFAULT_RUN], queryFn: () => api<RunDetail>(`/runs/${DEFAULT_RUN}`) });
  const events = useQuery({ queryKey: ["events", DEFAULT_RUN], queryFn: () => api<EventRec[]>(`/runs/${DEFAULT_RUN}/events/all`) });
  if (!run.data || !events.data) return <Skeleton h={160} />;
  const best = (run.data.state?.best_ppa ?? null) as PPA | null;
  const rows = events.data.filter((x) => x.stage !== "INIT" && x.status !== "EPISODE");
  const retries = rows.filter((x) => x.status === "RETRY").length;
  return (
    <Panel layer="signoff" title={<>What the full pipeline did with the same example <Prov mode={run.data.backend} /></>}
      actions={<Link className="btn sm" to={`/runs/${DEFAULT_RUN}`}>Open the full run</Link>}>
      <p className="muted small" style={{ maxWidth: 760, marginTop: 0 }}>
        A separate recording: a real pipeline run of <code>{run.data.module}</code> on the {run.data.backend} backend (Yosys, SymbiYosys, OpenROAD).
        The RTL above was not fed to it.{retries > 0 && ` Formal verification sent the first design back ${retries === 1 ? "once" : `${retries} times`}; the next one passed.`}
      </p>
      <div className="rp-run">
        <table className="t">
          <tbody>{rows.map((x) => (
            <tr key={x.seq}><td className="mono small">{x.stage.toLowerCase().replace("_", " ")}</td>
              <td><Badge kind={x.status === "PASS" ? "ok" : x.status === "RETRY" ? "warn" : "neutral"}>{x.status.toLowerCase()}</Badge></td>
              <td className="small">{x.detail}</td></tr>
          ))}</tbody>
        </table>
        <figure className="rp-layout">
          <img src={artifactUrl(DEFAULT_RUN, "layout.png")} alt={`Rendered layout of ${run.data.module}, from the real GDSII`} loading="lazy" />
          {best && <figcaption className="small muted">Worst slack {best.wns_ns.toFixed(2)} ns, area {Math.round(best.area_um2)} µm², {best.drc_violations} DRC violations, LVS {best.lvs_clean ? "clean" : "not clean"}.</figcaption>}
        </figure>
      </div>
    </Panel>
  );
}

export default function Replay() {
  const q = useQuery({ queryKey: ["trace"], queryFn: () => loadJson<Trace>("replay.json") });
  const trace = q.data;
  const [branch, setBranch] = useState<Branch | null>(null);
  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const approveBtn = useRef<HTMLButtonElement>(null);
  const reduced = useMemo(() => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false, []);

  const seq = useMemo(() => (trace ? [...trace.events_before, ...(branch ? trace[branch] : [])] : []), [trace, branch]);
  const gate = trace ? trace.events_before.length : 0;
  const atGate = !!trace && branch == null && pos >= gate;
  const finished = !!trace && branch != null && pos >= seq.length;

  const next = () => { if (trace && !atGate && pos < seq.length) setPos((p) => p + 1); };
  useEffect(() => {
    if (!playing || !trace) return;
    if (atGate || pos >= seq.length) { setPlaying(false); return; }
    const e = seq[pos];
    const base = e.kind === "tool_result" || e.kind === "approval_request" ? 520 : e.kind === "assistant" ? 900 : 700;
    const t = setTimeout(() => setPos((p) => p + 1), reduced ? 120 : base / speed);
    return () => clearTimeout(t);
  }, [playing, pos, seq, atGate, speed, trace, reduced]);
  useEffect(() => { if (atGate) approveBtn.current?.focus(); }, [atGate]);

  const choose = (b: Branch) => { setBranch(b); setPlaying(true); };
  const reset = () => { setBranch(null); setPos(0); setPlaying(false); };

  if (q.isLoading || !trace) return <Skeleton h={260} />;
  const shown = seq.slice(0, pos);
  const active = shown.length ? stageOf(shown[shown.length - 1]) ?? (shown.at(-1)?.context && shown.at(-1)?.context !== "full" ? "context" : null) : null;
  const seen = new Set(shown.flatMap((e) => [stageOf(e), e.context && e.context !== "full" ? "context" : null]).filter(Boolean) as string[]);

  return (
    <div className="stack">
      <div className="rp-prov" role="note">
        <b>Recorded replay.</b> The agent, its tools, the static linter and the approval path are the real code from this repository; the
        model is a script of fixed replies, so no language model is called and none is being judged. Nothing here runs live.
      </div>

      <div className="rp-controls" role="toolbar" aria-label="Replay controls">
        <button className="btn primary" onClick={() => (finished ? reset() : setPlaying((p) => !p))} disabled={atGate}>
          {finished ? <><RotateCcw size={15} /> Replay</> : playing ? <><Pause size={15} /> Pause</> : <><Play size={15} /> {pos === 0 ? "Play" : "Resume"}</>}
        </button>
        <button className="btn" onClick={() => { setPlaying(false); next(); }} disabled={atGate || finished}><StepForward size={15} /> Step</button>
        <button className="btn" onClick={reset} disabled={pos === 0 && !branch}><Undo2 size={15} /> Reset</button>
        <label className="rp-speed small">Speed
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Playback speed">
            <option value={0.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option>
          </select>
        </label>
        <span className="grow" />
        <span className="muted small" aria-live="polite">{Math.min(pos, seq.length)} of {branch ? seq.length : `${gate}+`} events</span>
      </div>

      <ol className="rp-stages" aria-label="Stages of the work">
        {STAGES.map((s) => (
          <li key={s.key} data-stage={s.key} data-seen={seen.has(s.key)} aria-current={active === s.key ? "step" : undefined}>{s.label}</li>
        ))}
      </ol>

      <div className="rp-grid">
        <section className="rp-log" aria-label="Session transcript" aria-live="polite">
          <div className="rp-task muted small">Task given to the agent: {trace.task}</div>
          {shown.map((e, k) => <EventRow key={`${branch}-${e.i}-${k}`} e={e} prev={k > 0 ? shown.slice(0, k).reverse().find((x) => x.context)?.context ?? "full" : "full"} />)}
          {pos === 0 && <div className="empty small">Press Play. The replay pauses at the first edit and waits for your decision.</div>}
          {atGate && (
            <div className="rp-gate" role="group" aria-label="Approval checkpoint">
              <h3>Approval needed</h3>
              <p>The agent wants to run <code>{trace.checkpoint.tool}</code> on <code>{trace.checkpoint.args.path}</code> ({trace.checkpoint.permission} permission, workspace in “ask” mode). Nothing is written until you decide.</p>
              <pre className="code mono rp-code" tabIndex={0}>{String(trace.checkpoint.args.content)}</pre>
              <div className="row wrap">
                <button ref={approveBtn} className="btn primary" onClick={() => choose("approve")}>Approve</button>
                <button className="btn" onClick={() => choose("revise")}>Request revision</button>
              </div>
              <p className="muted small">Both outcomes were recorded from the real agent. A decline ends the turn; the revision is then a second message from you.</p>
            </div>
          )}
          {finished && <div className="rp-end small muted">End of recording ({branch === "approve" ? "approved" : "revision requested"}). <button className="btn quiet sm" onClick={reset}>Try the other choice</button></div>}
        </section>

        <aside className="rp-side">
          <Panel title="What is real here" className="sunken">
            <ul className="small rp-list">
              {trace.provenance.real.map((r) => <li key={r}>{r}</li>)}
            </ul>
            <div className="small muted" style={{ marginTop: 8 }}>Scripted: {trace.provenance.scripted.join(", ")}. Context budget {trace.provenance.context_chars.toLocaleString()} characters (small on purpose, so compaction shows).</div>
          </Panel>
        </aside>
      </div>

      {finished && <RecordedRun />}
      <div className="muted small">Recorded with SiliconForge {trace.provenance.siliconforge_version}. Regenerate with <code>python scripts/export_demo_bundle.py</code>. The terminal version of the same approval flow is on the other tab.</div>
    </div>
  );
}

export function TerminalTab() {
  return (
    <div className="stack">
      <p className="muted small" style={{ maxWidth: 720, margin: 0 }}>The same agent runs in a terminal (<code>siliconforge chat</code>) with the same approval prompt. These are captures of the real interface.</p>
      <div className="rp-shots">
        <figure><img src={assetUrl("tui-chat.png")} alt="Terminal chat: a conversation with tool-call cards" loading="lazy" /><figcaption className="small muted">Chat with tool-call cards.</figcaption></figure>
        <figure><img src={assetUrl("tui-approval.png")} alt="Terminal approval prompt for an edit" loading="lazy" /><figcaption className="small muted">The approval prompt before an edit.</figcaption></figure>
      </div>
      <div className="rp-shots">
        <figure><img src={assetUrl("tui.gif")} alt="Animated capture of the terminal interface" loading="lazy" /><figcaption className="small muted">A real session of the terminal interface with a local 9B model, about a minute, captured through a browser-hosted terminal.</figcaption></figure>
        <figure><img src={assetUrl("web-tour.gif")} alt="Animated capture of the web interface running a flow" loading="lazy" /><figcaption className="small muted">Web interface, animated.</figcaption></figure>
      </div>
    </div>
  );
}
