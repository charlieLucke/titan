"""
init_col.py – Qdrant Collection initialisieren
===============================================
Erstellt die Hauptkollektion mit den korrekten Vektorkonfigurationen:
  - dense:   1024-dim Cosine (BGE-M3 dense)
  - colbert: 1024-dim MaxSim Multivector (BGE-M3 ColBERT, seit FlagEmbedding 1.3)
  - sparse:  SparseVector (BGE-M3 sparse)

Aufruf:
    python -m titan.tools.init_col
    python -m titan.tools.init_col --recreate   # löscht bestehende Collection zuerst

Umgebungsvariablen (via .env):
    QDRANT_HOST         – Standard: localhost
    QDRANT_GRPC_PORT    – Standard: 6334
    QDRANT_API_KEY      – optional
    COLLECTION_NAME     – Standard: mein_wissen
"""

from __future__ import annotations

import argparse
import logging

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    MultiVectorComparator,
    MultiVectorConfig,
    SparseVectorParams,
    VectorParams,
)

from titan.config import settings

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Konfiguration ───────────────────────────────────────────────────────────
QDRANT_HOST: str = settings.qdrant_host
QDRANT_GRPC_PORT: int = settings.qdrant_grpc_port
QDRANT_API_KEY: str = settings.qdrant_api_key
COLLECTION_NAME: str = settings.collection_name


def init_collection(recreate: bool = False) -> None:
    """Erstellt (oder recreated) die Qdrant-Hauptkollektion.

    Args:
        recreate: Wenn True, wird die bestehende Collection gelöscht und neu erstellt.
    """
    client = QdrantClient(
        host=QDRANT_HOST,
        grpc_port=QDRANT_GRPC_PORT,
        prefer_grpc=True,
        api_key=QDRANT_API_KEY or None,
        https=False,
        check_compatibility=False,
    )

    if recreate:
        log.info("Lösche bestehende Collection '%s'...", COLLECTION_NAME)
        client.delete_collection(collection_name=COLLECTION_NAME)
        log.info("Collection gelöscht.")

    log.info("Erstelle Collection '%s'...", COLLECTION_NAME)
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            "dense": VectorParams(size=1024, distance=Distance.COSINE),
            # ColBERT-Dimension 1024 seit FlagEmbedding 1.3 (vorher 128).
            # Diese Collection muss exakt zur installierten FE-Version passen.
            "colbert": VectorParams(
                size=1024,
                distance=Distance.COSINE,
                multivector_config=MultiVectorConfig(
                    comparator=MultiVectorComparator.MAX_SIM,
                ),
            ),
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(),
        },
    )
    log.info("Collection '%s' erfolgreich erstellt.", COLLECTION_NAME)


def main() -> None:
    """CLI entry point für python -m titan.tools.init_col."""
    parser = argparse.ArgumentParser(
        description="Titan – Qdrant Collection initialisieren",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Bestehende Collection löschen und neu erstellen",
    )
    args = parser.parse_args()

    init_collection(recreate=args.recreate)


if __name__ == "__main__":
    main()
