import { useMemo } from "react";
import type { PPA, WaveTrace } from "./api";

const scale = (v: number, d0: number, d1: number, r0: number, r1: number) => (d1 === d0 ? (r0 + r1) / 2 : r0 + ((v - d0) / (d1 - d0)) * (r1 - r0));
const nice = (lo: number, hi: number): [number, number] => {
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.15;
  return [lo - pad, hi + pad];
};
export function HatchDefs() {
  return (
    <defs>
      <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <rect width="6" height="6" fill="var(--amber-soft)" />
        <line x1="0" y1="0" x2="0" y2="6" stroke="var(--amber)" strokeWidth="2" opacity="0.65" />
      </pattern>
    </defs>
  );
}

const isClean = (e: PPA, minWns = 0) => e.wns_ns >= minWns && e.drc_violations === 0 && e.lvs_clean;

/** Area vs worst slack for every P&R episode. Hatched marks are simulated; a dashed line marks WNS = 0. */
export function ParetoChart({ episodes, front, bestIndex }: { episodes: PPA[]; front: PPA[]; bestIndex: number }) {
  const W = 560, H = 300, m = { l: 52, r: 16, t: 14, b: 40 };
  const pts = episodes.map((e, i) => ({ e, i, front: front.some((f) => f.reward === e.reward && f.area_um2 === e.area_um2) }));
  const [x0, x1] = useMemo(() => nice(Math.min(...episodes.map((e) => e.area_um2)), Math.max(...episodes.map((e) => e.area_um2))), [episodes]);
  const [y0, y1] = useMemo(() => nice(Math.min(0, ...episodes.map((e) => e.wns_ns)), Math.max(0.5, ...episodes.map((e) => e.wns_ns))), [episodes]);
  const X = (v: number) => scale(v, x0, x1, m.l, W - m.r);
  const Y = (v: number) => scale(v, y0, y1, H - m.b, m.t);
  const xt = [0, 1, 2, 3, 4].map((k) => x0 + ((x1 - x0) * k) / 4);
  const yt = [0, 1, 2, 3, 4].map((k) => y0 + ((y1 - y0) * k) / 4);
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Area versus worst negative slack per episode" style={{ width: "100%", height: "auto" }}>
      <HatchDefs />
      {yt.map((v) => <g key={`y${v}`}><line className="grid" x1={m.l} x2={W - m.r} y1={Y(v)} y2={Y(v)} /><text x={m.l - 8} y={Y(v) + 4} textAnchor="end">{v.toFixed(1)}</text></g>)}
      {xt.map((v) => <g key={`x${v}`}><line className="grid" y1={m.t} y2={H - m.b} x1={X(v)} x2={X(v)} /><text x={X(v)} y={H - m.b + 16} textAnchor="middle">{v.toFixed(0)}</text></g>)}
      <line className="axis" x1={m.l} x2={W - m.r} y1={H - m.b} y2={H - m.b} /><line className="axis" x1={m.l} x2={m.l} y1={m.t} y2={H - m.b} />
      <line x1={m.l} x2={W - m.r} y1={Y(0)} y2={Y(0)} stroke="var(--green)" strokeDasharray="5 4" strokeWidth="1.5" />
      <text x={W - m.r} y={Y(0) - 5} textAnchor="end" style={{ fill: "var(--green)" }}>timing met</text>
      <text x={(m.l + W - m.r) / 2} y={H - 6} textAnchor="middle">Standard-cell area (µm²)</text>
      <text transform={`translate(13 ${(m.t + H - m.b) / 2}) rotate(-90)`} textAnchor="middle">Worst slack (ns)</text>
      {pts.map(({ e, i, front: onFront }) => {
        const sim = e.mode === "simulated";
        const col = isClean(e) ? "var(--green)" : "var(--red)";
        return (
          <g key={i}>
            <circle cx={X(e.area_um2)} cy={Y(e.wns_ns)} r={onFront ? 8 : 6} fill={sim ? "url(#hatch)" : col} stroke={sim ? "var(--amber)" : col} strokeWidth={onFront ? 2.5 : 1.5} fillOpacity={sim ? 1 : 0.8} />
            {i === bestIndex && <circle cx={X(e.area_um2)} cy={Y(e.wns_ns)} r={12} fill="none" stroke="var(--blue)" strokeWidth="2" />}
            <title>{`Episode ${i + 1}: WNS ${e.wns_ns.toFixed(2)} ns, area ${e.area_um2.toFixed(0)} µm², power ${e.total_power_mw.toFixed(2)} mW, DRC ${e.drc_violations}`}</title>
          </g>
        );
      })}
    </svg>
  );
}

export function RewardChart({ episodes }: { episodes: PPA[] }) {
  const W = 360, H = 180, m = { l: 40, r: 12, t: 12, b: 28 };
  const rs = episodes.map((e) => e.reward);
  const best = rs.reduce<number[]>((acc, r) => [...acc, Math.max(r, acc.length ? acc[acc.length - 1] : -Infinity)], []);
  const [y0, y1] = nice(Math.min(...rs), Math.max(...rs));
  const X = (i: number) => scale(i, 0, Math.max(1, rs.length - 1), m.l, W - m.r);
  const Y = (v: number) => scale(v, y0, y1, H - m.b, m.t);
  const path = (arr: number[]) => arr.map((v, i) => `${i ? "L" : "M"}${X(i)} ${Y(v)}`).join(" ");
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Reward per episode" style={{ width: "100%", height: "auto" }}>
      <line className="axis" x1={m.l} x2={W - m.r} y1={H - m.b} y2={H - m.b} /><line className="axis" x1={m.l} x2={m.l} y1={m.t} y2={H - m.b} />
      {[y0, (y0 + y1) / 2, y1].map((v) => <g key={v}><line className="grid" x1={m.l} x2={W - m.r} y1={Y(v)} y2={Y(v)} /><text x={m.l - 6} y={Y(v) + 4} textAnchor="end">{v.toFixed(1)}</text></g>)}
      <path d={path(rs)} fill="none" stroke="var(--line-strong)" strokeWidth="1.5" />
      <path d={path(best)} fill="none" stroke="var(--blue)" strokeWidth="2.5" />
      {rs.map((r, i) => <circle key={i} cx={X(i)} cy={Y(r)} r="3.5" fill="var(--surface-2)" stroke="var(--ink-2)" />)}
      {rs.map((_, i) => <text key={`t${i}`} x={X(i)} y={H - 10} textAnchor="middle">{i + 1}</text>)}
      <text x={W - m.r} y={m.t + 8} textAnchor="end" style={{ fill: "var(--blue)" }}>best so far</text>
    </svg>
  );
}

/** Horizontal bars for token / cost breakdowns. */
export function Bars({ items, unit = "", fmt = (v: number) => String(v) }: { items: { label: string; value: number; sub?: string }[]; unit?: string; fmt?: (v: number) => string }) {
  const max = Math.max(...items.map((i) => i.value), 1e-9);
  return (
    <div className="bars">
      {items.map((i) => (
        <div className="bar" key={i.label}>
          <span title={i.sub}>{i.label}</span>
          <div className="track"><div className="fill" style={{ width: `${(i.value / max) * 100}%` }} /></div>
          <span className="num mono" style={{ textAlign: "right" }}>{fmt(i.value)}{unit}</span>
        </div>
      ))}
    </div>
  );
}

// ---------- waveform ----------
const hex = (bin: string) => (/[xz]/i.test(bin) ? bin.toUpperCase() : "0x" + parseInt(bin, 2).toString(16).toUpperCase());

/** Counterexample viewer: one lane per signal, the violating cycle shaded. `fail` is the reported step. */
export function Waveform({ trace, failStep, highlight }: { trace: WaveTrace; failStep: number; highlight?: string }) {
  const steps = Math.max(1, Math.round(trace.end / 10));
  const names = trace.signals.filter((s) => !["smt_step", "smt_clock", "f_past_valid"].includes(s.name) && !s.name.endsWith("_EN") && !s.name.startsWith("alu_comb"));
  const order = [...names].sort((a, b) => Number(b.name === highlight) - Number(a.name === highlight));
  const L = 118, laneH = 30, W = 640, T = 22;
  const H = T + order.length * laneH + 8;
  const X = (t: number) => L + (t / (steps * 10)) * (W - L - 8);
  const failFrom = (failStep - 1) * 10, failTo = failStep * 10;
  return (
    <svg className="wave" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Counterexample waveform" style={{ width: "100%", height: "auto" }}>
      <rect x={X(failFrom)} y={T - 4} width={X(failTo) - X(failFrom)} height={H - T} fill="var(--red-soft)" opacity="0.7" />
      {Array.from({ length: steps + 1 }, (_, i) => (
        <g key={i}><line x1={X(i * 10)} x2={X(i * 10)} y1={T - 6} y2={H} stroke="var(--line)" strokeDasharray="2 4" /><text x={X(i * 10)} y={12} textAnchor="middle">{i}</text></g>
      ))}
      {order.map((s, row) => {
        const y = T + row * laneH + laneH / 2;
        const ch = s.changes.length ? s.changes : [[0, "0"] as [number, string]];
        const segs = ch.map(([t, v], i) => ({ t0: t, t1: i + 1 < ch.length ? ch[i + 1][0] : steps * 10, v }));
        return (
          <g key={s.name}>
            <text x={L - 8} y={y + 4} textAnchor="end" style={{ fill: s.name === highlight ? "var(--red)" : "var(--ink)", fontWeight: s.name === highlight ? 700 : 400 }}>{s.name}</text>
            {s.width === 1
              ? segs.map((g, i) => {
                  const hi = g.v === "1", yv = hi ? y - 8 : y + 8;
                  const prev = i > 0 && segs[i - 1].v !== g.v;
                  return <g key={i}>{prev && <line x1={X(g.t0)} x2={X(g.t0)} y1={y - 8} y2={y + 8} stroke="var(--blue)" strokeWidth="1.8" />}<line x1={X(g.t0)} x2={X(g.t1)} y1={yv} y2={yv} stroke="var(--blue)" strokeWidth="1.8" /></g>;
                })
              : segs.map((g, i) => (
                  <g key={i}>
                    <rect x={X(g.t0) + 1} y={y - 9} width={Math.max(2, X(g.t1) - X(g.t0) - 2)} height={18} rx="3" fill="var(--blue-soft)" stroke="var(--blue)" />
                    {X(g.t1) - X(g.t0) > 36 && <text x={(X(g.t0) + X(g.t1)) / 2} y={y + 4} textAnchor="middle">{hex(g.v)}</text>}
                  </g>
                ))}
          </g>
        );
      })}
      <text x={X(failFrom) + 4} y={H - 2} style={{ fill: "var(--red)" }}>assertion violated</text>
    </svg>
  );
}
