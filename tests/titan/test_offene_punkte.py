"""Tests fuer den Sammler der offenen Punkte.

Zwei der Faelle hier sind Regressionen aus dem allerersten Lauf gegen den
echten Vault: Der Titel holte sich das erste **fett** aus einer
Fortsetzungszeile (es entstand ein Punkt namens "nicht"), und ein **fett**
mitten im Satz wurde zum Titel (es entstand einer namens "fremden"). Beide
Textstellen stehen unveraendert in coolify-prod.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from titan.tools.offene_punkte import (
    H_IDEEN,
    H_OFFEN,
    _abschnitt,
    _alter,
    _tage,
    _zerlege,
    sortiert,
    unindiziert,
    versteckt,
)

# ─── _abschnitt ──────────────────────────────────────────────────────────────

NOTIZ = """\
# Titel

## Offene Punkte

- [ ] 🔴 **Erstes.** Text. (seit 2026-08-01)
- [x] ~~**Zweites.**~~ - erledigt 2026-08-28
- [ ] Drittes ohne Marker

## Ideen

- [ ] **Eine Idee.** Text. (seit 2026-08-29)

## Verwandte Notizen

- [[irgendwas]]
"""


def test_liest_nur_den_gewuenschten_abschnitt() -> None:
    punkte = _abschnitt(NOTIZ.splitlines(), H_OFFEN)
    # Die Idee aus dem naechsten Abschnitt darf nicht mitkommen.
    assert [_zerlege(p)["titel"] for p in punkte] == ["Erstes", "Zweites", "Drittes ohne Marker"]


def test_stoppt_vor_der_naechsten_ueberschrift() -> None:
    # "Verwandte Notizen" darf nicht als Idee durchgehen.
    ideen = _abschnitt(NOTIZ.splitlines(), H_IDEEN)
    assert len(ideen) == 1


def test_checkbox_wird_ausgewertet() -> None:
    punkte = _abschnitt(NOTIZ.splitlines(), H_OFFEN)
    assert [p["erledigt"] for p in punkte] == [False, True, False]


def test_punkt_ohne_checkbox_gilt_als_offen() -> None:
    punkte = _abschnitt(["## Offene Punkte", "- Ganz schlicht"], H_OFFEN)
    assert punkte[0]["erledigt"] is False


def test_fehlende_ueberschrift_gibt_nichts() -> None:
    assert _abschnitt(["# Nur ein Titel", "- ein Punkt"], H_OFFEN) == []


def test_beispiel_im_codeblock_ist_kein_punkt() -> None:
    """Regression: die Formvorlage in ideen-ohne-zuhause stand als echte Idee im Bericht."""
    zeilen = [
        "## Offene Punkte",
        "- [ ] **Echt.** (seit 2026-08-29)",
        "```markdown",
        "- [ ] **Kurztitel.** Was und warum. (seit 2026-08-29)",
        "```",
    ]
    punkte = _abschnitt(zeilen, H_OFFEN)
    assert [_zerlege(p)["titel"] for p in punkte] == ["Echt"]


# ─── Fortsetzungszeilen ──────────────────────────────────────────────────────

MEHRZEILIG = """\
## Offene Punkte

- [x] ~~2FA aktivieren~~ - erledigt 28.08.2026. Zu finden unter
      Profile, **nicht** unter Settings.
"""


def test_fortsetzung_landet_nicht_im_titel() -> None:
    """Regression: der Titel war "nicht"."""
    punkt = _zerlege(_abschnitt(MEHRZEILIG.splitlines(), H_OFFEN)[0])
    assert punkt["titel"] != "nicht"
    assert "Settings" in punkt["text"]


def test_fettdruck_mitten_im_satz_ist_kein_titel() -> None:
    """Regression: der Titel war "fremden"."""
    zeilen = ["## Offene Punkte", "- [ ] Vor der ersten **fremden** Seite: Backups pruefen"]
    punkt = _zerlege(_abschnitt(zeilen, H_OFFEN)[0])
    assert punkt["titel"].startswith("Vor der ersten")


# ─── _zerlege ────────────────────────────────────────────────────────────────


def test_marker_titel_und_datum() -> None:
    zeilen = ["## Offene Punkte", "- [ ] ⚠️ **Der Titel.** Der Text. (seit 2026-08-16)"]
    punkt = _zerlege(_abschnitt(zeilen, H_OFFEN)[0])
    assert punkt["marker"] == "⚠️"
    assert punkt["titel"] == "Der Titel"
    assert punkt["seit"] == "2026-08-16"
    assert "(seit" not in punkt["text"]


def test_ohne_datum_ist_none() -> None:
    zeilen = ["## Offene Punkte", "- [ ] **Titel.** Text."]
    assert _zerlege(_abschnitt(zeilen, H_OFFEN)[0])["seit"] is None


# ─── Sortierung ──────────────────────────────────────────────────────────────


def _p(marker: str, seit: str | None) -> dict[str, object]:
    return {"marker": marker, "seit": seit}


def test_dringend_vor_wichtig_vor_rest() -> None:
    reihe = sortiert([_p("", "2026-01-01"), _p("⚠️", "2026-01-01"), _p("🔴", "2026-01-01")])
    assert [e["marker"] for e in reihe] == ["🔴", "⚠️", ""]


def test_innerhalb_der_stufe_aeltestes_zuerst() -> None:
    reihe = sortiert([_p("🔴", "2026-08-01"), _p("🔴", "2026-01-01")])
    assert [e["seit"] for e in reihe] == ["2026-01-01", "2026-08-01"]


def test_ohne_datum_ans_ende_der_stufe() -> None:
    reihe = sortiert([_p("🔴", None), _p("🔴", "2026-08-01")])
    assert [e["seit"] for e in reihe] == ["2026-08-01", None]


# ─── _alter ──────────────────────────────────────────────────────────────────


def test_alter_in_tagen() -> None:
    assert _alter("2026-08-01", date(2026, 8, 29)) == 28


def test_alter_bei_unsinn_ist_none() -> None:
    assert _alter("2026-13-99", date(2026, 8, 29)) is None
    assert _alter(None, date(2026, 8, 29)) is None


def test_ein_tag_ist_singular() -> None:
    assert _tage(1) == "1 Tag"
    assert _tage(0) == "0 Tage"
    assert _tage(75) == "75 Tage"


# ─── unindiziert ─────────────────────────────────────────────────────────────


def test_bericht_und_anweisungen_werden_uebersprungen() -> None:
    """Ohne das meldet der Bericht seine eigene Ueberschrift als Konventionsbruch."""
    assert unindiziert("---\nindexed: false\n---\n\n# Bericht\n")
    assert not unindiziert("---\ndomain: betrieb\n---\n\n# Notiz\n")
    assert not unindiziert("# Ganz ohne Frontmatter\n")


def test_versteckte_ordner_werden_uebersprungen(tmp_path: Path) -> None:
    """Regression: der Bericht las .claude/skills/*/SKILL.md mit."""
    (tmp_path / "notes").mkdir()
    (tmp_path / ".claude" / "skills" / "x").mkdir(parents=True)
    assert versteckt(tmp_path / ".claude" / "skills" / "x" / "SKILL.md", tmp_path)
    assert not versteckt(tmp_path / "notes" / "betrieb" / "a.md", tmp_path)
