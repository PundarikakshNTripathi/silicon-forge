# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `custom` arm: SiliconForge's own agent loop (`AgentSession`), exactly as shipped.

It speaks the JSON-action protocol (schema-constrained decoding, one object per turn), keeps its context inside a budget, and has
its guards on: loop detection, tool-output fencing, secret redaction. It is pointed at the measuring proxy through a throwaway
catalog entry, with streaming off so it calls the model the same way the framework arms do.

The session gets its *own* tracer, used only to learn two things the HTTP seam cannot see: which model replies were not a valid
action, and which tool names it was asked for that do not exist. Tool timing and success come from the shared tool seam.
"""
from __future__ import annotations

from services.orchestrator.agent.session import AgentSession
from services.orchestrator.agentbench.arms.base import ArmContext, ArmResult
from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.catalog import Catalog, Target
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.telemetry import Tracer
from services.orchestrator.telemetry.metrics import INVALID_ACTION

TIERS = ("local_fast", "balanced", "frontier_reasoning")


def bench_router(base_url: str, model: str, max_tokens: int) -> ModelRouter:
    catalog = Catalog(
        providers={"bench": {"kind": "openai_compat", "base_url": base_url, "local": True, "supports_json_schema": True,
                             "cache_prompt": True, "stream": False, "max_tokens": max_tokens, "timeout_s": 300,
                             "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}},
        profiles={"bench": {t: [Target("bench", model)] for t in TIERS}})
    return ModelRouter(InferenceConfig(profile="bench", enable_semantic_cache=False), CostTracker(), catalog=catalog)


class CustomArm:
    name = "custom"
    guarded = False  # the session applies its own guards; the toolbox must not apply them a second time
    ask_first = True  # `custom_prev` sets this False: the loop as released in v0.6.0, for a before/after on the same tasks

    def run(self, ctx: ArmContext) -> ArmResult:
        router = bench_router(ctx.base_url, ctx.model, ctx.max_tokens)
        own = Tracer()
        session = AgentSession(router, ctx.tools.ws, ctx.tools, mode="auto", max_steps=ctx.max_steps, tracer=own,
                               ask_first=self.ask_first)
        res = session.send(ctx.task.prompt)
        llm = sorted((s for s in own.spans if s.kind == "llm"), key=lambda s: s.start)
        invalid = [i for i, s in enumerate(llm) if s.status == "error" and s.error == INVALID_ACTION]
        unknown = sum(1 for s in own.spans if s.kind == "tool" and s.error == "unknown_tool")
        stopped = res.stopped if res.stopped in ("done", "step_limit") else "error"
        return ArmResult(res.reply, stopped, res.steps, "" if stopped != "error" else res.reply[:160], invalid, unknown)
