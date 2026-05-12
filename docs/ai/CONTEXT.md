# Project Context

> Read this first. Keep under 200 lines. Update as the project evolves.

## What this project does

Titan ist ein lokales, hochperformantes RAG-System (Retrieval-Augmented Generation) auf einer
einzelnen Workstation. Es indiziert PDFs und Markdown-Dateien, zerlegt sie per Late Chunking in
Chunks, bettet sie mit BGE-M3 ein (Multi-Vector: dense + sparse + colbert), speichert sie in
Qdrant und beantwortet Queries mit Reciprocal Rank Fusion + Phi-4 als Generator. Zielgruppe:
ausschließlich persönlicher Einsatz (kein Multi-User, kein Cloud-Deployment).

## Stack
- **Language:** Python 3.12+
- **Package manager:** uv
- **Test runner:** pytest
- **Lint/format:** ruff (line length 100, double quotes)
- **Type checker:** mypy (strict)
- **CI:** GitHub Actions
- **Pre-commit:** ruff, mypy, hygiene checks
- **Embeddings:** BGE-M3 via FlagEmbedding (dense + sparse + colbert)
- **LLM:** Phi-4 via Ollama (lokal, kein API-Key)
- **Vector DB:** Qdrant (lokal, gRPC port 6334)
- **PDF-Parsing:** Docling
- **GPU:** NVIDIA, VRAM ~16 GB, Lock via fcntl (/tmp/bge_m3.lock)

## Project Layout
```
src/titan/
├── main.py          # Dispatcher: python -m titan service | help
├── utils.py         # GPU-Lock, stable_uuid, cache_uuid, sanitize
├── ingest.py        # PDF-Parsing (Docling), Markdown-Reader (frontmatter),
│                    # Late Chunking, BGE-M3 Embed, make_point, Qdrant-Upsert
├── search.py        # Query-Decompose (Phi-4), BGE-M3, RRF, Epic-5B Cache
│                    # search() mit injectablem model + qdrant_client
├── generate.py      # Phi-4 Answer Generation aus Chunk-Kontext
├── evaluate.py      # LLM-as-Judge Evaluation (Epic 4)
├── service/
│   ├── _vram_probe.py  # VRAM-Mess-Skript (einmalig manuell ausführen)
│   ├── state.py        # ServiceState Singleton
│   ├── app.py          # FastAPI App + Lifespan (BGE-M3 Singleton, GPU-Lock)
│   ├── schemas.py      # Pydantic Request/Response Schemas
│   └── routes.py       # HTTP Endpoints (health, search, ingest, domains, …)
├── eval/
│   ├── ab_eval.py   # A/B-Eval Tool (vergleicht zwei Runs)
│   └── fixtures/
│       └── cases.json
└── tools/
    ├── __init__.py
    └── init_col.py  # Qdrant Collection Setup
deploy/              # systemd-Service-Datei + Installations-Anleitung
tests/
├── titan/           # Unit-Tests (mirrors src/titan/)
└── integration/     # Service-Integration-Tests (pytest.mark.integration)
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

## Service-Commands
- `uv run python -m titan service` — Service starten (Port 8765)
- `uv run python -m titan service --port 9000` — alternativer Port
- `uv run python -m titan.service._vram_probe` — VRAM-Probe ausführen
- `uv run pytest tests/integration/ -m integration -v` — Integration-Tests
- `curl localhost:8765/health` — Service-Health prüfen

## Environment Variables (.env)
See `.env.example` for all variables. Key ones:
- `QDRANT_HOST`, `QDRANT_GRPC_PORT`, `COLLECTION_NAME`
- `GPU_LOCK_PATH` — default `/tmp/bge_m3.lock`
- `INGEST_BASE_DIR` — input directory for PDFs, default `/mnt/f/data/titan-input`
- `OLLAMA_URL`, `OLLAMA_MODEL` — Phi-4 via Ollama
- `CACHE_ENABLED`, `CACHE_COLLECTION_NAME` — Epic 5B Semantic Cache

## Known pitfalls
- `torch` via `uv add` installiert standardmäßig CPU-Wheel. Nach Installation prüfen:
  `uv run python -c "import torch; print(torch.cuda.is_available())"` → muss `True` sein.
  Sonst CUDA-Index in `pyproject.toml` via `[[tool.uv.index]]` eintragen.
- `flagembedding`, `docling` und `frontmatter` haben keine Typestubs → mypy `ignore_missing_imports = true`
  für diese Module (siehe pyproject.toml `[[tool.mypy.overrides]]`).
- GPU-Lock: `acquire_gpu_lock()` muss das zurückgegebene Handle bis Prozessende am Leben lassen.
  Nicht in einer temporären Variable abfangen die sofort out-of-scope geht.
- FastAPI-Decorators sind in mypy strict `untyped-decorator` → Override in pyproject.toml
  für `titan.service.routes` nötig.
- pre-commit mypy-Hook: braucht pydantic/fastapi/torch/httpx als `additional_dependencies`
  in `.pre-commit-config.yaml`, sonst "BaseModel has type Any".
- `qdrant_client.count()` gibt `.count` als `Any` zurück → explizit `int()` casten.
- WSL2 systemd: `/etc/wsl.conf` muss `[boot]\nsystemd=true` enthalten.
  Nach Änderung: `wsl --shutdown` aus PowerShell.

## Glossary
- **Late Chunking:** Chunking-Strategie die erst den ganzen Dokument-Kontext embedded, dann
  chunked (statt umgekehrt). Bewahrt semantischen Kontext über Chunk-Grenzen.
- **BGE-M3:** Embedding-Modell mit drei Vektoren pro Chunk (dense, sparse, colbert).
- **RRF (Reciprocal Rank Fusion):** Fusioniert mehrere Ranglisten (dense/sparse/colbert) zu
  einer konsolidierten Rangliste. k=60 ist der Standard-Parameter.
- **Epic 5A:** Contextual Retrieval via Phi-4-Summaries — in v1.0 verworfen, nicht portiert.
- **Epic 5B:** Semantic Caching von Query-Ergebnissen in separater Qdrant-Collection.
- **Domain:** Klassifizierungslabel pro Dokument (z.B. `lernen`, `trading`, `titan`).
  Ermöglicht isolierte Suche pro Wissensbereich.
- **run_id:** UUID pro Ingest-Run, im Chunk-Payload gespeichert. Ermöglicht
  Upsert-before-Delete beim Re-Indexieren: alte Chunks mit anderem run_id werden gelöscht.
- **Service-Pfad:** Aufruf über `titan.service` mit vorgeladenem Modell (Singleton).
  Kein GPU-Lock pro Request nötig, da der Service den Lock beim Start erwirbt.
- **CLI-Pfad:** Direktaufruf (`python -m titan.search`). Lädt BGE-M3 selbst, erwirbt GPU-Lock.
- **VAULT_ROOT:** Basis-Verzeichnis des Obsidian-Vaults. Alle Service-Pfade müssen darunter
  liegen (Pfad-Traversal-Schutz). Default: `/mnt/f/vault`.
