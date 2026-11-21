// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUp, FileText, Folder, FolderOpen, Send } from "lucide-react";
import { api, post, put, del } from "../api";
import { Banner, Empty, Field, Panel } from "../ui";

interface WsInfo { root: string; name: string; profile: string; backend: string; allowed_models: string[]; has_manifest: boolean; instructions_file: string | null }
interface FsList { path: string; parent: string | null; roots: { name: string; path: string }[]; entries: { name: string; path: string; workspace: boolean }[]; workspace?: boolean }
interface AgentEvent { seq: number; kind: string; text: string; data: Record<string, any> }
type Mode = "ask" | "accept-edits" | "auto" | "plan";

const MODE_HELP: Record<Mode, string> = {
  ask: "Asks before every edit and command",
  "accept-edits": "Edits files freely, asks before running tools",
  auto: "Asks nothing. Use only in a folder you trust",
  plan: "Read-only: describes changes instead of making them",
};

declare global { interface Window { siliconforgeDesktop?: { pickFolder?: (start?: string) => Promise<string | null> } } }

function FolderBrowser({ onPick }: { onPick: (path: string) => void }) {
  const [path, setPath] = useState("");
  const list = useQuery({ queryKey: ["fs", path], queryFn: () => api<FsList>(`/fs?path=${encodeURIComponent(path)}`) });
  const d = list.data;
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="btn sm" disabled={!d?.parent} onClick={() => d?.parent && setPath(d.parent)} aria-label="Up one folder"><ArrowUp size={14} /> Up</button>
        <code className="grow" style={{ overflowWrap: "anywhere" }}>{d?.path || "Choose a drive or folder"}</code>
        {d?.path && <button className="btn sm primary" onClick={() => onPick(d.path)}>Use this folder</button>}
      </div>
      <ul className="filelist" aria-label="Folders">
        {(d?.path ? [] : d?.roots ?? []).map((r) => <li key={r.path} onClick={() => setPath(r.path)}><Folder size={16} />{r.name}</li>)}
        {(d?.entries ?? []).map((e) => <li key={e.path} className={e.workspace ? "ws" : ""} onClick={() => setPath(e.path)}><Folder size={16} />{e.name}</li>)}
        {d && d.path && d.entries.length === 0 && <li className="muted">No subfolders here.</li>}
      </ul>
    </div>
  );
}

function Chat({ ws }: { ws: WsInfo }) {
  const [sid, setSid] = useState<string | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<Mode>("ask");
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const last = useRef(-1);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let dead = false;
    setSid(null); setEvents([]); last.current = -1;
    post<{ id: string }>("/agent/sessions", { workspace: ws.root, mode: "ask" })
      .then((r) => { if (!dead) { setSid(r.id); setMode("ask"); } })
      .catch((e) => setErr(String(e.message ?? e)));
    return () => { dead = true; };
  }, [ws.root]);

  useEffect(() => {
    if (!sid) return;
    const t = setInterval(async () => {
      try {
        const r = await api<{ events: AgentEvent[]; busy: boolean }>(`/agent/sessions/${sid}/events?after=${last.current}`);
        if (r.events.length) { last.current = r.events[r.events.length - 1].seq; setEvents((p) => [...p, ...r.events]); }
        setBusy(r.busy);
      } catch { /* the session ended */ }
    }, 600);
    return () => { clearInterval(t); if (sid) del(`/agent/sessions/${sid}`).catch(() => undefined); };
  }, [sid]);

  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [events.length]);

  const pending = events.filter((e) => e.kind === "approval_request" && !events.some((r) => r.kind === "approval_result" && r.data.id === e.data.id));
  const send = async () => {
    if (!sid || !text.trim()) return;
    setErr(null);
    try { await post(`/agent/sessions/${sid}/messages`, { text }); setText(""); setBusy(true); }
    catch (e: any) { setErr(e.message ?? String(e)); }
  };
  const decide = (id: string, allow: boolean) => post(`/agent/sessions/${sid}/approvals/${id}`, { allow }).catch((e) => setErr(e.message));
  const changeMode = (m: Mode) => { setMode(m); if (sid) put(`/agent/sessions/${sid}/mode`, { mode: m }).catch((e) => setErr(e.message)); };

  const shown = events.filter((e) => ["user", "assistant", "tool_call", "tool_result", "approval_request", "error", "turn_end"].includes(e.kind));
  return (
    <div>
      <div className="row wrap" style={{ marginBottom: 10 }}>
        <Field label="Permission mode" hint={MODE_HELP[mode]}>
          <select value={mode} onChange={(e) => changeMode(e.target.value as Mode)}>
            {(Object.keys(MODE_HELP) as Mode[]).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </Field>
        {ws.allowed_models.length > 0 && <span className="small muted">Models limited to {ws.allowed_models.join(", ")}</span>}
      </div>
      {err && <Banner kind="bad">{err}</Banner>}
      <div className="chat" role="log" aria-live="polite">
        {shown.length === 0 && <div className="empty"><h3>Ask for something in this folder</h3>
          <p>For example: &ldquo;write rtl/counter.sv, lint it, and run a Verilator lint.&rdquo; The agent can only see this folder, and it asks before it changes anything unless you chose auto.</p></div>}
        {shown.map((e) => {
          if (e.kind === "turn_end") return null;
          const who = e.kind === "user" ? "You" : e.kind === "assistant" ? "Agent" : e.kind === "error" ? "Problem" : e.kind === "approval_request" ? "Needs you" : "Tool";
          return (
            <div key={e.seq} className={`msg ${e.kind === "user" ? "user" : ""} ${e.kind === "tool_call" || e.kind === "tool_result" ? "tool" : ""} ${e.kind === "approval_request" ? "ask" : ""}`}>
              <span className="who">{who}</span><span className="what">{e.text}</span>
            </div>
          );
        })}
        <div ref={end} />
      </div>
      {pending.map((p) => (
        <Banner key={p.seq}>
          <div className="grow"><b>Allow this {p.data.permission === "edit" ? "edit" : "command"}?</b><div className="mono small" style={{ overflowWrap: "anywhere" }}>{p.text}</div></div>
          <button className="btn sm primary" onClick={() => decide(p.data.id, true)}>Allow</button>
          <button className="btn sm" onClick={() => decide(p.data.id, false)}>Decline</button>
        </Banner>
      ))}
      <div className="composer">
        <textarea value={text} placeholder="What should the agent do?" onChange={(e) => setText(e.target.value)} disabled={!sid}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); } }} aria-label="Message to the agent" />
        <button className="btn primary" onClick={send} disabled={!sid || busy || !text.trim()}><Send size={15} /> {busy ? "Working" : "Send"}</button>
      </div>
      <p className="hint" style={{ marginTop: 4 }}>Ctrl or Cmd + Enter sends.</p>
    </div>
  );
}

function Tree({ root }: { root: string }) {
  const [dir, setDir] = useState(".");
  const [file, setFile] = useState<string | null>(null);
  const tree = useQuery({ queryKey: ["tree", root, dir], queryFn: () => api<{ entries: { name: string; size: number | null }[] }>(`/workspace/tree?root=${encodeURIComponent(root)}&path=${encodeURIComponent(dir)}`) });
  const content = useQuery({ queryKey: ["file", root, file], enabled: !!file, queryFn: () => api<{ content: string }>(`/workspace/file?root=${encodeURIComponent(root)}&path=${encodeURIComponent(file!)}`) });
  const join = (n: string) => (dir === "." ? n : `${dir}/${n}`);
  return (
    <div>
      <ul className="filelist">
        {dir !== "." && <li onClick={() => setDir(dir.includes("/") ? dir.slice(0, dir.lastIndexOf("/")) : ".")}><ArrowUp size={16} />..</li>}
        {(tree.data?.entries ?? []).map((e) => e.name.endsWith("/")
          ? <li key={e.name} onClick={() => setDir(join(e.name.slice(0, -1)))}><Folder size={16} />{e.name}</li>
          : <li key={e.name} onClick={() => setFile(join(e.name))} style={file === join(e.name) ? { background: "var(--amber-wash)" } : undefined}><FileText size={16} />{e.name}</li>)}
        {tree.data && tree.data.entries.length === 0 && <li className="muted">This folder is empty.</li>}
      </ul>
      {file && <Panel title={file} flush><pre className="code" style={{ padding: 12, maxHeight: 280 }}>{content.data?.content ?? "Loading"}</pre></Panel>}
    </div>
  );
}

export default function WorkspacePage() {
  const [ws, setWs] = useState<WsInfo | null>(null);
  const [picking, setPicking] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const recent = useQuery({ queryKey: ["workspaces"], queryFn: () => api<{ recent: { path: string; name: string; profile: string }[] }>("/workspaces") });

  const open = async (path: string, create = false) => {
    setErr(null);
    try { setWs(await post<WsInfo>("/workspaces/open", { path, create })); setPicking(false); recent.refetch(); }
    catch (e: any) { setErr(e.message ?? String(e)); }
  };
  const choose = async () => {
    const native = window.siliconforgeDesktop?.pickFolder;
    if (native) { const p = await native(ws?.root); if (p) open(p); } else setPicking(true);
  };

  return (
    <>
      <div className="topbar"><span className="title">Workspace</span><span className="grow" />
        {ws && <button className="btn sm" onClick={choose}><FolderOpen size={14} /> Change folder</button>}</div>
      <div className="page stack">
        {err && <Banner kind="bad">{err}</Banner>}
        {!ws && (
          <div className="grid2 split-c">
            <div className="stack" style={{ gap: 14 }}>
              <h1>Work in a folder</h1>
              <p className="ink2" style={{ maxWidth: "58ch" }}>Pick the folder your project lives in. The agent reads and writes only there, follows the instructions in its
                <code> SILICONFORGE.md</code> or <code>AGENTS.md</code>, and keeps its runs and usage ledger under <code>.siliconforge/</code>.</p>
              <div className="row"><button className="btn primary big" onClick={choose}><FolderOpen size={17} /> Choose a folder</button></div>
            </div>
            <Panel title="Recent folders" flush>
              {(recent.data?.recent ?? []).length === 0
                ? <Empty title="No folders yet">Folders you open appear here.</Empty>
                : <ul className="filelist" style={{ borderTop: 0 }}>{recent.data!.recent.map((r) => <li key={r.path} onClick={() => open(r.path)}><Folder size={16} /><span className="grow"><b>{r.name}</b><span className="muted small" style={{ display: "block", overflowWrap: "anywhere" }}>{r.path}</span></span></li>)}</ul>}
            </Panel>
          </div>
        )}
        {picking && <Panel title="Choose a folder"><FolderBrowser onPick={(p) => open(p)} /></Panel>}
        {ws && (
          <div className="ws-grid">
            <div className="stack" style={{ gap: 14 }}>
              <div><h2>{ws.name}</h2><code className="muted small" style={{ overflowWrap: "anywhere" }}>{ws.root}</code></div>
              <div className="small ink2">Profile <b>{ws.profile}</b>, backend <b>{ws.backend}</b>{ws.instructions_file ? <>, instructions from <b>{ws.instructions_file}</b></> : ", no instruction file"}.
                {!ws.has_manifest && <> No <code>siliconforge.yaml</code> yet: <button className="btn sm" onClick={() => open(ws.root, true)}>Create one</button></>}</div>
              <Tree root={ws.root} />
            </div>
            <Chat ws={ws} />
          </div>
        )}
      </div>
    </>
  );
}
