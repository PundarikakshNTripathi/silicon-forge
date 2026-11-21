// SPDX-License-Identifier: AGPL-3.0-or-later
import { STAGES, type RunView, type StageKey } from "./derive";
import { secs } from "./lib";

/** Hatch fills in each stage's own hue, for work done by surrogate models (same pattern as the simulated stamp). */
function Hatch() {
  return (
    <defs>
      {STAGES.map((s) => (
        <pattern key={s.key} id={`hatch-${s.key}`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" fill="var(--paper-2)" />
          <line x1="0" y1="0" x2="0" y2="6" stroke={`var(--layer-${s.key})`} strokeWidth="2.2" />
        </pattern>
      ))}
    </defs>
  );
}

function sub(view: RunView, k: StageKey): string {
  switch (k) {
    case "contract": return view.contract ? `${view.contract.ports.length} ports` : "";
    case "rtl": return view.rtl.length ? `${view.rtl.length} version${view.rtl.length > 1 ? "s" : ""}` : "";
    case "guardrails": return view.guardrails.length ? `${view.guardrails.length} audit${view.guardrails.length > 1 ? "s" : ""}` : "";
    case "formal": return view.formal.length ? `${view.formal.length} run${view.formal.length > 1 ? "s" : ""}` : "";
    case "lec": return view.lec ? (view.lec.ok ? `${view.lec.cells} cells, equivalent` : "not equivalent") : "";
    case "pnr": return view.episodes.length ? `${view.episodes.length} episode${view.episodes.length > 1 ? "s" : ""}` : "";
    case "signoff": return view.signoff ? (view.signoff.ok ? "closed" : "not closed") : "";
  }
}

/**
 * The flow drawn as a stack of layers read like a timing diagram: one row per stage, each in its own layer hue, time running left
 * to right, a bar where the stage was working. Bars are solid when real tools produced the work and hatched when a surrogate did.
 * An amber diamond marks each time a stage sent the design back for repair, so the loop is visible where it happened (amber means
 * nothing else in the cockpit).
 * The time axis is real (first event to last), not decoration.
 */
export function PipelineStrip({ view, onSelect }: { view: RunView; onSelect?: (k: StageKey) => void }) {
  const W = 980, left = 168, right = 24, top = 30, rowH = 34, H = top + STAGES.length * rowH + 28;
  const sim = view.mode === "simulated";
  const t0 = view.t0 ?? 0;
  const span = Math.max(1, (view.t1 ?? t0) - t0);
  const X = (ts: number) => left + ((ts - t0) / span) * (W - left - right);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div className="strip" role="img" aria-label={`Flow by stage over ${secs(span)}: ${STAGES.map((s) => `${s.label} ${view.stages[s.key].status}`).join(", ")}`}>
      <svg className="tl" viewBox={`0 0 ${W} ${H}`}>
        <Hatch />
        {ticks.map((f) => {
          const x = left + f * (W - left - right);
          return <g key={f}><line className="grid" x1={x} x2={x} y1={top - 6} y2={H - 26} /><text className="axis" x={x} y={H - 8} textAnchor={f === 0 ? "start" : f === 1 ? "end" : "middle"}>{f === 0 ? "0 s" : secs(span * f)}</text></g>;
        })}
        {STAGES.map((s, i) => {
          const y = top + i * rowH, mid = y + rowH / 2;
          const st = view.stages[s.key].status;
          const tm = view.times[s.key];
          const hasTime = tm.start != null && tm.end != null;
          const x1 = hasTime ? X(tm.start!) : left, x2 = hasTime ? Math.max(X(tm.end!), X(tm.start!) + 6) : left;
          const cls = st === "done" ? (sim ? "sim" : "") : st === "pending" ? "pending" : st;
          const h = 16;
          const hue = `var(--layer-${s.key})`;
          return (
            <g key={s.key} className="stage" style={{ "--stage": hue } as React.CSSProperties} onClick={() => onSelect?.(s.key)} tabIndex={onSelect ? 0 : -1} role={onSelect ? "button" : undefined}
              aria-label={`${s.label}: ${st}${sub(view, s.key) ? ", " + sub(view, s.key) : ""}`} onKeyDown={(e) => { if (e.key === "Enter") onSelect?.(s.key); }}>
              <rect className="band" x={0} y={y} width={W} height={rowH} fill={hue} />
              <rect className="hit" x={0} y={y} width={W} height={rowH} />
              <rect x={0} y={y} width={5} height={rowH} fill={hue} />
              <line className="row-line" x1={0} x2={W} y1={y + rowH} y2={y + rowH} />
              <text x={16} y={mid - 2} style={{ fontWeight: 700 }}>{s.label}</text>
              <text className="sub" x={16} y={mid + 12}>{sub(view, s.key) || s.sub}</text>
              <line className="base" x1={left} x2={W - right} y1={mid} y2={mid} />
              {hasTime
                ? <rect className={`pulse ${cls}`} x={x1} y={mid - h / 2} width={x2 - x1} height={h} rx={2} style={cls === "sim" ? { fill: `url(#hatch-${s.key})` } : undefined} />
                : <rect className="pulse pending" x={left} y={mid - h / 2} width={W - left - right} height={h} opacity={0.5} />}
              {view.retries.filter((r) => r.stage === s.key).map((r, k) => (
                <rect key={k} className="redo" x={X(r.ts) - 6} y={mid - 6} width={12} height={12} transform={`rotate(45 ${X(r.ts)} ${mid})`}><title>Sent back for repair</title></rect>
              ))}
            </g>
          );
        })}
        <line className="base" x1={left} x2={left} y1={top - 6} y2={H - 26} />
      </svg>
    </div>
  );
}
