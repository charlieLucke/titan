"""
utils.py – Gemeinsame Hilfsfunktionen für Titan
================================================
Zentralisiert Funktionen, die in mehreren Modulen verwendet werden.
Verhindert, dass Security-Fixes auseinanderlaufen.

Enthält:
  - acquire_gpu_lock() – systemweiter VRAM-Schutz gegen parallele BGE-M3-Instanzen
  - stable_uuid()      – deterministische Qdrant-IDs gegen Duplikate bei Retry
  - cache_uuid()       – deterministische UUIDs für Epic-5B Semantic Cache
  - sanitize()         – Prompt-Injection-Schutz für Chunk-Felder und Query

Note: Epic 5A (Contextual Retrieval) wurde beim Port aus RAG_System entfernt.
      Referenz: ~/projects/RAG_System/execution/utils.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import uuid

from titan.config import settings

log = logging.getLogger(__name__)

# Gemeinsamer Lock-Pfad für alle BGE-M3-nutzenden Module.
# Wert in .env setzen: GPU_LOCK_PATH=/tmp/bge_m3.lock
GPU_LOCK_PATH = settings.gpu_lock_path

# fcntl ist Unix-only – auf Windows entfällt der harte GPU-Lock
_FCNTL_AVAILABLE: bool = importlib.util.find_spec("fcntl") is not None


# ─── GPU-Lock ────────────────────────────────────────────────────────────────


def acquire_gpu_lock() -> object | None:
    """Verhindert, dass ingest.py und search.py gleichzeitig BGE-M3 in den VRAM laden.

    Beide Module nutzen denselben Lock-Pfad (GPU_LOCK_PATH) – der Lock
    ist daher systemweit wirksam, nicht nur innerhalb eines Modultyps.

    Returns:
        Das offene Lock-File-Handle. Es muss bis zum Prozessende offen
        bleiben, damit das OS-Lock aktiv bleibt. Auf Windows: None.

    Raises:
        RuntimeError: Wenn BGE-M3 bereits durch einen anderen Prozess gehalten wird.
    """
    if not _FCNTL_AVAILABLE:
        log.warning(
            "fcntl nicht verfügbar (Windows?) – GPU-Lock deaktiviert. "
            "Parallele Instanzen können VRAM-OOM verursachen."
        )
        return None

    lf = open(GPU_LOCK_PATH, "w")  # noqa: SIM115
    try:
        import fcntl as _fcntl  # narrowed import for type checker

        _fcntl.flock(lf, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except BlockingIOError:
        lf.close()
        raise RuntimeError(
            "BGE-M3 bereits aktiv (ingest.py oder search.py läuft) – "
            "bitte warten bis der laufende Prozess abgeschlossen ist."
        ) from None
    log.info("GPU-Lock erworben (%s).", GPU_LOCK_PATH)
    return lf


# ─── Stabile Qdrant-IDs ──────────────────────────────────────────────────────


def stable_uuid(source: str, chunk_id: int) -> str:
    """Deterministische UUID aus (source, chunk_id) via SHA-256.

    Verhindert Duplikate bei Pipeline-Retry: Qdrant-Upsert mit gleicher ID
    überschreibt den existierenden Datenpunkt, statt ihn doppelt einzufügen.

    Args:
        source: Quelldatei-Pfad oder -Name.
        chunk_id: Numerischer Offset des Chunks innerhalb der Quelle.

    Returns:
        UUID als String (lowercase hex mit Bindestrichen).
    """
    key = f"{source}::{chunk_id}"
    h = hashlib.sha256(key.encode()).hexdigest()
    return str(uuid.UUID(h[:32]))


def cache_uuid(sub_query: str, domain: str) -> str:
    """Deterministische UUID aus (sub_query, domain) via SHA-256 für Epic 5B.

    Trennt Cache-Semantik sauber von Ingest-Semantik.

    Args:
        sub_query: Die (Teil-)Query die gecacht werden soll.
        domain: Domain-Filter der Query.

    Returns:
        UUID als String (lowercase hex mit Bindestrichen).
    """
    key = f"cache::{domain}::{sub_query}"
    h = hashlib.sha256(key.encode()).hexdigest()
    return str(uuid.UUID(h[:32]))


# ─── Prompt-Injection-Schutz ─────────────────────────────────────────────────


def sanitize(val: str, max_len: int = 2000) -> str:
    """Entschärft strukturelle Prompt-Separatoren in extern kontrollierten Strings.

    Verhindert Indirect Prompt Injection: Ein manipuliertes Dokument könnte
    Anweisungen wie '=== NEUE ANWEISUNG ===' einschleusen.

    Wird in generate.py für text/header/source/query verwendet.

    Args:
        val: Der zu bereinigende String.
        max_len: Maximale Länge nach dem Truncate (Standard 2000).

    Returns:
        Bereinigter String mit entschärften Separatoren.
    """
    val = val[:max_len]
    val = val.replace("=== ", "~~~ ").replace("---", "–––")
    return val
