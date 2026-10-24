"""Tests fuer die extraktive Kompression. Laeuft ohne GPU: das Modell ist ein Stub.

Die Eigenschaft, um die es geht: **nichts im Ergebnis ist neu formuliert.** Jede
Zeile steht woertlich im Treffer, und wo etwas fehlt, steht sichtbar `[…]`.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from titan.kompakt import AUSGELASSEN, einheiten, kompaktiere, waehle

TREFFER = """## Hardware

- Rechnername: `charlie-mini-pc`
- CPU: Intel Pentium J3710 (4 schwache Kerne)
- RAM: 7,7 GB

| Teil | Wert |
|---|---|
| Platte | 116 GB |

```bash
lsblk
df -h
```

Ein Absatz ohne Bezug.
"""


def test_einheiten_trennt_ueberschrift_listenpunkte_tabellenzeilen_und_code() -> None:
    assert einheiten(TREFFER) == [
        "## Hardware",
        "- Rechnername: `charlie-mini-pc`",
        "- CPU: Intel Pentium J3710 (4 schwache Kerne)",
        "- RAM: 7,7 GB",
        "| Teil | Wert |",
        "| Platte | 116 GB |",
        "```bash\nlsblk\ndf -h\n```",
        "Ein Absatz ohne Bezug.",
    ]


def test_ein_listenpunkt_ueber_zwei_zeilen_bleibt_ganz() -> None:
    teile = einheiten("- [x] **Aufgenommen** (13.09.2026, Charlies\nEntscheidung).\n- naechster")
    assert teile == ["- [x] **Aufgenommen** (13.09.2026, Charlies Entscheidung).", "- naechster"]


def test_langer_absatz_wird_in_saetze_zerlegt() -> None:
    absatz = " ".join(["Das ist ein ziemlich langer Satz ueber nichts Bestimmtes."] * 10)
    assert len(einheiten(absatz)) == 10


def test_waehle_behaelt_kopf_und_beste_einheit_in_originalreihenfolge() -> None:
    teile = ["## Kopf", "a" * 50, "b" * 50, "c" * 50]
    text = waehle(teile, [0.0, 0.2, 0.9, 0.5], budget=100)
    assert text.splitlines() == ["## Kopf", AUSGELASSEN, "b" * 50, "c" * 50]


def test_waehle_nimmt_die_beste_einheit_auch_ueber_dem_budget() -> None:
    assert waehle(["x" * 500, "y"], [0.9, 0.1], budget=10).startswith("x" * 500)


def test_kompaktiere_gibt_nur_woertliche_zeilen_zurueck() -> None:
    class Stub:
        """Bettet so ein, dass genau die CPU-Zeile zur Frage passt."""

        def encode(self, texte: list[str], **_: Any) -> dict[str, Any]:
            return {
                "dense_vecs": np.array(
                    [[1.0, 0.0] if "CPU" in t or "Prozessor" in t else [0.0, 1.0] for t in texte]
                )
            }

    (kurz,) = kompaktiere(Stub(), "Welcher Prozessor?", [TREFFER], budget=60)

    assert "Intel Pentium J3710" in kurz
    assert "Ein Absatz ohne Bezug." not in kurz
    original = set(einheiten(TREFFER)) | {AUSGELASSEN}
    assert all(zeile in original for zeile in kurz.splitlines())
