# Architektur

> Design auf Systemebene. Aktualisieren, wenn sich Module, Contracts oder Datenmodelle ändern.

## Überblick

Titan ist ein lokales RAG-System (Retrieval-Augmented Generation) auf einer
einzigen Workstation. Dokumente (PDFs, Markdown) werden mit BGE-M3 multi-vektoriell
eingebettet (dense + sparse + ColBERT) und in Qdrant gespeichert. Anfragen werden
über hybride Suche (Prefetch + ColBERT-MaxSim-Reranking) und Reciprocal Rank Fusion
beantwortet; die Antwortgenerierung übernimmt Phi-4 via Ollama.

Ab Phase 1 hält ein **FastAPI-Service** (`titan service`) BGE-M3 dauerhaft im VRAM
und stellt eine HTTP-API für Suche und Ingest bereit. Ein MCP-Server (`brain-mcp`,
Phase 2) macht das System aus Claude heraus nutzbar und überwacht den
Obsidian-Vault per watchdog.

## Modulübersicht

```
src/titan/
├── __init__.py
├── __main__.py          # CLI: python -m titan [service | ...]
├── main.py              # Dispatcher: Service-Modus + Hilfe
├── utils.py             # GPU-Lock (fcntl), stable_uuid, cache_uuid, sanitize
├── ingest.py            # PDF-Parsing (Docling), Markdown-Reader (Frontmatter),
│                        # Late Chunking, BGE-M3 Embedding, make_point, Qdrant-Upsert
├── search.py            # Query-Zerlegung (Phi-4), BGE-M3, hybride Suche (RRF),
│                        # Epic-5B Semantic Cache; search() für den Service injizierbar
├── generate.py          # Phi-4 Antwortgenerierung (liest Chunks von stdin)
├── evaluate.py          # LLM-as-Judge (RAG-Triade: CR / GR / AR)
├── service/
│   ├── __init__.py
│   ├── _vram_probe.py   # Einmaliges VRAM-Messskript (A0)
│   ├── state.py         # ServiceState-Singleton (model, client, gpu_lock, domain_counts)
│   ├── app.py           # FastAPI-App + Lifespan (BGE-M3 laden, ColBERT-Dim-Check)
│   ├── schemas.py       # Pydantic Request/Response-Typen
│   └── routes.py        # Alle Endpunkte: /health /search /ingest/file /domains
│                        #                /notes /find_related DELETE /chunks
├── eval/
│   ├── ab_eval.py       # A/B-Evaluation über Eval-Cases
│   └── fixtures/cases.json
└── tools/
    └── init_col.py      # Qdrant-Collection initialisieren (--recreate)

deploy/
├── titan-service.service  # systemd-User-Service
└── README.md              # Installationsanleitung

tests/
├── titan/               # Unit-Tests (spiegelt src/titan/)
└── integration/
    └── test_service.py  # 16 Integrationstests (pytest.mark.integration)
```

## Datenmodell

### Qdrant-Chunk (Payload)

```
{
  "text":         str,   # Chunk-Text
  "source":       str,   # Dateipfad (CLI-Ingest, Legacy)
  "source_path":  str,   # Dateipfad (Service-Ingest, Alias von source)
  "chunk_id":     int,   # Position im Dokument (0-basiert)
  "chunk_offset": int,   # Alias von chunk_id (Service-Schema)
  "header":       str,   # nächste Markdown-Überschrift
  "domain":       str,   # Klassifikations-Label (lernen, trading, titan, …)
  "run_id":       str,   # UUID des Ingest-Laufs (für upsert-before-delete)
}
```

Vektoren pro Chunk: `dense` (1024d COSINE), `sparse` (lexikalisch), `colbert` (1024d MULTI_SIM).

### Qdrant-Cache-Einträge (Epic 5B, Collection: `query_cache`)

```
{
  "sub_query_normalized": str,
  "domain":               str | null,
  "chunks":               list[dict],
  "created_at":           float,  # Unix-Timestamp
}
```

## Externe Services

| Service | Verbindung | Zweck |
|---|---|---|
| Qdrant | gRPC :6334 (lokal, Docker) | Vektordatenbank, Haupt-Collection + Cache-Collection |
| Ollama | HTTP :11434 (lokal) | Phi-4 für Query-Zerlegung und Antwortgenerierung |
| BGE-M3 | CUDA (VRAM, via FlagEmbedding) | Multi-Vektor-Embedding (dense + sparse + ColBERT) |

## Datenfluss

### CLI-Ingest (PDF)
```
PDF → Docling (parsen) → chunk_markdown() → late_chunk_embed(BGE-M3)
    → upsert_files_to_qdrant() → Qdrant (gleiches Payload-Schema wie Service: source_path, run_id, content_hash)
```

### Service-Ingest (Markdown, POST /ingest/file)
```
.md-Datei → read_markdown() [Frontmatter parsen, sanitize, indexed-Check]
          → late_chunk_and_embed(content, model=Singleton)
          → make_point() [mit run_id] → qdrant.upsert()
          → qdrant.delete(alte Chunks mit abweichender run_id)
          → cache_invalidate(domain)
          → domain_counter-Aktualisierung
```

### Suche (POST /search)
```
query → decompose_query(Phi-4) → sub_queries[]
      → embed_query(BGE-M3) × n  → [dense, sparse, colbert] × n
      → hybrid_search_with_rerank(Qdrant) × n  [Prefetch + ColBERT MaxSim]
      → Epic-5B Cache-Lookup/Write
      → rrf_fusion() → top_k Chunks
      → SearchResponse
```

### Vollständige CLI-Pipeline
```
python -m titan.search "Frage" --json | python -m titan.generate
```

## Deployment

**Lokal, WSL2, einzelne Workstation.**

- **titan-service:** systemd-User-Service (`~/.config/systemd/user/titan-service.service`)
  Bindet auf `127.0.0.1:8765`. BGE-M3 dauerhaft im VRAM. GPU-Lock via fcntl.
- **Qdrant:** Docker-Container, gRPC :6334
- **Ollama:** systemd-Service oder manuell, HTTP :11434
- **brain-mcp (Phase 2):** stellt die MCP-Tools für Claude bereit — stdio (lokal) oder,
  wie deployt, Streamable-HTTP über Tailscale Funnel als Custom Connector. Der
  Vault-Watcher läuft als separater systemd-User-Service (`brain-watcher.service`).

**WSL2-Voraussetzung:** `/etc/wsl.conf` mit `[boot] systemd=true`.
```
