"""
models.py – Typisierte Domänenmodelle der Ingest-Pipeline
==========================================================
Ersetzt die früheren dict[str, Any]-Chunks: unter mypy strict prüfte dort
faktisch nichts (alles Any), und ein Tippfehler in einem Key fiel erst zur
Laufzeit auf. Search-Ergebnisse bleiben Dicts — sie sind API-förmig und
laufen durch die Pydantic-Schemas in titan.service.schemas.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    """Ein semantischer Abschnitt einer Quelldatei, optional mit Embeddings.

    chunk_markdown() erzeugt Chunks ohne Vektoren; late_chunk_embed() befüllt
    dense/sparse/colbert in-place — die Vektorfelder sind deshalb Optionals.
    make_point() verlangt befüllte Vektoren und wirft sonst ValueError.
    """

    text: str
    source: str
    chunk_id: int
    header: str
    section_id: str
    document_title: str
    dense: list[float] | None = None
    sparse: dict[str, float] | None = None
    colbert: list[list[float]] | None = None
