# Projektkontext

> Zuerst lesen. Unter 200 Zeilen halten. Mit der Weiterentwicklung des Projekts aktualisieren.

## Was dieses Projekt macht

Titan ist ein lokales, hochperformantes RAG-System (Retrieval-Augmented Generation)
auf einer einzigen Workstation. Es indexiert PDFs und Markdown-Dateien, zerlegt sie
per Late Chunking in Chunks, bettet sie mit BGE-M3 ein (Multi-Vektor: dense +
sparse + colbert), speichert sie in Qdrant und beantwortet Anfragen mit Reciprocal
Rank Fusion + Phi-4 als Generator. Auslegung: Single-Workstation-Deployment
(kein Mehrbenutzerbetrieb, kein Cloud-Deployment).

## Stack
- **Sprache:** Python 3.12+
- **Paketmanager:** uv
- **Test-Runner:** pytest
- **Lint/Format:** ruff (Zeilenlänge 100, doppelte Anführungszeichen)
- **Typprüfer:** mypy (strict)
- **CI:** GitHub Actions
- **Pre-commit:** ruff, mypy, Hygiene-Checks
- **Embeddings:** BGE-M3 via FlagEmbedding (dense + sparse + colbert)
- **LLM:** Phi-4 via Ollama (lokal, kein API-Key)
- **Vektor-DB:** Qdrant (lokal, gRPC-Port 6334)
- **PDF-Parsing:** Docling
- **GPU:** NVIDIA, ~16 GB VRAM, Lock via fcntl (`/tmp/bge_m3.lock`)

## Projektaufbau
```
src/titan/
├── main.py          # Dispatcher: python -m titan service | help
├── utils.py         # GPU-Lock, stable_uuid, cache_uuid, sanitize
├── ingest.py        # PDF-Parsing (Docling), Markdown-Reader (Frontmatter),
│                    # Late Chunking, BGE-M3 Embedding, make_point, Qdrant-Upsert
├── search.py        # Query-Zerlegung (Phi-4), BGE-M3, RRF, Epic-5B-Cache
│                    # search() mit injizierbarem Modell + qdrant_client
├── generate.py      # Phi-4 Antwortgenerierung aus Chunk-Kontext
├── evaluate.py      # LLM-as-Judge-Evaluation (Epic 4)
├── service/
│   ├── _vram_probe.py  # VRAM-Messskript (einmalig, manuell ausführen)
│   ├── state.py        # ServiceState-Singleton
│   ├── app.py          # FastAPI-App + Lifespan (BGE-M3-Singleton, GPU-Lock)
│   ├── schemas.py      # Pydantic Request/Response-Schemas
│   └── routes.py       # HTTP-Endpunkte (health, search, ingest, domains, …)
├── eval/
│   ├── ab_eval.py   # A/B-Eval-Tool (vergleicht zwei Läufe)
│   └── fixtures/
│       └── cases.json
└── tools/
    ├── __init__.py
    └── init_col.py  # Qdrant-Collection-Setup
deploy/              # systemd-Service-Datei + Installationsanleitung
tests/
├── titan/           # Unit-Tests (spiegelt src/titan/)
└── integration/     # Service-Integrationstests (pytest.mark.integration)
docs/ai/             # Doku für KI-Agenten
.github/workflows/   # CI
```

## Konventionen

### Code-Stil
- Zeilenlänge: 100
- Anführungszeichen: doppelt
- Type-Hints auf allen Funktionssignaturen erforderlich (mypy strict)
- `from __future__ import annotations` am Anfang jedes Moduls
- Docstrings: Google-Stil für öffentliche APIs

### Fehlerbehandlung
- Spezifische Exceptions werfen, kein nacktes `Exception`
- Keine nackten `except:`-Klauseln
- Exceptions nicht abfangen, nur um sie zu verschlucken

### Benennung
- Module: `lower_snake_case`
- Klassen: `PascalCase`
- Funktionen/Variablen: `lower_snake_case`
- Konstanten: `UPPER_SNAKE_CASE`
- Privat: führender Unterstrich

### Testing
- Eine Testdatei pro Quellmodul: `src/titan/foo.py` → `tests/titan/test_foo.py`
- pytest-Fixtures verwenden, nicht `setUp`/`tearDown`
- Langsame Tests mit `@pytest.mark.slow` markieren
- Integrationstests mit `@pytest.mark.integration` markieren

### Commits
- Format: `<type>: <subject>` (Typen: feat, fix, refactor, test, docs, chore)
- Imperativ: „add X" nicht „added X"
- Eine logische Änderung pro Commit

## Befehle (immer diese verwenden)
- `make install` — Abhängigkeiten und pre-commit-Hooks installieren
- `make test` — Tests mit Coverage ausführen
- `make test-fast` — langsame + Integrationstests überspringen
- `make check` — vollständiges Quality-Gate (Lint + Typen + Tests)
- `make format` — Stil automatisch korrigieren

## Service-Befehle
- `uv run python -m titan service` — Service starten (Port 8765)
- `uv run python -m titan service --port 9000` — alternativer Port
- `uv run python -m titan.service._vram_probe` — VRAM-Probe ausführen
- `uv run pytest tests/integration/ -m integration -v` — Integrationstests
- `curl localhost:8765/health` — Service-Health prüfen

## Umgebungsvariablen (.env)
Siehe `.env.example` für alle Variablen. Die wichtigsten:
- `QDRANT_HOST`, `QDRANT_GRPC_PORT`, `COLLECTION_NAME`
- `GPU_LOCK_PATH` — Default `/tmp/bge_m3.lock`
- `INGEST_BASE_DIR` — Eingabeverzeichnis für PDFs, Default `/mnt/f/data/titan-input`
- `OLLAMA_URL`, `OLLAMA_MODEL` — Phi-4 via Ollama
- `CACHE_ENABLED`, `CACHE_COLLECTION_NAME` — Epic-5B Semantic Cache

## Bekannte Fallstricke
- `torch` via `uv add` installiert standardmäßig das CPU-Wheel. Nach der Installation prüfen:
  `uv run python -c "import torch; print(torch.cuda.is_available())"` → muss `True` sein.
  Andernfalls den CUDA-Index in `pyproject.toml` via `[[tool.uv.index]]` ergänzen.
- `flagembedding`, `docling` und `frontmatter` liefern keine Type-Stubs → mypy `ignore_missing_imports = true`
  für diese Module (siehe `pyproject.toml` `[[tool.mypy.overrides]]`).
- GPU-Lock: `acquire_gpu_lock()` muss das zurückgegebene Handle bis zum Prozessende am Leben halten.
  Nicht in einer temporären Variable erfassen, die sofort aus dem Scope fällt.
- FastAPI-Dekoratoren sind unter mypy strict `untyped-decorator` → Override in `pyproject.toml`
  für `titan.service.routes` nötig.
- pre-commit mypy-Hook: benötigt pydantic/fastapi/torch/httpx als `additional_dependencies`
  in `.pre-commit-config.yaml`, sonst „BaseModel has type Any".
- `qdrant_client.count()` gibt `.count` als `Any` zurück → explizit mit `int()` casten.
- WSL2 systemd: `/etc/wsl.conf` muss `[boot]\nsystemd=true` enthalten.
  Nach der Änderung: `wsl --shutdown` aus PowerShell.

## Glossar
- **Late Chunking:** eine Chunking-Strategie, die zuerst den vollständigen Dokumentkontext
  einbettet und dann chunkt (statt umgekehrt). Erhält den semantischen Kontext über
  Chunk-Grenzen hinweg.
- **BGE-M3:** Embedding-Modell mit drei Vektoren pro Chunk (dense, sparse, colbert).
- **RRF (Reciprocal Rank Fusion):** führt mehrere gerankte Listen (dense/sparse/colbert)
  zu einem konsolidierten Ranking zusammen. k=60 ist der Default-Parameter.
- **Epic 5A:** Contextual Retrieval via Phi-4-Zusammenfassungen — in v1.0 verworfen, nicht portiert.
- **Epic 5B:** semantisches Caching von Query-Ergebnissen in einer separaten Qdrant-Collection.
- **Domain:** ein Klassifikations-Label pro Dokument (z. B. `lernen`, `trading`, `titan`).
  Ermöglicht isolierte Suche pro Wissensbereich.
- **run_id:** eine UUID pro Ingest-Lauf, im Chunk-Payload gespeichert. Ermöglicht
  upsert-before-delete bei Neuindexierung: alte Chunks mit abweichender run_id werden gelöscht.
- **Service-Pfad:** Aufruf über `titan.service` mit vorgeladenem Modell (Singleton).
  Kein GPU-Lock pro Request nötig, da der Service das Lock beim Start erwirbt.
- **CLI-Pfad:** direkter Aufruf (`python -m titan.search`). Lädt BGE-M3 selbst, erwirbt das GPU-Lock.
- **VAULT_ROOT:** Basisverzeichnis des Obsidian-Vaults. Alle Service-Pfade müssen darunter
  liegen (Schutz vor Path-Traversal). Default: `/mnt/f/vault`.
