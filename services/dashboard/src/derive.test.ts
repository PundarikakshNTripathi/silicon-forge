// SPDX-License-Identifier: AGPL-3.0-or-later
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { STAGES, STAGE_OF, deriveView } from "./derive";
import type { EventRec } from "./api";

let seq = 0;
const ev = (stage: string, status: string, ts: number, payload: Record<string, any> = {}, detail = ""): EventRec => ({ seq: seq++, ts, stage, status, detail, payload });

describe("deriveView", () => {
  it("turns the event stream into per-stage status, loops and times", () => {
    const view = deriveView([
      ev("INIT", "INFO", 100, { eda_mode: "wsl" }),
      ev("ARCHITECT", "PASS", 101, { contract: { ports: [{ name: "clk" }] } }),
      ev("RTL", "VERSION", 102, { label: "v1", source: "module m; endmodule" }),
      ev("GUARDRAIL_AUDIT", "PASS", 103, {}),
      ev("FORMAL_VERIFY", "RETRY", 104, { task_status: { p: "FAIL" }, iteration: 1, witnesses: [] }),
      ev("FORMAL_VERIFY", "PASS", 107, { task_status: { p: "PASS" }, iteration: 2, witnesses: [] }),
    ]);
    expect(view.mode).toBe("wsl");
    expect(view.stages.contract.status).toBe("done");
    expect(view.stages.rtl.status).toBe("done"); // the RTL stage is finished once guardrails have started
    expect(view.stages.formal.status).toBe("done");
    expect(view.stages.formal.loops).toBe(1); // one retry
    expect(view.retries).toEqual([{ stage: "formal", ts: 104 }]); // where the diamond is drawn
    expect(view.times.formal).toEqual({ start: 104, end: 107 });
    expect(view.t0).toBe(100);
    expect(view.t1).toBe(107);
  });

  it("marks the stage that was running as failed when the run fails", () => {
    const view = deriveView([ev("ARCHITECT", "PASS", 1), ev("RTL_GEN", "INFO", 2), ev("FAILED", "FAIL", 3, {}, "model call failed")]);
    expect(view.stages.rtl.status).toBe("fail");
    expect(view.error).toBe("model call failed");
    expect(view.finished).toBe(true);
  });

  it("accumulates model calls into cost and cache hits, without letting them stretch the timeline", () => {
    const call = { prompt_tokens: 100, completion_tokens: 20, cost_usd: 0.01, cache_hit: true };
    const view = deriveView([ev("ARCHITECT", "PASS", 10), ev("LLM", "INFO", 500, call), ev("LLM", "INFO", 501, { ...call, cache_hit: false })]);
    expect(view.cost.calls).toBe(2);
    expect(view.cost.tokens).toBe(240);
    expect(view.cost.cacheHits).toBe(1);
    expect(view.t1).toBe(10); // an LLM event is not stage work
  });
});

describe("stage identity", () => {
  it("every stage has a hue in the generated token file, and no two stages share one", () => {
    const css = readFileSync(new URL("./tokens.css", import.meta.url), "utf8");
    const light = css.slice(0, css.indexOf(":root[data-theme"));
    const hues = STAGES.map((s) => {
      const m = light.match(new RegExp(`--layer-${s.key}: (#[0-9a-fA-F]{6});`));
      expect(m, `--layer-${s.key} missing from tokens.css`).not.toBeNull();
      return m![1].toLowerCase();
    });
    expect(new Set(hues).size).toBe(hues.length);
  });

  it("every pipeline event name that the page understands maps to a stage that exists", () => {
    const keys = new Set(STAGES.map((s) => s.key));
    for (const [name, key] of Object.entries(STAGE_OF)) expect(keys.has(key), name).toBe(true);
  });
});
