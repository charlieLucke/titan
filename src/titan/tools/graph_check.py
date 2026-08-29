"""Graph-Bericht ueber den Vault: verwaiste Notizen und tote Links.

Bewusst ein CLI und **kein** MCP-Werkzeug. Das hier ist Wartung, die man
gelegentlich anstoesst, nicht etwas, das ein Agent mitten im Gespraech braucht.
Jedes MCP-Werkzeug kostet dauerhaft Fixkontext in jeder Sitzung - siehe die
Vault-Notiz mcp-token-oekonomie. Ein Bericht, den man alle paar Wochen liest,
verdient das nicht.

    uv run python -m titan.tools.graph_check
    uv run python -m titan.tools.graph_check --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests

# Der Service-Port steht nicht in den Settings (er kommt als CLI-Argument in
# main.py); hier derselbe Default wie in der systemd-Unit.
DEFAULT_URL = "http://127.0.0.1:8765"


def sammle(base_url: str) -> list[dict[str, Any]]:
    resp = requests.get(f"{base_url}/notes", timeout=30)
    resp.raise_for_status()
    notes: list[dict[str, Any]] = resp.json()["notes"]
    return notes


def bericht(notes: list[dict[str, Any]]) -> dict[str, Any]:
    namen = {Path(n["source_path"]).stem for n in notes}
    eingehend: dict[str, list[str]] = defaultdict(list)
    tote: list[tuple[str, str]] = []

    for n in notes:
        quelle = Path(n["source_path"]).stem
        for ziel in n.get("links") or []:
            if ziel in namen:
                eingehend[ziel].append(quelle)
            else:
                tote.append((quelle, ziel))

    verwaist = sorted(
        Path(n["source_path"]).stem for n in notes if not eingehend.get(Path(n["source_path"]).stem)
    )
    ohne_ausgang = sorted(Path(n["source_path"]).stem for n in notes if not (n.get("links") or []))

    return {
        "notizen": len(notes),
        "links_gesamt": sum(len(n.get("links") or []) for n in notes),
        "tote_links": sorted(tote),
        "verwaist": verwaist,
        "ohne_ausgehende_links": ohne_ausgang,
        "meistverlinkt": sorted(((z, len(q)) for z, q in eingehend.items()), key=lambda x: -x[1])[
            :5
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--json", action="store_true", help="Maschinenlesbar ausgeben")
    args = ap.parse_args()

    try:
        notes = sammle(args.url)
    except requests.RequestException as exc:
        print(f"titan nicht erreichbar unter {args.url}: {exc}", file=sys.stderr)
        return 2

    b = bericht(notes)

    if args.json:
        print(json.dumps(b, indent=2, ensure_ascii=False))
        return 1 if b["tote_links"] else 0

    print(f"{b['notizen']} Notizen, {b['links_gesamt']} Wikilinks\n")

    if b["tote_links"]:
        print(f"TOTE LINKS ({len(b['tote_links'])}) — Ziel existiert nicht:")
        for quelle, ziel in b["tote_links"]:
            print(f"  {quelle} -> [[{ziel}]]")
        print()
    else:
        print("Keine toten Links.\n")

    if b["verwaist"]:
        print(f"VERWAIST ({len(b['verwaist'])}) — niemand verlinkt darauf:")
        for name in b["verwaist"]:
            print(f"  {name}")
        print()
    else:
        print("Keine verwaisten Notizen.\n")

    if b["ohne_ausgehende_links"]:
        print(f"OHNE AUSGEHENDE LINKS ({len(b['ohne_ausgehende_links'])}) — Sackgassen:")
        for name in b["ohne_ausgehende_links"]:
            print(f"  {name}")
        print()

    if b["meistverlinkt"]:
        print("MEISTVERLINKT:")
        for ziel, anzahl in b["meistverlinkt"]:
            print(f"  {anzahl:>3}x  {ziel}")

    # Exit 1 bei toten Links, damit sich das in einen Check haengen laesst.
    return 1 if b["tote_links"] else 0


if __name__ == "__main__":
    sys.exit(main())
