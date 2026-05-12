"""
state.py – Singleton-Holder für Service-weite Ressourcen
=========================================================
Hält BGE-M3-Modell, Qdrant-Client und GPU-Lock als Modul-globale Instanz.
Wird im Lifespan von app.py befüllt und freigegeben.
"""

from __future__ import annotations

from collections import Counter
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


state = ServiceState()
