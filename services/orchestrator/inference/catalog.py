"""Model catalog: prices, provider definitions and routing profiles loaded from configs/models.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TIERS = ("local_fast", "balanced", "frontier_reasoning")


@dataclass(frozen=True)
class ModelPrice:
    input: float = 0.0
    output: float = 0.0
    cache_read: float | None = None
    cache_write: float | None = None

    def cost_usd(
        self, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0, cache_write_tokens: int = 0
    ) -> float:
        """Cost with provider cache pricing: fresh input + cache reads + cache writes + output."""
        fresh = max(0, prompt_tokens - cached_tokens - cache_write_tokens)
        read_price = self.cache_read if self.cache_read is not None else self.input
        write_price = self.cache_write if self.cache_write is not None else self.input
        return (
            fresh * self.input
            + cached_tokens * read_price
            + cache_write_tokens * write_price
            + completion_tokens * self.output
        ) / 1_000_000.0


@dataclass(frozen=True)
class Target:
    provider: str
    model: str


@dataclass
class Catalog:
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    models: dict[str, ModelPrice] = field(default_factory=dict)
    profiles: dict[str, dict[str, list[Target]]] = field(default_factory=dict)
    pricing_as_of: str = ""

    def price(self, model: str) -> ModelPrice:
        return self.models.get(model, ModelPrice())

    def chain(self, profile: str, tier: str) -> list[Target]:
        try:
            return self.profiles[profile][tier]
        except KeyError as exc:
            raise KeyError(f"Unknown profile/tier '{profile}/{tier}'. Profiles: {sorted(self.profiles)}") from exc

    def provider_is_local(self, name: str) -> bool:
        cfg = self.providers.get(name, {})
        return bool(cfg.get("local")) or cfg.get("kind") == "replay"


def load_catalog(path: str | Path | None = None, repo_root: Path | None = None) -> Catalog:
    root = repo_root or Path(__file__).resolve().parents[3]
    file = Path(path) if path else root / "configs" / "models.yaml"
    if not file.is_absolute():
        file = root / file
    raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    models = {
        name: ModelPrice(
            input=float(v.get("input", 0)),
            output=float(v.get("output", 0)),
            cache_read=float(v["cache_read"]) if "cache_read" in v else None,
            cache_write=float(v["cache_write"]) if "cache_write" in v else None,
        )
        for name, v in (raw.get("models") or {}).items()
    }
    profiles = {
        pname: {tier: [Target(t["provider"], t["model"]) for t in chain] for tier, chain in tiers.items()}
        for pname, tiers in (raw.get("profiles") or {}).items()
    }
    return Catalog(
        providers=dict(raw.get("providers") or {}),
        models=models,
        profiles=profiles,
        pricing_as_of=str(raw.get("pricing_as_of", "")),
    )
