"""
state.py – Singleton-Holder für Service-weite Ressourcen
=========================================================
Hält BGE-M3-Modell, Qdrant-Client und GPU-Lock als Modul-globale Instanz.
Wird im Lifespan von app.py befüllt und freigegeben.
"""

from __future__ import annotations

import time
from collections import Counter, deque
from typing import Any


class ServiceState:
    """Hält alle lang-lebigen Service-Ressourcen."""

    def __init__(self) -> None:
        self.bge_model: Any | None = None
        self.qdrant_client: Any | None = None
        self.gpu_lock: Any | None = None
        # Domain-Counter: {domain: chunk_count} – inkrementell aktualisiert,
        # beim Start via Voll-Scan initialisiert (Task A7).
        self.domain_counts: Counter[str] = Counter()
        # Cached ColBERT output dimension, set once in lifespan after BGE-M3 loads.
        # Avoids a GPU encode on every /health request.
        self.colbert_dim: int | None = None
        # Observability counters (in-memory, reset on restart) — surfaced by GET /stats.
        self.started_at: float = time.monotonic()
        self.search_count: int = 0
        self.cache_hit_count: int = 0
        self.search_latencies_ms: deque[int] = deque(maxlen=256)
        self.last_ingest_at: float | None = None  # time.monotonic() of the last real ingest


state = ServiceState()
