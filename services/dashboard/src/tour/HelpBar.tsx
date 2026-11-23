// SPDX-License-Identifier: AGPL-3.0-or-later
// "About this page": one plain sentence or two under the top of every page, and a button for a short walk through it.
import { useState } from "react";
import { useLocation } from "react-router-dom";
import { HelpCircle, X } from "lucide-react";
import { DEMO } from "../demo/mode";
import { HELP, PAGE_TOURS, helpKey } from "./steps";
import { startTour } from "./Tour";

const key = (k: string) => `sf-help-hidden-${k}`;
const hidden = (k: string) => { try { return localStorage.getItem(key(k)) === "1"; } catch { return false; } };

export default function HelpBar() {
  const { pathname } = useLocation();
  const k = helpKey(pathname);
  const [, bump] = useState(0);
  if (!k) return null;
  const h = HELP[k];
  const steps = PAGE_TOURS[k];
  if (hidden(k)) {
    return (
      <div className="helpbar slim">
        <button className="btn quiet sm" onClick={() => { try { localStorage.removeItem(key(k)); } catch { /* fine */ } bump((n) => n + 1); }}>
          <HelpCircle size={14} /> About this page
        </button>
      </div>
    );
  }
  return (
    <div className="helpbar" role="note" aria-label="About this page">
      <HelpCircle size={16} aria-hidden />
      <p><b>{h.title}.</b> {DEMO && h.demo ? h.demo : h.live}</p>
      {steps && <button className="btn sm" onClick={() => startTour(steps)}>Show me around</button>}
      <button className="btn quiet sm" aria-label="Hide this explanation" onClick={() => { try { localStorage.setItem(key(k), "1"); } catch { /* fine */ } bump((n) => n + 1); }}><X size={14} /></button>
    </div>
  );
}
