// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo, type ReactNode } from "react";
import { diffLines } from "diff";
import { AlertTriangle, CheckCircle2, CircleSlash, FlaskConical, ShieldCheck, XCircle } from "lucide-react";
import type { RunStatus } from "./api";
import { modeLabel } from "./lib";

// ---------- status & provenance ----------
export function Badge({ kind = "neutral", children }: { kind?: "ok" | "bad" | "warn" | "info" | "neutral" | "run"; children: ReactNode }) {
  return <span className={`badge ${kind}`}>{children}</span>;
}

const STATUS_KIND: Record<RunStatus, { kind: "ok" | "bad" | "warn" | "info" | "neutral" | "run"; label: string }> = {
  queued: { kind: "neutral", label: "Queued" },
  running: { kind: "run", label: "Running" },
  succeeded: { kind: "ok", label: "Passed" },
  failed: { kind: "bad", label: "Failed" },
  cancelled: { kind: "neutral", label: "Cancelled" },
  interrupted: { kind: "warn", label: "Interrupted" },
};
export function StatusBadge({ status }: { status: RunStatus }) {
  const s = STATUS_KIND[status];
  return <Badge kind={s.kind}>{s.label}</Badge>;
}

/** Hatched amber = simulated surrogate; solid green = produced by real tools. */
export function Prov({ mode }: { mode: string }) {
  const sim = mode === "simulated";
  return (
    <span className={`prov ${sim ? "sim" : "real"}`} title={sim ? "Surrogate models: not real EDA results" : "Produced by real EDA tools"}>
      {sim ? <FlaskConical size={12} /> : <ShieldCheck size={12} />}
      {modeLabel(mode)}
    </span>
  );
}

// ---------- layout atoms ----------
export function Panel({ title, actions, children, flush, className = "", layer }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string; layer?: string;
}) {
  return (
    <section className={`panel ${className}`} data-layer={layer}>
      {(title || actions) && <div className="head"><h3>{title}</h3>{actions}</div>}
      <div className={`body ${flush ? "flush" : ""}`}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone, sim }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "bad"; sim?: boolean }) {
  return (
    <div className={`panel stat ${sim ? "hatched" : ""}`}>
      <div className="k">{label}</div>
      <div className={`v ${tone ?? ""}`}>{value}</div>
      {sub && <div className="s">{sub}</div>}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: {
  tabs: { id: T; label: string; flag?: boolean }[]; value: T; onChange: (v: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={t.id === value} onClick={() => onChange(t.id)}>
          {t.label}{t.flag && <span className="dot" aria-label="needs attention" />}
        </button>
      ))}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty"><h3>{title}</h3><p>{children}</p>{action && <div style={{ marginTop: 14 }}>{action}</div>}</div>;
}

export function Banner({ kind, children }: { kind?: "sim" | "bad" | "good"; children: ReactNode }) {
  const icon = kind === "bad" ? <XCircle size={16} /> : kind === "good" ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />;
  return <div className={`banner ${kind ?? ""}`} role="status"><span style={{ marginTop: 2 }}>{icon}</span><div>{children}</div></div>;
}

export function Check({ ok, title, detail }: { ok: boolean | null; title: string; detail?: ReactNode }) {
  return (
    <div className="check">
      <span className={`ic ${ok ? "ok" : ok === false ? "bad" : ""}`}>
        {ok ? <CheckCircle2 size={16} /> : ok === false ? <XCircle size={16} /> : <CircleSlash size={16} />}
      </span>
      <div className="grow"><div style={{ fontWeight: 500 }}>{title}</div>{detail && <div className="muted small">{detail}</div>}</div>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}{hint && <span className="hint">{hint}</span>}</label>;
}

// ---------- code ----------
const KW = new Set(("module endmodule input output inout logic wire reg always_ff always_comb always_latch always assign begin end if else case " +
  "casez endcase default posedge negedge parameter localparam typedef enum function endfunction task endtask generate endgenerate genvar for " +
  "initial integer signed unsigned typedef struct packed interface endinterface").split(" "));
const TOKEN = /(\/\/.*|\/\*[\s\S]*?\*\/)|("(?:[^"\\]|\\.)*")|(\d*'[sS]?[bBdDhHoO][0-9a-fA-FxXzZ_]+|\b\d+\b)|(\$\w+)|(\b[A-Za-z_]\w*\b)|(`\w+)/g;

export function highlight(line: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  line.replace(TOKEN, (m, cm, str, nu, sys, id, tick, off: number) => {
    if (off > last) out.push(line.slice(last, off));
    const cls = cm ? "tk-cm" : str ? "tk-str" : nu ? "tk-num" : sys || tick ? "tk-sys" : id && KW.has(id) ? "tk-kw" : "";
    out.push(cls ? <span key={off} className={cls}>{m}</span> : m);
    last = off + m.length;
    return m;
  });
  if (last < line.length) out.push(line.slice(last));
  return out;
}

export function CodeView({ source, highlightLines = [], maxHeight }: { source: string; highlightLines?: number[]; maxHeight?: number }) {
  const lines = useMemo(() => source.replace(/\n$/, "").split("\n"), [source]);
  return (
    <pre className="code mono" style={maxHeight ? { maxHeight } : undefined} tabIndex={0}>
      {lines.map((l, i) => (
        <div key={i} className={`ln ${highlightLines.includes(i + 1) ? "hl" : ""}`}><span>{i + 1}</span><span>{highlight(l)}</span></div>
      ))}
    </pre>
  );
}

export function DiffView({ before, after }: { before: string; after: string }) {
  const parts = useMemo(() => diffLines(before, after), [before, after]);
  let n = 0;
  const rows: ReactNode[] = [];
  parts.forEach((p, pi) => {
    p.value.replace(/\n$/, "").split("\n").forEach((l, li) => {
      if (!p.removed) n += 1;
      rows.push(
        <div key={`${pi}-${li}`} className={`ln ${p.added ? "add" : p.removed ? "del" : ""}`}>
          <span>{p.removed ? "-" : n}</span><span>{p.added ? "+ " : p.removed ? "- " : "  "}{highlight(l)}</span>
        </div>,
      );
    });
  });
  const changed = parts.some((p) => p.added || p.removed);
  return changed ? <pre className="code mono" tabIndex={0}>{rows}</pre> : <div className="empty small">No differences between these versions.</div>;
}

export function Skeleton({ h = 16, w = "100%" }: { h?: number; w?: number | string }) {
  return <div className="skeleton" style={{ height: h, width: w }} aria-hidden />;
}
