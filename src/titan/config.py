"""
config.py – Zentrale Laufzeit-Konfiguration für Titan
======================================================
Einzige Stelle, die Umgebungsvariablen liest (vorher: vierfach duplizierte
os.getenv-Blöcke in ingest.py, search.py, app.py und routes.py plus weitere
Kopien in den Eval-/Tool-Modulen — Drift-Gefahr bei jedem neuen Default).

Die Variablennamen bleiben unpräfixiert (QDRANT_HOST, COLLECTION_NAME, …),
damit bestehende .env-Dateien und systemd-Units unverändert weiterlaufen.
pydantic-settings matcht Feldnamen case-insensitiv gegen die Umgebung.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TitanSettings(BaseSettings):
    """Runtime configuration loaded from environment variables or .env."""

    # ── Qdrant ────────────────────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_grpc_port: int = 6334
    qdrant_api_key: str = ""
    collection_name: str = "mein_wissen"

    # ── Ingest ────────────────────────────────────────────────────────────
    max_workers: int = 12
    embed_batch_size: int = 32
    # Path-Traversal-Schutz: CLI-Ingest nur aus diesem Verzeichnis erlaubt
    ingest_base_dir: Path = Path("/mnt/f/data/titan-input")
    late_chunk_window_tokens: int = 7800
    # Aus seit 24.10.2026: am Goldset (165 Fragen) fand Late Chunking die richtige
    # Stelle unter den ersten fuenf in 38 %, jeder Chunk einzeln mit Kopfzeile in
    # 82 %. Begruendung in docs/ai/DECISIONS.md. Nach dem Umschalten den Vault neu
    # ingestieren — die beiden Vektorarten vertragen sich nicht in einem Index.
    late_chunking: bool = False
    # Zuschnitt der Chunks. Standardwerte = bisheriges Verhalten (H1/H2, 2800/400).
    # Tiefe 3 schneidet zusaetzlich an "###". Nach jeder Aenderung den Vault neu
    # einlesen — der Index enthaelt sonst Chunks aus zwei verschiedenen Zuschnitten.
    chunk_header_tiefe: int = Field(default=2, ge=1, le=6)
    chunk_max_zeichen: int = Field(default=2_800, ge=600)
    chunk_overlap_zeichen: int = 400

    # ── Vault (Service-Ingest, POST /ingest/file) ─────────────────────────
    vault_root: Path = Path("/mnt/f/vault")

    # ── Ollama (Query-Decomposition, generate/evaluate) ──────────────────
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "phi4:latest"
    ollama_timeout: int = 120

    # ── LLM-Judge (evaluate.py) ───────────────────────────────────────────
    judge_model: str = "phi4:latest"
    judge_timeout: int = 60

    # ── Cross-Encoder (titan.rerank) ──────────────────────────────────────
    # Leer = aus. "qwen3-reranker-4b" kostet rund 8 GB VRAM, die Phi-4 dann fehlen;
    # der Dienst laedt ihn beim Start. Gemessen und begruendet in docs/ai/DECISIONS.md.
    rerank_model: str = ""
    rerank_pool: int = 10
    rerank_max_tokens: int = 512

    # ── Epic 5B: Semantic Caching ─────────────────────────────────────────
    cache_enabled: bool = True
    cache_collection_name: str = "query_cache"
    cache_threshold: float = 0.95
    cache_ttl_seconds: int = 604_800  # 7 Tage
    cache_top_k: int = 1

    # ── GPU-Lock (systemweiter VRAM-Schutz, siehe utils.acquire_gpu_lock) ─
    # noqa-Begruendung: der Lock muss systemweit sein — jeder Prozess, der die GPU
    # belegt, muss denselben Pfad sehen. Ein Pfad unter $TMPDIR waere pro Nutzer
    # verschieden und der Lock damit wirkungslos. Konfigurierbar, falls das stoert.
    gpu_lock_path: str = "/tmp/bge_m3.lock"  # noqa: S108

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def _overlap_kleiner_als_chunk(self) -> TitanSettings:
        """Der Overlap muss kleiner als der Chunk sein, sonst kommt das Splitten nicht voran."""
        if not 0 <= self.chunk_overlap_zeichen < self.chunk_max_zeichen:
            raise ValueError(
                f"chunk_overlap_zeichen ({self.chunk_overlap_zeichen}) muss zwischen 0 und "
                f"chunk_max_zeichen ({self.chunk_max_zeichen}) liegen (exklusiv)"
            )
        return self


settings = TitanSettings()
