"""
evaluate.py – Epic 4: Automatisierte Evaluation (LLM-as-a-Judge)
================================================================
Pipeline (RAG-Triade):
  1. Context Relevance: Sind die Chunks relevant für die Frage?
  2. Groundedness: Basiert die Antwort auf den Chunks?
  3. Answer Relevance: Beantwortet die Antwort die Frage?

Aufruf:
    python -m titan.evaluate
    python -m titan.evaluate --summary

Umgebungsvariablen (via .env):
    OLLAMA_URL      – Standard: http://localhost:11434
    JUDGE_MODEL     – Standard: phi4:latest
    JUDGE_TIMEOUT   – Timeout in Sekunden (Standard: 60)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import re
import sys
import textwrap
import time
from collections import defaultdict
from typing import Any

import requests

from titan.config import settings
from titan.utils import sanitize

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
# Sektion A — Konfiguration & Konstanten
# ════════════════════════════════════════════════════════════════════════════
OLLAMA_URL: str = settings.ollama_url
JUDGE_MODEL: str = settings.judge_model
JUDGE_TIMEOUT: int = settings.judge_timeout
JUDGE_NUM_PREDICT: int = 512

LABELS_CR = ["RELEVANT", "TEILWEISE_RELEVANT", "IRRELEVANT"]
LABELS_GR = ["VOLLSTÄNDIG_BELEGT", "TEILWEISE_BELEGT", "NICHT_BELEGT", "WIDERSPRÜCHLICH"]
LABELS_AR = ["BEANTWORTET", "TEILWEISE_BEANTWORTET", "NICHT_BEANTWORTET", "ABLENKUNG"]

PROMPT_CR = textwrap.dedent("""\
    Du bist ein objektiver Bewerter für RAG-Systeme.
    Deine Aufgabe: Bewerte, ob die abgerufenen Kontext-Chunks relevant für die Frage des Nutzers sind.

    ERLAUBTE LABELS:
    - RELEVANT: Der Kontext enthält Informationen, die direkt nützlich sind, um die Frage vollständig zu beantworten.
    - TEILWEISE_RELEVANT: Der Kontext enthält nützliche Informationen, reicht aber allein nicht für eine vollständige Antwort.
    - IRRELEVANT: Der Kontext hat nichts mit der Frage zu tun oder hilft nicht bei der Beantwortung.

    JSON-SCHEMA: Du musst strikt im JSON-Format antworten: {"begruendung": "...", "label": "..."}
    Die Begründung muss zuerst geschrieben werden (Chain-of-Thought), erst danach das Label.

    BEISPIELE:
    Frage: "Was ist ein Apfel?"
    Kontext: "Ein Apfel ist eine essbare Frucht, die vom Apfelbaum stammt."
    Antwort: {"begruendung": "Der Kontext definiert exakt, was ein Apfel ist.", "label": "RELEVANT"}

    Frage: "Wie funktioniert die Photosynthese?"
    Kontext: "Pflanzen sind grün."
    Antwort: {"begruendung": "Der Kontext erwähnt Pflanzen, erklärt aber nicht den Prozess der Photosynthese.", "label": "IRRELEVANT"}

    Frage: "Wer ist der CEO von Microsoft?"
    Kontext: "Satya Nadella ist ein indisch-amerikanischer Manager. Er arbeitet in leitender Position bei Microsoft."
    Antwort: {"begruendung": "Der Kontext nennt Satya Nadella bei Microsoft, bestätigt aber nicht explizit seine Rolle als CEO.", "label": "TEILWEISE_RELEVANT"}
""").strip()

PROMPT_GR = textwrap.dedent("""\
    Du bist ein strenger Faktenprüfer für RAG-Systeme.
    Deine Aufgabe: Bewerte, ob die Antwort exakt und ausschließlich durch den bereitgestellten Kontext belegt wird.

    ERLAUBTE LABELS:
    - VOLLSTÄNDIG_BELEGT: Jede einzelne Aussage in der Antwort findet sich im Kontext wieder.
    - TEILWEISE_BELEGT: Einige Aussagen der Antwort sind belegt, andere Aussagen stammen aus externem Wissen oder fehlen im Kontext.
    - NICHT_BELEGT: Die Antwort enthält Informationen, die gar nicht im Kontext stehen (Halluzination).
    - WIDERSPRÜCHLICH: Die Antwort widerspricht direkt den Fakten im Kontext.

    JSON-SCHEMA: Du musst strikt im JSON-Format antworten: {"begruendung": "...", "label": "..."}
    Die Begründung muss zuerst geschrieben werden (Chain-of-Thought), erst danach das Label.

    BEISPIELE:
    Kontext: "Paris ist die Hauptstadt von Frankreich."
    Antwort: "Die Hauptstadt von Frankreich ist Paris."
    Bewertung: {"begruendung": "Die Aussage der Antwort ist zu 100% im Kontext enthalten.", "label": "VOLLSTÄNDIG_BELEGT"}

    Kontext: "Der Apfel ist rot."
    Antwort: "Der Apfel ist rot und schmeckt süß."
    Bewertung: {"begruendung": "Dass der Apfel rot ist, ist belegt. Dass er süß schmeckt, steht nicht im Kontext und ist eine Halluzination.", "label": "TEILWEISE_BELEGT"}

    Kontext: "Der Himmel ist blau."
    Antwort: "Der Himmel ist grün."
    Bewertung: {"begruendung": "Die Antwort behauptet, der Himmel sei grün, was dem Kontext ('blau') direkt widerspricht.", "label": "WIDERSPRÜCHLICH"}
""").strip()

PROMPT_AR = textwrap.dedent("""\
    Du bist ein Qualitätsprüfer für KI-Antworten.
    Deine Aufgabe: Bewerte, ob die generierte Antwort tatsächlich die ursprüngliche Frage des Nutzers beantwortet.

    ERLAUBTE LABELS:
    - BEANTWORTET: Die Antwort geht direkt, klar und präzise auf die Frage ein.
    - TEILWEISE_BEANTWORTET: Die Antwort ist vage oder beantwortet nur einen Teil einer mehrteiligen Frage.
    - NICHT_BEANTWORTET: Die Antwort weicht aus oder erklärt, dass sie die Frage nicht beantworten kann.
    - ABLENKUNG: Die Antwort spricht über ein völlig anderes Thema als in der Frage verlangt.

    JSON-SCHEMA: Du musst strikt im JSON-Format antworten: {"begruendung": "...", "label": "..."}
    Die Begründung muss zuerst geschrieben werden (Chain-of-Thought), erst danach das Label.

    BEISPIELE:
    Frage: "Wie hoch ist der Eiffelturm?"
    Antwort: "Der Eiffelturm ist 330 Meter hoch."
    Bewertung: {"begruendung": "Die Antwort liefert exakt die geforderte Information zur Höhe.", "label": "BEANTWORTET"}

    Frage: "Wer schrieb Faust und wann wurde es veröffentlicht?"
    Antwort: "Faust wurde von Johann Wolfgang von Goethe geschrieben."
    Bewertung: {"begruendung": "Die Antwort nennt den Autor, lässt aber das Veröffentlichungsdatum weg.", "label": "TEILWEISE_BEANTWORTET"}

    Frage: "Wie funktioniert ein Quantencomputer?"
    Antwort: "Klassische Computer basieren auf Bits, die 0 oder 1 sein können."
    Bewertung: {"begruendung": "Die Antwort weicht auf klassische Computer aus und erklärt nicht die Quantenmechanik.", "label": "ABLENKUNG"}
""").strip()


# ════════════════════════════════════════════════════════════════════════════
# Sektion B — Judge-Core
# ════════════════════════════════════════════════════════════════════════════


def parse_response(raw: str, valid_labels: list[str]) -> tuple[str, str]:
    """3-stufiger Fallback-Parser für die Judge-Antwort.

    Args:
        raw: Rohe LLM-Antwort.
        valid_labels: Liste der erlaubten Label-Strings.

    Returns:
        Tuple (label, begruendung). Bei Fehler: ("PARSING_FAILED", raw[:200]).
    """
    raw_str = raw.strip()

    # Stufe 1: JSON Parse
    try:
        data: dict[str, Any] = json.loads(raw_str)
        lbl = data.get("label", "").strip().upper()
        if lbl in valid_labels:
            return lbl, str(data.get("begruendung", raw_str))
    except json.JSONDecodeError:
        pass

    # Stufe 2: Regex nach [LABEL]
    match = re.search(r"\[([A-ZÄÖÜ_]+)\]", raw_str)
    if match:
        lbl = match.group(1)
        if lbl in valid_labels:
            return lbl, raw_str

    return "PARSING_FAILED", raw_str[:200]


def call_judge(prompt: str, valid_labels: list[str]) -> dict[str, Any]:
    """POST an Ollama mit format="json" und keep_alive=0.

    Args:
        prompt: Der Bewertungs-Prompt.
        valid_labels: Erlaubte Label-Strings für den Parser.

    Returns:
        Dict mit label, begruendung, latency_ms, raw.
    """
    url = f"{OLLAMA_URL}/api/generate"
    payload: dict[str, Any] = {
        "model": JUDGE_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "keep_alive": 0,
        "options": {
            "temperature": 0.0,
            "num_predict": JUDGE_NUM_PREDICT,
        },
    }

    t0 = time.perf_counter()
    try:
        resp = requests.post(url, json=payload, timeout=JUDGE_TIMEOUT)
        resp.raise_for_status()
        raw: str = resp.json().get("response", "")
        latency = int((time.perf_counter() - t0) * 1000)

        label, begruendung = parse_response(raw, valid_labels)
        return {
            "label": label,
            "begruendung": begruendung,
            "latency_ms": latency,
            "raw": raw,
        }
    except requests.exceptions.ConnectionError:
        log.error("Ollama nicht erreichbar für Judge-Call.")
        return {
            "label": "JUDGE_UNAVAILABLE",
            "begruendung": "Ollama Service offline",
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "raw": "",
        }
    except requests.exceptions.Timeout:
        log.error("Timeout beim Judge-Call.")
        return {
            "label": "JUDGE_TIMEOUT",
            "begruendung": f"Zeitüberschreitung nach {JUDGE_TIMEOUT}s",
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "raw": "",
        }


# ════════════════════════════════════════════════════════════════════════════
# Sektion C — RAG-Triade-Metriken
# ════════════════════════════════════════════════════════════════════════════


def eval_context_relevance(query: str, contexts: list[str]) -> dict[str, Any]:
    """Bewertet ob die abgerufenen Chunks für die Query relevant sind."""
    q_safe = sanitize(query)
    c_safe = "\n\n".join([f"[Chunk {i + 1}]\n{sanitize(c)}" for i, c in enumerate(contexts)])

    prompt = (
        f"{PROMPT_CR}\n\n"
        f"=== EINGABE ===\n"
        f"Frage: {q_safe}\n\n"
        f"Kontext:\n{c_safe}\n\n"
        f"=== DEINE BEWERTUNG (als JSON) ==="
    )
    return call_judge(prompt, LABELS_CR)


def eval_groundedness(answer: str, contexts: list[str]) -> dict[str, Any]:
    """Bewertet ob die Antwort durch den Kontext gedeckt ist."""
    a_safe = sanitize(answer)
    c_safe = "\n\n".join([f"[Chunk {i + 1}]\n{sanitize(c)}" for i, c in enumerate(contexts)])

    prompt = (
        f"{PROMPT_GR}\n\n"
        f"=== EINGABE ===\n"
        f"Kontext:\n{c_safe}\n\n"
        f"Antwort: {a_safe}\n\n"
        f"=== DEINE BEWERTUNG (als JSON) ==="
    )
    return call_judge(prompt, LABELS_GR)


def eval_answer_relevance(query: str, answer: str) -> dict[str, Any]:
    """Bewertet ob die Antwort die ursprüngliche Frage beantwortet."""
    q_safe = sanitize(query)
    a_safe = sanitize(answer)

    prompt = (
        f"{PROMPT_AR}\n\n"
        f"=== EINGABE ===\n"
        f"Frage: {q_safe}\n\n"
        f"Antwort: {a_safe}\n\n"
        f"=== DEINE BEWERTUNG (als JSON) ==="
    )
    return call_judge(prompt, LABELS_AR)


# ════════════════════════════════════════════════════════════════════════════
# Sektion D — Orchestrierung
# ════════════════════════════════════════════════════════════════════════════


def evaluate_triad(query: str, contexts: list[str], answer: str) -> dict[str, Any]:
    """Ruft alle drei RAG-Triade-Metriken sequenziell auf.

    Args:
        query: Die ursprüngliche Suchanfrage.
        contexts: Liste der abgerufenen Chunk-Texte.
        answer: Die generierte Antwort.

    Returns:
        Dict mit context_relevance, groundedness, answer_relevance, overall_pass.
    """
    if not query or not contexts or not answer:
        log.warning("Leere Eingabe für Triade, breche ab.")
        return {"overall_pass": False, "error": "Leere Eingabedaten"}

    t0 = time.perf_counter()

    res_cr = eval_context_relevance(query, contexts)
    res_gr = eval_groundedness(answer, contexts)
    res_ar = eval_answer_relevance(query, answer)

    total_latency = int((time.perf_counter() - t0) * 1000)

    overall_pass = (
        res_cr["label"] == "RELEVANT"
        and res_gr["label"] == "VOLLSTÄNDIG_BELEGT"
        and res_ar["label"] == "BEANTWORTET"
    )

    return {
        "query": query,
        "context_relevance": res_cr,
        "groundedness": res_gr,
        "answer_relevance": res_ar,
        "overall_pass": overall_pass,
        "total_latency_ms": total_latency,
    }


# ════════════════════════════════════════════════════════════════════════════
# Sektion E — Aggregation
# ════════════════════════════════════════════════════════════════════════════


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Berechnet Batch-Statistiken für Regressionstests.

    Args:
        results: Liste von evaluate_triad()-Outputs.

    Returns:
        Aggregiertes Summary-Dict.
    """
    labels_summary: dict[str, dict[str, int]] = {
        "context_relevance": defaultdict(int),
        "groundedness": defaultdict(int),
        "answer_relevance": defaultdict(int),
    }
    summary: dict[str, Any] = {
        "total_cases": len(results),
        "overall_pass_rate": 0.0,
        "parsing_failed_count": 0,
        "judge_unavailable_count": 0,
        "avg_total_latency_ms": 0.0,
        "labels": labels_summary,
    }

    if not results:
        return summary

    passes = 0
    tot_lat = 0

    for r in results:
        if r.get("overall_pass"):
            passes += 1
        tot_lat += r.get("total_latency_ms", 0)

        for metric in ("context_relevance", "groundedness", "answer_relevance"):
            m_res = r.get(metric, {})
            lbl: str = m_res.get("label", "UNKNOWN")
            labels_summary[metric][lbl] += 1
            if lbl == "PARSING_FAILED":
                summary["parsing_failed_count"] += 1
            if lbl in ("JUDGE_UNAVAILABLE", "JUDGE_TIMEOUT"):
                summary["judge_unavailable_count"] += 1

    summary["overall_pass_rate"] = passes / len(results)
    summary["avg_total_latency_ms"] = tot_lat / len(results)

    return summary


# ════════════════════════════════════════════════════════════════════════════
# Sektion F — Einstiegspunkt
# ════════════════════════════════════════════════════════════════════════════


def main() -> None:
    """CLI entry point für python -m titan.evaluate."""
    parser = argparse.ArgumentParser(description="Epic 4: RAG Evaluation (LLM-as-a-Judge)")
    parser.add_argument("--summary", action="store_true", help="Nur Zusammenfassung anzeigen")
    args = parser.parse_args()

    case_1: dict[str, Any] = {
        "query": "Was ist der HNSW-Algorithmus?",
        "contexts": [
            "HNSW (Hierarchical Navigable Small World) ist ein Algorithmus für die schnelle "
            "ungefähre Nächste-Nachbarn-Suche in hochdimensionalen Räumen. "
            "Er basiert auf Multi-Layer-Graphen."
        ],
        "answer": "Der HNSW-Algorithmus ist ein Verfahren zur schnellen ungefähren Suche von "
        "nächsten Nachbarn in hochdimensionalen Vektorräumen, welches auf mehrschichtigen "
        "Graphen basiert.",
    }

    case_2: dict[str, Any] = {
        "query": "Was ist der HNSW-Algorithmus?",
        "contexts": [
            "HNSW (Hierarchical Navigable Small World) ist ein Algorithmus für die schnelle "
            "ungefähre Nächste-Nachbarn-Suche in hochdimensionalen Räumen. "
            "Er basiert auf Multi-Layer-Graphen."
        ],
        "answer": "HNSW wurde 1995 von Tim Berners-Lee in Paris als revolutionäres "
        "Verschlüsselungsverfahren für Bitcoin-Transaktionen entwickelt.",
    }

    case_3: dict[str, Any] = {
        "query": "Basiert HNSW auf Graphen oder Bäumen?",
        "contexts": [
            "HNSW (Hierarchical Navigable Small World) ist ein Algorithmus für die schnelle "
            "ungefähre Nächste-Nachbarn-Suche in hochdimensionalen Räumen. "
            "Er basiert auf Multi-Layer-Graphen."
        ],
        "answer": "Der HNSW-Algorithmus basiert vollständig auf binären Suchbäumen und nutzt "
        "keine Graphen-Strukturen.",
    }

    log.info("Starte Evaluation - Case 1 (Positiv) ...")
    res_1 = evaluate_triad(case_1["query"], case_1["contexts"], case_1["answer"])

    log.info("Starte Evaluation - Case 2 (Halluzination) ...")
    res_2 = evaluate_triad(case_2["query"], case_2["contexts"], case_2["answer"])

    log.info("Starte Evaluation - Case 3 (Widerspruch) ...")
    res_3 = evaluate_triad(case_3["query"], case_3["contexts"], case_3["answer"])

    results = [res_1, res_2, res_3]

    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    if args.summary:
        print(json.dumps(summarize_results(results), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
