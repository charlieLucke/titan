"""Tests fuer die Wikilink-Extraktion und den Graph-Bericht.

Der Vault pflegt seine [[Links]] von Hand und vollstaendig. Das ist die beste
Verwandtschaftsinformation, die es gibt, und sie stand bis 29.08.2026 nicht im
Index - find_related rechnete rein semantisch.
"""

from __future__ import annotations

from titan.ingest import extract_wikilinks
from titan.tools.graph_check import bericht

# ─── extract_wikilinks ───────────────────────────────────────────────────────


def test_findet_einfache_links() -> None:
    assert extract_wikilinks("Siehe [[mein-pc]] und [[hub-mini-pc]].") == [
        "hub-mini-pc",
        "mein-pc",
    ]


def test_sortiert_und_ohne_doppelte() -> None:
    assert extract_wikilinks("[[b]] [[a]] [[b]]") == ["a", "b"]


def test_anzeigetext_wird_abgeschnitten() -> None:
    assert extract_wikilinks("[[coolify-prod|der VPS]]") == ["coolify-prod"]


def test_anker_wird_abgeschnitten() -> None:
    assert extract_wikilinks("[[mein-pc#Hardware]]") == ["mein-pc"]


def test_code_bloecke_werden_ignoriert() -> None:
    """Der konkrete Fall aus dem Vault.

    coolify-prod-notfall beschreibt, dass die noVNC-Konsole aus {{.Names}} ein
    [[.Names]] macht. Ohne Code-Filter waere das ein Link auf eine Notiz, die es
    nicht gibt - und der Graph-Bericht haette einen Dauer-Fehlalarm.
    """
    text = "Aus `docker ps --format '{{.Names}}'` wird ```\n[[.Names]]\n``` im Fenster."
    assert extract_wikilinks(text) == []


def test_link_ausserhalb_von_code_bleibt() -> None:
    text = "Code: `[[ignoriert]]`, aber [[echt]] zaehlt."
    assert extract_wikilinks(text) == ["echt"]


def test_leerer_text() -> None:
    assert extract_wikilinks("") == []


def test_leerzeichen_werden_getrimmt() -> None:
    assert extract_wikilinks("[[  mein-pc  ]]") == ["mein-pc"]


# ─── Graph-Bericht ───────────────────────────────────────────────────────────


def _note(name: str, links: list[str], domain: str = "vault") -> dict:
    return {
        "source_path": f"/mnt/f/vault/notes/{name}.md",
        "domain": domain,
        "chunk_count": 1,
        "links": links,
    }


def test_toter_link_wird_gefunden() -> None:
    b = bericht([_note("a", ["gibtesnicht"]), _note("b", [])])
    assert b["tote_links"] == [("a", "gibtesnicht")]


def test_verwaiste_notiz_wird_gefunden() -> None:
    """Niemand verlinkt auf b - genau der Fall, den die Pruefung von Hand suchte."""
    b = bericht([_note("a", ["c"]), _note("b", []), _note("c", [])])
    assert "b" in b["verwaist"]
    assert "c" not in b["verwaist"]


def test_sackgasse_wird_gefunden() -> None:
    b = bericht([_note("a", ["b"]), _note("b", [])])
    assert b["ohne_ausgehende_links"] == ["b"]


def test_meistverlinkt() -> None:
    notes = [_note("a", ["ziel"]), _note("b", ["ziel"]), _note("ziel", [])]
    b = bericht(notes)
    assert b["meistverlinkt"][0] == ("ziel", 2)


def test_gesunder_graph_meldet_nichts() -> None:
    b = bericht([_note("a", ["b"]), _note("b", ["a"])])
    assert b["tote_links"] == []
    assert b["verwaist"] == []
    assert b["ohne_ausgehende_links"] == []


def test_fehlendes_links_feld_bricht_nicht() -> None:
    """Legacy-Chunks und PDF-Ingests haben kein links im Payload."""
    ohne = {"source_path": "/x/a.md", "domain": "projekte", "chunk_count": 1}
    b = bericht([ohne])
    assert b["links_gesamt"] == 0
