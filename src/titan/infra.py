"""
infra.py – Gemeinsame Ressourcen-Factories (BGE-M3, Qdrant-Client)
===================================================================
Eine Implementierung statt drei Kopien (vorher je eine in ingest.py,
search.py und app.py). Die Module behalten dünne Wrapper für ihr
spezifisches Verhalten (CLI: sys.exit, Collection-Check, Singleton) —
Konstruktion und Konfiguration leben nur hier.
"""

from __future__ import annotations

import logging
from typing import Any

from titan.config import settings

log = logging.getLogger(__name__)


def load_bge_m3_model() -> Any:
    """Lädt das BGE-M3 Modell via FlagEmbedding mit CUDA (FP16).

    use_fp16=True: Halbiert VRAM-Verbrauch, vernachlässigbare Qualitätseinbuße.

    Returns:
        BGEM3FlagModel-Instanz.

    Raises:
        RuntimeError: Wenn FlagEmbedding nicht installiert ist. CLI-Caller
            fangen das und beenden mit sys.exit(1); der Service-Lifespan
            lässt die Exception zum Startup-Abbruch durchschlagen.
    """
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        raise RuntimeError("FlagEmbedding fehlt. Installation: uv add flagembedding") from exc

    log.info("Lade BAAI/bge-m3 auf CUDA (FP16) …")
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device="cuda")
    log.info("BGE-M3 bereit.")
    return model


def make_qdrant_client() -> Any:
    """Erstellt einen Qdrant-Client via gRPC aus den zentralen Settings.

    gRPC ist schneller als REST bei großen Batch-Uploads. Macht bewusst
    keinen Collection-Check — den ergänzen die Caller je nach Kontext
    (CLI: sys.exit bei fehlender Collection, Service: Dimension-Check).

    Returns:
        QdrantClient-Instanz.
    """
    from qdrant_client import QdrantClient

    masked = (settings.qdrant_api_key[:4] + "***") if settings.qdrant_api_key else "—"
    log.info(
        "Verbinde mit Qdrant gRPC: %s:%d (key: %s)",
        settings.qdrant_host,
        settings.qdrant_grpc_port,
        masked,
    )
    return QdrantClient(
        host=settings.qdrant_host,
        grpc_port=settings.qdrant_grpc_port,
        prefer_grpc=True,
        api_key=settings.qdrant_api_key or None,
        https=False,
        check_compatibility=False,
    )
