"""
Pure-Function-Tests für den Pipeline-Kern (P3.1) — kein GPU, kein Qdrant.

Abgedeckt: chunk_markdown (Header-Splitting, Sub-Chunk-Overlap),
_group_into_windows (Fenstergrenzen mit Fake-Tokenizer), rrf_fusion und
das decompose_query-Parsing (Ollama-Antwortvarianten, gemockt).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from titan.ingest import MAX_SUPER_CHUNK_CHARS, _group_into_windows, chunk_markdown
from titan.models import Chunk
from titan.search import decompose_query, rrf_fusion

SRC = Path("/vault/notes/test.md")


# ─── chunk_markdown ──────────────────────────────────────────────────────────


def test_chunk_per_header() -> None:
    md = "# Titel\nIntro.\n## A\nInhalt A.\n## B\nInhalt B."
    chunks = chunk_markdown(md, SRC)
    assert [c.header for c in chunks] == ["Titel", "A", "B"]
    assert [c.chunk_id for c in chunks] == [0, 1, 2]
    assert all(c.source == "test.md" for c in chunks)
    assert all(c.document_title == "Titel" for c in chunks)
    # Embeddings werden erst von late_chunk_embed befüllt
    assert all(c.dense is None and c.sparse is None and c.colbert is None for c in chunks)


def test_empty_document_returns_no_chunks() -> None:
    assert chunk_markdown("", SRC) == []
    assert chunk_markdown("   \n  ", SRC) == []


def test_headerless_document_single_chunk() -> None:
    chunks = chunk_markdown("Nur Fließtext ohne Header.", SRC)
    assert len(chunks) == 1
    assert chunks[0].header == "test"  # Datei-Stem als Fallback-Header
    assert chunks[0].document_title == "test"


def test_oversized_section_split_with_overlap() -> None:
    body = ("wort " * 6000).strip()  # ~30 000 Zeichen > MAX_SUPER_CHUNK_CHARS
    chunks = chunk_markdown("# Big\n" + body, SRC)

    assert len(chunks) >= 2
    assert chunks[0].header == "Big"
    assert chunks[1].header == "Big (Teil 2)"
    assert all(len(c.text) <= MAX_SUPER_CHUNK_CHARS for c in chunks)
    assert [c.chunk_id for c in chunks] == list(range(len(chunks)))
    # Overlap-Invariante: der Anfang von Teil 2 steht bereits am Ende von Teil 1
    assert chunks[1].text[:200] in chunks[0].text


def test_split_keeps_document_ends() -> None:
    body = ("abc def ghi jkl " * 2000).strip() + " ENDE-MARKER"
    chunks = chunk_markdown("# Big\n" + body, SRC)
    assert chunks[0].text.startswith("# Big")
    assert "ENDE-MARKER" in chunks[-1].text


# ─── _group_into_windows ─────────────────────────────────────────────────────


class _FakeTokenizer:
    """1 Token = 1 Wort; deterministisch und GPU-frei."""

    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        return text.split()


def _mk(text: str, cid: int) -> Chunk:
    return Chunk(
        text=text,
        source="t.md",
        chunk_id=cid,
        header=f"h{cid}",
        section_id="s",
        document_title="t",
    )


def test_windows_respect_token_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("titan.ingest.LATE_CHUNK_WINDOW_TOKENS", 10)
    chunks = [_mk("a b c", 0), _mk("d e f", 1), _mk("g h i j k", 2)]  # 3 + 3 + 5 Tokens
    windows = _group_into_windows(chunks, _FakeTokenizer())
    # 3+3 passen (6 ≤ 10); +5 = 11 > 10 → neues Fenster
    assert [len(w) for w in windows] == [2, 1]


def test_oversized_chunk_gets_own_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("titan.ingest.LATE_CHUNK_WINDOW_TOKENS", 4)
    chunks = [_mk("a b", 0), _mk("x " * 10, 1), _mk("c d", 2)]
    windows = _group_into_windows(chunks, _FakeTokenizer())
    assert [len(w) for w in windows] == [1, 1, 1]
    assert windows[1][0].chunk_id == 1


def test_window_order_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("titan.ingest.LATE_CHUNK_WINDOW_TOKENS", 100)
    chunks = [_mk("w", i) for i in range(5)]
    windows = _group_into_windows(chunks, _FakeTokenizer())
    assert [c.chunk_id for w in windows for c in w] == [0, 1, 2, 3, 4]


# ─── rrf_fusion ──────────────────────────────────────────────────────────────


def test_rrf_doc_in_both_lists_wins() -> None:
    a = [{"id": "x", "text": "X"}, {"id": "y", "text": "Y"}]
    b = [{"id": "z", "text": "Z"}, {"id": "x", "text": "X"}]
    fused = rrf_fusion([a, b])
    assert fused[0]["id"] == "x"
    assert fused[0]["rank"] == 1
    assert fused[0]["score"] == pytest.approx(1 / 61 + 1 / 62, abs=1e-6)


def test_rrf_preserves_payload() -> None:
    fused = rrf_fusion([[{"id": "x", "text": "X", "extra": 1}]])
    assert fused[0]["extra"] == 1
    assert fused[0]["rank"] == 1


def test_rrf_empty_lists() -> None:
    assert rrf_fusion([]) == []
    assert rrf_fusion([[], []]) == []


# ─── decompose_query (Ollama-Antwortvarianten, gemockt) ──────────────────────


def _ollama_response(payload: str) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"response": payload}
    resp.raise_for_status.return_value = None
    return resp


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["a", "b"]', ["a", "b"]),  # sauberes Array
        ('Hier: ["a", "b"] fertig.', ["a", "b"]),  # Array in Fließtext
        ('{"queries": ["a", "b"]}', ["a", "b"]),  # Dict mit bekanntem Key
        ('{"foo": "a", "bar": "b"}', ["a", "b"]),  # Dict-Fallback über values
    ],
)
def test_decompose_parses_variants(raw: str, expected: list[str]) -> None:
    with patch("titan.search.requests.post", return_value=_ollama_response(raw)):
        assert decompose_query("egal") == expected


def test_decompose_falls_back_on_garbage() -> None:
    with patch("titan.search.requests.post", return_value=_ollama_response("kein json")):
        assert decompose_query("orig") == ["orig"]


def test_decompose_falls_back_on_connection_error() -> None:
    import requests

    with patch(
        "titan.search.requests.post", side_effect=requests.exceptions.ConnectionError("down")
    ):
        assert decompose_query("orig") == ["orig"]
