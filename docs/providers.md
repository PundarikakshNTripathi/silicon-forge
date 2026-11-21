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

### Speculative decoding

llama.cpp can draft several tokens cheaply and have the main model verify them in one step. Two forms are exposed:

```bash
# model-free: draft from n-grams already in the context. Suits edits, where the output repeats the file.
siliconforge models start --path models/gguf/Qwen3.5-9B-Q4_K_M.gguf --spec-type ngram-mod
# draft model: a smaller model of the same family and tokenizer
siliconforge models start --path big.gguf --draft-model small.gguf --draft-max 8
```

It is off by default. The flags are the ones the bundled llama.cpp build (b12600) lists (`--spec-type`, `--spec-draft-model`,
`--spec-draft-n-max`); a combination that cannot work (a draft type without a draft model, a draft model with speculation
off, more than one slot) is refused with a message rather than passed through. A draft model's weights count against the free
VRAM check, and the guard still refuses to start when another program holds the GPU.

**Measured** on Qwen3.5-9B Q4_K_M, RTX 5060 Laptop (8 GB), 16k context, one request at a time, temperature 0, thinking off, with four
bundled designs: a fresh-generation prompt (write the module from its spec) and an edit prompt (rewrite the file adding comments)
for each, every prompt sent once per configuration (`benchmarks/results/speed-Qwen3.5-9B-Q4_K_M-*.json`):

| Configuration | Fresh generation | Editing a file | Greedy output same as baseline (generate / edit) | VRAM |
| :--- | ---: | ---: | :---: | ---: |
| Baseline (q8_0 KV) | 56.9 tok/s | 56.9 tok/s | n/a | 5,766 MiB |
| Baseline run again at the end (control) | 57.0 | 56.9 | 4/4, 4/4 | 5,790 MiB |
| `ngram-mod` | 56.4 (0.99x), 1.6% of drafted tokens accepted | **167.1 (2.94x)**, 76% accepted | 4/4, **1/4** | 5,766 MiB |
| `ngram-simple` | 56.5 (0.99x), 7% accepted | **160.3 (2.82x)**, 60% accepted | 2/4, 3/4 | 5,766 MiB |
| KV cache q4_0 | 55.9 (0.98x) | 56.9 (1.00x) | 0/4, 0/4 | 5,638 MiB |

What this shows, and what it does not:

- **N-gram speculation is a large win for editing and neutral for fresh generation** on this model: about 2.9x faster when the
  answer largely repeats the prompt, no measurable cost when it does not. It needs no second model.
- **It is not output-identical.** In theory verification makes speculative decoding lossless; in practice the batched
  verification changes floating-point results, and greedy output differed from the plain run on 3 of 4 edit prompts with `ngram-mod`.
  The control matters: the same server configuration run twice produced identical text 8 times out of 8, so the differences come from speculation. The
  differences seen were in the wording of comments and in where the model branched. **Whether they change RTL correctness has not
  been measured**, so speculation stays off by default; it is the thing to try for edit-heavy agent sessions, with the tool checks
  (lint, equivalence, simulation) as the safety net they already are.
- **KV q4_0** saves 128 MiB at this context length, is no faster, and changed every output. There is no reason to use it at 16k.
- **Not measured:** an f16 KV cache and a 32k context did not fit beside this model on the 8 GB card (the VRAM guard
  estimated 7.9 GB and 8.1 GB needed against 7.8 GB free) and were not tried; no draft-model arm, because no smaller model of
  the same family is downloaded; one model, one GPU. An earlier version of this benchmark sent each prompt three times and
  reported 375 tok/s on fresh generation with 100% acceptance: that was the n-gram pool replaying the previous answer, not a
  speed-up, and it was discarded.

`python benchmarks/bench_speed.py --model <gguf> --arms baseline,ngram-mod,kv-q4_0,baseline@end` reproduces it (the `@end`
suffix re-runs a configuration as a control) and needs a free GPU.

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

## Retries and rate limits

Every provider already retries a transient failure (HTTP 429, 5xx, a timeout) three times with exponential backoff and jitter.
Two refinements:

- **`Retry-After` is honoured.** When a 429 or 503 carries the header (seconds or an HTTP date), the retry waits that long
  instead of a random few seconds, so the three attempts are not burned inside the server's cool-down. A request to wait longer
  than 60 seconds is not waited out: the error is raised and the router moves to the next target in the chain.
- **Pacing before the request.** A provider entry in `configs/models.yaml` may declare a limit, and the router spaces calls to
  stay under it instead of discovering it as a 429:

  ```yaml
  providers:
    openai:
      kind: openai_compat
      rate_limit: {rpm: 60, tpm: 200000, max_wait_s: 30}
  ```

  It is a sliding 60-second window over requests and estimated prompt tokens (about four characters per token, so leave
  headroom in `tpm`). If pacing a call would take longer than `max_wait_s`, the router falls through to the next target without
  counting it as a provider failure, and the time spent pacing is reported as `rate_wait_s` in the router's stats. A request
  larger than the whole per-minute token allowance is refused immediately, and a malformed `rate_limit` is an error. No provider
  has a limit configured by default: the right numbers depend on your plan.

## Cost control

- **Pre-run estimate** on the New run screen (`GET /api/estimate`), from typical per-stage token volumes.
- **Budgets**: a USD ceiling, a total-token ceiling, and per-stage token ceilings (`inference.stage_token_budgets`). A run
  that hits one stops with a clear message instead of spending more.
- **Cache-aware pricing**: provider cache reads and writes are priced from the catalog, and savings are reported.
- **Response cache**: an exact-match SQLite cache for low-temperature calls, persisted across runs.
- **EDA result cache**: identical RTL + knobs + PDK reuse a previous place-and-route result instead of re-running it.

Prices in `models.yaml` carry a `pricing_as_of` date; check them against your plan.
