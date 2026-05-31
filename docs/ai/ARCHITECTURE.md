# Architecture

> System-level design. Update when modules, contracts, or data models change.

## Overview

Titan is a local RAG (Retrieval-Augmented Generation) system on a single
workstation. Documents (PDFs, Markdown) are embedded multi-vectorially with BGE-M3
(dense + sparse + ColBERT) and stored in Qdrant. Queries are answered via hybrid
search (prefetch + ColBERT MaxSim reranking) and Reciprocal Rank Fusion; answer
generation is done by Phi-4 via Ollama.

From Phase 1 on, a **FastAPI service** (`titan service`) keeps BGE-M3 permanently in
VRAM and exposes an HTTP API for search and ingest. An MCP server (`brain-mcp`,
Phase 2) makes the system usable from Claude and watches the Obsidian vault via
watchdog.

## Module Map

```
src/titan/
├── __init__.py
├── __main__.py          # CLI: python -m titan [service | ...]
├── main.py              # Dispatcher: service mode + help
├── utils.py             # GPU lock (fcntl), stable_uuid, cache_uuid, sanitize
├── ingest.py            # PDF parsing (Docling), Markdown reader (frontmatter),
│                        # Late Chunking, BGE-M3 embed, make_point, Qdrant upsert
├── search.py            # Query decompose (Phi-4), BGE-M3, hybrid search (RRF),
│                        # Epic-5B semantic cache; search() injectable for the service
├── generate.py          # Phi-4 answer generation (reads chunks from stdin)
├── evaluate.py          # LLM-as-judge (RAG triad: CR / GR / AR)
├── service/
│   ├── __init__.py
│   ├── _vram_probe.py   # One-off VRAM measurement script (A0)
│   ├── state.py         # ServiceState singleton (model, client, gpu_lock, domain_counts)
│   ├── app.py           # FastAPI app + lifespan (load BGE-M3, ColBERT dim check)
│   ├── schemas.py       # Pydantic request/response types
│   └── routes.py        # All endpoints: /health /search /ingest/file /domains
│                        #                /notes /find_related DELETE /chunks
├── eval/
│   ├── ab_eval.py       # A/B evaluation over eval cases
│   └── fixtures/cases.json
└── tools/
    └── init_col.py      # Initialize the Qdrant collection (--recreate)

deploy/
├── titan-service.service  # systemd user service
└── README.md              # installation guide

tests/
├── titan/               # Unit tests (mirrors src/titan/)
└── integration/
    └── test_service.py  # 16 integration tests (pytest.mark.integration)
```

## Data Model

### Qdrant Chunk (payload)

```
{
  "text":         str,   # chunk text
  "source":       str,   # file path (CLI ingest, legacy)
  "source_path":  str,   # file path (service ingest, alias of source)
  "chunk_id":     int,   # position in the document (0-based)
  "chunk_offset": int,   # alias of chunk_id (service schema)
  "header":       str,   # nearest Markdown heading
  "domain":       str,   # classification label (lernen, trading, titan, …)
  "run_id":       str,   # UUID of the ingest run (for upsert-before-delete)
}
```

Vectors per chunk: `dense` (1024d COSINE), `sparse` (lexical), `colbert` (1024d MULTI_SIM).

### Qdrant cache entries (Epic 5B, collection: `query_cache`)

```
{
  "sub_query_normalized": str,
  "domain":               str | null,
  "chunks":               list[dict],
  "created_at":           float,  # Unix timestamp
}
```

## External Services

| Service | Connection | Purpose |
|---|---|---|
| Qdrant | gRPC :6334 (local, Docker) | Vector database, main collection + cache collection |
| Ollama | HTTP :11434 (local) | Phi-4 for query decompose and answer generation |
| BGE-M3 | CUDA (VRAM, via FlagEmbedding) | Multi-vector embedding (dense + sparse + ColBERT) |

## Data Flow

### CLI ingest (PDF)
```
PDF → Docling (parse) → chunk_markdown() → late_chunk_embed(BGE-M3)
    → upsert_to_qdrant() → Qdrant
```

### Service ingest (Markdown, POST /ingest/file)
```
.md file → read_markdown() [frontmatter parse, sanitize, indexed check]
         → late_chunk_and_embed(content, model=singleton)
         → make_point() [with run_id] → qdrant.upsert()
         → qdrant.delete(old chunks with a different run_id)
         → cache_invalidate(domain)
         → domain_counter update
```

### Search (POST /search)
```
query → decompose_query(Phi-4) → sub_queries[]
      → embed_query(BGE-M3) × n  → [dense, sparse, colbert] × n
      → hybrid_search_with_rerank(Qdrant) × n  [prefetch + ColBERT MaxSim]
      → Epic-5B cache lookup/write
      → rrf_fusion() → top_k chunks
      → SearchResponse
```

### Full CLI pipeline
```
python -m titan.search "question" --json | python -m titan.generate
```

## Deployment

**Local, WSL2, single workstation.**

- **titan-service:** systemd user service (`~/.config/systemd/user/titan-service.service`)
  Binds on `127.0.0.1:8765`. BGE-M3 permanently in VRAM. GPU lock via fcntl.
- **Qdrant:** Docker container, gRPC :6334
- **Ollama:** systemd service or manual, HTTP :11434
- **brain-mcp (Phase 2):** serves the MCP tools to Claude — stdio (local) or, as
  deployed, Streamable-HTTP exposed via Tailscale Funnel as a custom connector. The
  vault watcher runs as a separate systemd user service (`brain-watcher.service`).

**WSL2 prerequisite:** `/etc/wsl.conf` with `[boot] systemd=true`.
```
