// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, MinusCircle, RefreshCw, Save, Search } from "lucide-react";
import { api, post, put, type Doctor } from "../api";
import { Banner, Check, Empty, Field, Panel, Prov, Skeleton } from "../ui";
import { toast } from "../lib";

// ---------- knowledge ----------
interface Skill { name: string; content: string; tokens: number }

export function KnowledgePage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["skills"], queryFn: () => api<Skill[]>("/skills") });
  const [sel, setSel] = useState<string>("");
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const hits = useQuery({ queryKey: ["retrieve", query], queryFn: () => post<{ name: string; content: string }[]>("/skills/retrieve", { query, top_k: 2 }), enabled: query.length > 2 });
  useEffect(() => { if (q.data?.length && !sel) { setSel(q.data[0].name); setText(q.data[0].content); } }, [q.data, sel]);
  const save = useMutation({ mutationFn: () => put(`/skills/${sel}`, { content: text }), onSuccess: () => { toast("Saved. New runs use the updated pack."); qc.invalidateQueries({ queryKey: ["skills"] }); } });
  return (
    <>
      <div className="topbar"><span className="title">Knowledge</span></div>
      <div className="page stack">
        <p className="muted" style={{ maxWidth: 720 }}>Knowledge packs are short, verified notes about your process node, OpenROAD recipes and formal patterns. The agents retrieve the most relevant packs for each step, which keeps them grounded.</p>
        <div className="grid2 split-k" style={{ alignItems: "start" }}>
          <Panel title="Packs" flush>
            {!q.data ? <div className="body"><Skeleton h={80} /></div> : q.data.map((s) => (
              <button key={s.name} className="choice" style={{ border: 0, borderRadius: 0, borderBottom: "1px solid var(--line)" }} role="radio" aria-checked={sel === s.name}
                onClick={() => { setSel(s.name); setText(s.content); }}><div><div className="t">{s.name.replaceAll("_", " ")}</div><div className="muted small">{s.tokens} tokens</div></div></button>))}
          </Panel>
          <div className="stack">
            <Panel title={sel.replaceAll("_", " ") || "Pack"} actions={<button className="btn sm primary" onClick={() => save.mutate()} disabled={!sel || save.isPending}><Save size={13} /> Save</button>}>
              <textarea rows={14} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label="Pack content" />
            </Panel>
            <Panel title="Try retrieval">
              <div style={{ position: "relative" }}>
                <Search size={14} style={{ position: "absolute", left: 9, top: 10, color: "var(--ink-3)" }} />
                <input type="text" style={{ paddingLeft: 28 }} placeholder="Type what an agent might look up, for example: sky130 clock budget" value={query} onChange={(e) => setQuery(e.target.value)} />
              </div>
              <div className="stack" style={{ marginTop: 10 }}>
                {hits.data?.map((h, i) => <div key={h.name} className="small"><strong>{i + 1}. {h.name.replaceAll("_", " ")}</strong></div>)}
                {query.length > 2 && hits.data?.length === 0 && <div className="muted small">Nothing relevant. The agents would get the default packs.</div>}
              </div>
            </Panel>
          </div>
        </div>
      </div>
    </>
  );
}

// ---------- system ----------
interface Cfg { pdk: string; targets: Record<string, number>; pipeline: Record<string, any> }

export function SystemPage() {
  const qc = useQueryClient();
  const d = useQuery({ queryKey: ["doctor", "probe"], queryFn: () => api<Doctor>("/system/doctor"), staleTime: 30_000 });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<Record<string, any>>("/settings") });
  const cfg = useQuery({ queryKey: ["cfg"], queryFn: () => api<Cfg>("/config/defaults") });
  const save = useMutation({ mutationFn: (v: Record<string, unknown>) => put("/settings", v), onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); toast("Settings saved"); } });
  const s = settings.data;
  return (
    <>
      <div className="topbar"><span className="title">Toolchain and settings</span><span className="grow" />
        <button className="btn" onClick={() => qc.invalidateQueries({ queryKey: ["doctor"] })}><RefreshCw size={14} /> Check again</button></div>
      <div className="page stack">
        <div className="grid2" style={{ alignItems: "start" }}>
          <Panel title="Where results can come from">
            {!d.data ? <Skeleton h={160} /> : Object.entries(d.data.backends).map(([name, b]) => (
              <div key={name} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
                <div className="row"><Prov mode={name} /><span className="grow" />{b.available ? <span className="pos row small"><CheckCircle2 size={14} /> Ready</span> : <span className="muted row small"><MinusCircle size={14} /> Not available</span>}</div>
                {Object.keys(b.tools).length > 0 && <div className="row wrap small" style={{ marginTop: 6 }}>{Object.entries(b.tools).map(([t, ok]) => <span key={t} className={ok ? "pos" : "muted"}>{ok ? "✓" : "✗"} {t}</span>)}</div>}
                {b.note && <div className="muted small">{b.note}</div>}
                {b.error && <div className="small neg">{b.error}</div>}
              </div>))}
            {d.data && !d.data.backends.wsl?.available && !d.data.backends.native?.available && (
              <div style={{ marginTop: 12 }}><Banner kind="sim">No real EDA tools were found, so runs can only be simulated. Follow <code>docs/eda-setup.md</code> to install Yosys, SymbiYosys and LibreLane.</Banner></div>)}
          </Panel>
          <Panel title="This machine">
            {!d.data ? <Skeleton h={160} /> : (
              <>
                <Check ok={!!d.data.gpu.name} title={d.data.gpu.name ?? "No NVIDIA GPU"} detail={d.data.gpu.vram_total_mib ? `${(d.data.gpu.vram_total_mib / 1024).toFixed(0)} GB VRAM` : "Local models will run on the CPU"} />
                <Check ok={d.data.llama_cpp.installed} title="llama.cpp" detail={d.data.llama_cpp.installed ? `build ${d.data.llama_cpp.tag}, ${d.data.llama_cpp.models.length} model(s)` : "Install it in Models"} />
                <Check ok={d.data.telemetry_engine_built} title="C++ telemetry engine" detail={d.data.telemetry_engine_built ? "Built" : "Not built. Run python scripts/dev.py build-cpp (Python fallbacks are used meanwhile)"} />
                <div className="small muted" style={{ paddingTop: 8 }}>{d.data.platform}, Python {d.data.python}</div>
              </>
            )}
          </Panel>
        </div>

        <Panel title="Defaults">
          {!s || !cfg.data ? <Skeleton h={120} /> : (
            <div className="grid3">
              <Field label="Default model profile"><select value={s.default_profile} onChange={(e) => save.mutate({ default_profile: e.target.value })}>{["replay", "local", "hybrid", "cloud", "openai", "gemini", "subscription"].map((p) => <option key={p}>{p}</option>)}</select></Field>
              <Field label="Default backend"><select value={s.default_backend} onChange={(e) => save.mutate({ default_backend: e.target.value })}>{["simulated", "wsl", "native", "docker"].map((p) => <option key={p}>{p}</option>)}</select></Field>
              <Field label="Timing signoff corner" hint="Worst covers every corner and is the honest choice. Typical (tt) is optimistic.">
                <select value={s.timing_corner} onChange={(e) => save.mutate({ timing_corner: e.target.value })}><option value="worst">Worst of all corners</option><option value="tt">Typical (tt)</option><option value="ss">Slow (ss)</option><option value="ff">Fast (ff)</option></select></Field>
              <Field label="Local model context (tokens)"><select value={s.llama_ctx} onChange={(e) => save.mutate({ llama_ctx: +e.target.value })}>{[8192, 16384, 32768].map((c) => <option key={c} value={c}>{c}</option>)}</select></Field>
              <Field label="Local KV cache precision" hint="q8_0 halves the VRAM used by the cache with little quality loss."><select value={s.llama_kv} onChange={(e) => save.mutate({ llama_kv: e.target.value })}>{["f16", "q8_0", "q4_0"].map((c) => <option key={c}>{c}</option>)}</select></Field>
            </div>
          )}
        </Panel>
        {d.error && <Empty title="Could not read the toolchain">{String(d.error)}</Empty>}
      </div>
    </>
  );
}
