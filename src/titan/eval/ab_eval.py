"""
ab_eval.py – Evaluation Suite
==============================
Unterstützt:
  --label <string>                   Label für den Durchlauf (z.B. "baseline", "v2")
  --cases-file <pfad>                Externe Case-Datei (Default: fixtures/cases.json)
  --determinism-test [--runs N]      V1: N× identischer Triade-Call
  --rank-only                        V3: Nur Retrieval-Ränge, kein LLM-Generate/Judge
  --category A|B|C|D|all             Nur bestimmte Kategorie evaluieren

Aufruf:
    python -m titan.eval.ab_eval --label baseline
    python -m titan.eval.ab_eval --determinism-test --runs 5
    python -m titan.eval.ab_eval --rank-only --category B
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

import requests
from dotenv import load_dotenv
from qdrant_client import QdrantClient

from titan.evaluate import eval_answer_relevance, evaluate_triad, summarize_results
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

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "phi4:latest")
QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
QDRANT_GRPC_PORT: int = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
QDRANT_API_KEY: str = os.getenv("QDRANT_API_KEY", "")
COLLECTION_NAME: str = os.getenv("COLLECTION_NAME", "mein_wissen")

# ── Globale Modell-Instanzen (einmal laden, für alle Cases wiederverwenden) ──
_BGE_MODEL: Any = None
_QDRANT_CLIENT: QdrantClient | None = None


def get_bge_model() -> Any:
    """Singleton-Loader für BGE-M3 (teurer GPU-Ladevorgang)."""
    global _BGE_MODEL
    if _BGE_MODEL is None:
        from FlagEmbedding import BGEM3FlagModel

        log.info("Lade BGE-M3 (einmalig für alle Cases)...")
        _BGE_MODEL = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device="cuda")
        log.info("BGE-M3 geladen.")
    return _BGE_MODEL


def get_qdrant_client() -> QdrantClient:
    """Singleton-Loader für Qdrant-Client."""
    global _QDRANT_CLIENT
    if _QDRANT_CLIENT is None:
        _QDRANT_CLIENT = QdrantClient(
            host=QDRANT_HOST,
            grpc_port=QDRANT_GRPC_PORT,
            prefer_grpc=True,
            api_key=QDRANT_API_KEY or None,
            https=False,
            check_compatibility=False,
        )
        log.info("Qdrant-Client verbunden.")
    return _QDRANT_CLIENT


def load_cases(cases_file: str) -> list[dict[str, Any]]:
    """Lädt Eval-Cases aus einer JSON-Datei."""
    with open(cases_file, encoding="utf-8") as f:
        data: Any = json.load(f)
    if isinstance(data, dict):
        return cast(list[dict[str, Any]], data.get("cases", data))
    return cast(list[dict[str, Any]], data)


def run_search(query: str, domain: str | None, top_k: int = 5) -> list[dict[str, Any]]:
    """Dense-Search mit optionalem Domain-Filter."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    model = get_bge_model()
    client = get_qdrant_client()

    result: dict[str, Any] = model.encode(
        [query], return_dense=True, return_sparse=True, return_colbert_vecs=False
    )
    dense_vec: list[float] = result["dense_vecs"][0].tolist()

    domain_filter = None
    if domain:
        domain_filter = Filter(must=[FieldCondition(key="domain", match=MatchValue(value=domain))])

    hits = client.query_points(
        collection_name=COLLECTION_NAME,
        query=dense_vec,
        using="dense",
        query_filter=domain_filter,
        limit=top_k,
        with_payload=True,
    )

    chunks: list[dict[str, Any]] = []
    for i, point in enumerate(hits.points):
        chunks.append(
            {
                "rank": i + 1,
                "score": point.score,
                "text": point.payload.get("text", "") if point.payload else "",
                "header": point.payload.get("header", "") if point.payload else "",
                "source": point.payload.get("source", "") if point.payload else "",
            }
        )
    return chunks


def run_generate(query: str, chunks: list[dict[str, Any]]) -> str:
    """Nicht-Streaming Ollama-Call für Evaluation."""
    context_parts = []
    for i, chunk in enumerate(chunks):
        header = sanitize(chunk.get("header", ""), max_len=200)
        source = sanitize(chunk.get("source", ""), max_len=200)
        text = sanitize(chunk.get("text", "").strip())
        label = f"[Abschnitt {i + 1}]"
        if header:
            label += f" {header}"
        if source:
            label += f" (Quelle: {source})"
        context_parts.append(f"{label}\n{text}")

    context_block = "\n\n---\n\n".join(context_parts)

    system_prompt = (
        "Du bist ein technischer Analyst. Beantworte NUR die gestellte Frage auf Basis "
        "der Kontextabschnitte. Keine Einleitungen, keine Floskeln. Antworte auf Deutsch. "
        "Wenn die Antwort nicht ableitbar ist: 'Die bereitgestellten Informationen enthalten "
        "keine Antwort auf diese Frage.'"
    )

    prompt = (
        f"{system_prompt}\n\n"
        f"=== KONTEXT ===\n\n{context_block}\n\n"
        f"=== FRAGE ===\n\n{sanitize(query, max_len=500)}\n\n"
        f"=== ANTWORT ==="
    )

    payload: dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": 0,
        "options": {"temperature": 0.1, "num_predict": 512},
    }

    try:
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120)
        resp.raise_for_status()
        return str(resp.json().get("response", "")).strip()
    except requests.exceptions.RequestException as exc:
        log.error("Generate fehlgeschlagen: %s", exc)
        return ""


def find_keyword_rank(chunks: list[dict[str, Any]], keyword: str) -> int | None:
    """Gibt den Rang des ersten Chunks zurück, der das Keyword enthält."""
    if not keyword:
        return None
    for chunk in chunks:
        if keyword.lower() in chunk.get("text", "").lower():
            return int(chunk["rank"])
    return None


# ════════════════════════════════════════════════════════════════════════════
# V1 — Determinismus-Test
# ════════════════════════════════════════════════════════════════════════════


def run_determinism_test(runs: int = 10) -> dict[str, Any]:
    """N× identischer Triade-Call, Label-Stabilität messen."""
    log.info("═══ V1: DETERMINISMUS-TEST (%d Runs) ═══", runs)

    query = "Welche technischen Indikatoren nutzt der Algorithmus?"
    chunks = run_search(query, domain="trading", top_k=4)
    contexts = [c["text"] for c in chunks if c.get("text")]
    answer = run_generate(query, chunks)

    log.info("  Fixierte Antwort: %s...", answer[:80])
    log.info("  Fixierte Contexts: %d Chunks", len(contexts))

    results: list[dict[str, Any]] = []
    for run_idx in range(runs):
        t0 = time.perf_counter()
        triad = evaluate_triad(query, contexts, answer)
        latency = int((time.perf_counter() - t0) * 1000)

        cr = triad["context_relevance"]["label"]
        gr = triad["groundedness"]["label"]
        ar = triad["answer_relevance"]["label"]

        results.append({"run": run_idx + 1, "CR": cr, "GR": gr, "AR": ar, "latency_ms": latency})
        log.info(
            "  Run %d/%d: CR=%s | GR=%s | AR=%s (%dms)", run_idx + 1, runs, cr, gr, ar, latency
        )

    cr_labels = [r["CR"] for r in results]
    gr_labels = [r["GR"] for r in results]
    ar_labels = [r["AR"] for r in results]

    cr_mode = max(set(cr_labels), key=cr_labels.count)
    gr_mode = max(set(gr_labels), key=gr_labels.count)
    ar_mode = max(set(ar_labels), key=ar_labels.count)

    cr_stable = cr_labels.count(cr_mode)
    gr_stable = gr_labels.count(gr_mode)
    ar_stable = ar_labels.count(ar_mode)

    log.info("  DETERMINISMUS-ERGEBNIS")
    log.info("  CR Stabilität: %d/%d (Mode: %s)", cr_stable, runs, cr_mode)
    log.info("  GR Stabilität: %d/%d (Mode: %s)", gr_stable, runs, gr_mode)
    log.info("  AR Stabilität: %d/%d (Mode: %s)", ar_stable, runs, ar_mode)
    log.info("  Gesamt: %d/%d schwächste Metrik", min(cr_stable, gr_stable, ar_stable), runs)

    return {
        "test": "determinism",
        "runs": runs,
        "query": query,
        "answer": answer[:200],
        "results": results,
        "stability": {
            "CR": {"stable": cr_stable, "total": runs, "mode": cr_mode},
            "GR": {"stable": gr_stable, "total": runs, "mode": gr_mode},
            "AR": {"stable": ar_stable, "total": runs, "mode": ar_mode},
            "min_stability": min(cr_stable, gr_stable, ar_stable),
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# V3 — Rank-Only Mode
# ════════════════════════════════════════════════════════════════════════════


def run_rank_comparison(cases: list[dict[str, Any]], label: str) -> dict[str, Any]:
    """Für jeden Case: Rang des Keyword-Chunks ermitteln."""
    log.info("═══ V3: RANK-VERGLEICH [%s] ═══", label.upper())

    results: list[dict[str, Any]] = []
    for i, case in enumerate(cases):
        case_id = case.get("id", str(i + 1))
        query: str = case["query"]
        domain: str | None = case.get("domain")
        keyword: str = case.get("expected_keyword", "")

        log.info("  [%s] %s...", case_id, query[:50])

        chunks = run_search(query, domain, top_k=5)
        rank = find_keyword_rank(chunks, keyword)

        secondary_kw: str = case.get("expected_secondary_keyword", "")
        secondary_rank = find_keyword_rank(chunks, secondary_kw) if secondary_kw else None

        expected_src: str = case.get("expected_source", "")
        source_found: bool | None = None
        if expected_src and expected_src != "both":
            source_found = any(expected_src in c.get("source", "") for c in chunks[:3])

        result: dict[str, Any] = {
            "case_id": case_id,
            "category": case.get("category", "?"),
            "query": query[:60],
            "keyword": keyword,
            "keyword_rank": rank,
            "secondary_keyword": secondary_kw,
            "secondary_rank": secondary_rank,
            "source_in_top3": source_found,
            "top3_sources": [c.get("source", "?") for c in chunks[:3]],
        }
        results.append(result)

        rank_str = f"Rang {rank}" if rank else "NICHT GEFUNDEN"
        log.info("    → Keyword '%s': %s", keyword, rank_str)

    return {"label": label, "rank_results": results}


# ════════════════════════════════════════════════════════════════════════════
# V4 — Voller Eval
# ════════════════════════════════════════════════════════════════════════════


def run_full_eval(cases: list[dict[str, Any]], label: str) -> dict[str, Any]:
    """Voller Search → Generate → Judge Durchlauf für alle Cases."""
    log.info("═══ EVAL [%s] — %d Cases ═══", label.upper(), len(cases))

    results: list[dict[str, Any]] = []
    for i, case in enumerate(cases):
        case_id: str = case.get("id", str(i + 1))
        category: str = case.get("category", "?")
        query: str = case["query"]
        domain: str | None = case.get("domain")
        keyword: str = case.get("expected_keyword", "")
        secondary_kw: str = case.get("expected_secondary_keyword", "")

        log.info("── [%s] (%s) %s...", case_id, category, query[:55])

        chunks = run_search(query, domain, top_k=4)
        if not chunks:
            log.warning("  Keine Chunks für [%s], überspringe.", case_id)
            continue

        contexts = [c["text"] for c in chunks if c.get("text")]
        log.info("  %d Chunks gefunden", len(contexts))

        answer = run_generate(query, chunks)
        if not answer:
            log.warning("  Keine Antwort für [%s], überspringe.", case_id)
            continue

        log.info("  Antwort: %s...", answer[:80])

        kw_found: bool | None = keyword.lower() in answer.lower() if keyword else None
        sec_found: bool | None = secondary_kw.lower() in answer.lower() if secondary_kw else None

        if keyword and kw_found:
            log.info("  Keyword '%s' gefunden", keyword)
        elif keyword:
            log.warning("  Keyword '%s' NICHT in Antwort", keyword)

        if secondary_kw and sec_found:
            log.info("  Secondary '%s' gefunden", secondary_kw)
        elif secondary_kw:
            log.warning("  Secondary '%s' NICHT in Antwort", secondary_kw)

        expect_no_answer: bool = case.get("expect_no_answer", False)
        no_answer_sentinel = (
            "keine antwort" in answer.lower()
            or "nicht beantwortet" in answer.lower()
            or "enthalten keine" in answer.lower()
        )
        if expect_no_answer:
            if no_answer_sentinel:
                log.info("  OOD korrekt abgelehnt")
            else:
                log.warning("  OOD nicht erkannt — potentielle Halluzination")

        triad: dict[str, Any]
        if not expect_no_answer:
            triad = evaluate_triad(query, contexts, answer)
        else:
            ar = eval_answer_relevance(query, answer)
            triad = {
                "query": query,
                "context_relevance": {
                    "label": "N/A_OOD",
                    "begruendung": "Out-of-Corpus",
                    "latency_ms": 0,
                },
                "groundedness": {
                    "label": "N/A_OOD",
                    "begruendung": "Out-of-Corpus",
                    "latency_ms": 0,
                },
                "answer_relevance": ar,
                "overall_pass": no_answer_sentinel,
                "total_latency_ms": ar["latency_ms"],
            }

        triad["case_id"] = case_id
        triad["category"] = category
        triad["domain"] = domain
        triad["expected_keyword"] = keyword
        triad["keyword_found"] = kw_found
        triad["secondary_keyword"] = secondary_kw
        triad["secondary_found"] = sec_found
        triad["expect_no_answer"] = expect_no_answer
        triad["no_answer_detected"] = no_answer_sentinel if expect_no_answer else None
        triad["keyword_rank"] = find_keyword_rank(chunks, keyword)
        results.append(triad)

        cr = triad["context_relevance"]["label"]
        gr = triad["groundedness"]["label"]
        ar_lbl = triad["answer_relevance"]["label"]
        status = "PASS" if triad["overall_pass"] else "FAIL"
        log.info("  %s | CR=%s | GR=%s | AR=%s", status, cr, gr, ar_lbl)

    summary = summarize_results([r for r in results if not r.get("expect_no_answer")])

    cat_stats: dict[str, Any] = {}
    for cat in ["A", "B", "C", "D"]:
        cat_results = [r for r in results if r.get("category") == cat]
        if not cat_results:
            continue
        passes = sum(1 for r in cat_results if r.get("overall_pass"))
        kw_hits = sum(1 for r in cat_results if r.get("keyword_found"))
        cat_stats[cat] = {
            "total": len(cat_results),
            "pass": passes,
            "pass_rate": passes / len(cat_results) if cat_results else 0,
            "keyword_hits": kw_hits,
            "keyword_total": sum(1 for r in cat_results if r.get("expected_keyword")),
        }

    log.info("  EVAL [%s]", label.upper())
    log.info("  Cases: %d/%d", len(results), len(cases))
    for cat, stats in cat_stats.items():
        log.info(
            "  Kat %s: Pass %d/%d | Keywords %d/%d",
            cat,
            stats["pass"],
            stats["total"],
            stats["keyword_hits"],
            stats["keyword_total"],
        )
    non_ood = [r for r in results if not r.get("expect_no_answer")]
    if non_ood:
        pass_rate = sum(1 for r in non_ood if r.get("overall_pass")) / len(non_ood)
        log.info("  Gesamt Pass-Rate (ohne OOD): %.0f%%", pass_rate * 100)

    output: dict[str, Any] = {
        "label": label,
        "results": results,
        "summary": summary,
        "category_stats": cat_stats,
    }
    output["summary"]["labels"] = {k: dict(v) for k, v in output["summary"]["labels"].items()}
    return output


# ════════════════════════════════════════════════════════════════════════════
# Einstiegspunkt
# ════════════════════════════════════════════════════════════════════════════

_DEFAULT_CASES = str(Path(__file__).parent / "fixtures" / "cases.json")


def main() -> None:
    """CLI entry point für python -m titan.eval.ab_eval."""
    # defaultdict import only needed in summarize_results (imported above)
    _ = defaultdict  # satisfy unused-import check

    parser = argparse.ArgumentParser(
        description="Titan Evaluation Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--label",
        type=str,
        default="baseline",
        help="Label für diesen Durchlauf (z.B. 'baseline', 'v2')",
    )
    parser.add_argument(
        "--cases-file",
        type=str,
        default=_DEFAULT_CASES,
        help="Pfad zur JSON-Datei mit Eval-Cases",
    )
    parser.add_argument(
        "--determinism-test",
        action="store_true",
        help="V1: Determinismus-Test (N× identischer Triade-Call)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=10,
        help="Anzahl Runs für Determinismus-Test",
    )
    parser.add_argument(
        "--rank-only",
        action="store_true",
        help="V3: Nur Retrieval-Ränge ausgeben, kein Generate/Judge",
    )
    parser.add_argument(
        "--category",
        type=str,
        default="all",
        choices=["A", "B", "C", "D", "all"],
        help="Nur bestimmte Kategorie evaluieren",
    )
    args = parser.parse_args()

    if args.determinism_test:
        result = run_determinism_test(runs=args.runs)
        out_path = Path.cwd() / "validation_v1_determinism.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        log.info("V1 gespeichert: %s", out_path)
        return

    cases = load_cases(args.cases_file)
    if args.category != "all":
        cases = [c for c in cases if c.get("category") == args.category]
    log.info("%d Cases geladen (Kategorie: %s)", len(cases), args.category)

    if args.rank_only:
        result = run_rank_comparison(cases, args.label)
        out_path = Path.cwd() / f"validation_v3_ranks_{args.label}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        log.info("V3 gespeichert: %s", out_path)
        return

    result = run_full_eval(cases, args.label)
    out_path = Path.cwd() / f"eval_{args.label}_v2.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info("Ergebnisse gespeichert: %s", out_path)


if __name__ == "__main__":
    main()
