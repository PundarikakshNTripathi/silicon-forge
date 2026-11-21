# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `langgraph` arm: the agent built the way the LangChain/LangGraph documentation builds one.

`langchain.agents.create_agent` (the prebuilt agent that runs on a LangGraph state graph), a `ChatOpenAI` client pointed at an
OpenAI-compatible endpoint, the shared tools wrapped as `StructuredTool`s with a *dict* schema (so the model sees the same JSON
schema and the arguments reach the shared tool seam unvalidated), native tool calling, and LangGraph's `recursion_limit` as the
step budget. Nothing else is added: no loop guard, no context budget, no redaction. Needs `pip install "siliconforge[bench]"`.
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


def final_text(messages: list[Any]) -> str:
    """The last assistant message's text; LangChain content can be a string or a list of content blocks."""
    for msg in reversed(messages):
        if getattr(msg, "type", "") == "ai":
            content = msg.content
            if isinstance(content, list):
                content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
            if content:
                return str(content)
    return ""


class LangGraphArm:
    name = "langgraph"

    def __init__(self, guarded: bool = False) -> None:
        self.guarded = guarded

    def run(self, ctx: ArmContext) -> ArmResult:
        from langchain.agents import create_agent
        from langchain_core.tools import StructuredTool
        from langchain_openai import ChatOpenAI
        from langgraph.errors import GraphRecursionError

        llm = ChatOpenAI(model=ctx.model, base_url=ctx.base_url, api_key=ctx.api_key, temperature=ctx.temperature,
                         max_tokens=ctx.max_tokens, extra_body=EXTRA_BODY, streaming=False, max_retries=0)
        tools = [StructuredTool(name=s.name, description=s.description, args_schema=tool_json_schema(s),
                                func=_tool_fn(ctx.tools, s.name)) for s in ctx.tools.specs()]
        agent = create_agent(model=llm, tools=tools, system_prompt=SHARED_ROLE)
        try:
            out = agent.invoke({"messages": [{"role": "user", "content": ctx.task.prompt}]},
                               config={"recursion_limit": 2 * ctx.max_steps + 1})
        except GraphRecursionError:
            return ArmResult("", "step_limit", ctx.max_steps)
        msgs = out["messages"]
        return ArmResult(final_text(msgs), "done", sum(1 for m in msgs if getattr(m, "type", "") == "ai"))
