import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Play } from "lucide-react";
import { api, post, type Doctor, type Profile, type Provider } from "../api";
import { Banner, Field, Panel, Prov, Skeleton } from "../ui";
import { toast, usd } from "../lib";

interface Example { name: string; spec: string }
interface ProvidersResp { providers: Provider[]; profiles: Profile[] }
interface Estimate { usd: number; tokens: number }

const PROFILE_COPY: Record<string, { title: string; text: string }> = {
  replay: { title: "Bundled examples", text: "Replays recorded designs offline. Free. Shows the whole flow without a model." },
  local: { title: "Local model", text: "Your GPU through llama.cpp. Private, no per-token cost." },
  hybrid: { title: "Local + Claude", text: "Local for routine steps, Claude for hard reasoning, with automatic fallback." },
  cloud: { title: "Claude", text: "Haiku, Sonnet and Opus by task difficulty, with prompt caching." },
  openai: { title: "OpenAI", text: "Uses your OpenAI API key." },
  gemini: { title: "Gemini", text: "Uses your Gemini API key." },
  subscription: { title: "Signed-in CLI", text: "Runs your claude CLI on your subscription. Opt-in, see the terms note in Models." },
};
const BACKEND_COPY: Record<string, string> = {
  simulated: "Surrogate models. Fast, results are marked simulated.",
  wsl: "Real Yosys, SymbiYosys and LibreLane in WSL2.",
  native: "Real tools on this machine's PATH.",
  docker: "Real formal and LEC in a container (no place-and-route yet).",
};

function readiness(p: Profile, providers: Provider[]): { ok: boolean; why: string } {
  for (const name of p.providers) {
    const pr = providers.find((x) => x.name === name);
    if (!pr) return { ok: false, why: `${name} is not configured` };
    if (pr.kind === "replay") continue;
    if (pr.kind === "cli_bridge") {
      if (!pr.enabled) return { ok: false, why: "Enable the CLI bridge in Models" };
      if (!pr.installed) return { ok: false, why: "CLI not installed" };
    } else if (pr.local) {
      if (!pr.reachable) return { ok: false, why: "Start a local model in Models" };
    } else if (!pr.key_present) {
      // hybrid still works without the cloud key because the local target comes first for cheap tiers: flag, don't block
      if (p.name !== "hybrid") return { ok: false, why: `Add your ${pr.name} API key in Models` };
    }
  }
  return { ok: true, why: "" };
}

export default function NewRun() {
  const nav = useNavigate();
  const examples = useQuery({ queryKey: ["examples"], queryFn: () => api<Example[]>("/examples") });
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api<ProvidersResp>("/providers"), refetchInterval: 8000 });
  const doctor = useQuery({ queryKey: ["doctor"], queryFn: () => api<Doctor>("/system/doctor"), staleTime: 60_000 });

  const [spec, setSpec] = useState("");
  const [profile, setProfile] = useState("replay");
  const [backend, setBackend] = useState("simulated");
  const [clock, setClock] = useState(10);
  const [area, setArea] = useState(25000);
  const [power, setPower] = useState(5);
  const [pdk, setPdk] = useState("sky130A");
  const [injectBug, setInjectBug] = useState(true);
  const [verifyOnly, setVerifyOnly] = useState(false);
  const [parallel, setParallel] = useState(1);
  const [episodes, setEpisodes] = useState(6);
  const [relax, setRelax] = useState(false);
  const [budget, setBudget] = useState(15);

  const est = useQuery({ queryKey: ["estimate", profile], queryFn: () => api<Estimate>(`/estimate?profile=${profile}`) });
  const start = useMutation({
    mutationFn: () => post<{ run_id: string }>("/runs", {
      spec_text: spec, profile, backend, pdk, inject_bug: profile === "replay" && injectBug,
      clock_period_ns: clock, max_area_um2: area, max_power_mw: power, max_budget_usd: budget,
      stop_after: verifyOnly ? "lec" : null, parallel_episodes: parallel, max_pnr_episodes: episodes, allow_clock_relax: relax,
    }),
    onSuccess: (r) => nav(`/runs/${r.run_id}`),
    onError: (e: Error) => toast(`Could not start the run: ${e.message}`),
  });

  const matchedExample = useMemo(() => examples.data?.find((e) => spec.includes(e.name)), [spec, examples.data]);
  const replayMiss = profile === "replay" && spec.trim().length > 0 && !matchedExample;
  const selected = providers.data?.profiles.find((p) => p.name === profile);
  const ready = selected && providers.data ? readiness(selected, providers.data.providers) : { ok: true, why: "" };
  const backendOk = (b: string) => b === "simulated" || doctor.data?.backends[b]?.available;
  const canStart = spec.trim().length >= 10 && ready.ok && backendOk(backend) && !replayMiss && !start.isPending;

  return (
    <>
      <div className="topbar"><span className="title">New run</span></div>
      <div className="page stack" style={{ paddingBottom: 0 }}>
        <div>
          <h1>Describe the chip block you want</h1>
          <p className="muted" style={{ marginTop: 4 }}>
            Write the interface, clock, reset and what must always be true. The agent turns it into a contract, writes the RTL, proves it, and runs it through synthesis and layout.
          </p>
        </div>

        <div className="grid2 split-a" style={{ alignItems: "start" }}>
          <div className="stack">
            <Panel title="Specification" actions={
              <div className="row wrap">
                {examples.data?.map((e) => <button key={e.name} className="btn sm" onClick={() => setSpec(e.spec)}>{e.name}</button>)}
              </div>}>
              <textarea value={spec} onChange={(e) => setSpec(e.target.value)} rows={20} spellCheck={false}
                placeholder={"Pick an example above, or describe your own block, for example:\n\n# fifo_sync (8-bit, 16 deep)\n- clk, rst_n (async, active low)\n- push/pop with full/empty flags\n- Formal: never overflow, never underflow"} />
              {replayMiss && (
                <div style={{ marginTop: 10 }}>
                  <Banner kind="sim">The bundled-examples profile only knows the example designs. For your own spec, choose a model profile on the right.</Banner>
                </div>
              )}
            </Panel>
          </div>

          <div className="stack">
            <Panel title="Model">
              <div className="stack" style={{ gap: 8 }} role="radiogroup" aria-label="Model profile">
                {providers.isLoading && <Skeleton h={120} />}
                {providers.data?.profiles.map((p) => {
                  const r = readiness(p, providers.data.providers);
                  const copy = PROFILE_COPY[p.name] ?? { title: p.name, text: "" };
                  return (
                    <button key={p.name} className="choice" role="radio" aria-checked={profile === p.name} aria-disabled={!r.ok}
                      onClick={() => r.ok && setProfile(p.name)}>
                      <div className="grow">
                        <div className="t">{copy.title}</div>
                        <div className="muted small">{copy.text}</div>
                        {!r.ok && <div className="small" style={{ color: "var(--amber)", marginTop: 2 }}>{r.why}. <Link to="/models">Set up</Link></div>}
                      </div>
                    </button>
                  );
                })}
              </div>
            </Panel>

            <Panel title="Where it runs">
              <div className="stack" style={{ gap: 8 }} role="radiogroup" aria-label="EDA backend">
                {["simulated", "wsl", "native", "docker"].map((b) => {
                  const ok = backendOk(b);
                  const unavailable = doctor.data?.backends[b];
                  return (
                    <button key={b} className="choice" role="radio" aria-checked={backend === b} aria-disabled={!ok} onClick={() => ok && setBackend(b)}>
                      <div className="grow">
                        <div className="row"><span className="t">{b === "simulated" ? "Simulated" : b === "wsl" ? "WSL2" : b === "native" ? "This machine" : "Docker"}</span><Prov mode={b} /></div>
                        <div className="muted small">{BACKEND_COPY[b]}</div>
                        {!ok && !doctor.isLoading && <div className="small" style={{ color: "var(--amber)" }}>Tools not found{unavailable?.error ? `: ${unavailable.error}` : ""}. <Link to="/system">Check toolchain</Link></div>}
                      </div>
                    </button>
                  );
                })}
                {doctor.isLoading && <div className="hint">Checking installed tools…</div>}
              </div>
            </Panel>

            <Panel title="Targets">
              <div className="grid2" style={{ gap: 12 }}>
                <Field label="Clock period (ns)" hint={`${(1000 / clock).toFixed(0)} MHz`}>
                  <input type="number" min={0.5} step={0.5} value={clock} onChange={(e) => setClock(+e.target.value)} />
                </Field>
                <Field label="Max area (µm²)"><input type="number" min={100} step={500} value={area} onChange={(e) => setArea(+e.target.value)} /></Field>
                <Field label="Max power (mW)"><input type="number" min={0.1} step={0.5} value={power} onChange={(e) => setPower(+e.target.value)} /></Field>
                <Field label="Process">
                  <select value={pdk} onChange={(e) => setPdk(e.target.value)}><option value="sky130A">SkyWater 130 nm (sky130A)</option><option value="asap7">ASAP7 7 nm (predictive)</option></select>
                </Field>
              </div>
              <details className="adv" style={{ marginTop: 12 }}>
                <summary>More options</summary>
                <div className="stack" style={{ marginTop: 10 }}>
                  {profile === "replay" && (
                    <label className="row"><input type="checkbox" checked={injectBug} onChange={(e) => setInjectBug(e.target.checked)} />
                      <span>Start with a known bug so you can watch the repair loop</span></label>)}
                  <label className="row"><input type="checkbox" checked={verifyOnly} onChange={(e) => setVerifyOnly(e.target.checked)} />
                    <span>Verify only: stop after formal proof and equivalence check</span></label>
                  <label className="row"><input type="checkbox" checked={relax} onChange={(e) => setRelax(e.target.checked)} />
                    <span>Let the optimizer try slower clocks and report the fastest that closes</span></label>
                  <Field label="Layout episodes" hint="How many layout recipes the optimizer may try. It stops early once one closes signoff.">
                    <input type="number" min={1} max={40} value={episodes} onChange={(e) => setEpisodes(+e.target.value)} />
                  </Field>
                  <Field label="Parallel layout episodes" hint="Each episode takes minutes on real tools. Use more on a many-core machine.">
                    <input type="number" min={1} max={8} value={parallel} onChange={(e) => setParallel(+e.target.value)} />
                  </Field>
                  <Field label="Spending limit (USD)"><input type="number" min={0.1} step={1} value={budget} onChange={(e) => setBudget(+e.target.value)} /></Field>
                </div>
              </details>
            </Panel>
          </div>
        </div>
      </div>

      <div className="sticky-bar" style={{ marginTop: 24 }}>
        <div className="grow small ink2">
          {backend === "simulated" ? <><Prov mode="simulated" /> Results will be labelled simulated.</> : <><Prov mode={backend} /> Real tool results.</>}
          {" "}Estimated model cost: <strong>{est.data ? usd(est.data.usd) : "…"}</strong>
          {est.data && est.data.usd === 0 && profile !== "replay" ? " (runs on your hardware)" : ""}.
          {!ready.ok && <span style={{ color: "var(--amber)" }}> {ready.why}.</span>}
        </div>
        <button className="btn primary big" disabled={!canStart} onClick={() => start.mutate()}>
          <Play size={16} /> {start.isPending ? "Starting…" : "Start run"}
        </button>
      </div>
    </>
  );
}
