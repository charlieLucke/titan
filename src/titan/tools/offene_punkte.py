"""Bericht ueber alles, was im Vault offen ist - und ueber alle Ideen.

Bewusst ein CLI und **kein** MCP-Werkzeug, aus demselben Grund wie
``graph_check``: Jedes MCP-Werkzeug kostet dauerhaft Fixkontext in jeder
Sitzung (Vault-Notiz ``mcp-token-oekonomie``). Ein Bericht, den man alle paar
Wochen liest, verdient das nicht.

Bewusst **erzeugt statt gepflegt**. Eine handgeschriebene Sammelnotiz waere die
zweite Stelle fuer jedes Thema und damit genau die Konstellation, vor der
``00-home`` warnt: "Offene Punkte an genau einer Stelle je Thema." Offene
Punkte sind ausserdem reine Zustaende, und die Vault-Pruefung vom 29.08.2026
hat gezeigt, dass ausschliesslich Zustaende veralten - kein einziges "Warum"
war falsch. Eine Notiz, die nur aus Zustaenden besteht, verrottet also
schneller als jede andere und sieht dabei autoritativ aus.

Die Wahrheit bleibt deshalb in der Projektnotiz. Hier wird nur eingesammelt.

    uv run python -m titan.tools.offene_punkte
    uv run python -m titan.tools.offene_punkte --ideen
    uv run python -m titan.tools.offene_punkte --alle      # auch Erledigtes
    uv run python -m titan.tools.offene_punkte --json
    uv run python -m titan.tools.offene_punkte --markdown > /mnt/f/vault/offene-punkte.md

Konvention in den Notizen (siehe CLAUDE.md im Vault). Die Checkbox-Schreibweise
ist nicht erfunden, sondern die, die coolify-prod und projekt-infra ohnehin
schon benutzen - dazugekommen sind nur der Dringlichkeitsmarker und das Datum:

    ## Offene Punkte

    - [ ] 🔴 **Kurztitel.** Was fehlt und warum es draengt. (seit 2026-08-29)
    - [ ] ⚠️ **Kurztitel.** Text. (seit 2026-08-16)
    - [ ] **Kurztitel.** Ohne Marker heisst: kann warten. (seit 2026-07-02)
    - [x] ~~**Erledigtes.**~~ - erledigt 2026-08-28

    ## Ideen

    - [ ] **Kurztitel.** Text. (seit 2026-08-29)

Erledigtes bleibt stehen, statt geloescht zu werden - dieselbe Regel wie fuer
ueberholte Aussagen im Vault. Der Bericht zeigt es nur auf Wunsch (``--alle``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from titan.config import settings

# Die kanonischen Ueberschriften. Alles andere ist ein Konventionsbruch.
H_OFFEN = "Offene Punkte"
H_IDEEN = "Ideen"

# Ueberschriften, die dasselbe meinen koennten, aber anders heissen. Das ist ein
# Hinweis zum Nachsehen, kein Urteil: "Grenzen" und "Luecke" stehen bewusst
# nicht drin, weil sie im Vault ueberwiegend etwas anderes bedeuten.
_FASTTREFFER = re.compile(r"(offen|to-?do|n[aä]chste (konkrete )?schritte|idee)", re.IGNORECASE)

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# Listenpunkt, optional mit Aufgaben-Checkbox.
_ITEM = re.compile(r"^[-*]\s+(?:\[([ xX])\]\s*)?(.*)$")
_SEIT = re.compile(r"\(seit\s+(\d{4}-\d{2}-\d{2})\)")
# Nur ein **fett** ganz am Anfang ist ein Titel. Ein Fettdruck mitten im Satz
# ist Betonung - sonst heisst der Punkt "fremden" statt "Vor der ersten ...".
_TITEL = re.compile(r"^\*\*(.+?)\*\*")
_FM_DOMAIN = re.compile(r"^domain:\s*(\S+)\s*$", re.MULTILINE)
_FM_UNINDIZIERT = re.compile(r"^indexed:\s*false\s*$", re.MULTILINE | re.IGNORECASE)

# Reihenfolge ist die Ausgabereihenfolge.
STUFEN: tuple[tuple[str, str], ...] = (
    ("🔴", "DRINGEND"),
    ("⚠️", "WICHTIG"),
    ("", "KANN WARTEN"),
)


def _frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    teile = text.split("---", 2)
    return teile[1] if len(teile) > 1 else ""


def _domain(text: str) -> str:
    m = _FM_DOMAIN.search(_frontmatter(text))
    return m.group(1) if m else "?"


def unindiziert(text: str) -> bool:
    """`indexed: false` heisst: keine Notiz, sondern Anweisung oder Bericht.

    Sonst meldet der Bericht seine eigene Ueberschrift als Konventionsbruch -
    und CLAUDE.md, wo die Konvention definiert wird, gleich mit.
    """
    return bool(_FM_UNINDIZIERT.search(_frontmatter(text)))


def _abschnitt(zeilen: list[str], ueberschrift: str) -> list[dict[str, Any]]:
    """Listenpunkte unter der Ueberschrift.

    Die Kopfzeile wird getrennt vom Rest gefuehrt. Sonst holt sich der Titel
    das erste **fett** aus einer Fortsetzungszeile - so entstand beim ersten
    Lauf ein Punkt namens "nicht".
    """
    punkte: list[dict[str, Any]] = []
    tiefe: int | None = None
    im_code = False
    for zeile in zeilen:
        if zeile.lstrip().startswith("```"):
            # Ein Beispiel im Codeblock ist kein Punkt. ideen-ohne-zuhause zeigt
            # die Form vor - ohne diese Zeile stand die Vorlage als echte Idee
            # im Bericht.
            im_code = not im_code
            continue
        if im_code:
            continue
        kopf = _HEADING.match(zeile)
        if kopf:
            if tiefe is not None and len(kopf.group(1)) <= tiefe:
                break  # Abschnitt zu Ende
            if kopf.group(2).strip() == ueberschrift:
                tiefe = len(kopf.group(1))
            continue
        if tiefe is None:
            continue
        eintrag = _ITEM.match(zeile)
        if eintrag:
            box, rest = eintrag.group(1), eintrag.group(2).strip()
            punkte.append({"erledigt": (box or " ").lower() == "x", "kopfzeile": rest, "rest": ""})
        elif zeile.startswith(("  ", "\t")) and zeile.strip() and punkte:
            punkte[-1]["rest"] += " " + zeile.strip()
    return punkte


def _zerlege(punkt: dict[str, Any]) -> dict[str, Any]:
    kopf = punkt["kopfzeile"]
    marker = ""
    for zeichen, _ in STUFEN:
        if zeichen and kopf.startswith(zeichen):
            marker = zeichen
            kopf = kopf[len(zeichen) :].strip()
            break
    ganz = f"{kopf} {punkt['rest']}".strip()
    # Erledigtes steht durchgestrichen da (~~**Titel.**~~). Der Titel steckt
    # darunter, sonst wird die ganze Zeile zum Titel.
    kopf = kopf.removeprefix("~~").strip()
    seit = _SEIT.search(ganz)
    titel = _TITEL.search(kopf)
    return {
        "erledigt": punkt["erledigt"],
        "marker": marker,
        "seit": seit.group(1) if seit else None,
        "titel": (titel.group(1) if titel else kopf[:70]).strip().rstrip(".:~"),
        "text": _SEIT.sub("", ganz).strip(),
    }


def sammle(wurzel: Path, ueberschrift: str) -> tuple[list[dict[str, Any]], list[str]]:
    treffer: list[dict[str, Any]] = []
    hinweise: list[str] = []
    for pfad in sorted(wurzel.rglob("*.md")):
        if ".git" in pfad.parts:
            continue
        text = pfad.read_text(encoding="utf-8", errors="replace")
        if unindiziert(text):
            continue
        zeilen = text.splitlines()
        domain = _domain(text)
        for roh in _abschnitt(zeilen, ueberschrift):
            eintrag = _zerlege(roh)
            eintrag["notiz"] = pfad.stem
            eintrag["domain"] = domain
            treffer.append(eintrag)
        for zeile in zeilen:
            kopf = _HEADING.match(zeile)
            # Nur Abschnitte (##+), nicht der Titel der Notiz. Und nur kurze
            # Ueberschriften: "Wie ich alle Ideen auf einmal sehe" ist eine
            # Anleitung, keine falsch benannte Liste.
            if not kopf or len(kopf.group(1)) < 2:
                continue
            titel = kopf.group(2).strip()
            if titel in (H_OFFEN, H_IDEEN) or len(titel.split()) > 4:
                continue
            if _FASTTREFFER.search(titel):
                hinweise.append(f"{pfad.stem}: {titel}")
    return treffer, hinweise


def _alter(seit: str | None, heute: date) -> int | None:
    if not seit:
        return None
    try:
        return (heute - date.fromisoformat(seit)).days
    except ValueError:
        return None


def _sortiert(treffer: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rang = {zeichen: i for i, (zeichen, _) in enumerate(STUFEN)}
    # Innerhalb einer Stufe das aelteste zuerst: Was am laengsten liegt, ist das,
    # was am ehesten vergessen wurde. Ohne Datum ans Ende - das ist kein alter
    # Punkt, sondern ein Konventionsbruch.
    return sorted(treffer, key=lambda e: (rang.get(e["marker"], 9), e["seit"] or "9999-99-99"))


def _gruppen(treffer: list[dict[str, Any]], ideen: bool) -> list[tuple[str, list[dict[str, Any]]]]:
    if ideen:
        return [("IDEEN", treffer)] if treffer else []
    return [
        (name, [e for e in treffer if e["marker"] == zeichen])
        for zeichen, name in STUFEN
        if any(e["marker"] == zeichen for e in treffer)
    ]


def _tage(n: int | None) -> str:
    return "1 Tag" if n == 1 else f"{n} Tage"


def _als_text(treffer: list[dict[str, Any]], ideen: bool, erledigt: int) -> list[str]:
    was = "Ideen" if ideen else "offene Punkte"
    zeilen = [f"{len(treffer)} {was}, {erledigt} erledigt (Stand {date.today()})", ""]
    for name, gruppe in _gruppen(treffer, ideen):
        zeilen.append(f"{name} ({len(gruppe)})")
        for e in gruppe:
            alt = f"{e['alter_tage']:>4}d" if e["alter_tage"] is not None else "   ?"
            zeilen.append(f"  {alt}  {e['domain']:<12} {e['notiz']:<36} {e['titel']}")
        zeilen.append("")
    return zeilen


def _als_markdown(treffer: list[dict[str, Any]], ideen: bool, erledigt: int) -> list[str]:
    titel = "Ideen" if ideen else "Offene Punkte"
    # Der Befehl im Kopf muss der sein, der genau diese Datei erzeugt - sonst
    # schreibt ihn jemand ueber den falschen Bericht.
    befehl = (
        "uv run python -m titan.tools.offene_punkte"
        + (" --ideen" if ideen else "")
        + " --markdown > /mnt/f/vault/"
        + ("ideen.md" if ideen else "offene-punkte.md")
    )
    zeilen = [
        "---",
        "indexed: false",
        "---",
        "",
        f"# {titel} - erzeugt am {date.today().isoformat()}",
        "",
        "> **Nicht von Hand bearbeiten.** Das hier ist ein Bericht, keine Notiz. Er wird",
        "> aus den Abschnitten der einzelnen Notizen erzeugt und beim naechsten Lauf",
        "> ueberschrieben. Wer einen Punkt aendern will, aendert ihn dort, wo er steht.",
        "> `indexed: false` haelt ihn aus der Suche heraus, damit er keine zweite",
        "> Wahrheit wird.",
        "",
        "```bash",
        befehl,
        "```",
        "",
        f"{len(treffer)} {'Ideen' if ideen else 'offen'}, {erledigt} erledigt.",
        "",
    ]
    for name, gruppe in _gruppen(treffer, ideen):
        zeilen += [f"## {name.title()} ({len(gruppe)})", ""]
        for e in gruppe:
            alt = (
                f" · seit {e['seit']} ({_tage(e['alter_tage'])})"
                if e["seit"]
                else " · **ohne Datum**"
            )
            zeilen.append(f"- **{e['titel']}** — [[{e['notiz']}]] `{e['domain']}`{alt}")
        zeilen.append("")
    return zeilen


def main() -> int:
    ap = argparse.ArgumentParser(description="Offene Punkte und Ideen aus dem Vault einsammeln")
    ap.add_argument("--vault", type=Path, default=settings.vault_root)
    ap.add_argument("--ideen", action="store_true", help="Ideen statt offener Punkte")
    ap.add_argument("--alle", action="store_true", help="Erledigtes mit anzeigen")
    ap.add_argument("--json", action="store_true", help="Maschinenlesbar")
    ap.add_argument("--markdown", action="store_true", help="Als Bericht mit indexed: false")
    args = ap.parse_args()

    if not args.vault.is_dir():
        print(f"Vault nicht gefunden: {args.vault}", file=sys.stderr)
        return 2

    alle, hinweise = sammle(args.vault, H_IDEEN if args.ideen else H_OFFEN)
    erledigt = sum(1 for e in alle if e["erledigt"])
    treffer = _sortiert(alle if args.alle else [e for e in alle if not e["erledigt"]])
    heute = date.today()
    for e in treffer:
        e["alter_tage"] = _alter(e["seit"], heute)

    ohne_datum = [e for e in treffer if not e["erledigt"] and not e["seit"]]

    if args.json:
        print(
            json.dumps(
                {
                    "stand": heute.isoformat(),
                    "punkte": treffer,
                    "erledigt": erledigt,
                    "ohne_datum": [f"{e['notiz']}: {e['titel']}" for e in ohne_datum],
                    "hinweise": hinweise,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 1 if ohne_datum else 0

    md = args.markdown
    zeilen = (_als_markdown if md else _als_text)(treffer, args.ideen, erledigt)
    strich, punkt = ("## ", "- ") if md else ("", "  ")

    if ohne_datum:
        zeilen += [f"{strich}Ohne Datum ({len(ohne_datum)}) - Konventionsbruch", ""]
        zeilen += [f"{punkt}{e['notiz']}: {e['titel']}" for e in ohne_datum]
        zeilen.append("")

    if hinweise:
        zeilen += [f"{strich}Vielleicht auch gemeint ({len(hinweise)}) - nur ein Hinweis", ""]
        zeilen += [f"{punkt}{h}" for h in hinweise]
        zeilen += ["", f"{punkt}Kanonisch: '## {H_OFFEN}' bzw. '## {H_IDEEN}'"]

    print("\n".join(zeilen))
    # Exit 1 nur bei einem echten Konventionsbruch (Punkt ohne Datum), damit
    # sich das in einen Check haengen laesst. Die Ueberschriften-Hinweise sind
    # bewusst nicht scharf geschaltet: sie raten, sie wissen nichts.
    return 1 if ohne_datum else 0


if __name__ == "__main__":
    sys.exit(main())
