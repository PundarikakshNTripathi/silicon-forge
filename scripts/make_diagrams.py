#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate the architecture diagrams (assets/diagrams/*.svg) in the cockpit's visual language.

    python scripts/make_diagrams.py

Each SVG carries its own light and dark palette (prefers-color-scheme), so it renders correctly on GitHub in both themes.
Boxes are ruled rectangles; amber marks only repair loops and the one thing each diagram wants you to notice; solid ink
stamps and hatched outlines mean real and simulated, as in the app.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "assets" / "diagrams"

TOKENS = json.loads((Path(__file__).resolve().parents[1] / "configs" / "design" / "tokens.json").read_text(encoding="utf-8"))


def _vars(mode: str) -> str:
    m = TOKENS[mode]
    base = (f"--paper:{m['paper']}; --card:{m['paper-2']}; --ink:{m['ink']}; --ink2:{m['ink-2']}; --ink3:{m['ink-3']}; "
            f"--rule:{m['rule']}; --amber:{m['repair']}; --wash:{m['accent-wash']};")
    return base + " " + " ".join(f"--l-{k}:{v[mode]};" for k, v in TOKENS["layers"].items())


STYLE = f"""
<style>
  :root {{ {_vars("light")} }}
  @media (prefers-color-scheme: dark) {{ :root {{ {_vars("dark")} }} }}
  text {{ font-family: "Atkinson Hyperlegible Next", "Segoe UI", system-ui, -apple-system, sans-serif; fill: var(--ink); }}
  .bg {{ fill: var(--paper); }}
  .box {{ fill: var(--card); stroke: var(--ink); stroke-width: 1.6; }}
  .box.quiet {{ stroke: var(--rule); stroke-width: 1.2; }}
  .box.hot {{ fill: var(--wash); stroke: var(--ink); stroke-width: 1.6; }}
  .box.stamp {{ fill: var(--ink); }}
  .frame {{ fill: none; stroke: var(--rule); stroke-width: 1.2; stroke-dasharray: 5 4; }}
  .t {{ font-size: 15px; font-weight: 700; }} .s {{ font-size: 12.5px; fill: var(--ink2); }} .c {{ font-size: 12.5px; font-weight: 700; fill: var(--ink3); }}
  .tw {{ fill: var(--paper); }}
  .ln {{ stroke: var(--ink); stroke-width: 1.6; fill: none; }} .ln.q {{ stroke: var(--ink3); stroke-width: 1.2; }}
  .loop {{ stroke: var(--amber); stroke-width: 3; fill: none; }} .arrowhead {{ fill: var(--ink); }}
  .redo {{ fill: var(--amber); stroke: var(--ink); stroke-width: 1.4; }}
  .hatch {{ fill: url(#h); stroke: var(--ink); stroke-width: 1.4; stroke-dasharray: 4 3; }}
  .band {{ stroke: none; }}
</style>
<defs>
  <pattern id="h" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--card)"/><line x1="0" y1="0" x2="0" y2="6" stroke="var(--ink)" stroke-width="1.6"/></pattern>
  <marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="arrowhead"/></marker>
  <marker id="am" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="var(--amber)"/></marker>
</defs>
"""


class Svg:
    def __init__(self, w: int, h: int, title: str, desc: str) -> None:
        self.w, self.h, self.title, self.desc, self.parts = w, h, title, desc, []

    def box(self, x, y, w, h, title, sub="", kind="", cap=""):
        cls = f"box {kind}".strip()
        self.parts.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}"/>')
        ty = y + (22 if not cap else 36)
        if cap:
            self.parts.append(f'<text class="c" x="{x + 12}" y="{y + 20}">{escape(cap)}</text>')
        tcls = "t tw" if kind == "stamp" else "t"
        self.parts.append(f'<text class="{tcls}" x="{x + 12}" y="{ty}">{escape(title)}</text>')
        for i, line in enumerate(sub.split("\n") if sub else []):
            scls = "s tw" if kind == "stamp" else "s"
            self.parts.append(f'<text class="{scls}" x="{x + 12}" y="{ty + 19 + i * 16}">{escape(line)}</text>')

    def frame(self, x, y, w, h, caption):
        self.parts.append(f'<rect class="frame" x="{x}" y="{y}" width="{w}" height="{h}"/>')
        self.parts.append(f'<text class="c" x="{x + 10}" y="{y - 8}">{escape(caption)}</text>')

    def line(self, pts, kind="", arrow=True):
        d = "M" + " L".join(f"{x} {y}" for x, y in pts)
        marker = ' marker-end="url(#am)"' if kind == "loop" else (' marker-end="url(#a)"' if arrow else "")
        self.parts.append(f'<path class="{"loop" if kind == "loop" else "ln " + kind}" d="{d}"{marker}/>')

    def text(self, x, y, s, cls="s", anchor="start"):
        self.parts.append(f'<text class="{cls}" x="{x}" y="{y}" text-anchor="{anchor}">{escape(s)}</text>')

    def raw(self, s):
        self.parts.append(s)

    def save(self, name):
        OUT.mkdir(parents=True, exist_ok=True)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="t d">'
               f'<title id="t">{escape(self.title)}</title><desc id="d">{escape(self.desc)}</desc>{STYLE}'
               f'<rect class="bg" width="{self.w}" height="{self.h}"/>' + "".join(self.parts) + "</svg>")
        (OUT / name).write_text(svg, encoding="utf-8")
        print("wrote", OUT / name)


LAYER_OF = {"Contract": "contract", "RTL": "rtl", "Guardrails": "guardrails", "Formal": "formal", "Synthesis": "lec", "Layout": "pnr", "Signoff": "signoff"}


def flow() -> None:
    s = Svg(1100, 400, "SiliconForge flow", "Spec to contract, RTL, guardrails, formal, synthesis and equivalence, layout, signoff, with repair loops back to RTL.")
    stages = [("Contract", "ports, clocks,\nbudgets, typed IR"), ("RTL", "SystemVerilog\nfrom any model"), ("Guardrails", "6 gates: ports, lint,\nPDK cells, flow vars,\nvacuity, LEC"),
              ("Formal", "SymbiYosys: BMC,\nPDR proof, cover"), ("Synthesis", "Yosys, then prove\nRTL = netlist"), ("Layout", "OpenROAD,\nRL knob tuning"), ("Signoff", "worst-corner\ntiming, DRC, LVS")]
    x0, w, gap, y, h = 24, 128, 24, 120, 112
    s.box(x0, 28, 1052, 44, "Spec in plain language", "", kind="quiet")
    s.text(x0 + 330, 56, "Tool verdicts decide every edge below. The model proposes; formal tools, equivalence checks and layout reports dispose.", "s")
    for i, (t, sub) in enumerate(stages):
        x = x0 + i * (w + gap)
        s.box(x, y, w, h, t, sub, kind="")
        s.raw(f'<rect class="band" x="{x}" y="{y}" width="{w}" height="7" fill="var(--l-{LAYER_OF[t]})"/>')
        if i:
            s.line([(x - gap + 2, y + h / 2), (x - 2, y + h / 2)])
    s.line([(x0 + 64, 72), (x0 + 64, y - 2)])
    # repair loops: Guardrails -> RTL and Formal -> RTL, drawn under the row
    xr, xg, xf = x0 + 1 * (w + gap) + w / 2, x0 + 2 * (w + gap) + w / 2, x0 + 3 * (w + gap) + w / 2
    s.line([(xg, y + h + 2), (xg, y + h + 34), (xr + 18, y + h + 34), (xr + 18, y + h + 4)], "loop")
    s.line([(xf, y + h + 2), (xf, y + h + 66), (xr - 18, y + h + 66), (xr - 18, y + h + 4)], "loop")
    s.text(xg + 12, y + h + 28, "lint and contract fixes", "s")
    s.text(xf + 12, y + h + 60, "counterexample repair: only the failing cone of logic is sent back", "s")
    s.box(24, 330, 1052, 52, "Provenance on every result", "", kind="quiet")
    s.raw('<rect class="box stamp" x="260" y="344" width="132" height="24"/><text class="s tw" x="272" y="361" style="font-weight:700">real · WSL</text>')
    s.text(404, 361, "produced by Yosys, SymbiYosys, OpenROAD", "s")
    s.raw('<rect class="hatch" x="692" y="344" width="132" height="24"/><text class="s" x="704" y="361" style="font-weight:700;paint-order:stroke;stroke:var(--card);stroke-width:5px">simulated</text>')
    s.text(836, 361, "surrogate models, labelled", "s")
    s.save("flow.svg")


def system() -> None:
    s = Svg(1100, 560, "SiliconForge system", "Desktop, web, terminal and MCP clients feed the workspace agent and pipeline, which call the model router and the EDA runner.")
    s.frame(20, 40, 1060, 76, "Surfaces")
    for i, (t, sub) in enumerate([("Desktop app", "Electron"), ("Web cockpit", "React"), ("Terminal", "chat, agent, TUI"), ("MCP server", "Claude Code, Codex, Antigravity")]):
        s.box(36 + i * 262, 52, 240, 52, t, sub)
    s.frame(20, 160, 1060, 96, "Core (Python)")
    s.box(36, 172, 330, 72, "Workspace agent", "sandboxed tools, permission modes, undo")
    s.box(386, 172, 330, 72, "Deterministic pipeline", "contract > RTL > guardrails > formal > layout")
    s.box(736, 172, 328, 72, "Verification and ISA toolkit", "lockstep, mutation, coverage, ISA generator")
    for x in (200, 550, 900):
        s.line([(x, 106), (x, 170)], "q")
    s.frame(20, 300, 520, 150, "Model router")
    s.box(36, 312, 160, 66, "Profiles", "tiers, fallbacks")
    s.box(210, 312, 150, 66, "Policy", "enforced allow-list", kind="hot")
    s.box(374, 312, 150, 66, "Cache, ledger", "budgets, usage log")
    s.box(36, 388, 150, 52, "llama.cpp", "local, GPU guard")
    s.box(198, 388, 150, 52, "Gemini via agy", "opt-in CLI")
    s.box(360, 388, 164, 52, "Claude, OpenAI APIs", "keys in the keychain")
    s.frame(570, 300, 510, 150, "EDA runner")
    s.raw('<rect class="box stamp" x="586" y="314" width="190" height="40"/><text class="t tw" x="598" y="339">native, WSL, Docker</text>')
    s.raw('<rect class="hatch" x="790" y="314" width="130" height="40"/><text class="t" x="802" y="339" style="paint-order:stroke;stroke:var(--card);stroke-width:5px">simulated</text>')
    for i, t in enumerate(["Yosys", "SymbiYosys", "Verilator", "Icarus, cocotb", "OpenROAD", "mcy"]):
        s.box(586 + (i % 3) * 160, 366 + (i // 3) * 38, 150, 30, t, kind="quiet")
    s.line([(200, 246), (200, 298)], "q")                        # agent -> router
    s.line([(470, 246), (470, 298)], "q")                        # pipeline -> router
    s.line([(640, 246), (640, 298)], "q")                        # pipeline -> EDA runner
    s.line([(900, 246), (900, 298)], "q")                        # toolkit -> EDA runner
    s.text(24, 500, "Missing tools raise an error. They are never silently replaced by simulated results.", "s")
    s.text(24, 522, "Another program's GPU job or model server is read, never stopped or evicted.", "s")
    s.save("system.svg")


def verification() -> None:
    s = Svg(1100, 520, "Verification stack", "One isa.yaml generates the decoder, assembler, golden model, timing checker and docs; the RTL is checked against the model in lockstep, by mutation, formal proof, coverage and gate-level simulation.")
    s.box(24, 40, 230, 96, "isa.yaml", "one source of truth:\nencoding, timing, semantics", kind="hot")
    gens = [("Verilog decoder", "one-hot, proved"), ("Assembler", "labels, line errors"), ("Golden model", "same decode as the tools"), ("Timing checker", "pin-event gaps, proved"), ("Docs", "bit-field diagrams")]
    for i, (t, sub) in enumerate(gens):
        s.box(320, 20 + i * 58, 220, 50, t, sub, kind="quiet")
        s.line([(254, 88), (318, 45 + i * 58)], "q")
    s.box(620, 120, 190, 96, "RTL under test", "written by a person\nor a model")
    s.line([(540, 45), (560, 45), (560, 150), (618, 150)])            # decoder is used by the RTL
    s.text(566, 100, "used by", "c")
    checks = [("Lockstep", "state vs the golden model,\nafter every instruction"), ("Mutation", "planted bugs,\nproved equivalents excluded"), ("Formal", "BMC, PDR proof,\nvacuity checked"), ("Coverage", "functional bins, toggle"), ("Gate level", "your tests on the netlist,\nPDK cell models")]
    for i, (t, sub) in enumerate(checks):
        y = 20 + i * 74
        s.box(870, y, 206, 64, t, sub)
        s.line([(810, 168), (840, 168), (840, y + 32), (868, y + 32)], "q")
    s.line([(540, 161), (590, 161), (590, 400), (850, 400), (850, 52), (868, 52)], "q")   # golden model feeds lockstep
    s.text(596, 394, "golden model feeds lockstep", "c")
    s.box(24, 420, 1052, 80, "Independent protocol decoders (UART, SPI, I2C, WS2812)", "", kind="quiet")
    s.text(36, 468, "They read only the pins, never the RTL, so a design cannot pass by sharing its author's misunderstanding. Every result names the backend that ran it.", "s")
    s.save("verification.svg")


def contest() -> None:
    s = Svg(1100, 300, "Competition workflow", "From a design brief through architecture and ISA, RTL and tests, verification, synthesis budget, gate-level simulation and hardening, to a write-up with an AI-usage ledger.")
    steps = [("Brief", "rules and budget"), ("Architecture", "spec, ISA,\narea estimate"), ("RTL and tests", "per module,\nlockstep"), ("Verify", "mutation, formal,\ncoverage"), ("Budget", "real IHP cells,\ntile fit"), ("Gate level", "PDK cell models"), ("Harden", "official flow,\nDocker or CI"), ("Write-up", "evidence and\nAI ledger")]
    w, gap = 118, 14
    for i, (t, sub) in enumerate(steps):
        x = 24 + i * (w + gap)
        s.box(x, 70, w, 96, t, sub, kind="hot" if t == "Verify" else "")
        if i:
            s.line([(x - gap + 1, 118), (x - 1, 118)])
    s.line([(24 + 3 * (w + gap) + 60, 168), (24 + 3 * (w + gap) + 60, 204), (24 + 2 * (w + gap) + 60, 204), (24 + 2 * (w + gap) + 60, 168)], "loop")
    s.text(24 + 2 * (w + gap), 226, "a surviving mutant or failing proof sends you back to tests or RTL", "s")
    s.text(24, 36, "Every model call is recorded in .siliconforge/ai-usage.jsonl (metadata and a prompt hash, never prompt text) and the router drops any model the workspace does not allow.", "s")
    s.text(24, 276, "You submit. The tool never submits for you.", "c")
    s.save("competition.svg")


def agent() -> None:
    s = Svg(1100, 520, "The agent loop", "A request becomes a prompt, the model answers with one JSON action, permission rules and your approval gate it, the tool runs in a sandbox, "
            "its output is redacted and fenced, and every step is recorded as metadata-only spans.")
    s.text(24, 28, "One turn, repeated until the model answers without a tool call (or a budget, the loop guard or you stops it).", "s")
    steps = [("Prompt", "instructions, project memory,\ntool list, transcript\n(older turns compacted)"), ("Model", "one JSON action:\nsay, tool, args\n(constrained decoding)"),
             ("Permission", "deny rules, then mode,\nthen allow rules,\nthen you approve"), ("Tool", "path-confined sandbox,\nundo, pre and post\nhooks"),
             ("Result", "keys redacted, wrapped\nas untrusted data,\nclipped")]
    x0, w, gap, y, h = 24, 188, 28, 60, 112
    for i, (t, sub) in enumerate(steps):
        x = x0 + i * (w + gap)
        s.box(x, y, w, h, t, sub, kind="hot" if t == "Permission" else "")
        if i:
            s.line([(x - gap + 2, y + h / 2), (x - 2, y + h / 2)])
    s.line([(x0 + 4 * (w + gap) + w / 2, y + h + 2), (x0 + 4 * (w + gap) + w / 2, y + h + 34), (x0 + w / 2, y + h + 34), (x0 + w / 2, y + h + 4)], "q")
    s.text(x0 + 2 * (w + gap) + 20, y + h + 28, "the result goes back into the transcript, and the model decides the next step", "s")
    s.frame(24, 238, 1052, 92, "Guards inside the loop (tested with scripted models)")
    guards = [("Loop guard", "a note at the third identical\ncall, a stop at the fifth"), ("Step budget", "per turn; a cost and token\nbudget per workspace"), ("Interrupt", "Esc stops the turn before\nthe next model call"),
              ("Policy", "the router drops models the\nworkspace does not allow")]
    for i, (t, sub) in enumerate(guards):
        s.box(36 + i * 262, 252, 244, 66, t, sub, kind="quiet")
    s.box(24, 366, 330, 62, "investigate", "a read-only helper with a fresh context;\nonly its short answer comes back", kind="quiet")
    s.box(374, 366, 330, 62, "Project memory", "notes in .siliconforge/memory.md,\nsecret-masked, editable by you", kind="quiet")
    s.text(24, 356, "Also part of the loop", "c")
    s.box(724, 366, 352, 62, "Spans: metadata only", "run > step > model call | tool call: time, tokens,\nerror class. No prompts, arguments or output.", kind="quiet")
    s.text(24, 462, "Every span feeds the same four measurements: where the time goes (model, tools, overhead), how often tools work, what a solved task costs, and whether the agent recovers.", "s")
    s.text(24, 482, "They reach the Insights page, `siliconforge trace`, OTLP (Phoenix, Langfuse) and a Prometheus endpoint.", "s")
    s.save("agent.svg")


def agentbench() -> None:
    s = Svg(1100, 470, "AgentBench-SF design", "Three agent loops get the same model, the same tools, the same sandbox and the same step budget. Model calls are timed at one proxy and tool "
            "calls at one tool box; a hidden verifier judges each run.")
    s.box(24, 50, 210, 130, "13 tasks", "a fixture folder, a step\nbudget, optional faults\nand a verifier the\nagent never sees", kind="quiet")
    s.frame(284, 34, 270, 262, "The only thing that differs")
    for i, (t, sub) in enumerate([("SiliconForge loop", "JSON actions, our guards"), ("LangGraph", "create_agent, native tool calls"), ("Pydantic AI", "typed tools, native tool calls")]):
        s.box(298, 50 + i * 82, 242, 70, t, sub)
        s.line([(234, 115), (262, 115), (262, 85 + i * 82), (296, 85 + i * 82)], "q")
    s.box(640, 50, 200, 76, "Measuring proxy", "every model call:\ntime, tokens, status")
    s.box(640, 196, 200, 76, "Instrumented tool box", "every tool call:\ntime, error class")
    s.box(900, 50, 176, 76, "llama-server", "Qwen3.5-9B Q4_K_M\none GPU, thinking off", kind="stamp")
    s.box(900, 196, 176, 76, "Sandbox tools", "read, edit, lint, grep,\nsimulate, formal", kind="quiet")
    for y in (85, 167, 249):  # every loop reaches the model through the proxy and the tools through the tool box
        s.line([(540, y), (590, y)], "q", arrow=False)
    s.line([(590, 85), (590, 249)], "q", arrow=False)
    s.line([(590, 88), (638, 88)])
    s.line([(590, 234), (638, 234)])
    s.line([(840, 88), (898, 88)])
    s.line([(840, 234), (898, 234)])
    s.box(24, 330, 520, 110, "A hidden verifier judges each run", "lint, or a simulation testbench, or an exact answer line; a safety\nviolation (a leaked secret, an obeyed injected instruction)\nfails the task whatever the answer. The task is the unit of analysis.", kind="hot")
    s.box(580, 330, 496, 110, "The same four measurements for every loop", "time split (model / tools / overhead), tool success with failures by class,\ntokens per solved task, recovery after a failure. Intervals are\nbootstrap over tasks; the loops are compared paired.", kind="quiet")
    s.line([(988, 274), (988, 300), (300, 300), (300, 328)], "q")
    s.text(24, 462, "Arms are rotated per task so none always runs first; the server is warmed up first, and one 9B model is the only model.", "s")
    s.save("agentbench.svg")


if __name__ == "__main__":
    for build in (flow, system, verification, contest, agent, agentbench):
        build()
