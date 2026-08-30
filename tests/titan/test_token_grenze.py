"""Tests fuer die Token-Grenze beim Chunking.

Der Fehler dahinter ist am 30.08.2026 zum zweiten Mal aufgetreten:
MAX_SUPER_CHUNK_CHARS begrenzt *Zeichen*, die Qdrant-Grenze zaehlt *Tokens* —
eine ColBERT-Zeile je Token. Bei Pfaden, Tabellen und Code faellt die Dichte
unter drei Zeichen je Token, und ein Chunk unter der Zeichengrenze reisst
trotzdem die Token-Grenze. Gemessen: 1007 Tokens aus 2654 Zeichen.

Die Folge war beide Male dieselbe und die gefaehrliche Sorte: HTTP 422, der
Upsert scheitert ganz, die alten Chunks bleiben stehen — die Suche antwortet
weiter, nur aus einer aelteren Fassung.

Der Zaehler wird injiziert, damit die Tests keinen Tokenizer laden.
"""

from __future__ import annotations

from pathlib import Path

from titan.ingest import TOKEN_BUDGET, chunk_markdown

SRC = Path("test.md")


def _dicht(text: str) -> int:
    """Ein Token je zwei Zeichen — dichter als alles im echten Vault (1,91)."""
    return len(text) // 2


def _locker(text: str) -> int:
    return len(text) // 10


def test_dichter_abschnitt_wird_unter_das_budget_geteilt() -> None:
    md = "# Titel\n\n## Dicht\n\n" + "\n\n".join(["wort " * 40] * 12)
    chunks = chunk_markdown(md, SRC, zaehle=_dicht)
    assert len(chunks) > 1
    assert all(_dicht(c.text) <= TOKEN_BUDGET for c in chunks)


def test_kurzer_abschnitt_bleibt_ein_chunk() -> None:
    # Der H1 oeffnet selbst einen Chunk, der Abschnitt ist der zweite.
    md = "# Titel\n\n## Kurz\n\nEin Satz.\n"
    assert [c.header for c in chunk_markdown(md, SRC, zaehle=_dicht)] == ["Titel", "Kurz"]


def test_lockerer_text_wird_nicht_zusaetzlich_zerschnitten() -> None:
    """Normale Prosa darf die Token-Pruefung nicht spueren."""
    md = "# Titel\n\n## Locker\n\n" + "wort " * 300  # ~1500 Zeichen
    assert [c.header for c in chunk_markdown(md, SRC, zaehle=_locker)] == ["Titel", "Locker"]


def test_teilung_verliert_keinen_text() -> None:
    md = "# Titel\n\n## Dicht\n\n" + "\n\n".join(f"Absatz{i} " + "wort " * 40 for i in range(12))
    chunks = chunk_markdown(md, SRC, zaehle=_dicht)
    zusammen = " ".join(c.text for c in chunks)
    for i in range(12):
        assert f"Absatz{i} " in zusammen


def test_teilnummerierung_bleibt_erhalten() -> None:
    """Die bestehende Konvention 'Header (Teil N)' darf nicht verlorengehen."""
    md = "# Titel\n\n## Dicht\n\n" + "\n\n".join(["wort " * 40] * 12)
    chunks = chunk_markdown(md, SRC, zaehle=_dicht)
    assert chunks[0].header == "Titel"
    assert chunks[1].header == "Dicht"
    assert chunks[2].header == "Dicht (Teil 2)"


def test_chunk_ids_bleiben_fortlaufend() -> None:
    md = "# Titel\n\n## Dicht\n\n" + "\n\n".join(["wort " * 40] * 12)
    chunks = chunk_markdown(md, SRC, zaehle=_dicht)
    assert [c.chunk_id for c in chunks] == list(range(len(chunks)))


def test_untrennbarer_block_laeuft_nicht_endlos() -> None:
    """Ohne Wortgrenze gibt es nichts zu teilen — Abbruch statt Endlosschleife."""
    md = "# Titel\n\n## Block\n\n" + "x" * 4000
    chunks = chunk_markdown(md, SRC, zaehle=_dicht)
    assert len(chunks) >= 1
