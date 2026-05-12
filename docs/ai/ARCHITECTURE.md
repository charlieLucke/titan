# Architecture

> System-level design. Update when modules, contracts, or data models change.

## Overview

Titan ist ein lokales RAG-System (Retrieval-Augmented Generation) auf einer einzelnen
Workstation. Dokumente (PDFs, Markdown) werden mit BGE-M3 multi-vektoriell eingebettet
(dense + sparse + ColBERT) und in Qdrant gespeichert. Suchanfragen werden via Hybrid-Search
(Prefetch + ColBERT MaxSim Reranking) und Reciprocal Rank Fusion beantwortet;
die Antwort-Generierung erfolgt durch Phi-4 via Ollama.

Ab Phase 1 läuft ein **FastAPI-Service** (`titan service`) der BGE-M3 dauerhaft im VRAM hält
und eine HTTP-API für Suche und Ingest bereitstellt. Ein MCP-Server (`brain-mcp`, Phase 2)
macht das System für Claude Desktop nutzbar und beobachtet den Obsidian-Vault per Watchdog.

## Module Map

```
src/titan/
├── __init__.py
├── __main__.py          # CLI: python -m titan [service | ...]
├── main.py              # Dispatcher: Service-Mode + Hilfe
├── utils.py             # GPU-Lock (fcntl), stable_uuid, cache_uuid, sanitize
├── ingest.py            # PDF-Parsing (Docling), Markdown-Reader (frontmatter),
│                        # Late Chunking, BGE-M3 Embed, make_point, Qdrant-Upsert
├── search.py            # Query-Decompose (Phi-4), BGE-M3, Hybrid-Search (RRF),
│                        # Epic-5B Semantic Cache; search() injectable für Service
├── generate.py          # Phi-4 Antwort-Generierung (liest Chunks von stdin)
├── evaluate.py          # LLM-as-Judge (RAG-Triade: CR / GR / AR)
├── service/
│   ├── __init__.py
│   ├── _vram_probe.py   # Einmaliges VRAM-Mess-Skript (A0)
│   ├── state.py         # ServiceState Singleton (model, client, gpu_lock, domain_counts)
│   ├── app.py           # FastAPI App + Lifespan (BGE-M3 laden, ColBERT-Dim-Check)
│   ├── schemas.py       # Pydantic Request/Response Types
│   └── routes.py        # Alle Endpoints: /health /search /ingest/file /domains
│                        #                 /find_related DELETE /chunks
├── eval/
│   ├── ab_eval.py       # A/B-Evaluierung über Eval-Cases
│   └── fixtures/cases.json
└── tools/
    └── init_col.py      # Qdrant-Collection initialisieren (--recreate)

deploy/
├── titan-service.service  # systemd User-Service
└── README.md              # Installations-Anleitung

tests/
├── titan/               # Unit-Tests (mirrors src/titan/)
└── integration/
    └── test_service.py  # 16 Integration-Tests (pytest.mark.integration)
```

## Data Model

### Qdrant Chunk (Payload)

```
{
  "text":         str,   # Chunk-Text
  "source":       str,   # Dateipfad (CLI-Ingest legacy)
  "source_path":  str,   # Dateipfad (Service-Ingest, Alias zu source)
  "chunk_id":     int,   # Position im Dokument (0-basiert)
  "chunk_offset": int,   # Alias zu chunk_id (Service-Schema)
  "header":       str,   # Nächste Markdown-Überschrift
  "domain":       str,   # Klassifizierungs-Label (lernen, trading, titan, …)
  "run_id":       str,   # UUID des Ingest-Runs (für Upsert-before-Delete)
}
```

Vektoren pro Chunk: `dense` (1024d COSINE), `sparse` (Lexical), `colbert` (1024d MULTI_SIM).

### Qdrant Cache-Einträge (Epic 5B, Collection: `query_cache`)

```
{
  "sub_query_normalized": str,
  "domain":               str | null,
  "chunks":               list[dict],
  "created_at":           float,  # Unix timestamp
}
```

## External Services

| Service | Verbindung | Zweck |
|---|---|---|
| Qdrant | gRPC :6334 (lokal, Docker) | Vektor-Datenbank, Haupt-Collection + Cache-Collection |
| Ollama | HTTP :11434 (lokal) | Phi-4 für Query-Decompose und Antwort-Generierung |
| BGE-M3 | CUDA (VRAM, über FlagEmbedding) | Multi-Vektor-Embedding (dense + sparse + ColBERT) |

## Data Flow

### CLI-Ingest (PDF)
```
PDF → Docling (parse) → chunk_markdown() → late_chunk_embed(BGE-M3)
    → upsert_to_qdrant() → Qdrant
```

### Service-Ingest (Markdown, POST /ingest/file)
```
.md file → read_markdown() [frontmatter parse, sanitize, indexed-check]
         → late_chunk_and_embed(content, model=singleton)
         → make_point() [mit run_id] → qdrant.upsert()
         → qdrant.delete(alte Chunks mit anderem run_id)
         → cache_invalidate(domain)
         → domain_counter update
```

### Suche (POST /search)
```
query → decompose_query(Phi-4) → sub_queries[]
      → embed_query(BGE-M3) × n  → [dense, sparse, colbert] × n
      → hybrid_search_with_rerank(Qdrant) × n  [Prefetch + ColBERT MaxSim]
      → Epic-5B Cache lookup/write
      → rrf_fusion() → top_k Chunks
      → SearchResponse
```

### Vollständige CLI-Pipeline
```
python -m titan.search "Frage" --json | python -m titan.generate
```

## Deployment

**Lokal, WSL2, single workstation.**

- **titan-service:** systemd user-service (`~/.config/systemd/user/titan-service.service`)
  Bindet auf `127.0.0.1:8765`. BGE-M3 dauerhaft im VRAM. GPU-Lock via fcntl.
- **Qdrant:** Docker-Container, gRPC :6334
- **Ollama:** Systemd-Service oder manuell, HTTP :11434
- **brain-mcp (Phase 2):** Per-Session-Prozess, gestartet von Claude Desktop via stdio MCP.
  Watcher als separater systemd user-service (`brain-watcher.service`).

**WSL2-Voraussetzung:** `/etc/wsl.conf` mit `[boot] systemd=true`.
