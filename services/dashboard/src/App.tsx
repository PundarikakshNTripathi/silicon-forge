// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Code, Coins, Cpu, FlaskConical, FolderTree, ListChecks, Moon, Play, Sun, Wrench } from "lucide-react";

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

const NAV = [
  { to: "/", label: "New run", icon: Play, end: true },
  { to: "/runs", label: "Runs", icon: ListChecks },
  { to: "/workspace", label: "Workspace", icon: FolderTree },
  { to: "/models", label: "Models", icon: Cpu },
  { to: "/cost", label: "Cost", icon: Coins },
  { to: "/evals", label: "Evals", icon: FlaskConical },
  { to: "/knowledge", label: "Knowledge", icon: BookOpen },
];

function Mark() {
  // a wafer with its flat, and a Manhattan-routed S landing on two via pads (same drawing as assets/logo.svg)
  return (
    <svg className="brand" viewBox="0 0 64 64" role="img" aria-label="SiliconForge">
      <path d="M14.34 55A29 29 0 1 1 49.66 55Z" fill="currentColor" />
      <path d="M44 18H24V30H40V42H20" fill="none" stroke="var(--sunken)" strokeWidth="5" />
      <rect x="39.5" y="13.5" width="9" height="9" fill="#f2a900" />
      <rect x="15.5" y="37.5" width="9" height="9" fill="#f2a900" />
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
        <div className="brandline"><Mark /><div><b>SiliconForge</b><small>spec to GDSII, verified</small></div></div>
        <div className="index-label">Sheets</div>
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav ${isActive ? "active" : ""}`}><Icon size={17} /><span>{label}</span></NavLink>
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
          <Route path="/" element={<NewRun />} />
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
