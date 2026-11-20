import React, { useState } from "react";
import type { RunResponse } from "./types";

const DEFAULT_SPEC = `# axi_lite_alu (32-bit Registered ALU)
Target PDK: sky130A (100 MHz, 10ns clock, <25,000 um^2, <5.0 mW).
Supports ADD, SUB, AND, OR, XOR with valid/ready handshake and registered zero_o flag.`;

export default function App() {
  const [specText, setSpecText] = useState(DEFAULT_SPEC);
  const [injectBug, setInjectBug] = useState(true);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function launchRun() {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch("/api/v1/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spec_text: specText, inject_bug: injectBug }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: RunResponse = await resp.json();
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reach orchestrator API");
    } finally {
      setLoading(false);
    }
  }

  const best = result?.state.best_ppa;

  return (
    <div style={{ maxWidth: 1120, margin: "0 auto", padding: "28px 20px" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, color: "#38bdf8" }}>SiliconForge RTL-to-GDSII Cockpit</h1>
          <p style={{ margin: "4px 0 0", color: "#94a3b8", fontSize: 13 }}>
            Autonomous Spec → DesignContract → SystemVerilog → Formal (SymbiYosys) → RL PPA (OpenROAD) → Signoff
          </p>
        </div>
        {result && (
          <span
            style={{
              padding: "6px 12px",
              borderRadius: 999,
              fontWeight: 600,
              fontSize: 12,
              background: result.state.signoff_passed ? "#065f46" : "#7f1d1d",
              color: "#f8fafc",
            }}
          >
            {result.state.signoff_passed ? "SIGNOFF CLEAN (GDSII READY)" : "SIGNOFF PENDING"}
          </span>
        )}
      </header>

      <section style={{ background: "#111827", border: "1px solid #1e293b", borderRadius: 12, padding: 18, marginBottom: 20 }}>
        <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Hardware Specification</label>
        <textarea
          value={specText}
          onChange={(e) => setSpecText(e.target.value)}
          rows={4}
          style={{
            width: "100%",
            background: "#0f172a",
            color: "#e2e8f0",
            border: "1px solid #334155",
            borderRadius: 8,
            padding: 10,
            fontFamily: "monospace",
            fontSize: 13,
            boxSizing: "border-box",
          }}
        />
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 12 }}>
          <label style={{ fontSize: 13, color: "#cbd5e1", display: "flex", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={injectBug} onChange={(e) => setInjectBug(e.target.checked)} />
            Inject initial zero_o bug (demonstrates SymbiYosys counterexample + COI repair loop)
          </label>
          <button
            onClick={launchRun}
            disabled={loading}
            style={{
              background: "#0284c7",
              color: "#fff",
              border: "none",
              borderRadius: 8,
              padding: "10px 18px",
              fontWeight: 600,
              cursor: loading ? "wait" : "pointer",
            }}
          >
            {loading ? "Running Flow..." : "Launch RTL-to-GDSII Run"}
          </button>
        </div>
        {error && <p style={{ color: "#f87171", marginTop: 10, fontSize: 13 }}>Error: {error}</p>}
      </section>

      {result && best && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 }}>
          {[
            { label: "Worst Negative Slack (WNS)", val: `${best.wns_ns.toFixed(3)} ns` },
            { label: "Core Area", val: `${best.area_um2.toFixed(0)} um²` },
            { label: "Total Power", val: `${best.total_power_mw.toFixed(2)} mW` },
            { label: "DRC / LVS / Cost", val: `${best.drc_violations} DRC · ${best.lvs_clean ? "LVS OK" : "LVS FAIL"} · $${result.cost.total_cost_usd.toFixed(4)}` },
          ].map((card) => (
            <div key={card.label} style={{ background: "#111827", border: "1px solid #1e293b", borderRadius: 10, padding: 14 }}>
              <div style={{ fontSize: 11, color: "#94a3b8", textTransform: "uppercase" }}>{card.label}</div>
              <div style={{ fontSize: 18, fontWeight: 700, marginTop: 6, color: "#f8fafc" }}>{card.val}</div>
            </div>
          ))}
        </div>
      )}

      {result && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <section style={{ background: "#111827", border: "1px solid #1e293b", borderRadius: 12, padding: 16 }}>
            <h2 style={{ margin: "0 0 12px", fontSize: 16 }}>Pipeline & Formal Repair Trace</h2>
            <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 8 }}>
              {result.state.events.map((ev, i) => (
                <li key={i} style={{ background: "#0f172a", padding: 10, borderRadius: 8, fontSize: 12 }}>
                  <strong style={{ color: ev.status === "PASS" ? "#34d399" : "#fbbf24" }}>[{ev.stage} · {ev.status}]</strong>{" "}
                  {ev.detail}
                </li>
              ))}
            </ul>
          </section>

          <section style={{ background: "#111827", border: "1px solid #1e293b", borderRadius: 12, padding: 16 }}>
            <h2 style={{ margin: "0 0 12px", fontSize: 16 }}>RL PPA Episodes ({result.state.ppa_episodes.length})</h2>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ color: "#94a3b8", textAlign: "left", borderBottom: "1px solid #1e293b" }}>
                  <th style={{ padding: "6px 4px" }}>Ep</th>
                  <th>Strategy</th>
                  <th>WNS (ns)</th>
                  <th>Area (um²)</th>
                  <th>DRC</th>
                  <th>Reward</th>
                </tr>
              </thead>
              <tbody>
                {result.state.ppa_episodes.map((ep, idx) => (
                  <tr key={idx} style={{ borderBottom: "1px solid #1e293b" }}>
                    <td style={{ padding: "6px 4px" }}>#{idx + 1}</td>
                    <td>{String(ep.knobs.SYNTH_STRATEGY ?? "-")}</td>
                    <td style={{ color: ep.wns_ns >= 0 ? "#34d399" : "#f87171" }}>{ep.wns_ns.toFixed(2)}</td>
                    <td>{ep.area_um2.toFixed(0)}</td>
                    <td>{ep.drc_violations}</td>
                    <td>{ep.reward.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
      )}
    </div>
  );
}
