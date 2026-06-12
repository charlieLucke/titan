"""
routes.py – FastAPI Router mit allen Endpoints
===============================================
Wird von app.py via include_router eingebunden.

Endpoints:
    GET  /health                    – Service-Status, BGE-M3 und Qdrant-Verfügbarkeit
    POST /search                    – Hybrid-Suche (A4)
    POST /ingest/file               – Markdown-Datei indexieren (A6)
    GET  /domains                   – Alle Domains mit Chunk-Counts (A7)
    POST /find_related              – Semantisch ähnliche Notes (A8)
    DELETE /chunks                  – Chunks einer Datei löschen (A9)
    GET  /notes                     – Alle indexierten Notes mit Chunk-Counts (A11)
    GET  /domains/{domain}/notes    – Notes einer einzelnen Domain (A12)
    GET  /stats                     – Laufzeit-Metriken (Uptime, Counts, Cache, Latenz)
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import time
import uuid
from pathlib import Path
from typing import Literal

import torch
from fastapi import APIRouter, HTTPException

from titan.config import settings
from titan.service.schemas import (
    Chunk,
    DeleteChunksResponse,
    DomainsResponse,
    FindRelatedRequest,
    FindRelatedResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    NoteInfo,
    NotesResponse,
    SearchRequest,
    SearchResponse,
    StatsResponse,
)
from titan.service.state import state
from titan.service.stats import build_stats

# Aliase auf die zentralen Settings (Import-Zeit-Snapshot; Tests patchen die
# Modul-Attribute, der Service liest Konfiguration ohnehin nur beim Start).
VAULT_ROOT: Path = settings.vault_root.resolve()

log = logging.getLogger(__name__)

COLLECTION_NAME: str = settings.collection_name

router = APIRouter()


# ─── Health ──────────────────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Service-Status: BGE-M3 geladen, Qdrant erreichbar, VRAM-Nutzung.

    Alle Handler in diesem Router sind bewusst sync (def): FastAPI führt sie im
    Threadpool aus. Als async def würde der synchrone GPU-/Ollama-/Qdrant-Code
    den Event-Loop blockieren — während eines Ingests wäre der ganze Service
    (inkl. /health) unerreichbar.
    """
    bge_loaded = state.bge_model is not None

    qdrant_reachable = False
    if state.qdrant_client is not None:
        try:
            state.qdrant_client.get_collection(COLLECTION_NAME)
            qdrant_reachable = True
        except Exception:
            qdrant_reachable = False

    vram_used_mb: int | None = None
    if bge_loaded and state.bge_model is not None:
        with contextlib.suppress(Exception):
            vram_used_mb = int(torch.cuda.memory_allocated() / 1024 / 1024)
    # colbert_dim is measured once at startup and cached in state (avoids per-request encode).
    colbert_dim: int | None = state.colbert_dim

    overall: Literal["ok", "degraded"] = "ok" if (bge_loaded and qdrant_reachable) else "degraded"

    return HealthResponse(
        status=overall,
        bge_loaded=bge_loaded,
        qdrant_reachable=qdrant_reachable,
        vram_used_mb=vram_used_mb,
        collection_name=COLLECTION_NAME,
        colbert_dim=colbert_dim,
    )


# ─── Stats ───────────────────────────────────────────────────────────────────


@router.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    """Laufzeit-Metriken: Uptime, Chunk-/Domain-Counts, Cache-Hit-Rate, Such-Latenz.

    Bewusst ohne 503-Guard — der Endpoint antwortet auch im degraded-Zustand (dann
    mit Nullwerten / cache_entries=None). Zähler sind in-memory und resetten bei
    jedem Neustart.
    """
    now = time.monotonic()

    # lazy import vermeidet Circular-Import und nutzt dieselbe Cache-Konfiguration
    from titan.search import CACHE_COLLECTION_NAME, CACHE_ENABLED

    cache_entries: int | None = None
    if CACHE_ENABLED and state.qdrant_client is not None:
        with contextlib.suppress(Exception):
            cache_entries = int(
                state.qdrant_client.count(collection_name=CACHE_COLLECTION_NAME, exact=True).count
            )

    last_ingest_age: int | None = (
        int(now - state.last_ingest_at) if state.last_ingest_at is not None else None
    )

    return build_stats(
        uptime_seconds=int(now - state.started_at),
        collection_name=COLLECTION_NAME,
        domain_counts=state.domain_counts,
        search_count=state.search_count,
        cache_hit_count=state.cache_hit_count,
        latencies=list(state.search_latencies_ms),
        cache_enabled=CACHE_ENABLED,
        cache_entries=cache_entries,
        last_ingest_age_seconds=last_ingest_age,
    )


# ─── Search (A4) ────────────────────────────────────────────────────────────


@router.post("/search", response_model=SearchResponse)
def search_endpoint(req: SearchRequest) -> SearchResponse:
    """Hybrid-Suche: Query-Decompose → BGE-M3 → RRF → (Epic-5B Cache).

    work_lock serialisiert die GPU-Nutzung gegen parallele Requests und Ingests;
    latency_ms enthält damit auch die Wartezeit auf das Lock (Caller-Sicht).
    """
    if state.bge_model is None or state.qdrant_client is None:
        raise HTTPException(503, "Service nicht bereit (BGE-M3 oder Qdrant nicht geladen)")

    from titan.search import search  # lokaler Import vermeidet Circular-Import-Risiko

    t0 = time.perf_counter()
    with state.work_lock:
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

        # Observability: record for GET /stats (in-memory, best-effort).
        state.search_count += 1
        if result.get("cache_hit", False):
            state.cache_hit_count += 1
        state.search_latencies_ms.append(latency_ms)

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
def list_domains() -> DomainsResponse:
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


def _count_chunks_for_path(file_path: Path) -> int:
    """Zählt Chunks einer Datei in Qdrant."""
    if state.qdrant_client is None:
        return 0
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    count_result = state.qdrant_client.count(
        collection_name=COLLECTION_NAME,
        count_filter=Filter(
            must=[FieldCondition(key="source_path", match=MatchValue(value=str(file_path)))]
        ),
        exact=True,
    )
    return int(count_result.count)


@router.post("/ingest/file", response_model=IngestResponse)
def ingest_file_endpoint(req: IngestRequest) -> IngestResponse:
    """Indexiert eine Markdown-Datei (Upsert-before-Delete mit run_id).

    work_lock umschließt Embedding (GPU) und die Upsert/Delete/Counter-Sequenz:
    zwei verzahnte Ingests derselben Datei würden sich sonst über das
    run_id-Delete gegenseitig die frischen Chunks löschen.
    """
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
    import yaml

    from titan.ingest import read_markdown  # lazy import

    try:
        content, meta = read_markdown(file_path)
    except ValueError as exc:
        # z.B. fehlendes/leeres 'domain'-Frontmatter: Client-Fehler, kein 500 —
        # der Watcher behandelt 4xx als permanent und requeued nicht sinnlos.
        raise HTTPException(422, str(exc)) from exc
    except yaml.YAMLError as exc:
        raise HTTPException(422, f"Ungültiges YAML-Frontmatter in {file_path}: {exc}") from exc

    with state.work_lock:
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

        # Compute content hash once per ingest (before chunking/embedding — same raw bytes).
        content_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()

        # A6: Upsert-before-Delete mit run_id
        run_id = str(uuid.uuid4())

        from titan.ingest import late_chunk_and_embed, make_point  # lazy imports

        new_chunks = late_chunk_and_embed(content, model=state.bge_model)

        # Count BEFORE upsert so chunks_deleted reflects the true size of the previous version.
        n_before = _count_chunks_for_path(file_path)

        from qdrant_client.models import PointStruct

        points: list[PointStruct] = [
            make_point(c, file_path, domain, run_id, content_hash) for c in new_chunks
        ]
        state.qdrant_client.upsert(collection_name=COLLECTION_NAME, points=points)

        # Delete old chunks (different run_id) for the same file.
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        state.qdrant_client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=Filter(
                must=[FieldCondition(key="source_path", match=MatchValue(value=str(file_path)))],
                must_not=[FieldCondition(key="run_id", match=MatchValue(value=run_id))],
            ),
        )

        # Domain-Counter aktualisieren (A10: Cache-Invalidierung folgt nach diesem Block)
        state.domain_counts[domain] = (
            state.domain_counts.get(domain, 0) - n_before + len(new_chunks)
        )

        # A10: Cache für diese Domain invalidieren
        from titan.search import invalidate_domain_cache

        invalidate_domain_cache(state.qdrant_client, domain)

        state.last_ingest_at = time.monotonic()  # for GET /stats "last ingest age"

    return IngestResponse(
        file_path=str(file_path),
        domain=domain,
        chunks_deleted=n_before,
        chunks_created=len(new_chunks),
        skipped_reason=None,
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


# ─── FindRelated (A8) ────────────────────────────────────────────────────────


@router.post("/find_related", response_model=FindRelatedResponse)
def find_related_endpoint(req: FindRelatedRequest) -> FindRelatedResponse:
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
def delete_chunks(source_path: str) -> DeleteChunksResponse:
    """Löscht alle Chunks einer Datei. Aktualisiert Domain-Counter."""
    if state.qdrant_client is None:
        raise HTTPException(503, "Service nicht bereit")

    path = _path_check(source_path)

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    with state.work_lock:
        n = _count_chunks_for_path(path)

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
            state.domain_counts[domain_to_update] = max(
                0, state.domain_counts[domain_to_update] - n
            )

    return DeleteChunksResponse(source_path=str(path), chunks_deleted=n)


# ─── Notes (A11) ─────────────────────────────────────────────────────────────


@router.get("/notes", response_model=NotesResponse)
def list_notes() -> NotesResponse:
    """Listet alle indexierten Notes, gruppiert nach source_path.

    Scrollt die gesamte Collection und aggregiert pro Datei die Chunk-Anzahl
    und Domain. Für einen persönlichen Vault (einige hundert/tausend Chunks)
    unkritisch.
    """
    if state.qdrant_client is None:
        raise HTTPException(503, "Service nicht bereit")

    counts: dict[str, int] = {}
    domains: dict[str, str] = {}
    hashes: dict[str, str | None] = {}
    offset = None
    while True:
        records, offset = state.qdrant_client.scroll(
            collection_name=COLLECTION_NAME,
            limit=256,
            offset=offset,
            with_payload=["source_path", "domain", "content_hash"],
            with_vectors=False,
        )
        for record in records:
            payload = record.payload or {}
            source_path = str(payload.get("source_path", ""))
            if not source_path:
                continue
            counts[source_path] = counts.get(source_path, 0) + 1
            domains.setdefault(source_path, str(payload.get("domain", "")))
            hashes.setdefault(source_path, payload.get("content_hash"))
        if offset is None:
            break

    notes = [
        NoteInfo(
            source_path=sp, domain=domains[sp], chunk_count=counts[sp], content_hash=hashes[sp]
        )
        for sp in sorted(counts)
    ]
    return NotesResponse(notes=notes, total=len(notes))


# ─── Domain Notes (A12) ──────────────────────────────────────────────────────


@router.get("/domains/{domain}/notes", response_model=NotesResponse)
def list_domain_notes(domain: str) -> NotesResponse:
    """Listet alle Notes der angegebenen Domain, gruppiert nach source_path.

    Filtert die Collection via Qdrant scroll_filter auf das domain-Payload-Feld.
    Unbekannte / leere Domains geben 200 mit notes=[], total=0 zurück (kein 404).
    """
    if state.qdrant_client is None:
        raise HTTPException(503, "Service nicht bereit")

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    counts: dict[str, int] = {}
    domains: dict[str, str] = {}
    hashes: dict[str, str | None] = {}
    offset = None
    while True:
        records, offset = state.qdrant_client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[FieldCondition(key="domain", match=MatchValue(value=domain))]
            ),
            limit=256,
            offset=offset,
            with_payload=["source_path", "domain", "content_hash"],
            with_vectors=False,
        )
        for record in records:
            payload = record.payload or {}
            source_path = str(payload.get("source_path", ""))
            if not source_path:
                continue
            counts[source_path] = counts.get(source_path, 0) + 1
            domains.setdefault(source_path, str(payload.get("domain", "")))
            hashes.setdefault(source_path, payload.get("content_hash"))
        if offset is None:
            break

    notes = [
        NoteInfo(
            source_path=sp, domain=domains[sp], chunk_count=counts[sp], content_hash=hashes[sp]
        )
        for sp in sorted(counts)
    ]
    return NotesResponse(notes=notes, total=len(notes))
