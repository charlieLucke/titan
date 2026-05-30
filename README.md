# titan

A local, high-performance RAG system that runs entirely on one workstation — no
cloud, no API keys, single user. It indexes PDFs and Markdown notes and answers
natural-language queries over them, and is the retrieval backend behind
`brain-mcp`, `brain-dashboard` and `obsidian-inbox-watcher`.

## How it works

```
PDF / Markdown ─▶ parse (Docling / frontmatter) ─▶ Late Chunking
              ─▶ BGE-M3 embed (dense + sparse + colbert) ─▶ Qdrant
Query ─▶ decompose (Phi-4) ─▶ BGE-M3 ─▶ Qdrant search ─▶ RRF fusion
      ─▶ (semantic cache) ─▶ Phi-4 answer generation
```

- **Late Chunking** embeds the full document context before splitting, preserving
  meaning across chunk boundaries.
- **BGE-M3** produces three vectors per chunk (dense, sparse, colbert); **Reciprocal
  Rank Fusion** consolidates the three rankings.
- **Phi-4 via Ollama** decomposes queries and generates answers — fully local.
- Re-indexing is **upsert-before-delete** keyed on a per-run `run_id`, so a note's
  old chunks are replaced atomically rather than duplicated.

## Stack

- **Language:** Python 3.12+ · **Package manager:** uv
- **Embeddings:** BGE-M3 via FlagEmbedding (dense + sparse + colbert)
- **LLM:** Phi-4 via Ollama (local, no API key)
- **Vector DB:** Qdrant (local, gRPC port 6334)
- **PDF parsing:** Docling
- **API:** FastAPI (service on `127.0.0.1:8765`)
- **GPU:** NVIDIA (~16 GB VRAM), serialized with an fcntl lock (`/tmp/bge_m3.lock`)

## Prerequisites

- An NVIDIA GPU with a CUDA-enabled `torch` build (the default `uv` wheel is
  CPU-only — verify with `uv run python -c "import torch; print(torch.cuda.is_available())"`).
- A running **Qdrant** instance (local, gRPC `6334`) and **Ollama** serving Phi-4.
- WSL2 users: enable systemd in `/etc/wsl.conf` (`[boot]\nsystemd=true`).

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
make install                       # deps + pre-commit hooks
uv run python -m titan.tools.init_col   # create the Qdrant collection
```

Copy `.env.example` to `.env` and adjust `QDRANT_*`, `COLLECTION_NAME`,
`OLLAMA_URL`/`OLLAMA_MODEL`, `INGEST_BASE_DIR`, `VAULT_ROOT`, and the cache
settings as needed.

## Running the service

```bash
uv run python -m titan service            # start the API on port 8765
uv run python -m titan service --port 9000  # alternative port
curl localhost:8765/health                # health check
```

In deployment it runs as a systemd user service (`titan-service`); see `deploy/`.

### HTTP API

| Method & path | Purpose |
|---|---|
| `GET /health` | Service status: BGE-M3 loaded, Qdrant reachable, VRAM usage. |
| `POST /search` | Hybrid search: query decompose → BGE-M3 → RRF → semantic cache. |
| `POST /ingest/file` | Index a Markdown file (upsert-before-delete via `run_id`). |
| `GET /domains` | All domains in the index with chunk counts. |
| `GET /notes` | All indexed notes grouped by source path, with domain + chunk count. |
| `POST /find_related` | Notes semantically similar to a given note. |
| `DELETE /chunks` | Remove all chunks of a file (by `source_path`). |

## Development

```bash
make test       # run tests with coverage
make test-fast  # skip slow + integration tests
make check      # full quality gate: lint + types + tests
make format     # auto-fix style issues
make help       # list all available commands

uv run pytest tests/integration/ -m integration -v   # integration tests (need a live service)
```

## Project Structure

```
src/titan/
├── main.py            # Dispatcher: python -m titan service | help
├── ingest.py          # Parse → Late Chunking → BGE-M3 embed → Qdrant upsert
├── search.py          # Query decompose, BGE-M3, RRF, semantic cache
├── generate.py        # Phi-4 answer generation from chunk context
├── evaluate.py        # LLM-as-judge evaluation
├── service/           # FastAPI app, state singleton, schemas, routes
├── eval/              # A/B evaluation tool + fixtures
└── tools/init_col.py  # Qdrant collection setup
deploy/                # systemd service unit + install guide
tests/{titan,integration}/   # unit + service-integration tests
docs/ai/               # AI agent context and plans
.github/workflows/     # CI configuration
```

## Tooling

| Tool         | Purpose                              |
|--------------|--------------------------------------|
| **uv**       | Package manager + Python installer   |
| **ruff**     | Linter + formatter                   |
| **mypy**     | Static type checker (strict mode)    |
| **pytest**   | Test runner with coverage            |
| **pre-commit** | Git hook runner                    |

All tools run in CI on every push.

## Working with AI Tools

This project uses a structured workflow for AI-assisted coding. Any AI agent (Claude, Gemini, Cursor, Aider, etc.) should read `CLAUDE.md` first — it's mirrored as `AGENTS.md` and `GEMINI.md` for tool compatibility.

Key files for AI context:

- `docs/ai/CONTEXT.md` — stack, conventions, glossary
- `docs/ai/CURRENT_TASK.md` — what's actively being worked on
- `docs/ai/HANDOFF.md` — state for resuming sessions across model switches
- `docs/ai/DECISIONS.md` — log of architectural decisions
- `docs/ai/plans/` — saved plans authored by a planning model (e.g. Opus)

The intended workflow:

1. Architecture and feature plans are authored by a strong reasoning model and saved to `docs/ai/plans/`
2. A faster/cheaper model implements the plans
3. Both reference the shared context in `docs/ai/`
4. State is preserved across sessions via `HANDOFF.md`

## License

TBD
