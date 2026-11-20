"""Base hardware engineering agent with grounded skill injection and JSON extraction."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.skills.registry import SkillRegistry


class BaseHardwareAgent:
    prompt_file: str = ""

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
