"""Retrieval-Messung am echten Vault, ueber die HTTP-Suche wie der Connector.

Plan: docs/ai/plans/2026-10-03_eval-vault-retrieval.md. Misst ohne LLM, ob die
Suche die Stelle findet, an der die Antwort steht, und was das an Kontext kostet.

Das Goldset liegt **nicht** in diesem Repo: titan ist oeffentlich, und jeder Fall
traegt ein woertliches Zitat aus dem Vault. Default ist `<vault>/.eval/goldset.jsonl`;
versteckte Ordner indexiert der Watcher nicht.

Aufruf (titan-service muss laufen):
    uv run python -m titan.eval.vault_eval --label baseline
    uv run python -m titan.eval.vault_eval --label kompakt600 --kompakt 600
    uv run python -m titan.eval.vault_eval --compare baseline kompakt
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import requests

from titan.config import settings

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8765"
LOG_DIR = Path("logs/eval")
# Grob fuer Deutsch plus Markdown; es geht um Verhaeltnisse, nicht um exakte Tokens.
ZEICHEN_JE_TOKEN = 3.5
# Kandidaten je Frage. Mehr als das groesste gemessene k, damit "hoechstens zwei
# Treffer je Notiz" aus echten Nachruecken auffuellen kann statt aus Luft.
KANDIDATEN = 20


@dataclass(frozen=True)
class Treffer:
    notiz: str
    text: str
    score: float
    quelle: str | None = None


@dataclass
class Fall:
    id: str
    typ: str
    frage: str
    soll: list[dict[str, str]]
    darf_nicht: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Variante:
    """Wie aus den Kandidaten das wird, was Claude zu sehen bekommt."""

    name: str
    k: int
    max_je_notiz: int | None = None


VARIANTEN = (
    Variante("k10", 10),
    Variante("k5", 5),
    Variante("k3", 3),
    Variante("k5_max2", 5, max_je_notiz=2),
    Variante("k5_max1", 5, max_je_notiz=1),
)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def lade_goldset(pfad: Path) -> list[Fall]:
    faelle = []
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        if zeile.strip():
            roh = json.loads(zeile)
            faelle.append(
                Fall(roh["id"], roh["typ"], roh["frage"], roh["soll"], roh.get("darf_nicht", []))
            )
    return faelle


def verwaiste(faelle: Sequence[Fall], vault: Path) -> set[str]:
    """IDs der Faelle, deren Beleg nicht mehr in seiner Notiz steht.

    Das Goldset veraltet mit dem Vault. Mit einem verwaisten Fall zu messen hiesse,
    der Suche einen Fehler anzurechnen, den die Notiz gemacht hat.
    """
    texte = {p.stem: norm(p.read_text(encoding="utf-8")) for p in (vault / "notes").rglob("*.md")}
    return {
        f.id
        for f in faelle
        if any(norm(s["beleg"]) not in texte.get(s["notiz"], "") for s in f.soll)
    }


def auswahl(kandidaten: Sequence[Treffer], v: Variante) -> list[Treffer]:
    if v.max_je_notiz is None:
        return list(kandidaten[: v.k])
    je_notiz: Counter[str] = Counter()
    out: list[Treffer] = []
    for t in kandidaten:
        if je_notiz[t.notiz] < v.max_je_notiz:
            out.append(t)
            je_notiz[t.notiz] += 1
        if len(out) == v.k:
            break
    return out


def formatiert(treffer: Sequence[Treffer]) -> str:
    """Nachbau von brain-mcp `_format_chunks`, damit die Kosten die echten sind."""
    zeilen = []
    for i, t in enumerate(treffer, 1):
        zeilen.append(
            f"## Result {i} (score: {t.score:.2f}, source: /mnt/f/vault/notes/{t.notiz}.md)"
        )
        zeilen.append(t.text.strip())
        zeilen.append("")
    zeilen.append("*Cache hit: false, latency: 100ms*")
    return "\n".join(zeilen)


def erster_rang(treffer: Sequence[Treffer], pruefe: Callable[[Treffer], bool]) -> int | None:
    return next((i for i, t in enumerate(treffer, 1) if pruefe(t)), None)


def bewerte(fall: Fall, treffer: Sequence[Treffer]) -> dict[str, Any]:
    """Messwerte eines Falls fuer eine Auswahl von Treffern."""
    soll_notizen = {s["notiz"] for s in fall.soll}
    notiz_rang = erster_rang(treffer, lambda t: t.notiz in soll_notizen)
    stellen_rang = erster_rang(
        treffer,
        lambda t: any(
            t.notiz == s["notiz"] and norm(s["beleg"]) in norm(t.text) for s in fall.soll
        ),
    )
    abgedeckt = sum(
        any(t.notiz == s["notiz"] and norm(s["beleg"]) in norm(t.text) for t in treffer)
        for s in fall.soll
    )
    nutz = sum(
        len(t.text)
        for t in treffer
        if any(t.notiz == s["notiz"] and norm(s["beleg"]) in norm(t.text) for s in fall.soll)
    )
    gesamt = sum(len(t.text) for t in treffer)
    notizen = [t.notiz for t in treffer]
    return {
        "notiz_rang": notiz_rang,
        "stellen_rang": stellen_rang,
        "abdeckung": abgedeckt / len(fall.soll) if fall.soll else None,
        "zeichen": len(formatiert(treffer)),
        "nutzanteil": nutz / gesamt if gesamt else 0.0,
        "dubletten": len(notizen) - len(set(notizen)),
        "leck": sorted({t.notiz for t in treffer if t.notiz in fall.darf_nicht}),
        "bester_score": treffer[0].score if treffer else None,
    }


def zusammenfassen(zeilen: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Kennzahlen einer Variante. Faelle ohne Sollstelle zaehlen nur bei den Kosten mit."""
    mit_soll = [z for z in zeilen if z["typ"] not in ("negativ", "geschuetzt")]
    n = len(mit_soll) or 1

    def recall(schluessel: str, k: int) -> float:
        return sum(1 for z in mit_soll if z[schluessel] is not None and z[schluessel] <= k) / n

    zeichen = sorted(z["zeichen"] for z in zeilen)
    return {
        "faelle": len(zeilen),
        "notiz_recall@1": recall("notiz_rang", 1),
        "notiz_recall@k": recall("notiz_rang", 99),
        "stellen_recall@1": recall("stellen_rang", 1),
        "stellen_recall@3": recall("stellen_rang", 3),
        "stellen_recall@k": recall("stellen_rang", 99),
        "abdeckung": statistics.mean(z["abdeckung"] for z in mit_soll) if mit_soll else 0.0,
        "mrr": sum(1 / z["stellen_rang"] for z in mit_soll if z["stellen_rang"]) / n,
        "zeichen_median": statistics.median(zeichen),
        "zeichen_p90": zeichen[int(0.9 * (len(zeichen) - 1))],
        "tokens_median": round(statistics.median(zeichen) / ZEICHEN_JE_TOKEN),
        "nutzanteil": statistics.mean(z["nutzanteil"] for z in mit_soll) if mit_soll else 0.0,
        "dubletten_je_frage": statistics.mean(z["dubletten"] for z in zeilen),
        "leck": sum(1 for z in zeilen if z["leck"]),
    }


def suche(
    url: str, frage: str, kompakt: int | None, zerlegen: bool = False
) -> tuple[list[Treffer], int]:
    payload: dict[str, Any] = {
        "query": frage,
        "top_k": KANDIDATEN,
        # Default wie der Connector: ohne Zerlegung. Ohne Cache, sonst misst der zweite
        # Lauf ihn.
        "use_decompose": zerlegen,
        "use_cache": False,
    }
    if kompakt:
        payload |= {"kompakt": True, "kompakt_zeichen": kompakt}
    t0 = time.perf_counter()
    resp = requests.post(f"{url}/search", json=payload, timeout=120)
    resp.raise_for_status()
    ms = int((time.perf_counter() - t0) * 1000)
    treffer = [
        Treffer(
            notiz=Path(c["source_path"]).stem,
            text=c["text"],
            score=c["score"],
            quelle=c.get("metadata", {}).get("quelle"),
        )
        for c in resp.json()["chunks"]
    ]
    return treffer, ms


def lauf(
    faelle: Sequence[Fall], url: str, kompakt: int | None, zerlegen: bool = False
) -> list[dict[str, Any]]:
    zeilen = []
    for i, fall in enumerate(faelle, 1):
        kandidaten, ms = suche(url, fall.frage, kompakt, zerlegen)
        zeile: dict[str, Any] = {"id": fall.id, "typ": fall.typ, "latenz_ms": ms}
        for v in VARIANTEN:
            zeile[v.name] = bewerte(fall, auswahl(kandidaten, v))
        zeile["kandidaten"] = [t.notiz for t in kandidaten]
        zeilen.append(zeile)
        if i % 25 == 0:
            log.info("%d/%d Faelle", i, len(faelle))
    return zeilen


def bericht(label: str, zeilen: Sequence[dict[str, Any]]) -> str:
    out = [f"# Vault-Eval `{label}` – {date.today().isoformat()}", ""]
    latenz = sorted(z["latenz_ms"] for z in zeilen)
    out.append(
        f"{len(zeilen)} Faelle, Latenz p50 {statistics.median(latenz):.0f} ms, "
        f"p95 {latenz[int(0.95 * (len(latenz) - 1))]} ms"
    )
    out += ["", "## Varianten", ""]
    spalten = [
        "stellen_recall@1",
        "stellen_recall@3",
        "stellen_recall@k",
        "notiz_recall@k",
        "abdeckung",
        "mrr",
        "tokens_median",
        "zeichen_p90",
        "nutzanteil",
        "dubletten_je_frage",
        "leck",
    ]
    out.append("| Variante | " + " | ".join(spalten) + " |")
    out.append("|---" * (len(spalten) + 1) + "|")
    for v in VARIANTEN:
        s = zusammenfassen([{"typ": z["typ"], **z[v.name]} for z in zeilen])
        out.append(f"| {v.name} | " + " | ".join(_zahl(s[c]) for c in spalten) + " |")
    out += [
        "",
        "## Stellen-Recall@k je Fragetyp (k5)",
        "",
        "| Typ | n | k3 | k5 | k10 |",
        "|---|---|---|---|---|",
    ]
    for typ in sorted({z["typ"] for z in zeilen}):
        gruppe = [z for z in zeilen if z["typ"] == typ]
        werte = [
            zusammenfassen([{"typ": z["typ"], **z[name]} for z in gruppe])["stellen_recall@k"]
            for name in ("k3", "k5", "k10")
        ]
        out.append(f"| {typ} | {len(gruppe)} | " + " | ".join(_zahl(w) for w in werte) + " |")
    out += ["", "## Negativfragen: bester Score", ""]
    neg = [
        z["k5"]["bester_score"] for z in zeilen if z["typ"] == "negativ" and z["k5"]["bester_score"]
    ]
    pos = [
        z["k5"]["bester_score"]
        for z in zeilen
        if z["typ"] not in ("negativ", "geschuetzt") and z["k5"]["stellen_rang"] == 1
    ]
    if neg and pos:
        out.append(
            f"negativ: median {statistics.median(neg):.2f}, max {max(neg):.2f} · "
            f"Treffer auf Platz 1: median {statistics.median(pos):.2f}, min {min(pos):.2f}"
        )
    verfehlt = [
        z["id"]
        for z in zeilen
        if z["typ"] not in ("negativ", "geschuetzt") and not z["k5"]["stellen_rang"]
    ]
    out += ["", f"## Verfehlt bei k5 ({len(verfehlt)})", "", ", ".join(verfehlt) or "keine"]
    return "\n".join(out) + "\n"


def _zahl(x: Any) -> str:
    return f"{x:.2f}" if isinstance(x, float) else str(x)


def vergleiche(a: str, b: str) -> str:
    za = _lade_lauf(a)
    zb = {z["id"]: z for z in _lade_lauf(b)}
    out = [f"# Vergleich `{a}` → `{b}`", ""]
    for v in VARIANTEN:
        sa = zusammenfassen([{"typ": z["typ"], **z[v.name]} for z in za])
        sb = zusammenfassen([{"typ": z["typ"], **zb[z["id"]][v.name]} for z in za])
        teile = [f"{k} {sa[k]:.2f}→{sb[k]:.2f}" for k in ("stellen_recall@k", "mrr", "nutzanteil")]
        teile.append(f"tokens {sa['tokens_median']}→{sb['tokens_median']}")
        out.append(f"- **{v.name}:** " + ", ".join(teile))
    besser = [
        z["id"] for z in za if not z["k5"]["stellen_rang"] and zb[z["id"]]["k5"]["stellen_rang"]
    ]
    schlechter = [
        z["id"] for z in za if z["k5"]["stellen_rang"] and not zb[z["id"]]["k5"]["stellen_rang"]
    ]
    out += [
        "",
        f"k5 neu gefunden: {', '.join(besser) or 'keine'}",
        f"k5 verloren: {', '.join(schlechter) or 'keine'}",
    ]
    return "\n".join(out) + "\n"


def _lade_lauf(label: str) -> list[dict[str, Any]]:
    pfade = sorted(LOG_DIR.glob(f"*_{label}.jsonl"))
    if not pfade:
        raise SystemExit(f"Kein Lauf mit Label {label!r} in {LOG_DIR}.")
    return [json.loads(z) for z in pfade[-1].read_text(encoding="utf-8").splitlines()]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--label", default="baseline")
    p.add_argument("--goldset", type=Path, default=settings.vault_root / ".eval" / "goldset.jsonl")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument(
        "--kompakt",
        type=int,
        metavar="ZEICHEN",
        help="Treffer extraktiv auf ZEICHEN je Treffer kuerzen",
    )
    p.add_argument("--zerlegen", action="store_true", help="Frage per Phi-4 in Teilfragen zerlegen")
    p.add_argument("--compare", nargs=2, metavar=("A", "B"))
    args = p.parse_args()

    if args.compare:
        print(vergleiche(*args.compare))
        return

    faelle = lade_goldset(args.goldset)
    weg = verwaiste(faelle, settings.vault_root)
    if weg:
        log.warning("%d verwaiste Faelle uebersprungen: %s", len(weg), ", ".join(sorted(weg)))
    faelle = [f for f in faelle if f.id not in weg]

    zeilen = lauf(faelle, args.url, args.kompakt, args.zerlegen)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamm = LOG_DIR / f"{date.today().isoformat()}_{args.label}"
    stamm.with_suffix(".jsonl").write_text(
        "".join(json.dumps(z, ensure_ascii=False) + "\n" for z in zeilen), encoding="utf-8"
    )
    text = bericht(args.label, zeilen)
    stamm.with_suffix(".md").write_text(text, encoding="utf-8")
    print(text)

    lecks = [z["id"] for z in zeilen if any(z[v.name]["leck"] for v in VARIANTEN)]
    if lecks:
        raise SystemExit(f"LECKAGE: geschuetzte Notizen in {lecks}")


if __name__ == "__main__":
    main()
