import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Coins, Cpu, FlaskConical, ListChecks, Moon, PlusCircle, Sun, Wrench } from "lucide-react";
import { api, type RunSummary } from "./api";
import { useHotkey, useTheme, useToasts } from "./lib";
import NewRun from "./pages/NewRun";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";
import Models from "./pages/Models";
import { CostPage, EvalsPage } from "./pages/Insights";
import { KnowledgePage, SystemPage } from "./pages/Admin";
import { Empty } from "./ui";

const NAV = [
  { to: "/", label: "New run", icon: PlusCircle, end: true },
  { to: "/runs", label: "Runs", icon: ListChecks },
  { to: "/models", label: "Models", icon: Cpu },
  { to: "/cost", label: "Cost", icon: Coins },
  { to: "/evals", label: "Evals", icon: FlaskConical },
  { to: "/knowledge", label: "Knowledge", icon: BookOpen },
];

function Mark() {
  // a die with routed traces: the app's only decorative element
  return (
    <svg className="brand" viewBox="0 0 36 36" role="img" aria-label="SiliconForge">
      <rect x="7" y="7" width="22" height="22" rx="3" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M3 13h4M3 23h4M29 13h4M29 23h4M13 3v4M23 3v4M13 29v4M23 29v4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M13 22l5-8 5 8" fill="none" stroke="var(--amber)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Palette({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [theme, setTheme] = useTheme();
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api<RunSummary[]>("/runs") });
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  const items = useMemo(() => {
    const go = (to: string) => () => { nav(to); onClose(); };
    const base = [
      ...NAV.map((n) => ({ label: `Go to ${n.label.toLowerCase()}`, run: go(n.to) })),
      { label: "Go to toolchain and settings", run: go("/system") },
      { label: `Switch to ${theme === "dark" ? "light" : "dark"} theme`, run: () => { setTheme(theme === "dark" ? "light" : "dark"); onClose(); } },
      ...(runs.data ?? []).slice(0, 12).map((r) => ({ label: `Open run: ${r.module ?? r.id}`, run: go(`/runs/${r.id}`) })),
    ];
    return base.filter((x) => x.label.toLowerCase().includes(q.toLowerCase()));
  }, [q, runs.data, theme]);
  return (
    <div className="palette-backdrop" onClick={onClose} role="dialog" aria-modal="true" aria-label="Command palette">
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input ref={input} type="text" placeholder="Jump to a page or run…" value={q} onChange={(e) => { setQ(e.target.value); setI(0); }}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "ArrowDown") { e.preventDefault(); setI((v) => Math.min(items.length - 1, v + 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setI((v) => Math.max(0, v - 1)); }
            if (e.key === "Enter") items[i]?.run();
          }} />
        <ul role="listbox">{items.map((it, k) => <li key={it.label} role="option" aria-selected={k === i} onMouseEnter={() => setI(k)} onClick={it.run}>{it.label}</li>)}
          {items.length === 0 && <li className="muted">Nothing matches.</li>}</ul>
      </div>
    </div>
  );
}

export default function App() {
  const [theme, setTheme] = useTheme();
  const [palette, setPalette] = useState(false);
  const toasts = useToasts();
  useHotkey("mod+k", () => setPalette((p) => !p));
  const dark = theme === "dark" || (theme === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  return (
    <div className="shell">
      <nav className="rail" aria-label="Main">
        <Mark />
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav ${isActive ? "active" : ""}`}><Icon size={19} /><span>{label}</span></NavLink>
        ))}
        <span className="spacer" />
        <NavLink to="/system" className={({ isActive }) => `nav ${isActive ? "active" : ""}`}><Wrench size={19} /><span>System</span></NavLink>
        <button className="nav btn quiet" style={{ width: 48, flexDirection: "column", padding: "6px 0", gap: 2, fontSize: 10.5, color: "var(--ink-3)" }}
          onClick={() => setTheme(dark ? "light" : "dark")} aria-label="Toggle theme">{dark ? <Sun size={18} /> : <Moon size={18} />}<span>Theme</span></button>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<NewRun />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/models" element={<Models />} />
          <Route path="/cost" element={<CostPage />} />
          <Route path="/evals" element={<EvalsPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="*" element={<div className="page"><Empty title="Page not found">Use the navigation on the left.</Empty></div>} />
        </Routes>
      </main>
      {palette && <Palette onClose={() => setPalette(false)} />}
      <div className="toasts" aria-live="polite">{toasts.map((t) => <div className="toast" key={t.id}>{t.text}</div>)}</div>
    </div>
  );
}
