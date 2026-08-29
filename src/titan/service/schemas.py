"""
schemas.py – Pydantic Request/Response Schemas für den Titan Service
====================================================================
Alle Ein- und Ausgabe-Typen der API. Einzige Stelle die die API-Contracts kennt.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ─── Health ──────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    bge_loaded: bool
    qdrant_reachable: bool
    vram_used_mb: int | None
    collection_name: str
    colbert_dim: int | None  # None wenn BGE nicht geladen


# ─── Search ──────────────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=10_000)
    domain: str | None = None
    top_k: int = Field(default=10, ge=1, le=50)
    use_decompose: bool = True
    use_cache: bool = True


class Chunk(BaseModel):
    text: str
    source_path: str
    domain: str
    chunk_offset: int
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    chunks: list[Chunk]
    sub_queries: list[str]
    cache_hit: bool
    latency_ms: int


# ─── Ask (RAG: Retrieval + Phi-4-Antwort) ─────────────────────────────────────


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=10_000)
    domain: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    use_decompose: bool = True
    use_cache: bool = True


class AskResponse(BaseModel):
    query: str
    answer: str
    model: str  # das verwendete Ollama-Modell (z.B. phi4:latest)
    chunks: list[Chunk]  # die als Kontext genutzten Retrieval-Treffer (für Quellen)
    sub_queries: list[str]
    cache_hit: bool
    latency_ms: int


# ─── Ingest ──────────────────────────────────────────────────────────────────


class IngestRequest(BaseModel):
    file_path: str = Field(..., description="Absoluter Pfad, muss unter VAULT_ROOT liegen")
    force: bool = Field(
        default=False,
        description="True erzwingt Re-Embed auch bei unverändertem content_hash",
    )


class IngestResponse(BaseModel):
    file_path: str
    domain: str | None  # None wenn indexed:false
    chunks_deleted: int
    chunks_created: int
    skipped_reason: str | None  # z.B. "indexed:false"
    latency_ms: int


# ─── Domains ─────────────────────────────────────────────────────────────────


class DomainsResponse(BaseModel):
    domains: list[str]  # alphabetisch sortiert
    counts: dict[str, int]  # domain → Anzahl Chunks


# ─── FindRelated ─────────────────────────────────────────────────────────────


class FindRelatedRequest(BaseModel):
    file_path: str
    top_k: int = Field(default=5, ge=1, le=20)
    exclude_self: bool = True


class FindRelatedResponse(BaseModel):
    source_path: str
    related: list[Chunk]
    latency_ms: int


# ─── Delete ──────────────────────────────────────────────────────────────────


class DeleteChunksResponse(BaseModel):
    source_path: str
    chunks_deleted: int


# ─── Notes ───────────────────────────────────────────────────────────────────


class NoteInfo(BaseModel):
    source_path: str
    domain: str
    chunk_count: int
    content_hash: str | None = None  # sha256 of the note's raw bytes; null for legacy/PDF chunks
    # Curation fields from the note's frontmatter. All optional and additive —
    # consumers written before these existed keep working unchanged.
    updated: str | None = None  # ISO date the note was last edited
    geprueft: str | None = None  # ISO date its claims were last checked against reality;
    # null means never — that is the answer worth asking for
    quelle: str | None = None  # gemessen | recherchiert | ueberlegt | agent-entwurf


class NotesResponse(BaseModel):
    notes: list[NoteInfo]  # alphabetisch nach source_path sortiert
    total: int


# ─── Stats ───────────────────────────────────────────────────────────────────


class LatencyStats(BaseModel):
    count: int  # number of samples in the in-memory window
    p50_ms: int | None
    p95_ms: int | None
    max_ms: int | None


class StatsResponse(BaseModel):
    uptime_seconds: int
    collection_name: str
    total_chunks: int  # sum over all domains
    domain_count: int
    searches_total: int  # since startup
    cache_hits: int
    cache_hit_rate: float  # 0.0–1.0; 0.0 when no searches yet
    search_latency: LatencyStats  # over the recent in-memory window
    cache_enabled: bool
    cache_entries: int | None  # None if cache disabled or Qdrant unreachable
    last_ingest_age_seconds: int | None  # None if no ingest since startup
