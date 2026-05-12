"""
app.py – Titan FastAPI Service
==============================
Startet den Service mit BGE-M3 als Singleton im VRAM.
Läuft auf 127.0.0.1:8765, nur lokal erreichbar.

Aufruf:
    uv run python -m titan service
    uv run python -m titan service --port 8765
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import torch
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from titan.service.state import state
from titan.utils import acquire_gpu_lock

load_dotenv()

log = logging.getLogger(__name__)

COLLECTION_NAME: str = os.getenv("COLLECTION_NAME", "mein_wissen")
QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
QDRANT_GRPC_PORT: int = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
QDRANT_API_KEY: str | None = os.getenv("QDRANT_API_KEY")


def _load_bge_m3() -> Any:
    """Lädt BGE-M3 (FP16) auf CUDA."""
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        raise RuntimeError("FlagEmbedding fehlt. Installation: uv add flagembedding") from exc

    log.info("Lade BAAI/bge-m3 auf CUDA (FP16) …")
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device="cuda")
    log.info("BGE-M3 bereit.")
    return model


def _make_qdrant_client() -> Any:
    """Erstellt einen Qdrant-Client via gRPC."""
    from qdrant_client import QdrantClient

    log.info("Verbinde mit Qdrant gRPC: %s:%d", QDRANT_HOST, QDRANT_GRPC_PORT)
    return QdrantClient(
        host=QDRANT_HOST,
        grpc_port=QDRANT_GRPC_PORT,
        prefer_grpc=True,
        api_key=QDRANT_API_KEY if QDRANT_API_KEY else None,
        https=False,
        check_compatibility=False,
    )


def _validate_embedding_dimension(client: Any) -> None:
    """Prüft, dass die installierte FlagEmbedding-Version zur Collection passt.

    Konkret: Embedding eines Dummy-Strings, Vergleich von len(colbert_vec[0])
    mit collection.vectors_config["colbert"].size. Mismatch → RuntimeError.
    """
    if state.bge_model is None:
        raise RuntimeError("BGE-M3 nicht geladen – kann Dimension nicht prüfen")

    log.info("Prüfe ColBERT-Dimension gegen Qdrant-Collection …")
    with torch.no_grad():
        output = state.bge_model.encode(
            ["dimension check"],
            return_dense=False,
            return_sparse=False,
            return_colbert_vecs=True,
            batch_size=1,
        )
    actual_dim = len(output["colbert_vecs"][0][0])

    collection_info = client.get_collection(COLLECTION_NAME)
    # vectors_config kann ein Dict (named vectors) oder VectorsConfig (single) sein
    vectors_cfg = collection_info.config.params.vectors
    if isinstance(vectors_cfg, dict):
        colbert_cfg = vectors_cfg.get("colbert")
        expected_dim = colbert_cfg.size if colbert_cfg else None
    else:
        expected_dim = None

    if expected_dim is not None and actual_dim != expected_dim:
        raise RuntimeError(
            f"ColBERT-Dimension-Mismatch: FlagEmbedding liefert {actual_dim}d, "
            f"Collection erwartet {expected_dim}d. "
            f"Collection mit init_col.py --recreate neu anlegen."
        )
    log.info("ColBERT-Dimension OK: %dd", actual_dim)


def _init_domain_counts(client: Any) -> Counter[str]:
    """Initialisiert den Domain-Counter per Voll-Scan beim Service-Start."""
    log.info("Initialisiere Domain-Counter via Qdrant-Scroll …")
    counts: Counter[str] = Counter()
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=None,
            limit=1000,
            offset=offset,
            with_payload=["domain"],
            with_vectors=False,
        )
        for rec in records:
            domain = (rec.payload or {}).get("domain")
            if domain:
                counts[domain] += 1
        if offset is None:
            break
    log.info("Domain-Counter: %s", dict(counts))
    return counts


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: Ressourcen laden. Shutdown: Ressourcen freigeben."""
    # ── Startup ──────────────────────────────────────────────────────────
    log.info("Titan Service startet …")

    state.gpu_lock = acquire_gpu_lock()

    state.bge_model = _load_bge_m3()
    state.qdrant_client = _make_qdrant_client()

    try:
        _validate_embedding_dimension(state.qdrant_client)
    except Exception as exc:
        log.critical("Startup-Fehler (Dimension-Check): %s", exc)
        raise

    try:
        state.domain_counts = _init_domain_counts(state.qdrant_client)
    except Exception as exc:
        log.warning("Domain-Counter-Init fehlgeschlagen (Service läuft weiter): %s", exc)
        state.domain_counts = Counter()

    log.info("Titan Service bereit.")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    log.info("Titan Service fährt herunter …")
    del state.bge_model
    state.bge_model = None
    torch.cuda.empty_cache()
    if state.gpu_lock is not None:
        lock = state.gpu_lock
        state.gpu_lock = None
        with contextlib.suppress(Exception):
            if hasattr(lock, "close"):
                lock.close()
    log.info("Titan Service gestoppt.")


def create_app() -> FastAPI:
    """Erstellt die FastAPI-App. Wird auch in Tests genutzt."""
    app = FastAPI(
        title="Titan RAG Service",
        description="Lokaler RAG-Service mit BGE-M3 und Qdrant",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Router werden nach Fertigstellung der Endpoints importiert
    from titan.service import routes

    app.include_router(routes.router)
    return app


def serve(port: int = 8765) -> None:
    """Startet den uvicorn-Server. Nur für Production-Use."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=port, log_config=None)
