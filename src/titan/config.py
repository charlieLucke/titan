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

    # ── Vault (Service-Ingest, POST /ingest/file) ─────────────────────────
    vault_root: Path = Path("/mnt/f/vault")

    # ── Ollama (Query-Decomposition, generate/evaluate) ──────────────────
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "phi4:latest"
    ollama_timeout: int = 120

    # ── LLM-Judge (evaluate.py) ───────────────────────────────────────────
    judge_model: str = "phi4:latest"
    judge_timeout: int = 60

    # ── Epic 5B: Semantic Caching ─────────────────────────────────────────
    cache_enabled: bool = True
    cache_collection_name: str = "query_cache"
    cache_threshold: float = 0.95
    cache_ttl_seconds: int = 604_800  # 7 Tage
    cache_top_k: int = 1

    # ── GPU-Lock (systemweiter VRAM-Schutz, siehe utils.acquire_gpu_lock) ─
    gpu_lock_path: str = "/tmp/bge_m3.lock"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = TitanSettings()
