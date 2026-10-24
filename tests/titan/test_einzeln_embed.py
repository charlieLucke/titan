"""Tests fuer das Einzel-Embedding mit Kopfzeile. Ohne GPU: das Modell ist ein Stub."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from titan import ingest
from titan.ingest import MAX_COLBERT_ROWS, chunk_and_embed, embed_chunks, mit_kopfzeile
from titan.models import Chunk


class StubModel:
    def __init__(self) -> None:
        self.texte: list[str] = []
        self.max_length: int | None = None

    def encode(self, texte: list[str], max_length: int, **_: Any) -> dict[str, Any]:
        self.texte, self.max_length = texte, max_length
        return {
            "dense_vecs": np.ones((len(texte), 4)),
            "lexical_weights": [{7: 0.5} for _ in texte],
            "colbert_vecs": [np.ones((3, 4)) for _ in texte],
        }


def _chunk(text: str = "CPU: Intel Pentium J3710") -> Chunk:
    return Chunk(
        text=text,
        source="hub.md",
        chunk_id=0,
        header="Hardware",
        section_id="x",
        document_title="Hub-Mini-PC",
    )


def test_kopfzeile_steht_vor_dem_text_und_der_text_bleibt_unveraendert() -> None:
    chunk = _chunk()
    assert mit_kopfzeile(chunk) == "Hub-Mini-PC › Hardware\n\nCPU: Intel Pentium J3710"
    assert chunk.text == "CPU: Intel Pentium J3710"


def test_einzeln_bettet_mit_kopfzeile_ein_und_bleibt_unter_qdrants_grenze(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingest.settings, "late_chunking", False)
    model = StubModel()
    (chunk,) = embed_chunks(model, [_chunk()])

    assert model.texte == ["Hub-Mini-PC › Hardware\n\nCPU: Intel Pentium J3710"]
    assert model.max_length == MAX_COLBERT_ROWS
    assert chunk.dense is not None and chunk.colbert is not None
    assert chunk.sparse == {"7": 0.5}  # Qdrant-Schluessel als String wie bei late_chunk_embed


def test_ohne_titel_wird_der_dateiname_zur_kopfzeile_nicht_ein_platzhalter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingest.settings, "late_chunking", False)
    model = StubModel()
    chunk_and_embed("## Hardware\n\nCPU: J3710\n", model, Path("/vault/notes/hub-mini-pc.md"))
    assert model.texte[0].startswith("hub-mini-pc › Hardware")


def test_late_chunking_bleibt_per_einstellung_erreichbar(monkeypatch: pytest.MonkeyPatch) -> None:
    aufgerufen: list[int] = []
    monkeypatch.setattr(ingest.settings, "late_chunking", True)
    monkeypatch.setattr(ingest, "late_chunk_embed", lambda _m, c: aufgerufen.append(len(c)) or c)
    embed_chunks(StubModel(), [_chunk()])
    assert aufgerufen == [1]
