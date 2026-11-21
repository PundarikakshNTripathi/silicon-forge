import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Download, GitCompare, Play, Repeat, Square } from "lucide-react";
import { api, artifactUrl, post, type PPA, type RunDetail as Run } from "../api";
import { deriveView, type FormalIter, type LlmCall, type RunView, type StageKey } from "../derive";
import { Bars, ParetoChart, RewardChart, Waveform } from "../charts";
import { PipelineStrip } from "../PipelineStrip";
import { Banner, Badge, Check, CodeView, DiffView, Empty, Panel, Prov, Skeleton, Stat, StatusBadge, Tabs } from "../ui";
import { compact, num, secs, toast, usd, useRunEvents } from "../lib";

type Tab = "overview" | "design" | "verify" | "ppa" | "layout" | "agent" | "cost";
const TAB_OF_STAGE: Record<StageKey, Tab> = { contract: "design", rtl: "design", guardrails: "design", formal: "verify", lec: "verify", pnr: "ppa", signoff: "overview" };

export default function RunDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const run = useQuery({
    queryKey: ["run", id], queryFn: () => api<Run>(`/runs/${id}`),
    refetchInterval: (q) => (q.state.data && (q.state.data.status === "running" || q.state.data.status === "queued") ? 3000 : false),
  });
  const allEvents = useRunEvents(id, run.data, () => qc.invalidateQueries({ queryKey: ["run", id] }));
  // replay: step through the stored event stream to see how the run unfolded
  const [replay, setReplay] = useState<number | null>(null);
  const query = useRef(new URLSearchParams(window.location.search));
  const autoplay = useRef(query.current.has("replay") || query.current.has("replayAt"));
  const frozen = useRef(query.current.has("replayAt")); // ?replayAt=N shows the run as of event N (deterministic docs and screenshots)
  useEffect(() => {
    if (autoplay.current && allEvents.length && run.data && !["running", "queued"].includes(run.data.status)) {
      autoplay.current = false;
      setReplay(frozen.current ? Math.min(allEvents.length, Number(query.current.get("replayAt"))) : 0);
    }
  }, [allEvents.length, run.data]);
  useEffect(() => {
    if (replay == null || frozen.current) return;
    if (replay >= allEvents.length) { setReplay(null); return; }
    const t = setTimeout(() => setReplay(replay + 1), allEvents[replay]?.stage === "LLM" ? 120 : 420);
    return () => clearTimeout(t);
  }, [replay, allEvents]);
  const events = replay == null ? allEvents : allEvents.slice(0, replay);
  const view = useMemo(() => deriveView(events), [events]);
  const cancel = useMutation({ mutationFn: () => post(`/runs/${id}/cancel`), onSuccess: () => toast("Cancelling after the current step…") });
  const rerun = useMutation({
    mutationFn: () => post<{ run_id: string }>("/runs", run.data?.request),
    onSuccess: (r) => nav(`/runs/${r.run_id}`),
    onError: (e: Error) => toast(`Could not start the run: ${e.message}`),
  });

  if (run.isLoading) return <div className="page stack"><Skeleton h={30} w={280} /><Skeleton h={150} /><Skeleton h={300} /></div>;
  if (run.error || !run.data) {
    return <div className="page"><Empty title="Run not found" action={<Link to="/runs" className="btn">Back to runs</Link>}>It may have been deleted.</Empty></div>;
  }
  const r = run.data;
  const live = r.status === "running" || r.status === "queued";
  const mode = view.mode !== "simulated" || events.length ? view.mode : r.backend;
  const mod = view.contract?.module_name ?? r.module;
  const fromEvents: PPA | null = view.episodes.length ? view.episodes.reduce((a, b) => (b.reward > a.reward ? b : a)) : null;
  // while replaying, show only what had happened by that event
  const best: PPA | null = replay != null ? fromEvents : ((r.state?.best_ppa as PPA | null) ?? fromEvents);
  const failedVerify = view.formal.some((f) => !f.passed) && !view.formal.at(-1)?.passed;
  const elapsed = r.duration_s ?? (live ? Date.now() / 1000 - r.created : null);

  return (
    <>
      <div className="topbar">
        <Link to="/runs" className="muted small">Runs</Link><span className="muted">/</span>
        <span className="title">{mod ?? "New design"}</span>
        <span className="grow" />
        {replay != null && <span className="badge info">Replaying {replay} of {allEvents.length}</span>}
        {replay != null ? <button className="btn" onClick={() => setReplay(null)}><Square size={14} /> Stop replay</button>
          : !live && allEvents.length > 0 && <button className="btn" onClick={() => setReplay(0)}><Play size={14} /> Replay</button>}
        {live && <button className="btn" onClick={() => cancel.mutate()}><Square size={14} /> Cancel</button>}
        {!live && r.request && <button className="btn" onClick={() => rerun.mutate()} disabled={rerun.isPending}><Repeat size={14} /> Run again</button>}
        <a className="btn" href={artifactUrl(r.id, "signoff_report.json")} download><Download size={14} /> Report</a>
      </div>

      <div className="page stack">
        {mode === "simulated" && (
          <Banner kind="sim"><strong>Simulated results.</strong> This run used surrogate models, so timing, area and proofs are not from real tools. Choose a real backend on the New run screen to get real results.</Banner>
        )}
        {r.status === "failed" && <FailureHelp error={r.error ?? view.error} />}
        {r.status === "interrupted" && <Banner kind="bad">The backend restarted while this run was in progress, so it cannot continue. Start it again to retry.</Banner>}

        <div className="titleblock" aria-label="Run identification">
          <div><div className="cap">Design</div><div className="val name">{mod ?? "New design"}</div></div>
          <div><div className="cap">RTL revision</div><div className="val">{view.rtl.length ? `rev ${view.rtl.length}` : "none yet"}</div></div>
          <div><div className="cap">Evidence from</div><div className="val"><Prov mode={mode} /></div></div>
          <div><div className="cap">Status</div><div className="val"><StatusBadge status={r.status} /></div></div>
          <div><div className="cap">Time, cost</div><div className="val">{secs(elapsed)}, {usd(view.cost.usd || r.cost_usd)}</div></div>
        </div>

        <ResultsTable best={best} contract={view.contract} sim={mode === "simulated"} />

        <PipelineStrip view={view} onSelect={(k) => setTab(TAB_OF_STAGE[k])} />

        <Tabs value={tab} onChange={setTab} tabs={[
          { id: "overview", label: "Overview" }, { id: "design", label: "Design" },
          { id: "verify", label: "Verification", flag: failedVerify }, { id: "ppa", label: "Performance" },
          { id: "layout", label: "Layout & files" }, { id: "agent", label: `Agent log${view.calls.length ? ` (${view.calls.length})` : ""}` },
          { id: "cost", label: "Cost" },
        ]} />

        {tab === "overview" && <Overview run={r} view={view} events={events} live={live} />}
        {tab === "design" && <Design run={r} view={view} />}
        {tab === "verify" && <Verify run={r} view={view} />}
        {tab === "ppa" && <Ppa run={r} view={view} best={best} mode={mode} />}
        {tab === "layout" && <LayoutFiles run={r} best={best} />}
        {tab === "agent" && <AgentLog calls={view.calls} />}
        {tab === "cost" && <Cost run={r} view={view} />}
      </div>
    </>
  );
}

function FailureHelp({ error }: { error: string | null }) {
  const e = error ?? "The run failed.";
  const missing = /missing required tools/i.test(e);
  const budget = /budget/i.test(e);
  const replay = /replay fixture/i.test(e);
  return (
    <Banner kind="bad">
      <strong>The run stopped.</strong> {e}
      {missing && <div className="small" style={{ marginTop: 4 }}>The selected backend has no EDA tools. <Link to="/system">Check the toolchain</Link> or choose another backend.</div>}
      {budget && <div className="small" style={{ marginTop: 4 }}>Raise the spending limit under More options when you start a run, or use a cheaper profile.</div>}
      {replay && <div className="small" style={{ marginTop: 4 }}>The bundled-examples profile only knows the example designs. Pick a model profile in <Link to="/models">Models</Link>.</div>}
    </Banner>
  );
}

// ---------- overview ----------
function Overview({ run, view, events, live }: { run: Run; view: RunView; events: { seq: number; stage: string; status: string; detail: string }[]; live: boolean }) {
  const logRef = useRef<HTMLDivElement>(null);
  const log = events.filter((e) => e.stage !== "LLM" && e.stage !== "RTL");
  useEffect(() => { if (live && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [log.length, live]);
  return (
    <div className="grid2 split-b" style={{ alignItems: "start" }}>
      <Panel title="Signoff checklist">
        {view.signoff ? (
          view.signoff.checks.map((c) => <Check key={c.name} ok={c.passed} title={c.name} detail={c.detail} />)
        ) : (
          <>
            <Check ok={view.lec ? view.lec.ok : null} title="RTL equals netlist (LEC)" detail={view.lec ? `${view.lec.cells} cells` : "Pending"} />
            <Check ok={view.formal.length ? !!view.formal.at(-1)?.passed : null} title="Formal proofs, non-vacuous" detail={view.formal.length ? `${view.formal.length} run(s)` : "Pending"} />
            <Check ok={view.sim ? view.sim.ok : null} title="Lint" detail={view.sim?.summary ?? "Pending"} />
            <div className="hint" style={{ paddingTop: 8 }}>{live ? "Timing, DRC and LVS are checked after layout." : "No signoff data for this run."}</div>
          </>
        )}
        {view.signoff?.eco && <div className="small" style={{ marginTop: 10 }}><strong>Suggested next step:</strong> {view.signoff.eco.recommendation}</div>}
      </Panel>
      <Panel title="What happened" flush actions={live ? <Badge kind="run">Live</Badge> : undefined}>
        <div className="log" ref={logRef} style={{ maxHeight: 420, overflowY: "auto" }}>
          {log.length === 0 && <div className="empty small">Waiting for the first step…</div>}
          {log.map((e) => (
            <div className="evt" key={e.seq}><span className="muted">#{e.seq}</span><span>{e.stage.toLowerCase().replaceAll("_", " ")}</span><span className={`st ${e.status}`}>{e.status.toLowerCase()}</span><span>{e.detail}</span></div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

// ---------- design ----------
function Design({ run, view }: { run: Run; view: RunView }) {
  const [ver, setVer] = useState(-1);
  const [diff, setDiff] = useState(false);
  const rtl = view.rtl.length ? view.rtl : ((run.state?.rtl_versions as { label: string; source: string }[]) ?? []);
  const idx = ver < 0 ? rtl.length - 1 : ver;
  const c = view.contract ?? (run.state?.contract as RunView["contract"]);
  return (
    <div className="stack">
      <div className="grid2 split-c" style={{ alignItems: "start" }}>
        <Panel title="Design contract" flush>
          {!c ? <Empty title="Waiting for the contract">The architect agent is reading the spec.</Empty> : (
            <>
              <div className="body small ink2">Clock <code>{c.clock_port}</code>, reset <code>{c.reset_port}</code> ({c.reset_active_low ? "active low" : "active high"}, {c.reset_sync ? "synchronous" : "asynchronous"}). Targets: {c.target_clock_ns} ns, {c.max_area_um2} µm², {c.max_power_mw} mW.</div>
              <table className="t"><thead><tr><th>Port</th><th>Direction</th><th className="num">Width</th><th>Purpose</th></tr></thead>
                <tbody>{c.ports.map((p) => <tr key={p.name}><td className="mono">{p.name}</td><td>{p.direction}</td><td className="num">{p.width}</td><td className="muted">{p.description}</td></tr>)}</tbody></table>
            </>
          )}
        </Panel>
        <Panel title="Guardrail audits">
          {view.guardrails.length === 0 && <div className="muted small">No audits yet.</div>}
          {view.guardrails.map((g, i) => (
            <div key={i} style={{ marginBottom: 10 }}>
              <Check ok={g.ok} title={`Attempt ${g.attempt}: ${g.ok ? "passed" : "violations found"}`} detail={g.detail} />
              {g.violations.map((v, j) => (
                <div key={j} className="small" style={{ paddingLeft: 26 }}><span className="mono">{v.rule_id}</span> {v.severity === "WARNING" ? <Badge kind="warn">warning</Badge> : null} {v.message}{v.line_number ? ` (line ${v.line_number})` : ""}</div>
              ))}
            </div>
          ))}
          {view.sim && <Check ok={view.sim.ok} title="Verilator lint" detail={view.sim.summary} />}
        </Panel>
      </div>
      <Panel title="RTL" flush actions={
        <div className="row">
          {rtl.length > 1 && (
            <>
              <select style={{ width: 230 }} value={idx} onChange={(e) => setVer(+e.target.value)} aria-label="RTL version">
                {rtl.map((v, i) => <option key={i} value={i}>{i + 1}. {v.label}</option>)}
              </select>
              <button className={`btn sm ${diff ? "primary" : ""}`} onClick={() => setDiff((d) => !d)} disabled={idx === 0}><GitCompare size={13} /> Compare with previous</button>
            </>
          )}
        </div>}>
        {rtl.length === 0 ? <Empty title="No RTL yet">The RTL agent has not produced code.</Empty>
          : diff && idx > 0 ? <DiffView before={rtl[idx - 1].source} after={rtl[idx].source} /> : <CodeView source={rtl[idx].source} />}
      </Panel>
    </div>
  );
}

// ---------- verification ----------
const taskKind = (s: string) => (s === "PASS" ? "ok" : s === "FAIL" ? "bad" : "warn");

function FormalCard({ f, mode }: { f: FormalIter; mode: string }) {
  return (
    <Panel title={<span>Formal run {f.iteration}</span>} actions={<span className="row"><Prov mode={mode} /><Badge kind={f.passed ? "ok" : "bad"}>{f.passed ? "Proven" : "Counterexample"}</Badge></span>}>
      <div className="row wrap" style={{ marginBottom: 8 }}>
        {Object.entries(f.task_status).map(([t, s]) => <Badge key={t} kind={taskKind(s)}>{t === "prove" ? "unbounded proof" : t === "bmc" ? "bounded check" : "cover"}: {s.toLowerCase()}</Badge>)}
        <span className="muted small">{secs(f.elapsed_sec)}</span>
      </div>
      {f.witnesses.map((w, i) => (
        <div key={i} className="stack" style={{ gap: 8, marginTop: 8 }}>
          <div><strong className="mono">{w.property_id}</strong> failed at step {w.cycle}. <span className="muted">Slicing on <code>{w.failing_signal || "n/a"}</code>.</span></div>
          <div className="small ink2">{w.summary}</div>
          {w.trace && <Waveform trace={w.trace} failStep={w.cycle} highlight={w.failing_signal} />}
        </div>
      ))}
      {!f.passed && f.witnesses.length === 0 && <div className="small muted">No trace available (vacuity or a front-end error).</div>}
    </Panel>
  );
}

function Verify({ run, view }: { run: Run; view: RunView }) {
  const props = (run.state?.formal_properties_ir as Record<string, any>[] | undefined) ?? [];
  const sva = (run.state?.sva_source as string | undefined) ?? "";
  return (
    <div className="stack">
      {view.formal.length === 0 && <Empty title="No formal runs yet">Properties are generated after the RTL passes the guardrails.</Empty>}
      <div className="grid2" style={{ alignItems: "start" }}>{view.formal.map((f) => <FormalCard key={f.iteration} f={f} mode={f.mode} />)}</div>
      <div className="grid2" style={{ alignItems: "start" }}>
        {view.lec && (
          <Panel title="Synthesis and equivalence" actions={<Prov mode={view.lec.mode} />}>
            <Check ok={view.lec.ok} title={view.lec.ok ? "Netlist is equivalent to the RTL" : "Netlist is not equivalent"} detail={`${view.lec.cells} cells synthesized in ${secs(view.lec.elapsed)}`} />
            {view.lec.logTail && <pre className="code mono" style={{ borderRadius: 6 }}>{view.lec.logTail}</pre>}
          </Panel>
        )}
        {props.length > 0 && (
          <Panel title="Properties being proven" flush>
            <table className="t"><thead><tr><th>Name</th><th>Kind</th><th>Meaning</th></tr></thead>
              <tbody>{props.map((p) => <tr key={p.id}><td className="mono">{p.id}</td><td>{String(p.kind).toLowerCase().replaceAll("_", " ")}</td><td className="muted">{p.description}</td></tr>)}</tbody></table>
          </Panel>
        )}
      </div>
      {sva && <Panel title="Generated assertions (SystemVerilog)" flush><CodeView source={sva} maxHeight={320} /></Panel>}
    </div>
  );
}

// ---------- performance ----------
function Ppa({ run, view, best, mode }: { run: Run; view: RunView; best: PPA | null; mode: string }) {
  const eps = view.episodes.length ? view.episodes : ((run.state?.ppa_episodes as PPA[]) ?? []);
  const front = (run.state?.pareto_front as PPA[]) ?? [];
  if (!eps.length) return <Empty title="No layout results yet">Place-and-route starts after synthesis passes equivalence checking.</Empty>;
  const bi = best ? eps.findIndex((e) => e.reward === best.reward && e.area_um2 === best.area_um2) : -1;
  return (
    <div className="stack">
      <div className="grid2 split-a" style={{ alignItems: "start" }}>
        <Panel title="Area and timing across layout episodes" actions={<Prov mode={mode} />}>
          <ParetoChart episodes={eps} front={front} bestIndex={bi} />
          <div className="hint">Large rings are on the Pareto front, the blue ring is the chosen result.{mode === "simulated" ? " Hatched marks are simulated." : ""}</div>
        </Panel>
        <Panel title="Optimizer reward"><RewardChart episodes={eps} /></Panel>
      </div>
      <Panel title="Episodes" flush>
        <div style={{ overflowX: "auto" }}>
          <table className="t">
            <thead><tr><th>#</th><th>Synthesis</th><th className="num">Util %</th><th className="num">Clock</th><th className="num">Slack ns</th><th className="num">Area µm²</th><th className="num">Power mW</th><th className="num">DRC</th><th className="num">Reward</th><th /></tr></thead>
            <tbody>{eps.map((e, i) => (
              <tr key={i} style={i === bi ? { background: "var(--blue-soft)" } : undefined}>
                <td>{i + 1}</td><td>{String(e.knobs.SYNTH_STRATEGY ?? "-")}</td><td className="num">{e.knobs.FP_CORE_UTIL ?? "-"}</td><td className="num">{e.knobs.CLOCK_PERIOD ?? "-"}</td>
                <td className={`num ${e.wns_ns >= 0 ? "pos" : "neg"}`}>{num(e.wns_ns)}</td><td className="num">{num(e.area_um2, 0)}</td><td className="num">{num(e.total_power_mw, 3)}</td>
                <td className="num">{e.drc_violations}</td><td className="num">{num(e.reward)}</td>
                <td>{e.cached && <Badge kind="info">cached</Badge>}{i === bi && <Badge kind="ok">chosen</Badge>}</td>
              </tr>))}</tbody>
          </table>
        </div>
      </Panel>
      {best && Object.keys(best.corner_wns ?? {}).length > 0 && (
        <Panel title="Setup slack by timing corner (chosen result)" flush>
          <table className="t"><thead><tr><th>Corner</th><th className="num">Slack ns</th></tr></thead>
            <tbody>{Object.entries(best.corner_wns).sort((a, b) => a[1] - b[1]).map(([c, v]) => <tr key={c}><td className="mono">{c}</td><td className={`num ${v >= 0 ? "pos" : "neg"}`}>{num(v)}</td></tr>)}</tbody></table>
        </Panel>
      )}
    </div>
  );
}

// ---------- layout & files ----------
function ZoomImage({ src }: { src: string }) {
  const [s, setS] = useState(1);
  const [o, setO] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number } | null>(null);
  return (
    <div className="layout-img" onWheel={(e) => { e.preventDefault(); setS((v) => Math.min(8, Math.max(0.3, v * (e.deltaY < 0 ? 1.12 : 0.89)))); }}
      onPointerDown={(e) => { drag.current = { x: e.clientX - o.x, y: e.clientY - o.y }; (e.target as HTMLElement).setPointerCapture?.(e.pointerId); }}
      onPointerMove={(e) => drag.current && setO({ x: e.clientX - drag.current.x, y: e.clientY - drag.current.y })}
      onPointerUp={() => (drag.current = null)} onDoubleClick={() => { setS(1); setO({ x: 0, y: 0 }); }}>
      <img src={src} alt="Rendered chip layout" style={{ transform: `translate(${o.x}px, ${o.y}px) scale(${s})`, height: "100%" }} draggable={false} />
    </div>
  );
}

function LayoutFiles({ run, best }: { run: Run; best: PPA | null }) {
  const files = useQuery({ queryKey: ["artifacts", run.id, run.status], queryFn: () => api<{ path: string; size: number; kind: string }[]>(`/runs/${run.id}/artifacts`).catch(() => []) });
  const render = files.data?.find((f) => f.kind === "png");
  const gds = files.data?.find((f) => f.kind === "gds");
  return (
    <div className="stack">
      {render ? (
        <Panel title="Layout" actions={gds ? <a className="btn sm" href={artifactUrl(run.id, gds.path)} download><Download size={13} /> GDSII</a> : undefined}>
          <ZoomImage src={artifactUrl(run.id, render.path)} />
          <div className="hint">Scroll to zoom, drag to pan, double-click to reset. Rendered from the real GDSII.</div>
        </Panel>
      ) : (
        <Empty title="No layout image">{best?.mode === "simulated" ? "Simulated runs do not produce a layout." : "A layout image appears when place-and-route finishes."}</Empty>
      )}
      <Panel title="Files" flush>
        {!files.data?.length ? <Empty title="No files yet" /> : (
          <table className="t"><thead><tr><th>File</th><th className="num">Size</th><th /></tr></thead>
            <tbody>{files.data.map((f) => (
              <tr key={f.path}><td className="mono">{f.path}</td><td className="num">{f.size > 1e6 ? `${(f.size / 1e6).toFixed(1)} MB` : `${Math.ceil(f.size / 1e3)} kB`}</td>
                <td><a className="btn quiet sm" href={artifactUrl(run.id, f.path)} download>Download</a></td></tr>))}</tbody></table>
        )}
      </Panel>
    </div>
  );
}

// ---------- agent log ----------
function CallRow({ c }: { c: LlmCall }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="link" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <td className="mono">#{c.seq}</td><td>{c.stage.toLowerCase().replaceAll("_", " ")}</td><td>{c.tier.replaceAll("_", " ")}</td>
        <td>{c.provider}/<span className="mono">{c.model}</span></td>
        <td className="num">{compact(c.prompt_tokens)}{c.cached_tokens ? <span className="muted"> ({compact(c.cached_tokens)} cached)</span> : null}</td>
        <td className="num">{compact(c.completion_tokens)}</td>
        <td className="num">{c.cache_hit ? <Badge kind="info">cache hit</Badge> : secs(c.latency_s)}</td>
        <td className="num">{c.tokens_per_s ? `${c.tokens_per_s.toFixed(0)} tok/s` : "-"}</td>
        <td className="num">{usd(c.cost_usd)}</td>
      </tr>
      {open && (
        <tr><td colSpan={9} style={{ background: "var(--sunken)" }}>
          <div className="stack" style={{ gap: 10 }}>
            {([["Instructions", c.system_prompt], ["Request", c.user_prompt], ["Response", c.response]] as const).map(([t, text]) => (
              <details key={t} open={t === "Response"}><summary className="small" style={{ cursor: "pointer", fontWeight: 600 }}>{t}</summary><pre className="mono" style={{ whiteSpace: "pre-wrap", maxHeight: 320, overflow: "auto", margin: "6px 0 0" }}>{text}</pre></details>
            ))}
          </div>
        </td></tr>
      )}
    </>
  );
}

function AgentLog({ calls }: { calls: LlmCall[] }) {
  if (!calls.length) return <Empty title="No model calls yet">Every prompt and response will appear here as the agents work.</Empty>;
  return (
    <Panel flush>
      <div style={{ overflowX: "auto" }}>
        <table className="t"><thead><tr><th>Call</th><th>Step</th><th>Tier</th><th>Model</th><th className="num">In tokens</th><th className="num">Out</th><th className="num">Time</th><th className="num">Speed</th><th className="num">Cost</th></tr></thead>
          <tbody>{calls.map((c) => <CallRow key={c.seq} c={c} />)}</tbody></table>
      </div>
    </Panel>
  );
}

// ---------- cost ----------
function Cost({ run, view }: { run: Run; view: RunView }) {
  const byStage = useMemo(() => {
    const m = new Map<string, { tokens: number; usd: number }>();
    view.calls.forEach((c) => { const x = m.get(c.stage) ?? { tokens: 0, usd: 0 }; x.tokens += c.prompt_tokens + c.completion_tokens; x.usd += c.cost_usd; m.set(c.stage, x); });
    return [...m.entries()].map(([label, v]) => ({ label: label.toLowerCase().replaceAll("_", " "), value: v.tokens, sub: usd(v.usd) }));
  }, [view.calls]);
  const byModel = useMemo(() => {
    const m = new Map<string, number>();
    view.calls.forEach((c) => m.set(`${c.provider}/${c.model}`, (m.get(`${c.provider}/${c.model}`) ?? 0) + c.prompt_tokens + c.completion_tokens));
    return [...m.entries()].map(([label, value]) => ({ label, value }));
  }, [view.calls]);
  const cost = (run.cost ?? {}) as Record<string, number>;
  const total = view.cost.usd || run.cost_usd;
  return (
    <div className="stack">
      <div className="grid4">
        <Stat label="Spent" value={usd(total)} sub={cost.saved_usd ? `${usd(cost.saved_usd)} saved by caching` : undefined} />
        <Stat label="Tokens" value={compact(view.cost.tokens || run.tokens)} sub={`${view.cost.calls || "-"} calls`} />
        <Stat label="Response cache hits" value={`${view.cost.cacheHits}`} sub="identical requests served locally" />
        <Stat label="Provider prefix cache" value={cost.prefix_cache_ratio != null ? `${Math.round(cost.prefix_cache_ratio * 100)}%` : "-"} sub="of prompt tokens read from cache" />
      </div>
      <div className="grid2" style={{ alignItems: "start" }}>
        <Panel title="Tokens by step">{byStage.length ? <Bars items={byStage} fmt={compact} /> : <div className="muted small">No calls recorded.</div>}</Panel>
        <Panel title="Tokens by model">{byModel.length ? <Bars items={byModel} fmt={compact} /> : <div className="muted small">No calls recorded.</div>}</Panel>
      </div>
    </div>
  );
}

// ---------- results against the contract ----------
function MarginBar({ used, bad }: { used: number; bad: boolean }) {
  const w = Math.max(0, Math.min(1, used)) * 100;
  return <div className="track" style={{ width: 120, display: "inline-block", verticalAlign: "middle" }} role="img" aria-label={`${Math.round(used * 100)} percent of the limit`}>
    <div className="fill" style={{ width: `${w}%`, background: bad ? "var(--fail)" : "var(--ink)" }} /></div>;
}

/** What the layout achieved against what the contract asked for: the numbers an engineer signs off on. */
function ResultsTable({ best, contract, sim }: { best: PPA | null; contract: RunView["contract"]; sim: boolean }) {
  if (!best) {
    return <div className="muted small" style={{ borderBottom: "1px solid var(--rule)", paddingBottom: 10 }}>No layout result yet. Timing, area and power appear here when place and route finishes.</div>;
  }
  const clockRaw = contract?.target_clock_ns ?? best.knobs?.CLOCK_PERIOD;
  const clock = clockRaw != null && Number(clockRaw) > 0 ? Number(clockRaw) : null;
  const areaMax = contract?.max_area_um2 ?? null, powerMax = contract?.max_power_mw ?? null;
  const rows: { name: string; value: string; target: string; used: number | null; bad: boolean; note?: string }[] = [
    { name: "Setup slack", value: `${num(best.wns_ns)} ns`, target: clock ? `at least 0 ns, ${clock} ns clock` : "at least 0 ns",
      used: clock ? Math.max(0, 1 - best.wns_ns / clock) : null, bad: best.wns_ns < 0,
      note: best.corner_wns && Object.keys(best.corner_wns).length ? "worst of all corners" : undefined },
    { name: "Cell area", value: `${compact(Math.round(best.area_um2))} µm², ${best.cell_count} cells`, target: areaMax ? `under ${compact(areaMax)} µm²` : "no limit set",
      used: areaMax ? best.area_um2 / areaMax : null, bad: !!areaMax && best.area_um2 > areaMax },
    { name: "Power", value: `${num(best.total_power_mw, 2)} mW`, target: powerMax ? `under ${num(powerMax, 1)} mW` : "no limit set",
      used: powerMax ? best.total_power_mw / powerMax : null, bad: !!powerMax && best.total_power_mw > powerMax },
    { name: "DRC and LVS", value: `${best.drc_violations} violations, LVS ${best.lvs_clean ? "clean" : "failed"}`, target: "0 violations, LVS clean",
      used: null, bad: best.drc_violations > 0 || !best.lvs_clean },
  ];
  return (
    <div style={{ overflowX: "auto" }}><table className="t" style={sim ? { backgroundImage: "var(--hatch)", minWidth: 640 } : { minWidth: 640 }}>
      <thead><tr><th>Result</th><th>Achieved</th><th>Contract asks</th><th>Against the limit</th><th /></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.name}>
          <td style={{ fontWeight: 700 }}>{r.name}</td>
          <td className={r.bad ? "neg" : ""} style={{ fontWeight: 700 }}>{r.value}</td>
          <td className="muted">{r.target}{r.note && <span className="small"> ({r.note})</span>}</td>
          <td>{r.used != null ? <MarginBar used={r.used} bad={r.bad} /> : <span className="muted">-</span>}</td>
          <td>{r.bad ? <span className="badge bad">Fails</span> : <span className="badge ok">Meets</span>}</td>
        </tr>))}
      </tbody>
    </table></div>
  );
}
