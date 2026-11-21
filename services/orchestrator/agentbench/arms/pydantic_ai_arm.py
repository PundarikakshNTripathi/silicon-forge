# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `pydantic_ai` arm: the agent built the way the Pydantic AI documentation builds one.

A plain `Agent` with the model behind an OpenAI-compatible provider, the shared tools registered through `Tool.from_schema` (so
each tool shows the model the same JSON schema and receives its arguments unvalidated, at the shared tool seam), native tool
calling, and the framework's own request limit as the step budget. Nothing else is added: no loop guard, no context budget, no
redaction. That is the point of the comparison. Needs `pip install "siliconforge[bench]"`.
"""
from __future__ import annotations

from typing import Any

from services.orchestrator.agentbench.arms.base import SHARED_ROLE, ArmContext, ArmResult
from services.orchestrator.agentbench.toolkit import InstrumentedToolBox, tool_json_schema

EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}


def _tool_fn(toolbox: InstrumentedToolBox, name: str) -> Any:
    def call(**kwargs: Any) -> str:
        return toolbox.call_text(name, kwargs)

    return call


class PydanticAiArm:
    name = "pydantic_ai"

    def __init__(self, guarded: bool = False) -> None:
        self.guarded = guarded  # an ablation: give the framework our redaction and fencing and see what changes

    def run(self, ctx: ArmContext) -> ArmResult:
        from pydantic_ai import Agent, Tool
        from pydantic_ai.exceptions import UsageLimitExceeded
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider
        from pydantic_ai.usage import UsageLimits

        model = OpenAIChatModel(ctx.model, provider=OpenAICompatibleProvider("bench", base_url=ctx.base_url, api_key=ctx.api_key))
        tools = [Tool.from_schema(function=_tool_fn(ctx.tools, s.name), name=s.name, description=s.description,
                                  json_schema=tool_json_schema(s)) for s in ctx.tools.specs()]
        agent = Agent(model, tools=tools, system_prompt=SHARED_ROLE,
                      model_settings={"temperature": ctx.temperature, "max_tokens": ctx.max_tokens, "extra_body": EXTRA_BODY})
        try:
            result = agent.run_sync(ctx.task.prompt, usage_limits=UsageLimits(request_limit=ctx.max_steps))
        except UsageLimitExceeded:
            return ArmResult("", "step_limit", ctx.max_steps)
        usage = result.usage  # a property in current releases (a method in older ones, where calling it is the way)
        if not hasattr(usage, "requests"):
            usage = usage()
        return ArmResult(str(result.output), "done", usage.requests)
