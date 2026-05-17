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


# ─── Ingest ──────────────────────────────────────────────────────────────────


class IngestRequest(BaseModel):
    file_path: str = Field(..., description="Absoluter Pfad, muss unter VAULT_ROOT liegen")
    force: bool = False


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


class NotesResponse(BaseModel):
    notes: list[NoteInfo]  # alphabetisch nach source_path sortiert
    total: int
