"""Builds provider instances from catalog definitions (lazily, so missing keys only matter when used)."""
from __future__ import annotations

from typing import Any

from services.orchestrator.inference.catalog import Catalog
from services.orchestrator.inference.providers.base import BaseLLMProvider, ProviderError
from services.orchestrator.inference.secrets import get_secret


def build_provider(name: str, catalog: Catalog, cli_bridge_enabled: bool = False) -> BaseLLMProvider:
    cfg: dict[str, Any] = catalog.providers.get(name)  # type: ignore[assignment]
    if cfg is None:
        raise ProviderError(f"Provider '{name}' is not defined in the model catalog.")
    kind = cfg.get("kind")
    if kind == "replay":
        from services.orchestrator.inference.providers.replay import ReplayProvider

        return ReplayProvider()
    if kind == "openai_compat":
        from services.orchestrator.inference.providers.openai_compat import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            base_url=cfg.get("base_url"),
            api_key=get_secret(cfg.get("api_key_env")) or "",
            name=name,
            local=bool(cfg.get("local", False)),
            supports_json_schema=bool(cfg.get("supports_json_schema", False)),
            cache_prompt=bool(cfg.get("cache_prompt", False)),
            send_temperature=bool(cfg.get("send_temperature", True)),
            max_tokens=int(cfg.get("max_tokens", 8192)),
            max_tokens_param=str(cfg.get("max_tokens_param", "max_tokens")),
            timeout_s=float(cfg.get("timeout_s", 300)),
            stream=bool(cfg.get("stream", True)),
            extra_body=dict(cfg.get("extra_body", {})),
        )
    if kind == "anthropic":
        from services.orchestrator.inference.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=get_secret(cfg.get("api_key_env")),
            efforts=dict(cfg.get("efforts", {})),
            default_effort=str(cfg.get("default_effort", "medium")),
            fallbacks=bool(cfg.get("fallbacks", True)),
        )
    if kind == "cli_bridge" and cfg.get("cli") == "agy":
        from services.orchestrator.inference.providers.agy import AgyProvider

        return AgyProvider(
            enabled=cli_bridge_enabled, default_effort=str(cfg.get("default_effort", "medium")),
            max_concurrency=int(cfg.get("max_concurrency", 2)), timeout_s=float(cfg.get("timeout_s", 600)),
            sandbox=bool(cfg.get("sandbox", True)),
        )
    if kind == "cli_bridge":
        from services.orchestrator.inference.providers.cli_bridge import CliBridgeProvider

        return CliBridgeProvider(
            cli=str(cfg.get("cli", "claude")), command=cfg.get("command"), enabled=cli_bridge_enabled
        )
    raise ProviderError(f"Provider '{name}' has unknown kind '{kind}'.")
