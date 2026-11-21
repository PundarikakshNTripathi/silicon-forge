import { Boxes, Cpu, FileCode2, FlaskConical, Layers, Route, ShieldCheck, Stamp } from "lucide-react";
import { STAGES, type RunView, type StageKey } from "./derive";
import { HatchDefs } from "./charts";

const ICONS: Record<StageKey, typeof Boxes> = {
  contract: Layers, rtl: FileCode2, guardrails: ShieldCheck, formal: FlaskConical, lec: Cpu, pnr: Route, signoff: Stamp,
};

function sub(view: RunView, k: StageKey): string {
  switch (k) {
    case "contract": return view.contract ? `${view.contract.ports.length} ports` : "";
    case "rtl": return view.rtl.length ? `${view.rtl.length} version${view.rtl.length > 1 ? "s" : ""}` : "";
    case "guardrails": return view.guardrails.length ? `${view.guardrails.length} audit${view.guardrails.length > 1 ? "s" : ""}` : "";
    case "formal": return view.formal.length ? `${view.formal.length} run${view.formal.length > 1 ? "s" : ""}` : "";
    case "lec": return view.lec ? (view.lec.ok ? `${view.lec.cells} cells` : "not equivalent") : "";
    case "pnr": return view.episodes.length ? `${view.episodes.length} episode${view.episodes.length > 1 ? "s" : ""}` : "";
    case "signoff": return view.signoff ? (view.signoff.ok ? "closed" : "not closed") : "";
  }
}

export function PipelineStrip({ view, onSelect }: { view: RunView; onSelect?: (k: StageKey) => void }) {
  const W = 920, y = 62, x0 = 70, dx = (W - 2 * x0) / (STAGES.length - 1);
  const X = (i: number) => x0 + i * dx;
  const sim = view.mode === "simulated";
  const idx = (k: StageKey) => STAGES.findIndex((s) => s.key === k);
  // feedback loops: guardrails -> rtl, formal -> rtl
  const loops: { from: StageKey; to: StageKey; depth: number; n: number; label: string }[] = [
    { from: "guardrails", to: "rtl", depth: 40, n: view.stages.guardrails.loops, label: "repair" },
    { from: "formal", to: "rtl", depth: 62, n: view.stages.formal.loops, label: "counterexample repair" },
  ];
  const loopPath = (a: number, b: number, d: number) => {
    const r = 10, ya = y + 18, yb = y + 18 + d;
    return `M${X(a)} ${ya} V${yb - r} Q${X(a)} ${yb} ${X(a) - r} ${yb} H${X(b) + r} Q${X(b)} ${yb} ${X(b)} ${yb - r} V${ya}`;
  };
  return (
    <div className="strip" role="img" aria-label={`Pipeline: ${STAGES.map((s) => `${s.label} ${view.stages[s.key].status}`).join(", ")}`}>
      <svg viewBox={`0 0 ${W} 152`}>
        <HatchDefs />
        {STAGES.slice(0, -1).map((s, i) => {
          const done = view.stages[s.key].status === "done";
          return <line key={s.key} className={`trace ${done ? (sim ? "sim" : "done") : ""}`} x1={X(i) + 18} x2={X(i + 1) - 18} y1={y} y2={y} />;
        })}
        {loops.map((l) => {
          const a = idx(l.from), b = idx(l.to), live = l.n > 0;
          return (
            <g key={l.label} opacity={live ? 1 : 0.35}>
              <path className="trace loop" d={loopPath(a, b, l.depth)} />
              <text className="loopnum" x={(X(a) + X(b)) / 2} y={y + 18 + l.depth - 5}>{live ? `${l.label} × ${l.n}` : l.label}</text>
            </g>
          );
        })}
        {STAGES.map((s, i) => {
          const st = view.stages[s.key].status;
          const Icon = ICONS[s.key];
          const cls = st === "done" ? (sim ? "sim" : "done") : st;
          return (
            <g key={s.key} onClick={() => onSelect?.(s.key)} style={{ cursor: onSelect ? "pointer" : "default" }} tabIndex={onSelect ? 0 : -1}
              onKeyDown={(e) => { if (e.key === "Enter") onSelect?.(s.key); }}>
              {st === "active" && <circle className="halo" cx={X(i)} cy={y} r="18" />}
              <circle className={`via ${cls}`} cx={X(i)} cy={y} r="18" />
              <Icon x={X(i) - 9} y={y - 9} size={18} stroke="var(--ink-2)" strokeWidth={1.7} />
              <text x={X(i)} y={y - 40} style={{ fontWeight: 600 }}>{s.label}</text>
              <text className="sub" x={X(i)} y={y - 26}>{sub(view, s.key) || s.sub}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
