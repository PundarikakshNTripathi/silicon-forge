# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record the agent trace that the static demo replays.

What is real and what is not, because the demo must never imply more than it did:

* **Real:** the agent session (`AgentSession`), its tool box, the permission and approval code, the loop guard, the transcript
  compaction, and the static lint engine (`sf-telemetry`) that produces the guardrail findings. Every tool result in the trace is
  what those tools returned.
* **Scripted:** the language model. The "model" here is a fixed list of replies written by the maintainers, so no model is called
  and no model quality is shown. The trace says so in its provenance block and the UI repeats it.

Two continuations are recorded from the same prefix: the human approves the first edit, or declines it and asks for a revision
(a decline ends the turn in the real agent, so the revision is a second user message). The visitor of the demo picks one.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from services.orchestrator.agent.session import AgentEvent, AgentSession
from services.orchestrator.agent.tools import ToolBox
from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.catalog import Catalog, Target
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.providers.base import BaseLLMProvider, LLMResponse
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.paths import RESOURCE_ROOT
from services.orchestrator.version import __version__
from services.orchestrator.workspace import init_workspace

SCHEMA = "siliconforge.demo-trace/1"
EXAMPLE = "up_down_counter"
RTL_PATH = "rtl/up_down_counter.sv"
# Small on purpose: it makes the real compaction stages kick in within a short session, which is the behaviour being shown.
CONTEXT_CHARS = 2600

TASK = ("Write rtl/up_down_counter.sv for the spec in spec.md, check it with the static linter and fix what it reports. "
        "Do not run the full flow; stop when the lint is clean.")

# The scripted model's first draft carries two habits a model picks up from testbench code: an `initial` block and a `$display`.
# The linter rejects the first (SF-RTL-002, error) and warns about the second (SF-RTL-003).
DRAFT = """// 8-bit loadable up/down counter with wrap flag
module up_down_counter (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       en_i,
    input  logic       load_i,
    input  logic       up_i,
    input  logic [7:0] load_val_i,
    output logic [7:0] count_o,
    output logic       wrap_o
);
    initial count_o = 8'd0;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count_o <= 8'd0;
            wrap_o  <= 1'b0;
        end else begin
            wrap_o <= 1'b0;
            if (load_i) begin
                count_o <= load_val_i;
            end else if (en_i) begin
                $display("count %0d", count_o);
                if (up_i) begin
                    count_o <= count_o + 8'd1;
                    wrap_o  <= (count_o == 8'hFF);
                end else begin
                    count_o <= count_o - 8'd1;
                    wrap_o  <= (count_o == 8'h00);
                end
            end
        end
    end
endmodule
"""

# The revision the human asks for on the second branch: a synchronous reset, as an alternative to the spec's asynchronous one.
DRAFT_REVISED = DRAFT.replace("always_ff @(posedge clk or negedge rst_n)", "always_ff @(posedge clk)")


def _act(say: str = "", tool: str | None = None, **args: Any) -> str:
    return json.dumps({"say": say, "tool": tool, "args": args})


class ScriptedModel(BaseLLMProvider):
    """Replays fixed replies. It is the only thing in the session that is not real."""

    name = "scripted"
    local = True

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):  # noqa: ANN001
        return LLMResponse(content=self.replies.pop(0), prompt_tokens=0, completion_tokens=0, model=model, provider=self.name)


def _session(ws, replies: list[str], approve: list[bool], log: list[dict[str, Any]]) -> AgentSession:
    catalog = Catalog(providers={"scripted": {"kind": "replay", "local": True}},
                      profiles={"dev": {t: [Target("scripted", "scripted-replies")] for t in ("local_fast", "balanced", "frontier_reasoning")}})
    router = ModelRouter(InferenceConfig(profile="dev", enable_semantic_cache=False), CostTracker(),
                         provider=ScriptedModel(replies), catalog=catalog)
    decisions = list(approve)
    holder: dict[str, AgentSession] = {}

    def approver(kind: str, summary: str, details: dict) -> bool:
        ok = decisions.pop(0)
        log.append({"kind": "approval_request", "permission": kind, "tool": details["tool"], "args": details["args"],
                    "summary": summary, "decision": "approved" if ok else "declined"})
        return ok

    def on_event(ev: AgentEvent) -> None:
        entry: dict[str, Any] = {"kind": ev.kind, "text": ev.text}
        if ev.data.get("tool"):
            entry["tool"] = ev.data["tool"]
        if ev.kind == "tool_call":
            entry["args"] = ev.data.get("args", {})
            # how much of the transcript the next prompt has to squeeze: full, or a compacted stage (see agent/compaction.py)
            entry["context"] = getattr(holder["s"], "compaction_stage", "full")
        log.append(entry)

    session = AgentSession(router, ws, ToolBox(ws), mode="ask", approver=approver, on_event=on_event,
                           context_chars=CONTEXT_CHARS, max_steps=12)
    holder["s"] = session
    return session


def _workspace(tmp: Path):
    ws = init_workspace(tmp / "counter", profile="dev", backend="simulated")
    (ws.root / "rtl").mkdir()
    spec = (RESOURCE_ROOT / "hardware" / "examples" / EXAMPLE / "spec.md").read_text(encoding="utf-8")
    (ws.root / "spec.md").write_text(spec, encoding="utf-8", newline="\n")  # same bytes on every platform
    return ws


def _approve_replies() -> list[str]:
    return [
        _act("I will read the spec first.", "read_file", path="spec.md"),
        _act("Writing a first draft of the module.", "write_file", path=RTL_PATH, content=DRAFT),
        _act("Checking the draft with the static linter.", "lint_rtl", path=RTL_PATH),
        _act("The linter rejects the initial block. Removing it.", "edit_file", path=RTL_PATH,
             old="    initial count_o = 8'd0;\n\n", new=""),
        _act("And the simulation task does not belong in RTL.", "edit_file", path=RTL_PATH,
             old='                $display("count %0d", count_o);\n', new=""),
        _act("Linting again.", "lint_rtl", path=RTL_PATH),
        _act("The module lints clean: no initial block, no simulation tasks, load has priority over counting as the spec asks."),
    ]


def _revise_replies() -> list[str]:
    return [
        _act("I will read the spec first.", "read_file", path="spec.md"),
        _act("Writing a first draft of the module.", "write_file", path=RTL_PATH, content=DRAFT),
        # the human declines the first edit; the turn ends. The next replies answer the follow-up message.
        _act("Understood: synchronous reset. Writing the revised draft.", "write_file", path=RTL_PATH, content=DRAFT_REVISED),
        _act("Checking the revision with the static linter.", "lint_rtl", path=RTL_PATH),
        _act("Removing the initial block.", "edit_file", path=RTL_PATH, old="    initial count_o = 8'd0;\n\n", new=""),
        _act("Removing the simulation task.", "edit_file", path=RTL_PATH, old='                $display("count %0d", count_o);\n', new=""),
        _act("Linting again.", "lint_rtl", path=RTL_PATH),
        _act("The revised module uses a synchronous reset and lints clean. Note that the spec asks for an asynchronous reset, "
             "so this deviates from it."),
    ]


REVISION_REQUEST = "Use a synchronous reset instead, and keep everything else."


def record() -> dict[str, Any]:
    """Run both continuations and return the trace document. Deterministic: nothing in it depends on time or paths."""
    with tempfile.TemporaryDirectory(prefix="sf-demo-") as t:
        tmp = Path(t)
        a_log: list[dict[str, Any]] = [{"kind": "user", "text": TASK}]
        a = _session(_workspace(tmp / "a"), _approve_replies(), [True, True, True], a_log)
        a_res = a.send(TASK)
        a.close()

        b_log: list[dict[str, Any]] = [{"kind": "user", "text": TASK}]
        b = _session(_workspace(tmp / "b"), _revise_replies(), [False, True, True, True], b_log)
        b_res = b.send(TASK)
        b_log.append({"kind": "user", "text": REVISION_REQUEST})
        b_res2 = b.send(REVISION_REQUEST)
        b.close()
    if a_res.stopped != "done" or b_res.stopped != "denied" or b_res2.stopped != "done":
        raise RuntimeError(f"unexpected turn results: {a_res.stopped}, {b_res.stopped}, {b_res2.stopped}")

    # both branches share everything up to the first approval request; split there so the player can offer the choice
    cut = next(i for i, e in enumerate(a_log) if e["kind"] == "approval_request")
    cut_b = next(i for i, e in enumerate(b_log) if e["kind"] == "approval_request")
    if a_log[:cut] != b_log[:cut_b]:
        raise RuntimeError("the two branches do not share a prefix")
    checkpoint = {k: a_log[cut][k] for k in ("permission", "tool", "args", "summary")}
    steps = [e for e in a_log[:cut] if e["kind"] == "tool_call"]
    numbered = lambda events: [{**e, "i": i} for i, e in enumerate(events)]  # noqa: E731
    return {
        "schema": SCHEMA,
        "provenance": {
            "model": "scripted replies written by the maintainers; no language model was called",
            "real": ["AgentSession and its tool box", "permission modes and the approval path", "loop guard",
                     "transcript compaction", "static lint engine (sf-telemetry)"],
            "scripted": ["the model's replies"],
            "context_chars": CONTEXT_CHARS,
            "siliconforge_version": __version__,
            "example": EXAMPLE,
        },
        "task": TASK,
        "events_before": numbered(a_log[:cut]),
        "checkpoint": checkpoint,
        "approve": numbered(a_log[cut:]),
        "revise": numbered(b_log[cut_b:]),
        "steps_before_checkpoint": len(steps),
    }


if __name__ == "__main__":
    print(json.dumps(record(), indent=2)[:6000])
