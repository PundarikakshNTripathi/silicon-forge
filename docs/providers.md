# Models, providers and cost

SiliconForge routes every model call through a **profile**: a map from three task tiers to an ordered fallback chain
of `(provider, model)` pairs. Profiles, providers and prices live in [`configs/models.yaml`](../configs/models.yaml).

| Tier | Used for |
| :--- | :--- |
| `local_fast` | log triage, lint fixes |
| `balanced` | RTL generation, ECO advice |
| `frontier_reasoning` | architecture contract, formal property synthesis, counterexample repair |

| Profile | What it does |
| :--- | :--- |
| `replay` | Bundled example designs, offline and free. Never invents a design for an unknown spec. |
| `local` | Your GPU through llama.cpp. |
| `hybrid` | Local for routine tiers, Claude for hard reasoning; each falls back to the other. |
| `cloud` | Claude Haiku / Sonnet / Opus by tier. |
| `openai`, `gemini` | Your OpenAI / Gemini key (Gemini through its OpenAI-compatible endpoint). |
| `subscription` | Opt-in bridge to your signed-in `claude` / `codex` / `gemini` CLI. |

Select a profile with `--profile` on the CLI or on the **New run** screen.

## Local models (llama.cpp)

```bash
siliconforge models install
siliconforge models pull --repo unsloth/Qwen3.5-9B-GGUF --file Qwen3.5-9B-Q4_K_M.gguf
siliconforge models start --file Qwen3.5-9B-Q4_K_M.gguf
siliconforge models status
```

The **Models** page (or `POST /api/models/install`, `/pull`, `/server/start`) installs the official llama.cpp build for
your machine (CUDA 13 on NVIDIA), downloads a GGUF from Hugging Face with checksum verification, and starts
`llama-server` on `127.0.0.1:8189` with:

- `-ngl 99` (all layers on the GPU), flash attention, quantized KV cache (`q8_0` halves the cache's VRAM),
- `--cache-reuse` plus `cache_prompt` so a shared prompt prefix is not recomputed,
- JSON-schema constrained decoding for the contract and property outputs.

Measured on this project: with the prefix cache, a repeated 394-token prompt reused 390 tokens and its first token
arrived **8.6× sooner** (CPU run; see `benchmarks/results/llamacpp-prefix-cache-cpu-*.json`).

Prompts put stable content (system prompt, PDK skill packs, contract) first and volatile content (diagnostics, RTL
slices) last so prefix caches hit.

## Cloud providers

API keys are read from the environment or your **operating system keychain** (set them on the Models page). They are
never written to config files or the database.

Claude is called through the official `anthropic` SDK with prompt caching on the stable prefix. The Claude 5.x models
reject sampling parameters, so SiliconForge never sends `temperature`; depth is controlled with `output_config.effort`
per model (see `providers.anthropic.efforts`). Refusals are surfaced and fall through to the next target in the chain.

## Subscription CLI bridge (opt-in)

The `subscription` profile launches the **official CLI** you are already signed into (`claude -p`, `codex exec`,
`gemini -p`), sending the prompt on stdin. It is off by default and requires `SILICONFORGE_ENABLE_CLI_BRIDGE=1` or the
toggle on the Models page.

SiliconForge never reads, copies or stores your sign-in tokens. Anthropic and Google restrict third-party use of
subscription OAuth, and whether driving their CLI from another program is allowed is a grey area that can change.
Check your plan's current terms before enabling this. Usage is billed to your subscription, so cost is recorded as
$0 and token counts are estimates.

## Cost control

- **Pre-run estimate** on the New run screen (`GET /api/estimate`), from typical per-stage token volumes.
- **Budgets**: a USD ceiling, a total-token ceiling, and per-stage token ceilings (`inference.stage_token_budgets`). A run
  that hits one stops with a clear message instead of spending more.
- **Cache-aware pricing**: provider cache reads and writes are priced from the catalog, and savings are reported.
- **Response cache**: an exact-match SQLite cache for low-temperature calls, persisted across runs.
- **EDA result cache**: identical RTL + knobs + PDK reuse a previous place-and-route result instead of re-running it.

Prices in `models.yaml` carry a `pricing_as_of` date; check them against your plan.
