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

### Gemini 3.8 Flash through Antigravity (`agy`)

If your Google AI plan includes Antigravity, `cli_agy` drives its official CLI in print mode. Profiles: `gemini-agy`
(everything on Gemini), `dev` (local model first, Gemini for the hard tiers) and `competition` (Gemini only). The reasoning
effort follows the tier: `gemini-3.8-flash-low`, `-medium`, `-high`.

How it behaves, measured on `agy` 1.5.13:

- The prompt travels as one JSON line on stdin, so there is no command-line length limit; the schema is applied to the
  final answer (`--json-schema`), which is what the contract and property stages need.
- `agy` is a full tool-using agent. Each call therefore runs in an empty temporary folder with a "no tools" instruction
  (verified: a request to create a file is refused and nothing is written) and is cleaned up afterwards.
- Usage is real, not estimated: every call reports its token counts, and carries roughly 10k tokens of agent overhead,
  so a trivial call takes about 13 s. Parallelism is capped (`max_concurrency`, default 2) so episodes cannot flood it.
- Rate-limit and quota errors are classified as transient, so the router falls back to the next target in the chain.

Same compliance note as above: it launches the vendor's CLI, never touches its credentials, and is off until you opt in.

## Sharing the GPU with other projects

SiliconForge starts its own `llama-server` (port 8189) and never touches anyone else's. Before loading a model it checks
the VRAM that is actually free and refuses, with options, when the model will not fit (`siliconforge models start` exits
with code 3). It will not stop or evict another program. `siliconforge models detect` lists local models already on the
machine (including your Hugging Face cache, used in place rather than copied) and other model servers it found, labelled
as belonging to another program. If another tool already serves the model you want, point `providers.llamacpp.base_url`
at it instead of loading a second copy.

## Model policy and the AI-usage ledger

A workspace can restrict which models may be called, for example only Gemini 3.8 Flash for a contest entry. The router
enforces this by dropping every other target from the profile's chain; an empty chain is an error, never a silent
fallback. Every call is recorded (metadata and a prompt hash, never prompt text) in `.siliconforge/ai-usage.jsonl`, and
`siliconforge report ai-usage` turns that into a disclosure document. See [workspaces](workspaces.md).

## Cost control

- **Pre-run estimate** on the New run screen (`GET /api/estimate`), from typical per-stage token volumes.
- **Budgets**: a USD ceiling, a total-token ceiling, and per-stage token ceilings (`inference.stage_token_budgets`). A run
  that hits one stops with a clear message instead of spending more.
- **Cache-aware pricing**: provider cache reads and writes are priced from the catalog, and savings are reported.
- **Response cache**: an exact-match SQLite cache for low-temperature calls, persisted across runs.
- **EDA result cache**: identical RTL + knobs + PDK reuse a previous place-and-route result instead of re-running it.

Prices in `models.yaml` carry a `pricing_as_of` date; check them against your plan.
