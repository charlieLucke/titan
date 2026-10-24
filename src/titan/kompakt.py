"""Extraktive Kompression von Suchtreffern: nur die Stellen, die zur Frage passen.

**Warum extraktiv.** Ein Treffer ist im Median rund 2 000 Zeichen lang, die Antwort
steht meist in ein, zwei Zeilen davon. Ein kleines Modell koennte zusammenfassen,
aber eine Zusammenfassung ist neuer Text — und neuer Text kann falsch sein, ohne
dass der Leser es merkt. Hier wird nichts formuliert: jede Zeile im Ergebnis steht
woertlich in der Notiz, die Luecken sind sichtbar markiert.

**Wie.** Der Treffer wird in Einheiten zerlegt (Absatz, Listenpunkt, Tabellenzeile,
Codeblock; lange Absaetze in Saetze). BGE-M3 bettet die Frage und alle Einheiten in
einem Batch ein; behalten werden die aehnlichsten Einheiten bis zu einem
Zeichenbudget, in ihrer urspruenglichen Reihenfolge. Die erste Ueberschrift bleibt
immer, damit der Leser weiss, wo er ist.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import torch

log = logging.getLogger(__name__)

AUSGELASSEN = "[…]"
# Laenger als das wird ein Absatz in Saetze zerlegt, damit eine passende Zeile nicht
# einen ganzen Block mit sich zieht.
LANGER_ABSATZ = 400
_SATZENDE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ\"„*`\[])")


def einheiten(text: str) -> list[str]:
    """Zerlegt einen Treffer in Einheiten, die einzeln behalten oder weggelassen werden.

    Codebloecke bleiben ganz: ein halber Befehl ist schlimmer als keiner.
    """
    out: list[str] = []
    absatz: list[str] = []
    code: list[str] | None = None
    # Ein Listenpunkt oder Zitat darf ueber mehrere Zeilen laufen; die Folgezeile
    # gehoert dann noch dazu, nicht in einen neuen Absatz.
    im_punkt = False

    def absatz_fertig() -> None:
        if absatz:
            block = " ".join(z.strip() for z in absatz)
            out.extend(_SATZENDE.split(block) if len(block) > LANGER_ABSATZ else [block])
            absatz.clear()

    for zeile in text.splitlines():
        if code is not None:
            code.append(zeile)
            if zeile.strip().startswith("```"):
                out.append("\n".join(code))
                code = None
            continue
        s = zeile.strip()
        if s.startswith("```"):
            absatz_fertig()
            code = [zeile]
            im_punkt = False
        elif not s:
            absatz_fertig()
            im_punkt = False
        elif s.startswith(("#", "|")):
            absatz_fertig()
            out.append(s)
            im_punkt = False
        elif s.startswith(("- ", "* ", ">")) or re.match(r"\d+\.\s", s):
            absatz_fertig()
            out.append(s)
            im_punkt = True
        elif im_punkt:
            out[-1] += " " + s
        else:
            absatz.append(s)
    if code is not None:
        out.append("\n".join(code))
    absatz_fertig()
    return [e for e in out if e.strip() and not re.fullmatch(r"\|[\s:|-]*\|", e)]


def waehle(teile: list[str], aehnlichkeit: list[float], budget: int) -> str:
    """Behaelt die aehnlichsten Einheiten bis zum Budget, in Originalreihenfolge.

    Mindestens die beste Einheit kommt immer mit, auch wenn sie allein das Budget
    sprengt — ein leerer Treffer waere nutzlos. Eine Ueberschrift ganz vorn bleibt
    stehen, ohne aufs Budget zu zaehlen.
    """
    if not teile:
        return ""
    kopf = 1 if teile[0].startswith("#") else 0
    rangfolge = sorted(range(kopf, len(teile)), key=lambda i: aehnlichkeit[i], reverse=True)
    behalten: set[int] = set(range(kopf))
    verbraucht = 0
    for i in rangfolge:
        if behalten - set(range(kopf)) and verbraucht + len(teile[i]) > budget:
            continue
        behalten.add(i)
        verbraucht += len(teile[i])

    zeilen: list[str] = []
    vorher = -1
    for i in sorted(behalten):
        if i != vorher + 1:
            zeilen.append(AUSGELASSEN)
        zeilen.append(teile[i])
        vorher = i
    if vorher != len(teile) - 1:
        zeilen.append(AUSGELASSEN)
    return "\n".join(zeilen)


def kompaktiere(model: Any, frage: str, texte: list[str], budget: int) -> list[str]:
    """Kuerzt jeden Text auf die Einheiten, die der Frage am naechsten sind.

    Ein encode()-Aufruf fuer Frage und alle Einheiten aller Treffer zusammen. Der
    Aufrufer haelt den GPU-Lock.
    """
    zerlegt = [einheiten(t) for t in texte]
    flach = [e for teile in zerlegt for e in teile]
    if not flach:
        return texte
    with torch.no_grad():
        roh = model.encode([frage, *flach], return_dense=True, batch_size=32)["dense_vecs"]
        vecs = torch.nn.functional.normalize(torch.as_tensor(roh, dtype=torch.float32), dim=1)
        sim: list[float] = (vecs[1:] @ vecs[0]).tolist()

    out: list[str] = []
    pos = 0
    for teile in zerlegt:
        out.append(waehle(teile, sim[pos : pos + len(teile)], budget))
        pos += len(teile)
    return out
