"""Cross-Encoder-Schritt (titan.rerank) und seine Stelle im Suchpfad."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from titan.rerank import QwenReranker, passage_text, rerank
from titan.search import search


class FakeScorer:
    """Wertet nach einem festen Wort im Text; merkt sich, was es gelesen hat."""

    def __init__(self, word: str) -> None:
        self.word = word
        self.seen: list[str] = []

    def scores(self, query: str, passages: Sequence[str]) -> list[float]:
        self.seen += passages
        return [1.0 if self.word in p else 0.0 for p in passages]


def _hit(n: int, text: str = "") -> dict[str, Any]:
    return {"id": f"p{n}", "rank": n, "score": 1 / n, "text": text or f"Text {n}"}


def test_rerank_moves_the_answering_passage_up() -> None:
    hits = [_hit(1), _hit(2), _hit(3, "die Antwort")]
    out = rerank(FakeScorer("Antwort"), "Frage", hits, pool=10)
    assert [h["id"] for h in out] == ["p3", "p1", "p2"]
    assert [h["rank"] for h in out] == [1, 2, 3]
    assert out[0]["rerank_score"] == 1.0
    assert out[0]["score"] == pytest.approx(1 / 3)


def test_rerank_keeps_the_order_on_ties_and_leaves_the_tail_alone() -> None:
    hits = [_hit(n) for n in range(1, 6)]
    scorer = FakeScorer("nirgends")
    out = rerank(scorer, "Frage", hits, pool=3)
    assert [h["id"] for h in out] == ["p1", "p2", "p3", "p4", "p5"]
    assert len(scorer.seen) == 3
    assert "rerank_score" not in out[3]


def test_rerank_of_nothing() -> None:
    assert rerank(FakeScorer("x"), "Frage", [], pool=10) == []


def test_passage_text_puts_note_and_section_first() -> None:
    hit = {"source": "/mnt/f/vault/notes/reise.md", "header": "Kosten", "text": "42 Euro"}
    assert passage_text(hit) == "reise › Kosten\n\n42 Euro"
    assert passage_text({"source": "a/b.md", "text": "x"}) == "b\n\nx"


def test_unknown_model_is_rejected() -> None:
    with pytest.raises(ValueError, match="unbekannter Reranker"):
        QwenReranker("gibt-es-nicht")


def _search(reranker: FakeScorer | None, top_k: int = 2) -> dict[str, Any]:
    lists = [_hit(1), _hit(2), _hit(3, "die Antwort")]
    with (
        patch("titan.search.embed_queries", return_value=[{"dense": [0.0]}]),
        patch("titan.search.hybrid_search_with_rerank", return_value=lists),
    ):
        return search(
            "Frage",
            top_k=top_k,
            use_decompose=False,
            use_cache=False,
            model=MagicMock(),
            qdrant_client=MagicMock(),
            reranker=reranker,
        )


def test_search_reranks_before_cutting_to_top_k() -> None:
    """Der beste Treffer auf Platz 3 kommt nach vorn, obwohl top_k 2 ist."""
    result = _search(FakeScorer("Antwort"))
    assert [c["id"] for c in result["chunks"]] == ["p3", "p1"]
    assert result["reranked"] is True


def test_search_without_reranker_is_unchanged() -> None:
    result = _search(None)
    assert [c["id"] for c in result["chunks"]] == ["p1", "p2"]
    assert result["reranked"] is False
