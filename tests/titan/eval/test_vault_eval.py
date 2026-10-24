"""Tests fuer die Messlogik der Vault-Eval. Ohne Netz, ohne titan-service."""

from __future__ import annotations

from pathlib import Path

from titan.eval.vault_eval import (
    Fall,
    Treffer,
    Variante,
    auswahl,
    bewerte,
    verwaiste,
    zusammenfassen,
)

FALL = Fall(
    id="g001",
    typ="fakt",
    frage="Welchen Prozessor hat der Hub?",
    soll=[{"notiz": "hub-mini-pc", "beleg": "Intel Pentium  J3710"}],
    darf_nicht=["leads-dach-2026-09"],
)


def _t(notiz: str, text: str = "nichts", score: float = 1.0) -> Treffer:
    return Treffer(notiz=notiz, text=text, score=score)


def test_die_stelle_zaehlt_erst_wenn_der_beleg_im_treffer_steht() -> None:
    treffer = [
        _t("hub-mini-pc", "## Rolle\nfalscher Abschnitt"),
        _t("hub-mini-pc", "CPU: Intel Pentium\nJ3710"),
    ]
    werte = bewerte(FALL, treffer)
    assert werte["notiz_rang"] == 1
    assert werte["stellen_rang"] == 2  # Leerzeichen und Umbrueche zaehlen nicht
    assert werte["dubletten"] == 1


def test_geschuetzte_notiz_im_ergebnis_ist_ein_leck() -> None:
    assert bewerte(FALL, [_t("leads-dach-2026-09")])["leck"] == ["leads-dach-2026-09"]


def test_hoechstens_n_je_notiz_rueckt_andere_notizen_nach() -> None:
    kandidaten = [_t("a"), _t("a"), _t("a"), _t("b"), _t("c")]
    notizen = [t.notiz for t in auswahl(kandidaten, Variante("x", 3, max_je_notiz=1))]
    assert notizen == ["a", "b", "c"]


def test_verfehlte_faelle_zaehlen_nicht_als_treffer() -> None:
    zeilen = [
        {"typ": "fakt", **bewerte(FALL, [_t("hub-mini-pc", "Intel Pentium J3710")])},
        {"typ": "fakt", **bewerte(FALL, [_t("mein-pc")])},
        {"typ": "negativ", **bewerte(FALL, [_t("mein-pc")])},
    ]
    s = zusammenfassen(zeilen)
    assert s["stellen_recall@k"] == 0.5  # die Negativfrage zaehlt nicht mit
    assert s["notiz_recall@k"] == 0.5


def test_verwaister_fall_wird_erkannt(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "hub-mini-pc.md").write_text("CPU: Intel Celeron", encoding="utf-8")
    assert verwaiste([FALL], tmp_path) == {"g001"}
