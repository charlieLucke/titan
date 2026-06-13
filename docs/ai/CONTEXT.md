# Project Context

> Read this first. Keep under 200 lines. Update as the project evolves.

## What this project does

Titan is a local, high-performance RAG (Retrieval-Augmented Generation) system on a
single workstation. It indexes PDFs and Markdown files, splits them into chunks via
Late Chunking, embeds them with BGE-M3 (multi-vector: dense + sparse + colbert),
stores them in Qdrant, and answers queries with Reciprocal Rank Fusion + Phi-4 as
the generator. Designed for single-workstation deployment (no multi-user, no cloud
deployment).

## Stack
- **Language:** Python 3.12+
- **Package manager:** uv
- **Test runner:** pytest
- **Lint/format:** ruff (line length 100, double quotes)
- **Type checker:** mypy (strict)
- **CI:** GitHub Actions
- **Pre-commit:** ruff, mypy, hygiene checks
- **Embeddings:** BGE-M3 via FlagEmbedding (dense + sparse + colbert)
- **LLM:** Phi-4 via Ollama (local, no API key)
- **Vector DB:** Qdrant (local, gRPC port 6334)
- **PDF parsing:** Docling
- **GPU:** NVIDIA, ~16 GB VRAM, lock via fcntl (`/tmp/bge_m3.lock`)

## Project Layout
```
src/titan/
├── main.py          # Dispatcher: python -m titan service | help
├── utils.py         # GPU lock, stable_uuid, cache_uuid, sanitize
├── ingest.py        # PDF parsing (Docling), Markdown reader (frontmatter),
│                    # Late Chunking, BGE-M3 embed, make_point, Qdrant upsert
├── search.py        # Query decompose (Phi-4), BGE-M3, RRF, Epic-5B cache
│                    # search() with injectable model + qdrant_client
├── generate.py      # Phi-4 answer generation from chunk context
├── evaluate.py      # LLM-as-judge evaluation (Epic 4)
├── service/
│   ├── _vram_probe.py  # VRAM measurement script (run once, manually)
│   ├── state.py        # ServiceState singleton
│   ├── app.py          # FastAPI app + lifespan (BGE-M3 singleton, GPU lock)
│   ├── schemas.py      # Pydantic request/response schemas
│   └── routes.py       # HTTP endpoints (health, search, ingest, domains, …)
├── eval/
│   ├── ab_eval.py   # A/B eval tool (compares two runs)
│   └── fixtures/
│       └── cases.json
└── tools/
    ├── __init__.py
    └── init_col.py  # Qdrant collection setup
deploy/              # systemd service file + installation guide
tests/
├── titan/           # Unit tests (mirrors src/titan/)
└── integration/     # Service integration tests (pytest.mark.integration)
docs/ai/             # AI agent docs
.github/workflows/   # CI
```

## Conventions

### Code style
- Line length: 100
- Quotes: double
- Type hints required on all function signatures (mypy strict)
- `from __future__ import annotations` at top of every module
- Docstrings: Google style for public APIs

### Error handling
- Raise specific exceptions, not bare `Exception`
- No bare `except:` clauses
- Don't catch exceptions just to silence them

### Naming
- Modules: `lower_snake_case`
- Classes: `PascalCase`
- Functions/variables: `lower_snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private: leading underscore

### Testing
- One test file per source module: `src/titan/foo.py` → `tests/titan/test_foo.py`
- Use pytest fixtures, not `setUp`/`tearDown`
- Mark slow tests with `@pytest.mark.slow`
- Mark integration tests with `@pytest.mark.integration`

### Commits
- Format: `<type>: <subject>` (types: feat, fix, refactor, test, docs, chore)
- Imperative mood: "add X" not "added X"
- One logical change per commit

## Commands (always use these)
- `make install` — install deps and pre-commit hooks
- `make test` — run tests with coverage
- `make test-fast` — skip slow + integration tests
- `make check` — full quality gate (lint + types + tests)
- `make format` — auto-fix style

## Service commands
- `uv run python -m titan service` — start the service (port 8765)
- `uv run python -m titan service --port 9000` — alternative port
- `uv run python -m titan.service._vram_probe` — run the VRAM probe
- `uv run pytest tests/integration/ -m integration -v` — integration tests
- `curl localhost:8765/health` — check service health

## Environment Variables (.env)
See `.env.example` for all variables. Key ones:
- `QDRANT_HOST`, `QDRANT_GRPC_PORT`, `COLLECTION_NAME`
- `GPU_LOCK_PATH` — default `/tmp/bge_m3.lock`
- `INGEST_BASE_DIR` — input directory for PDFs, default `/mnt/f/data/titan-input`
  (example from the author's WSL2 setup; freely configurable)
- `OLLAMA_URL`, `OLLAMA_MODEL` — Phi-4 via Ollama
- `CACHE_ENABLED`, `CACHE_COLLECTION_NAME` — Epic 5B semantic cache

## Known pitfalls
- `torch` installed via `uv add` defaults to the CPU wheel. After installing, verify:
  `uv run python -c "import torch; print(torch.cuda.is_available())"` → must be `True`.
  Otherwise add the CUDA index in `pyproject.toml` via `[[tool.uv.index]]`.
- `flagembedding`, `docling` and `frontmatter` ship no type stubs → mypy `ignore_missing_imports = true`
  for these modules (see `pyproject.toml` `[[tool.mypy.overrides]]`).
- GPU lock: `acquire_gpu_lock()` must keep the returned handle alive until the process ends.
  Don't capture it in a temporary variable that immediately goes out of scope.
- FastAPI decorators are `untyped-decorator` under mypy strict → override needed in `pyproject.toml`
  for `titan.service.routes`.
- pre-commit mypy hook: needs pydantic/fastapi/torch/httpx as `additional_dependencies`
  in `.pre-commit-config.yaml`, otherwise "BaseModel has type Any".
- `qdrant_client.count()` returns `.count` as `Any` → cast explicitly with `int()`.
- WSL2 systemd: `/etc/wsl.conf` must contain `[boot]\nsystemd=true`.
  After changing it: `wsl --shutdown` from PowerShell.

## Glossary
- **Late Chunking:** a chunking strategy that embeds the full document context first,
  then chunks (instead of the other way around). Preserves semantic context across
  chunk boundaries.
- **BGE-M3:** embedding model with three vectors per chunk (dense, sparse, colbert).
- **RRF (Reciprocal Rank Fusion):** fuses multiple ranked lists (dense/sparse/colbert)
  into one consolidated ranking. k=60 is the default parameter.
- **Epic 5A:** Contextual Retrieval via Phi-4 summaries — dropped in v1.0, not ported.
- **Epic 5B:** semantic caching of query results in a separate Qdrant collection.
- **Domain:** a classification label per document (e.g. `lernen`, `trading`, `titan`).
  Enables isolated search per knowledge area.
- **run_id:** a UUID per ingest run, stored in the chunk payload. Enables
  upsert-before-delete on re-indexing: old chunks with a different run_id are deleted.
- **Service path:** invocation via `titan.service` with a pre-loaded model (singleton).
  No per-request GPU lock needed, since the service acquires the lock at startup.
- **CLI path:** direct invocation (`python -m titan.search`). Loads BGE-M3 itself, acquires the GPU lock.
- **VAULT_ROOT:** base directory of the Obsidian vault. All service paths must live
  under it (path-traversal protection). Default: `/mnt/f/vault` (example; on native
  Linux e.g. `~/vault`).
