// SPDX-License-Identifier: AGPL-3.0-or-later
// The guided tour: a spotlight on one part of the page and a small card with an arrow pointing at it. It walks across pages,
// waits for data to arrive before pointing at it, and falls back to a centred card when a target does not exist.
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight } from "lucide-react";
import type { Step } from "./steps";

interface State { steps: Step[]; i: number }
let state: State | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const SEEN = "sf-tour-seen";

export const tourSeen = () => { try { return localStorage.getItem(SEEN) === "1"; } catch { return false; } };
const markSeen = () => { try { localStorage.setItem(SEEN, "1"); } catch { /* storage may be unavailable */ } };

export function startTour(steps: Step[], at = 0) { state = { steps, i: at }; markSeen(); emit(); }
export function stopTour() { state = null; emit(); }
const go = (d: number) => {
  if (!state) return;
  const i = state.i + d;
  if (i < 0) return;
  if (i >= state.steps.length) return stopTour();
  state = { ...state, i };
  emit();
};
const useTour = () => useSyncExternalStore((cb) => { listeners.add(cb); return () => { listeners.delete(cb); }; }, () => state);

interface Box { left: number; top: number; width: number; height: number }
const PAD = 7, GAP = 14, MARGIN = 12;
const reduced = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

function place(r: Box, w: number, h: number, pref: Step["placement"]) {
  const vw = window.innerWidth, vh = window.innerHeight;
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
  const spots = {
    bottom: { left: clamp(r.left + r.width / 2 - w / 2, MARGIN, vw - w - MARGIN), top: r.top + r.height + GAP },
    top: { left: clamp(r.left + r.width / 2 - w / 2, MARGIN, vw - w - MARGIN), top: r.top - h - GAP },
    right: { left: r.left + r.width + GAP, top: clamp(r.top + r.height / 2 - h / 2, MARGIN, vh - h - MARGIN) },
    left: { left: r.left - w - GAP, top: clamp(r.top + r.height / 2 - h / 2, MARGIN, vh - h - MARGIN) },
  };
  const fits = (s: { left: number; top: number }) => s.left >= MARGIN && s.top >= MARGIN && s.left + w <= vw - MARGIN && s.top + h <= vh - MARGIN;
  const order = [pref, "bottom", "top", "right", "left"].filter((x, i, a): x is NonNullable<Step["placement"]> => !!x && a.indexOf(x) === i);
  const side = order.find((o) => fits(spots[o]));
  if (!side) {
    // a target too big for any side (a whole page): float inside its lower edge, with no arrow
    return { left: clamp(r.left + r.width / 2 - w / 2, MARGIN, vw - w - MARGIN), top: clamp(r.top + r.height - h - 24, MARGIN, vh - h - MARGIN), side: null as null };
  }
  return { ...spots[side], side };
}

export default function TourHost() {
  const st = useTour();
  const nav = useNavigate();
  const loc = useLocation();
  const [target, setTarget] = useState<Element | null>(null);
  const [ready, setReady] = useState(false);
  const [box, setBox] = useState<Box | null>(null);
  const [pop, setPop] = useState<{ left: number; top: number; side: string | null; arrow: number } | null>(null);
  const card = useRef<HTMLDivElement>(null);
  const next = useRef<HTMLButtonElement>(null);
  const step = st ? st.steps[st.i] : null;
  const here = loc.pathname + loc.search;

  // 1. get to the right page
  useEffect(() => {
    if (step?.route && step.route !== here) nav(step.route);
  }, [step, here, nav]);

  // 2. wait for the target to exist (pages load their data after they mount)
  useEffect(() => {
    setTarget(null); setBox(null); setPop(null); setReady(false);
    if (!step || (step.route && step.route !== here)) return;
    let alive = true, tries = 0;
    const find = () => {
      if (!alive) return;
      const el = step.target ? [...document.querySelectorAll(step.target)].find((e) => e.getClientRects().length > 0) ?? null : null;
      if (el) {
        el.scrollIntoView({ block: "center", inline: "nearest", behavior: reduced() ? "auto" : "smooth" });
        setTarget(el); setReady(true);
      } else if (!step.target || tries++ > 30) setReady(true);
      else setTimeout(find, 100);
    };
    find();
    return () => { alive = false; };
  }, [st?.i, st?.steps, here]);

  // 3. follow the target while it scrolls or resizes, and keep the card next to it
  useEffect(() => {
    if (!step || !ready) return;
    let raf = 0;
    const tick = () => {
      const w = Math.min(344, window.innerWidth - 24), h = card.current?.offsetHeight ?? 190;
      if (target && target.isConnected) {
        const b = target.getBoundingClientRect();
        const r = { left: b.left - PAD, top: b.top - PAD, width: b.width + PAD * 2, height: b.height + PAD * 2 };
        setBox((p) => (p && p.left === r.left && p.top === r.top && p.width === r.width && p.height === r.height ? p : r));
        if (window.innerWidth < 700) {
          setPop((p) => (p && p.side === null && p.top === -1 ? p : { left: 12, top: -1, side: null, arrow: 0 }));
        } else {
          const s = place(r, w, h, step.placement);
          const arrow = s.side === "top" || s.side === "bottom" ? r.left + r.width / 2 - s.left : s.side ? r.top + r.height / 2 - s.top : 0;
          const a = Math.max(20, Math.min((s.side === "top" || s.side === "bottom" ? w : h) - 20, arrow));
          setPop((p) => (p && p.left === s.left && p.top === s.top && p.side === s.side && p.arrow === a ? p : { left: s.left, top: s.top, side: s.side, arrow: a }));
        }
      } else {
        setBox(null);
        setPop({ left: (window.innerWidth - w) / 2, top: Math.max(MARGIN, (window.innerHeight - h) / 2 - 20), side: null, arrow: 0 });
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [step, target, ready]);

  useEffect(() => { if (ready) next.current?.focus({ preventScroll: true }); }, [ready, st?.i]);

  // keys: Esc closes, arrows move, Tab stays inside the card
  useEffect(() => {
    if (!st) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); stopTour(); }
      else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
      else if (e.key === "Tab" && card.current) {
        const f = [...card.current.querySelectorAll<HTMLElement>("button")].filter((b) => !b.hasAttribute("disabled"));
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (!card.current.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
        else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [st]);

  if (!st || !step) return null;
  const total = st.steps.length, last = st.i === total - 1;
  const mobile = window.innerWidth < 700;
  const style: React.CSSProperties = !pop ? { visibility: "hidden" }
    : mobile && box ? { left: 12, right: 12, bottom: 76, width: "auto" }
    : { left: pop.left, top: pop.top };
  const arrow: React.CSSProperties | undefined = pop?.side && !mobile
    ? pop.side === "top" || pop.side === "bottom" ? { left: pop.arrow } : { top: pop.arrow } : undefined;
  return (
    <div className="tour-layer" role="presentation">
      <div className={`tour-dim ${box ? "targeted" : ""}`} />
      {box && <div className="tour-spot" style={{ left: box.left, top: box.top, width: box.width, height: box.height }} />}
      <div ref={card} className={`tour-card-pop side-${mobile ? "none" : pop?.side ?? "none"}`} style={style} role="dialog" aria-modal="true"
        aria-labelledby="tour-title" aria-describedby="tour-body">
        {arrow && <i className="tour-arrow" style={arrow} aria-hidden />}
        <div className="tour-meta small">{step.welcome ? "Guided tour" : `Step ${st.i + 1} of ${total}`}</div>
        <h3 id="tour-title">{step.title}</h3>
        <p id="tour-body">{step.body}</p>
        <div className="tour-actions">
          {step.welcome ? (
            <>
              <button ref={next} className="btn primary" onClick={() => go(1)}>Show me around <ArrowRight size={15} /></button>
              <button className="btn quiet" onClick={stopTour}>I will look around myself</button>
            </>
          ) : (
            <>
              <button className="btn quiet sm" onClick={stopTour}>Skip tour</button>
              <span className="grow" />
              <button className="btn sm" onClick={() => go(-1)} disabled={st.i === 0}><ArrowLeft size={14} /> Back</button>
              <button ref={next} className="btn primary sm" onClick={() => go(1)}>{last ? "Done" : <>Next <ArrowRight size={14} /></>}</button>
            </>
          )}
        </div>
        {!step.welcome && <div className="tour-dots" aria-hidden>{st.steps.map((_, k) => <i key={k} className={k === st.i ? "on" : k < st.i ? "past" : ""} />)}</div>}
      </div>
    </div>
  );
}
