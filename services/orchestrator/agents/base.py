# SPDX-License-Identifier: AGPL-3.0-or-later
"""Base hardware engineering agent: grounded skill injection, JSON extraction, schema-validated calls."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.skills.registry import SkillRegistry

T = TypeVar("T")


class StructuredOutputError(RuntimeError):
    """The model kept returning output that failed schema validation after all retries."""


class BaseHardwareAgent:
    prompt_file: str = ""
    #: how many times to re-ask after invalid JSON / schema violations
    max_schema_retries: int = 2

    def __init__(self, router: ModelRouter, skills: SkillRegistry, prompts_dir: Path) -> None:
        self.router = router
        self.skills = skills
        self.prompts_dir = prompts_dir
        self.system_prompt = (prompts_dir / self.prompt_file).read_text(encoding="utf-8")

    @staticmethod
    def extract_json(raw: str) -> Any:
        # Strip optional markdown code fences
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        return json.loads(cleaned)

    @staticmethod
    def extract_code(raw: str) -> str:
        m = re.search(r"```(?:systemverilog|verilog|sv)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else raw.strip()

    def complete_structured(
        self,
        *,
        stage: str,
        task_type: str,
        user_prompt: str,
        parse: Callable[[Any], T],
        json_schema: dict | None = None,
        temperature: float = 0.05,
    ) -> T:
        """Call the model and validate its JSON with `parse`; on failure re-ask with the exact error.

        Retries bypass the cache and escalate the model tier (`retry_attempt`), so a small local model that
        produces malformed JSON gets a stronger model on the next attempt.
        """
        prompt = user_prompt
        last_err = ""
        for attempt in range(self.max_schema_retries + 1):
            resp = self.router.complete(
                stage=stage,
                task_type=task_type,
                system_prompt=self.system_prompt,
                user_prompt=prompt,
                temperature=temperature,
                retry_attempt=attempt,
                bypass_cache=attempt > 0,
                json_schema=json_schema,
            )
            try:
                return parse(self.extract_json(resp.content))
            except (json.JSONDecodeError, ValidationError, ValueError, KeyError, TypeError) as exc:
                last_err = str(exc)[:1500]
                prompt = (
                    f"{user_prompt}\n\nYour previous reply was rejected:\n{last_err}\n"
                    "Return ONLY corrected JSON that satisfies the schema."
                )
        raise StructuredOutputError(
            f"{stage}: model output failed schema validation after "
            f"{self.max_schema_retries + 1} attempts: {last_err}"
        )
