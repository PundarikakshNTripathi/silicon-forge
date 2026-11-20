---
name: add-provider
description: Add an LLM provider to the inference layer (OpenAI-compatible, Anthropic, CLI bridge, replay) with normalized usage and cache accounting.
---
1. Implement `LLMProvider` in `services/orchestrator/inference/providers/`: streaming, timeout, retry with jitter, normalized usage (prompt, completion, cached tokens).
2. Keep the stable prompt prefix first so provider prompt caching hits; do not reorder messages.
3. Add pricing to `configs/models.yaml`; never hardcode keys: read from the OS keychain via `inference/secrets.py`.
4. Test with recorded fixtures or a local fake server: never live APIs in CI.
5. CLI-bridge providers stay opt-in and must not touch OAuth tokens.
