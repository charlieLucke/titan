"""
routes.py – FastAPI Router mit allen Endpoints
===============================================
Wird von app.py via include_router eingebunden.

Endpoints:
    GET  /health          – Service-Status, BGE-M3 und Qdrant-Verfügbarkeit
    POST /search          – Hybrid-Suche (A4)
    POST /ingest/file     – Markdown-Datei indexieren (A6)
    GET  /domains         – Alle Domains mit Chunk-Counts (A7)
    POST /find_related    – Semantisch ähnliche Notes (A8)
    DELETE /chunks        – Chunks einer Datei löschen (A9)
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Literal

import torch
from fastapi import APIRouter, HTTPException

from titan.service.schemas import (
    Chunk,
    DeleteChunksResponse,
    DomainsResponse,
    FindRelatedRequest,
    FindRelatedResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    SearchRequest,
    SearchResponse,
)
from titan.service.state import state

VAULT_ROOT = Path(os.getenv("VAULT_ROOT", "/mnt/f/vault")).resolve()

log = logging.getLogger(__name__)

COLLECTION_NAME: str = os.getenv("COLLECTION_NAME", "mein_wissen")

router = APIRouter()


# ─── Health ──────────────────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Service-Status: BGE-M3 geladen, Qdrant erreichbar, VRAM-Nutzung."""
    bge_loaded = state.bge_model is not None

    qdrant_reachable = False
    if state.qdrant_client is not None:
        try:
            state.qdrant_client.get_collection(COLLECTION_NAME)
            qdrant_reachable = True
        except Exception:
            qdrant_reachable = False

    vram_used_mb: int | None = None
    colbert_dim: int | None = None
    if bge_loaded and state.bge_model is not None:
        with contextlib.suppress(Exception):
            vram_used_mb = int(torch.cuda.memory_allocated() / 1024 / 1024)
        with contextlib.suppress(Exception):
            with torch.no_grad():
                out = state.bge_model.encode(
                    ["health check"],
                    return_dense=False,
                    return_sparse=False,
                    return_colbert_vecs=True,
                    batch_size=1,
                )
            colbert_dim = len(out["colbert_vecs"][0][0])

    overall: Literal["ok", "degraded"] = "ok" if (bge_loaded and qdrant_reachable) else "degraded"

    return HealthResponse(
        status=overall,
        bge_loaded=bge_loaded,
        qdrant_reachable=qdrant_reachable,
        vram_used_mb=vram_used_mb,
        collection_name=COLLECTION_NAME,
        colbert_dim=colbert_dim,
    )


# ─── Search (A4) ────────────────────────────────────────────────────────────


@router.post("/search", response_model=SearchResponse)
async def search_endpoint(req: SearchRequest) -> SearchResponse:
    """Hybrid-Suche: Query-Decompose → BGE-M3 → RRF → (Epic-5B Cache)."""
    if state.bge_model is None or state.qdrant_client is None:
        raise HTTPException(503, "Service nicht bereit (BGE-M3 oder Qdrant nicht geladen)")

    from titan.search import search  # lokaler Import vermeidet Circular-Import-Risiko

    t0 = time.perf_counter()
    result = search(
        query=req.query,
        domain=req.domain,
        top_k=req.top_k,
        use_decompose=req.use_decompose,
        use_cache=req.use_cache,
        model=state.bge_model,
        qdrant_client=state.qdrant_client,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    chunks = [
        Chunk(
            text=c.get("text", ""),
            source_path=c.get("source_path", ""),
            domain=c.get("domain", ""),
            chunk_offset=c.get("chunk_offset", 0),
            score=float(c.get("score", 0.0)),
            metadata={
                k: v
                for k, v in c.items()
                if k not in {"text", "source_path", "domain", "chunk_offset", "score"}
            },
        )
        for c in result["chunks"]
    ]
    return SearchResponse(
        query=req.query,
        chunks=chunks,
        sub_queries=result.get("sub_queries", []),
        cache_hit=result.get("cache_hit", False),
        latency_ms=latency_ms,
    )


# ─── Domains (A7) ────────────────────────────────────────────────────────────


@router.get("/domains", response_model=DomainsResponse)
async def list_domains() -> DomainsResponse:
    """Alle Domains im Index mit Chunk-Counts (aus in-memory Counter)."""
    domains = sorted(state.domain_counts.keys())
    return DomainsResponse(
        domains=domains,
        counts=dict(state.domain_counts),
    )


# ─── Ingest (A5 + A6) ────────────────────────────────────────────────────────


def _path_check(raw: str) -> Path:
    """Validiert und normalisiert einen Pfad gegen VAULT_ROOT.

    Raises:
        HTTPException 400: Wenn der Pfad außerhalb VAULT_ROOT liegt.
    """
    try:
        p = Path(raw).resolve()
    except Exception as exc:
        raise HTTPException(400, f"Ungültiger Pfad: {raw}") from exc
    if not p.is_relative_to(VAULT_ROOT):
        raise HTTPException(400, f"Pfad liegt außerhalb VAULT_ROOT ({VAULT_ROOT}): {p}")
    return p


def _delete_chunks_for_path(file_path: Path) -> int:
    """Löscht alle Chunks einer Datei aus Qdrant. Gibt Anzahl gelöschter Chunks zurück."""
    if state.qdrant_client is None:
        return 0
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    result = state.qdrant_client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="source_path", match=MatchValue(value=str(file_path)))]
        ),
    )
    # Qdrant gibt UpdateResult zurück; operation_id ist kein Count.
    # Für den genauen Count müsste man vorher zählen — wir approximieren mit scroll.
    # Alternativ: vor dem Delete scroll count. Hier nutzen wir result.status.
    _ = result  # ignoriert – Qdrant liefert keinen gelöschten-Count direkt
    return 0  # wird durch Caller überschrieben wenn bekannt


def _count_chunks_for_path(file_path: Path) -> int:
    """Zählt aktuelle Chunks einer Datei via Scroll."""
    if state.qdrant_client is None:
        return 0
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    _, _ = state.qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="source_path", match=MatchValue(value=str(file_path)))]
        ),
        limit=1,
        with_payload=False,
        with_vectors=False,
    )
    # Qdrant scroll gibt keine Gesamtanzahl zurück. Wir nutzen count().
    count_result = state.qdrant_client.count(
        collection_name=COLLECTION_NAME,
        count_filter=Filter(
            must=[FieldCondition(key="source_path", match=MatchValue(value=str(file_path)))]
        ),
        exact=True,
    )
    return int(count_result.count)


@router.post("/ingest/file", response_model=IngestResponse)
async def ingest_file_endpoint(req: IngestRequest) -> IngestResponse:
    """Indexiert eine Markdown-Datei (Upsert-before-Delete mit run_id)."""
    if state.bge_model is None or state.qdrant_client is None:
        raise HTTPException(503, "Service nicht bereit")

    file_path = _path_check(req.file_path)
    t0 = time.perf_counter()

    if file_path.suffix.lower() == ".pdf":
        raise HTTPException(400, "PDFs via CLI: python -m titan.ingest (nicht via Service)")
    if file_path.suffix.lower() != ".md":
        raise HTTPException(400, f"Nicht unterstütztes Format: {file_path.suffix}")
    if not file_path.exists():
        raise HTTPException(404, f"Datei nicht gefunden: {file_path}")

    # A5: Markdown lesen + Frontmatter
    from titan.ingest import read_markdown  # lazy import

    content, meta = read_markdown(file_path)

    # indexed:false → alte Chunks löschen, kein Neu-Ingest
    if meta.get("_skip"):
        n_old = _count_chunks_for_path(file_path)
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        state.qdrant_client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=Filter(
                must=[FieldCondition(key="source_path", match=MatchValue(value=str(file_path)))]
            ),
        )
        domain_val: str | None = meta.get("domain")
        if domain_val and domain_val in state.domain_counts:
            state.domain_counts[domain_val] = max(0, state.domain_counts[domain_val] - n_old)
        return IngestResponse(
            file_path=str(file_path),
            domain=None,
            chunks_deleted=n_old,
            chunks_created=0,
            skipped_reason="indexed:false",
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    domain: str = meta["domain"]

    # A6: Upsert-before-Delete mit run_id
    run_id = str(uuid.uuid4())

    from titan.ingest import late_chunk_and_embed, make_point  # lazy imports

    new_chunks = late_chunk_and_embed(content, model=state.bge_model)

    from qdrant_client.models import PointStruct

    points: list[PointStruct] = [make_point(c, file_path, domain, run_id) for c in new_chunks]
    state.qdrant_client.upsert(collection_name=COLLECTION_NAME, points=points)

    # Alte Chunks derselben Datei mit anderem run_id löschen
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    n_old = _count_chunks_for_path(file_path)
    # Subtrahiere neue Chunks vom Count (die sind bereits drin)
    n_old_estimate = max(0, n_old - len(new_chunks))

    state.qdrant_client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="source_path", match=MatchValue(value=str(file_path)))],
            must_not=[FieldCondition(key="run_id", match=MatchValue(value=run_id))],
        ),
    )

    # Domain-Counter aktualisieren (A10: Cache-Invalidierung folgt nach diesem Block)
    state.domain_counts[domain] = (
        state.domain_counts.get(domain, 0) - n_old_estimate + len(new_chunks)
    )

    # A10: Cache für diese Domain invalidieren
    _invalidate_cache_for_domain(domain)

    return IngestResponse(
        file_path=str(file_path),
        domain=domain,
        chunks_deleted=n_old_estimate,
        chunks_created=len(new_chunks),
        skipped_reason=None,
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


# ─── FindRelated (A8) ────────────────────────────────────────────────────────


@router.post("/find_related", response_model=FindRelatedResponse)
async def find_related_endpoint(req: FindRelatedRequest) -> FindRelatedResponse:
    """Findet semantisch ähnliche Notes via Dense-Vektor des ersten Chunks."""
    if state.qdrant_client is None:
        raise HTTPException(503, "Service nicht bereit")

    src_path = _path_check(req.file_path)
    t0 = time.perf_counter()

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    records, _ = state.qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="source_path", match=MatchValue(value=str(src_path)))]
        ),
        limit=1,
        with_vectors=True,
        with_payload=True,
    )
    if not records:
        raise HTTPException(404, f"Keine Chunks für: {src_path}")

    anchor = records[0]
    raw_vector = anchor.vector
    dense_vec = raw_vector["dense"] if isinstance(raw_vector, dict) else raw_vector

    results = state.qdrant_client.query_points(
        collection_name=COLLECTION_NAME,
        query=dense_vec,
        using="dense",
        limit=req.top_k,
        query_filter=Filter(
            must_not=[FieldCondition(key="source_path", match=MatchValue(value=str(src_path)))]
        ),
        with_payload=True,
    )

    related = [
        Chunk(
            text=(r.payload or {}).get("text", ""),
            source_path=(r.payload or {}).get("source_path", ""),
            domain=(r.payload or {}).get("domain", ""),
            chunk_offset=int((r.payload or {}).get("chunk_offset", 0)),
            score=float(r.score),
            metadata={
                k: v
                for k, v in (r.payload or {}).items()
                if k not in {"text", "source_path", "domain", "chunk_offset"}
            },
        )
        for r in results.points
    ]

    return FindRelatedResponse(
        source_path=str(src_path),
        related=related,
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


# ─── Delete Chunks (A9) ──────────────────────────────────────────────────────


@router.delete("/chunks", response_model=DeleteChunksResponse)
async def delete_chunks(source_path: str) -> DeleteChunksResponse:
    """Löscht alle Chunks einer Datei. Aktualisiert Domain-Counter."""
    if state.qdrant_client is None:
        raise HTTPException(503, "Service nicht bereit")

    path = _path_check(source_path)

    n = _count_chunks_for_path(path)

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    # Domain vor dem Löschen ermitteln (für Counter-Update)
    records, _ = state.qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="source_path", match=MatchValue(value=str(path)))]
        ),
        limit=1,
        with_payload=["domain"],
        with_vectors=False,
    )
    domain_to_update: str | None = None
    if records:
        domain_to_update = (records[0].payload or {}).get("domain")

    state.qdrant_client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="source_path", match=MatchValue(value=str(path)))]
        ),
    )

    if domain_to_update and domain_to_update in state.domain_counts:
        state.domain_counts[domain_to_update] = max(0, state.domain_counts[domain_to_update] - n)

    return DeleteChunksResponse(source_path=str(path), chunks_deleted=n)


# ─── Cache-Invalidierung (A10) ───────────────────────────────────────────────


def _invalidate_cache_for_domain(domain: str) -> None:
    """Löscht alle Cache-Einträge für eine Domain (aggressiv, aber einfach).

    Implementierungsort hier in titan.service.routes statt titan.search,
    da die Cache-Collection-Konfiguration aus state.qdrant_client kommt.
    """
    from titan.search import CACHE_COLLECTION_NAME, CACHE_ENABLED

    if not CACHE_ENABLED or state.qdrant_client is None:
        return
    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        state.qdrant_client.delete(
            collection_name=CACHE_COLLECTION_NAME,
            points_selector=Filter(
                must=[FieldCondition(key="domain", match=MatchValue(value=domain))]
            ),
        )
        log.info("Cache für Domain '%s' invalidiert.", domain)
    except Exception as exc:
        log.warning("Cache-Invalidierung für Domain '%s' fehlgeschlagen: %s", domain, exc)
