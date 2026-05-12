"""
generate.py – Phase 6: Kontext-Optimierung & Antwortgenerierung
================================================================
Pipeline:
  1. Nimmt Top-K Chunks (JSON von search.py) oder direkt als Argument
  2. LongContextReorder: Bester Chunk vorne, zweitbester hinten, Rest in der Mitte
  3. Prompt-Zusammenbau mit strukturiertem Kontext
  4. Ollama-Aufruf (Streaming) → Antwort auf stdout

Aufruf:
    # Direkt mit Pipe aus search.py:
    python -m titan.search "Was ist RAG?" --json | python -m titan.generate

    # Mit explizitem JSON-File:
    python -m titan.generate --chunks-file results.json

    # Mit eigenem Modell:
    python -m titan.generate --model llama3.1:70b

Umgebungsvariablen (via .env):
    OLLAMA_URL      – Standard: http://localhost:11434
    OLLAMA_MODEL    – Standard: phi4:latest
    OLLAMA_TIMEOUT  – Timeout in Sekunden (Standard: 120)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from titan.utils import sanitize

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Konfiguration ───────────────────────────────────────────────────────────
load_dotenv()

OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "phi4:latest")
OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "120"))


# ════════════════════════════════════════════════════════════════════════════
# PHASE 6a – LongContextReorder
# ════════════════════════════════════════════════════════════════════════════


def long_context_reorder(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """LongContextReorder-Heuristik nach Liu et al. (2023).

    "Lost in the Middle: How Language Models Use Long Contexts"

    LLMs tendieren dazu, Informationen am Anfang und Ende eines langen
    Kontexts besser zu verarbeiten als in der Mitte ("lost-in-the-middle").

    Strategie für N Chunks (sortiert nach Relevanz, Rang 1 = bester):
        Position 0       → Rang 1  (bester, LLM liest zuerst)
        Position N-1     → Rang 2  (zweitbester, LLM liest zuletzt)
        Positionen 1..N-2 → Rang 3, 4, 5, … (Mitte, weniger Aufmerksamkeit)

    Beispiel für 5 Chunks [1,2,3,4,5] → [1, 3, 4, 5, 2]

    Args:
        chunks: Liste von Chunk-Dicts, sortiert nach Relevanz (bester zuerst).

    Returns:
        Umgeordnete Chunk-Liste.
    """
    if len(chunks) <= 2:
        return chunks

    best = chunks[0]
    second_best = chunks[1]
    middle = chunks[2:]

    reordered = [best, *middle, second_best]
    log.info("LongContextReorder: %d Chunks umgeordnet (Rang 1 vorne, Rang 2 hinten).", len(chunks))
    return reordered


# ════════════════════════════════════════════════════════════════════════════
# PHASE 6b – Prompt-Zusammenbau
# ════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = textwrap.dedent("""\
    Du bist ein technischer Analyst. Deine Aufgabe ist ausschließlich die präzise
    Beantwortung der gestellten Frage auf Basis der bereitgestellten Kontextabschnitte.

    REGELN (strikt einzuhalten):
    1. Beantworte NUR die gestellte Frage. Nichts darüber hinaus.
    2. Ignoriere Kontext-Informationen, die für die Frage nicht relevant sind.
    3. Keine Einleitungen, keine Floskeln, keine Meta-Kommentare.
       Verboten: "Basierend auf dem Text...", "Laut Kontext...", "Gerne beantworte ich..."
    4. Wenn die Antwort nicht aus dem Kontext ableitbar ist: Antworte exakt mit
       "Die bereitgestellten Informationen enthalten keine Antwort auf diese Frage."
    5. Antworte auf Deutsch. Fachbegriffe bleiben in ihrer Originalsprache.
""").strip()


def build_prompt(query: str, chunks: list[dict[str, Any]]) -> str:
    """Baut den finalen Prompt zusammen.

    Chunks sind bereits per LongContextReorder optimal angeordnet.

    Args:
        query: Die ursprüngliche Frage (wird gesanitized).
        chunks: Chunk-Dicts mit keys text, header, source.

    Returns:
        Fertiger Prompt-String für Ollama.
    """
    query = sanitize(query, max_len=500)

    context_parts = []
    for i, ch in enumerate(chunks):
        header = sanitize(ch.get("header", ""), max_len=200)
        source = sanitize(ch.get("source", ""), max_len=200)
        text = sanitize(ch.get("text", "").strip())
        label = f"[Abschnitt {i + 1}]"
        if header:
            label += f" {header}"
        if source:
            label += f" (Quelle: {source})"
        context_parts.append(f"{label}\n{text}")

    context_block = "\n\n---\n\n".join(context_parts)

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"=== KONTEXT ===\n\n"
        f"{context_block}\n\n"
        f"=== FRAGE ===\n\n"
        f"{query}\n\n"
        f"=== ANTWORT ==="
    )
    log.info("Prompt gebaut: %d Abschnitte, %d Zeichen gesamt.", len(chunks), len(prompt))
    return prompt


# ════════════════════════════════════════════════════════════════════════════
# PHASE 6c – Ollama-Aufruf (Streaming)
# ════════════════════════════════════════════════════════════════════════════


def call_ollama_streaming(prompt: str, model: str) -> str:
    """Sendet den Prompt an die lokale Ollama-Instanz und streamt die Antwort.

    Nutzt /api/generate mit stream=True:
    - Jedes Token erscheint sofort im Terminal (kein Warten auf Komplett-Antwort)
    - Gibt die vollständige Antwort als String zurück (für --json Modus)

    Args:
        prompt: Der fertige Prompt.
        model: Ollama-Modell-Name (z.B. "phi4:latest").

    Returns:
        Die vollständige generierte Antwort als String.
    """
    url = f"{OLLAMA_URL}/api/generate"
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": 0.1,
            "num_predict": 1024,
        },
    }

    log.info("Sende Anfrage an Ollama: model=%s, url=%s", model, url)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        with contextlib.suppress(AttributeError):
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    print(f"\n{'═' * 70}")
    print(f"  Modell: {model}")
    print(f"{'═' * 70}\n")

    full_response: list[str] = []
    try:
        with requests.post(url, json=payload, stream=True, timeout=OLLAMA_TIMEOUT) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    token_data: dict[str, Any] = json.loads(line)
                except json.JSONDecodeError:
                    continue

                token = token_data.get("response", "")
                if token:
                    print(token, end="", flush=True)
                    full_response.append(token)

                if token_data.get("done", False):
                    break

    except requests.exceptions.ChunkedEncodingError:
        log.warning("Ollama-Stream unterbrochen – gebe Teilantwort zurück.")
    except requests.exceptions.ConnectionError:
        log.critical("Ollama nicht erreichbar unter %s. Läuft `ollama serve`?", OLLAMA_URL)
        sys.exit(1)
    except requests.exceptions.Timeout:
        log.error("Ollama-Timeout nach %ds. Modell ggf. zu groß?", OLLAMA_TIMEOUT)
        sys.exit(1)

    print("\n")
    return "".join(full_response)


# ════════════════════════════════════════════════════════════════════════════
# Eingabe: JSON von stdin oder --chunks-file
# ════════════════════════════════════════════════════════════════════════════

_MAX_INPUT_BYTES = 10 * 1024 * 1024  # 10 MB Hard-Cap


def load_chunks(chunks_file: str | None) -> tuple[str, list[dict[str, Any]]]:
    """Liest Chunks aus einer Datei oder stdin.

    Erwartet das Format von search.py:
        [{"rank": 1, "score": 0.9, "text": "...", "header": "...", ...}, ...]

    Args:
        chunks_file: Optionaler Pfad zu einer JSON-Datei. None = stdin.

    Returns:
        Tuple (query, chunks). Query ist leer wenn nicht im JSON enthalten.
    """
    if chunks_file:
        log.info("Lade Chunks aus Datei: %s", chunks_file)
        file_size = Path(chunks_file).stat().st_size
        if file_size > _MAX_INPUT_BYTES:
            log.critical("--chunks-file überschreitet 10 MB (%d Bytes) – Abbruch.", file_size)
            sys.exit(1)
        with open(chunks_file, encoding="utf-8") as f:
            data: Any = json.load(f)
    elif not sys.stdin.isatty():
        log.info("Lese Chunks von stdin …")
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            log.critical("stdin überschreitet 10 MB Hard-Cap – Abbruch.")
            sys.exit(1)
        data = json.loads(raw.decode("utf-8"))
    else:
        log.error(
            "Keine Chunks erhalten. Nutze Pipe: "
            "`python -m titan.search '...' --json | python -m titan.generate` "
            "oder --chunks-file"
        )
        sys.exit(1)

    if isinstance(data, list):
        return "", data
    if isinstance(data, dict):
        chunks_raw: list[dict[str, Any]] = data.get("chunks") or data.get("results") or []
        return data.get("query", ""), chunks_raw
    log.error("Unerwartetes JSON-Format.")
    sys.exit(1)


# ════════════════════════════════════════════════════════════════════════════
# Einstiegspunkt
# ════════════════════════════════════════════════════════════════════════════


def main() -> None:
    """CLI entry point für python -m titan.generate."""
    parser = argparse.ArgumentParser(
        description="Titan Generate – Phase 6: LongContextReorder + Ollama",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--query",
        "-q",
        type=str,
        default="",
        help="Die ursprüngliche Frage (optional wenn in den Chunks enthalten)",
    )
    parser.add_argument(
        "--chunks-file",
        type=str,
        default=None,
        help="Pfad zu einer JSON-Datei mit Chunks (alternativ: stdin)",
    )
    parser.add_argument(
        "--model",
        "-m",
        type=str,
        default=OLLAMA_MODEL,
        help=f"Ollama-Modell (Standard: {OLLAMA_MODEL})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Gibt Antwort als JSON aus",
    )
    parser.add_argument(
        "--no-reorder",
        action="store_true",
        help="LongContextReorder deaktivieren (für Vergleichstests)",
    )
    args = parser.parse_args()

    inferred_query, chunks = load_chunks(args.chunks_file)
    query = args.query or inferred_query

    if not chunks:
        log.error("Keine Chunks zum Verarbeiten.")
        sys.exit(1)

    if not query:
        log.warning(
            "Keine Query übergeben. Nutze --query 'Deine Frage' für einen "
            "besseren Prompt. Fortfahren mit leerem Query-Feld …"
        )

    log.info("Verarbeite %d Chunk(s) für Query: '%s'", len(chunks), query[:80])

    ordered_chunks = chunks if args.no_reorder else long_context_reorder(chunks)
    prompt = build_prompt(query, ordered_chunks)
    answer = call_ollama_streaming(prompt, model=args.model)

    if args.json:
        output = {
            "query": query,
            "answer": answer,
            "model": args.model,
            "sources": [
                {"rank": c.get("rank"), "source": c.get("source"), "header": c.get("header")}
                for c in chunks
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
