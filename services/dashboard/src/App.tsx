// SPDX-License-Identifier: AGPL-3.0-or-later
import { Mark, StackBar } from "./Mark";
import { useEffect, useMemo, useRef, useState } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Code, Coins, Cpu, FlaskConical, FolderTree, ListChecks, Moon, Play, Search, Sun, Wrench } from "lucide-react";

// AGPL-3.0 section 13: users of a modified network-served version must be offered its source. A fork sets VITE_SOURCE_URL.
const SOURCE_URL = (import.meta as { env?: Record<string, string> }).env?.VITE_SOURCE_URL ?? "https://github.com/PundarikakshNTripathi/silicon-forge";
import { api, type RunSummary } from "./api";
import { useHotkey, useTheme, useToasts } from "./lib";
import NewRun from "./pages/NewRun";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";
import Models from "./pages/Models";
import { EvalsPage } from "./pages/Evals";
import { CostPage } from "./pages/Insights";
import { KnowledgePage, SystemPage } from "./pages/Admin";
import WorkspacePage from "./pages/Workspace";
import { Empty } from "./ui";

const GROUPS = [
  { label: "Work", items: [
    { to: "/new", label: "New run", icon: Play },
    { to: "/runs", label: "Runs", icon: ListChecks },
    { to: "/workspace", label: "Agent", icon: FolderTree },
  ] },
  { label: "Evidence", items: [
    { to: "/evals", label: "Evals", icon: FlaskConical },
    { to: "/cost", label: "Insights", icon: Coins },
  ] },
  { label: "Setup", items: [
    { to: "/models", label: "Models", icon: Cpu },
    { to: "/knowledge", label: "Knowledge", icon: BookOpen },
  ] },
];
const NAV = GROUPS.flatMap((g) => g.items.map((i) => ({ ...i, end: false })));

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

/** First visit: start a run. After that, the place you come back to is your runs. */
function Landing() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api<RunSummary[]>("/runs") });
  if (runs.isLoading) return <div className="page"><div className="skeleton" style={{ height: 120 }} /></div>;
  return (runs.data?.length ?? 0) > 0 ? <Navigate to="/runs" replace /> : <NewRun />;
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
        <div className="brandline"><Mark /><div><b>SiliconForge</b><small>spec to GDSII, verified</small></div></div>
        <StackBar />
        <button className="search" onClick={() => setPalette(true)} aria-label="Search and jump (Ctrl K)"><Search size={15} /><span>Jump to…</span><kbd>Ctrl K</kbd></button>
        {GROUPS.map((g) => (
          <div className="group" key={g.label}>
            <div className="index-label">{g.label}</div>
            {g.items.map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={to} className={({ isActive }) => `nav ${isActive ? "active" : ""}`}><Icon size={17} /><span>{label}</span></NavLink>
            ))}
          </div>
        ))}
        <span className="spacer" />
        <div className="foot">
          <NavLink to="/system" className={({ isActive }) => `nav ${isActive ? "active" : ""}`}><Wrench size={17} /><span>System</span></NavLink>
          <a className="nav" href={SOURCE_URL} target="_blank" rel="noreferrer"><Code size={17} /><span>Source (AGPL-3.0)</span></a>
          <button className="nav" onClick={() => setTheme(dark ? "light" : "dark")} aria-label="Toggle theme">{dark ? <Sun size={17} /> : <Moon size={17} />}<span>{dark ? "Light theme" : "Dark theme"}</span></button>
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/new" element={<NewRun />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/workspace" element={<WorkspacePage />} />
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
