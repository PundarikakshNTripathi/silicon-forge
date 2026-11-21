import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cpu, Download, KeyRound, Play, Square, Trash2 } from "lucide-react";
import { api, del, post, put, type Profile, type Provider } from "../api";
import { Badge, Banner, Panel, Skeleton } from "../ui";
import { toast } from "../lib";

interface LocalStatus {
  installed: boolean; tag: string | null; models: { name: string; size_gb: number }[];
  server: { running: boolean; model: string | null; props: Record<string, any> };
  gpu: { name?: string; vram_total_mib?: number; vram_used_mib?: number };
  pulls: Record<string, { repo: string; file: string; status: string; error?: string }>;
}
interface ProvidersResp {
  providers: Provider[]; profiles: Profile[]; pricing_as_of: string;
  models: Record<string, { input: number; output: number; cache_read: number | null }>;
}

const CURATED = [
  { repo: "unsloth/Qwen3.5-9B-GGUF", file: "Qwen3.5-9B-Q4_K_M.gguf", note: "5.7 GB. General and coding, fits 8 GB of VRAM." },
  { repo: "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF", file: "qwen2.5-coder-7b-instruct-q4_k_m.gguf", note: "4.7 GB. Code-focused, leaves room for long context." },
  { repo: "Qwen/Qwen3-8B-GGUF", file: "Qwen3-8B-Q4_K_M.gguf", note: "5.0 GB. Strong reasoning for its size." },
];
const KEY_COPY: Record<string, string> = { anthropic: "Anthropic API key", openai: "OpenAI API key", gemini: "Gemini API key" };

export default function Models() {
  const qc = useQueryClient();
  const local = useQuery({ queryKey: ["local"], queryFn: () => api<LocalStatus>("/models"),
    refetchInterval: (q) => (Object.values(q.state.data?.pulls ?? {}).some((p) => p.status === "downloading") ? 2000 : 8000) });
  const prov = useQuery({ queryKey: ["providers"], queryFn: () => api<ProvidersResp>("/providers"), refetchInterval: 8000 });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<Record<string, any>>("/settings") });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["local"] }); qc.invalidateQueries({ queryKey: ["providers"] }); };

  const start = useMutation({ mutationFn: (m: string) => post("/models/server/start", { model: m }), onSuccess: () => { toast("Model server started"); refresh(); }, onError: (e: Error) => toast(`Could not start: ${e.message}`) });
  const stop = useMutation({ mutationFn: () => post("/models/server/stop"), onSuccess: refresh });
  const pull = useMutation({ mutationFn: (c: { repo: string; file: string }) => post("/models/pull", { repo: c.repo, filename: c.file }), onSuccess: () => { toast("Download started"); refresh(); } });
  const install = useMutation({ mutationFn: () => post("/models/install"), onSuccess: () => { toast("Installing llama.cpp…"); refresh(); } });
  const bridge = useMutation({ mutationFn: (v: boolean) => put("/settings", { cli_bridge_enabled: v }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); refresh(); } });

  const l = local.data;
  const vramPct = l?.gpu.vram_total_mib ? Math.round(((l.gpu.vram_used_mib ?? 0) / l.gpu.vram_total_mib) * 100) : 0;
  const downloading = Object.values(l?.pulls ?? {}).filter((p) => p.status === "downloading");

  return (
    <>
      <div className="topbar"><span className="title">Models and providers</span></div>
      <div className="page stack">
        <Panel title="Local model (llama.cpp)" actions={l?.server.running ? <Badge kind="ok">Serving {l.server.model}</Badge> : <Badge>Not running</Badge>}>
          {!l ? <Skeleton h={80} /> : (
            <div className="stack">
              <div className="row wrap">
                <Cpu size={16} />
                {l.gpu.name ? <span>{l.gpu.name}</span> : <span className="muted">No NVIDIA GPU detected, the server will use the CPU.</span>}
                {l.gpu.vram_total_mib ? <span className="muted small">{(l.gpu.vram_used_mib! / 1024).toFixed(1)} of {(l.gpu.vram_total_mib / 1024).toFixed(1)} GB VRAM in use ({vramPct}%)</span> : null}
              </div>
              {l.gpu.vram_total_mib ? <div className="track bars" style={{ height: 8, background: "var(--sunken)", borderRadius: 3, overflow: "hidden" }}><div style={{ width: `${vramPct}%`, height: "100%", background: vramPct > 90 ? "var(--red)" : "var(--blue)" }} /></div> : null}

              {!l.installed ? (
                <Banner>llama.cpp is not installed. SiliconForge downloads the official build for your machine (CUDA when a GPU is present).
                  <div style={{ marginTop: 8 }}><button className="btn primary" onClick={() => install.mutate()} disabled={install.isPending || downloading.some((d) => d.file === "llama-server")}><Download size={14} /> Install llama.cpp</button></div></Banner>
              ) : <div className="muted small">llama.cpp build {l.tag}</div>}

              {downloading.length > 0 && <Banner>Downloading {downloading.map((d) => d.file).join(", ")}… this can take several minutes.</Banner>}

              <table className="t"><thead><tr><th>Model on disk</th><th className="num">Size</th><th /></tr></thead>
                <tbody>
                  {l.models.length === 0 && <tr><td colSpan={3} className="muted">No models yet. Download one below.</td></tr>}
                  {l.models.map((m) => (
                    <tr key={m.name}><td className="mono">{m.name}</td><td className="num">{m.size_gb} GB</td>
                      <td className="num">{l.server.running && l.server.model === m.name
                        ? <button className="btn sm" onClick={() => stop.mutate()}><Square size={13} /> Stop</button>
                        : <button className="btn sm primary" disabled={!l.installed || start.isPending} onClick={() => start.mutate(m.name)}><Play size={13} /> {start.isPending ? "Loading…" : "Start"}</button>}</td></tr>
                  ))}
                </tbody></table>

              <div>
                <h3 style={{ marginBottom: 8 }}>Add a model</h3>
                <div className="grid3">
                  {CURATED.map((c) => {
                    const have = l.models.some((m) => m.name === c.file);
                    const busy = downloading.some((d) => d.file === c.file);
                    return (
                      <div key={c.file} className="panel" style={{ padding: 12 }}>
                        <div style={{ fontWeight: 600 }}>{c.file.replace(/-?Q4_K_M\.gguf/i, "").replace(/\.gguf/, "")}</div>
                        <div className="muted small" style={{ minHeight: 36 }}>{c.note}</div>
                        <button className="btn sm" style={{ marginTop: 8 }} disabled={have || busy} onClick={() => pull.mutate(c)}><Download size={13} /> {have ? "Downloaded" : busy ? "Downloading…" : "Download"}</button>
                      </div>
                    );
                  })}
                </div>
                <div className="hint" style={{ marginTop: 6 }}>Files come from Hugging Face and are verified against their published checksum.</div>
              </div>
            </div>
          )}
        </Panel>

        <Panel title="API keys" actions={<span className="muted small">Stored in your operating system keychain</span>}>
          {!prov.data ? <Skeleton h={90} /> : (
            <table className="t"><thead><tr><th>Provider</th><th>Status</th><th>Key</th></tr></thead>
              <tbody>
                {prov.data.providers.filter((p) => p.key_env).map((p) => <KeyRow key={p.name} p={p} onChange={refresh} />)}
              </tbody></table>
          )}
        </Panel>

        <Panel title="Use a subscription you already have" actions={<Badge kind="warn">Opt-in</Badge>}>
          <div className="stack">
            <Banner>
              This runs the official <code>claude</code>, <code>codex</code> or <code>gemini</code> command-line tool you are already signed into. SiliconForge never reads or stores your sign-in tokens.
              Anthropic and Google restrict third-party use of subscription sign-ins, and whether driving their CLI from another app is allowed can change, so check your plan's terms first.
            </Banner>
            <label className="row"><input type="checkbox" checked={!!settings.data?.cli_bridge_enabled} onChange={(e) => bridge.mutate(e.target.checked)} />
              <span>Enable the CLI bridge (also needs the <code>subscription</code> profile)</span></label>
            <div className="row wrap">
              {prov.data?.providers.filter((p) => p.kind === "cli_bridge").map((p) => (
                <Badge key={p.name} kind={p.installed ? "ok" : "neutral"}>{p.name.replace("cli_", "")} {p.installed ? "found" : "not installed"}</Badge>
              ))}
            </div>
          </div>
        </Panel>

        <Panel title="How each profile routes work" flush actions={prov.data && <span className="muted small">Prices as of {prov.data.pricing_as_of}, per million tokens</span>}>
          {!prov.data ? <div className="body"><Skeleton h={120} /></div> : (
            <div style={{ overflowX: "auto" }}>
              <table className="t"><thead><tr><th>Profile</th><th>Routine</th><th>Balanced</th><th>Hard reasoning</th></tr></thead>
                <tbody>{prov.data.profiles.map((p) => (
                  <tr key={p.name}><td><strong>{p.name}</strong></td>
                    {(["local_fast", "balanced", "frontier_reasoning"] as const).map((t) => (
                      <td key={t} className="small">{p.tiers[t].map((x, i) => {
                        const price = prov.data.models[x.model];
                        return <div key={i} className={i ? "muted" : ""}>{i ? "then " : ""}<span className="mono">{x.model}</span>{price ? <span className="muted"> ${price.input}/${price.output}</span> : null}</div>;
                      })}</td>))}
                  </tr>))}</tbody></table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}

function KeyRow({ p, onChange }: { p: Provider; onChange: () => void }) {
  const [v, setV] = useState("");
  const save = useMutation({
    mutationFn: () => put(`/secrets/${p.key_env}`, { value: v }),
    onSuccess: (r: any) => { toast(r.stored ? "Key saved to your keychain" : "No keychain available. Set the environment variable instead."); setV(""); onChange(); },
  });
  const remove = useMutation({ mutationFn: () => del(`/secrets/${p.key_env}`), onSuccess: () => { toast("Key removed"); onChange(); } });
  return (
    <tr>
      <td><strong>{KEY_COPY[p.name] ?? p.name}</strong><div className="muted small mono">{p.key_env}</div></td>
      <td>{p.key_present ? <Badge kind="ok">Key found</Badge> : <Badge>No key</Badge>}</td>
      <td>
        <div className="row">
          <input type="password" autoComplete="off" placeholder="Paste a key" value={v} onChange={(e) => setV(e.target.value)} style={{ maxWidth: 280 }} aria-label={`${p.name} API key`} />
          <button className="btn sm primary" disabled={!v.trim() || save.isPending} onClick={() => save.mutate()}><KeyRound size={13} /> Save</button>
          {p.key_present && <button className="btn sm quiet" aria-label="Remove key" onClick={() => remove.mutate()}><Trash2 size={13} /></button>}
        </div>
      </td>
    </tr>
  );
}
