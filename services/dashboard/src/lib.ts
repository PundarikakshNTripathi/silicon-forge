// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef, useState, useCallback } from "react";
import { api, withToken, type EventRec, type RunDetail } from "./api";

// ---------- formatting ----------
export const usd = (v: number | null | undefined) =>
  v == null ? "-" : v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
export const num = (v: number | null | undefined, d = 2) => (v == null || Number.isNaN(v) ? "-" : v.toFixed(d));
export const compact = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : `${n}`);
export const secs = (s: number | null | undefined) => {
  if (s == null) return "-";
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
};
export const ago = (ts: number) => {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString();
};
export const isLive = (mode: string) => mode !== "simulated";
export const modeLabel = (mode: string) =>
  ({ simulated: "simulated", native: "real · native", wsl: "real · WSL", docker: "real · Docker" } as Record<string, string>)[mode] ?? mode;

// ---------- theme ----------
export function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark" | "system">(() => {
    try { return (localStorage.getItem("sf-theme") as "light" | "dark" | "system") ?? "system"; } catch { return "system"; }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
    try { localStorage.setItem("sf-theme", theme); } catch { /* ignore */ }
  }, [theme]);
  return [theme, setTheme] as const;
}

// ---------- live run events ----------
const TERMINAL = new Set(["succeeded", "failed", "cancelled", "interrupted"]);

/** Loads past events, then follows the SSE stream until the run finishes. */
export function useRunEvents(runId: string | undefined, run: RunDetail | undefined, onDone: () => void) {
  const [events, setEvents] = useState<EventRec[]>([]);
  const last = useRef(-1);
  const done = useRef(onDone);
  done.current = onDone;
  const status = run?.status;

  useEffect(() => { setEvents([]); last.current = -1; }, [runId]);

  useEffect(() => {
    if (!runId || !status) return;
    let es: EventSource | null = null;
    let cancelled = false;
    const append = (batch: EventRec[]) => {
      const fresh = batch.filter((e) => e.seq > last.current);
      if (!fresh.length) return;
      last.current = fresh[fresh.length - 1].seq;
      setEvents((prev) => [...prev, ...fresh]);
    };
    (async () => {
      try { append(await api<EventRec[]>(`/runs/${runId}/events/all`)); } catch { /* run may have no events yet */ }
      if (cancelled || TERMINAL.has(status)) return;
      es = new EventSource(withToken(`/api/runs/${runId}/events?after=${last.current}`));
      es.onmessage = (m) => append([JSON.parse(m.data) as EventRec]);
      es.addEventListener("done", () => { es?.close(); done.current(); });
      es.onerror = () => { /* the browser retries; Last-Event-ID resumes without gaps */ };
    })();
    return () => { cancelled = true; es?.close(); };
  }, [runId, status === undefined ? "loading" : TERMINAL.has(status) ? "terminal" : "live"]);
  return events;
}

// ---------- tiny hotkey ----------
export function useHotkey(combo: string, fn: () => void) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const k = combo.toLowerCase();
      if (k === "mod+k" && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); ref.current(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [combo]);
}

// ---------- toasts ----------
export interface ToastMsg { id: number; text: string }
let toastId = 0;
const listeners = new Set<(t: ToastMsg) => void>();
export const toast = (text: string) => listeners.forEach((l) => l({ id: ++toastId, text }));
export function useToasts() {
  const [items, setItems] = useState<ToastMsg[]>([]);
  const push = useCallback((t: ToastMsg) => {
    setItems((s) => [...s, t]);
    setTimeout(() => setItems((s) => s.filter((x) => x.id !== t.id)), 4000);
  }, []);
  useEffect(() => { listeners.add(push); return () => { listeners.delete(push); }; }, [push]);
  return items;
}
