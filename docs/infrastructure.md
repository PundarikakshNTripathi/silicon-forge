# Infrastructure: what runs, what does not, and why

SiliconForge is a local-first tool for one engineer or a small team: one machine, one GPU if there is one, a folder of RTL. The
infrastructure follows from that. This page says what is in use, what was considered and left out, and what would change the answer.
Nothing here is a claim about what a hosted, multi-tenant service would need; that is a different product.

## Advice we were given, and what we did with it

A common checklist for "frontier-grade" agent systems says: evaluate continuously in sandboxed simulations with deterministic and
safety checks; serve with custom inference engines such as TensorRT or Triton; use a distributed vector database with hybrid search
(and "just use Postgres and pgvector"); anchor it with MLOps telemetry and drift detection. Taking each in turn:

| Advice | Verdict | Why, and what is built |
| :--- | :--- | :--- |
| Continuous evaluation in sandboxed simulation, deterministic and safety checks | **Yes, and built.** | `siliconforge agentbench` runs sandboxed tasks with hidden deterministic verifiers and safety probes; a safety violation fails a task whatever the answer. `evals` does the same for RTL. [docs/agent-benchmark.md](agent-benchmark.md), [docs/evals.md](evals.md). |
| TensorRT-LLM or Triton for serving | **No.** | Datacenter-NVIDIA stacks aimed at fleets. Users here run GGUF models on 8 to 24 GB cards, and llama.cpp already provides flash attention, quantized KV cache, prompt-prefix caching and speculative decoding, which are measured in [docs/providers.md](providers.md). A team with big GPUs can run vLLM or SGLang and point the `vllm` profile at it (below); nothing is vendored. |
| A distributed vector database with hybrid search | **Hybrid maybe, distributed no.** | The knowledge packs are a few hundred chunks. BM25 is strong for identifiers (Verilator warning codes, SDC commands), brute-force vector search over a few hundred vectors is exact and instant, and a distributed index would add an operational burden for no gain. Embeddings are adopted only if a labelled retrieval eval shows hybrid beating BM25 with a confidence interval; that eval has not been run, so retrieval stays BM25. |
| "Just use Postgres and pgvector" | **Not for the core.** | SQLite in WAL mode holds runs, events, cost and spans for a single-user tool with nothing to install, back up or secure. Postgres earns its place the day there is a shared service with several writers; the store is behind one small module (`api/store.py`), so that migration does not touch the pipeline. pgvector would then be an option for retrieval, not a reason to run a server today. |
| MLOps telemetry and drift detection | **Yes, scaled to the size.** | Metadata-only spans for every agent step, exportable over OTLP and as Prometheus text ([docs/telemetry.md](telemetry.md)); `agentbench compare` and `evals compare --gate` detect a proven drop between two runs; a manually started canary workflow runs a seeded subset against a provider and gates on it (below). No feature store and no training-pipeline orchestration, because there is no training pipeline to orchestrate. |
| Kubernetes, Kafka, a feature store | **No.** | None of them has a job here. They would make installation and reliability worse. |

## Inference backends

| Backend | When | Notes |
| :--- | :--- | :--- |
| llama.cpp (default for `local`) | One user, one GPU | Started and stopped by SiliconForge only when the GPU is free; never evicts another program's job. Constrained JSON decoding, prefix cache, optional speculative decoding. |
| Any OpenAI-compatible server (`vllm` profile) | A team's GPU box, or a model llama.cpp does not run well | vLLM, SGLang, TGI, LM Studio. Set `SILICONFORGE_VLLM_URL`, and `VLLM_API_KEY` if the server wants one. |
| Cloud APIs (Claude, OpenAI, Gemini) | Hard tiers, or no GPU | Keys in the operating system keychain; prices in `configs/models.yaml`; prompt caching on stable prefixes. |
| Subscription CLIs (`agy`, `claude`, `codex`, `gemini`) | You already pay for a plan | Opt-in, never reads or stores OAuth tokens. |

Using a server you run elsewhere:

```bash
# on the GPU machine (vLLM shown; any OpenAI-compatible server works)
vllm serve Qwen/Qwen3.5-9B --served-model-name local --enable-prefix-caching --port 8000
# on yours
export SILICONFORGE_VLLM_URL=http://gpu-box:8000/v1
siliconforge chat --profile vllm
```

The profile sends the served model name `local`, asks for JSON-schema constrained output (`response_format`), caps completions at
6,000 tokens, and reads cached-token counts from the usage block, so cost and cache accounting work as for any other provider.
**Tested against a stub OpenAI-compatible server** (`tests/test_infra_vllm.py`: the request body, streamed and plain replies,
usage and cached tokens, a 429 with `Retry-After`, the URL override), **not against a real vLLM or SGLang process**: no such
server has been run here. Treat the first run against one as the real test and report what differs.

## Data

- **SQLite (WAL)** for runs, events, cost and agent spans. One file, no service. Back it up by copying it.
- **Files** for everything an engineer might want to read or diff: RTL, reports (`report.json`, `report.html`), `flow.lock`, saved
  agent conversations (redacted), project memory (`.siliconforge/memory.md`).
- **Durable runs.** A run that is interrupted (the machine slept, the process was killed) is marked `interrupted`; it does not resume
  yet, and the stage events it already wrote stay in the database. Resuming from the last completed stage is planned.

## Observability and drift

- Every agent step is a span with times, token counts and an error class, never prompts, arguments or output. They show on the
  Insights page, in `siliconforge trace`, over OTLP (the `otel` extra: Phoenix, Langfuse, any collector) and at `/api/metrics`
  as Prometheus text. [docs/telemetry.md](telemetry.md).
- `siliconforge evals compare BASELINE CANDIDATE --gate --max-drop 0.05` exits 1 only when the candidate is worse by at least that
  much **and** the paired 95% interval excludes zero, so a small noisy subset cannot raise an alarm by itself.
  `siliconforge agentbench compare` does the same for agent loops.
- The **Canary** workflow (`.github/workflows/canary.yml`) is started by hand: it runs a seeded subset of VerilogEval against the
  profile you name, using your API key as a repository secret, and compares it with a result file you commit. It spends your tokens,
  so it has no schedule unless you add one, and tests in this repository never call a live model.

## Containers

`docker/Dockerfile.orchestrator` builds the API and the web UI in two stages (the UI is built with pnpm from the lockfile, the runtime
is `python:3.12-slim-bookworm`), runs as a fixed unprivileged user (uid 10001), writes only to `/data`, and has a health check
against `/api/health`. The compose file publishes the port on the loopback interface only, drops all capabilities, sets
`no-new-privileges`, and passes `SILICONFORGE_API_TOKEN` through. The **Docker** workflow builds the image on every change to these
files, checks that it answers its health check and is not root, and writes an SBOM (SPDX JSON) as a build artifact.

Not done: base images are pinned by tag, not by digest (a digest changes with every upstream rebuild, so pinning is a release-time
step); the image is not signed; the EDA worker image (`docker/Dockerfile.eda`) is separate and downloads the OSS CAD Suite release
named in its build arguments.

## What would change these answers

- Several people writing to one deployment at once: Postgres for the store, and a real authentication layer instead of one shared
  token.
- A knowledge base in the tens of thousands of chunks, or a retrieval eval where embeddings win: hybrid search (pgvector at that point, or
  an in-process index until then).
- A model that needs more than one GPU or batched serving for throughput: the `vllm` profile, then measure.
- A cloud model that changes under you: schedule the canary.
