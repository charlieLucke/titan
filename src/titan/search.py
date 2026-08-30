"""
search.py – Phase 5 + Epic 2: Hybrid Search, Query De-construction & RRF
=========================================================================
Pipeline:
  0. Epic 2a – Query De-construction: Phi-4 (Ollama) zerlegt die Anfrage in
     2–3 atomare Sub-Queries (VOR dem GPU-Lock, keep_alive=0 → kein VRAM-Konflikt)
  1. BGE-M3 (CUDA) vektorisiert alle Sub-Queries → Dense + Sparse + ColBERT
  2. Qdrant Hybrid Prefetch (Dense ∪ Sparse) → Top-N Kandidaten je Sub-Query
  3. Qdrant nativer ColBERT MaxSim → Re-Ranking auf Top-K je Sub-Query
  4. Epic 2b – RRF-Fusion: Ergebnislisten zu einer Finalliste verschmolzen

Aufruf:
    python -m titan.search "Was ist der Unterschied zwischen RAG und Fine-Tuning?"
    python -m titan.search "Erkläre HNSW" --top-k 10 --domain trading
    python -m titan.search "..." --prefetch 200 --top-k 5 --domain titan --json

Umgebungsvariablen (via .env):
    QDRANT_HOST          – Standard: localhost
    QDRANT_GRPC_PORT     – Standard: 6334
    QDRANT_API_KEY       – Qdrant API-Key
    COLLECTION_NAME      – Standard: mein_wissen
    OLLAMA_URL           – Standard: http://localhost:11434
    OLLAMA_MODEL         – Standard: phi4:latest
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import defaultdict
from typing import Any

import requests
import torch

from titan.config import settings
from titan.utils import acquire_gpu_lock, cache_uuid, sanitize

# Windows: stdout-Pipe auf UTF-8 erzwingen (verhindert UnicodeEncodeError bei
# Umlauten wenn search.py via Pipe in generate.py weiterleitet, cp1252/cp850).
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Konfiguration (zentral in titan.config, hier nur Aliase) ────────────────
COLLECTION_NAME: str = settings.collection_name
OLLAMA_URL: str = settings.ollama_url
OLLAMA_MODEL: str = settings.ollama_model

# Epic 5B Semantic Caching
CACHE_ENABLED: bool = settings.cache_enabled
CACHE_COLLECTION_NAME: str = settings.cache_collection_name
CACHE_THRESHOLD: float = settings.cache_threshold
CACHE_TTL_SECONDS: int = settings.cache_ttl_seconds


# ════════════════════════════════════════════════════════════════════════════
# EPIC 2a – Query De-construction via Ollama/Phi-4
# ════════════════════════════════════════════════════════════════════════════

# Strikt anweisender Prompt – Phi-4 soll nur logisch zerlegen, nie halluzinieren.
# Kein str.format()-Platzhalter: geschweifte Klammern in der Query (z.B. "{HNSW}")
# würden sonst als Format-Slot interpretiert und einen KeyError werfen.
_DECOMPOSE_PROMPT_PREFIX = (
    "Zerlege die folgende Suchanfrage in genau 2 bis 3 isolierte, atomare "
    "Suchbegriffe. Antworte ausschließlich mit einem JSON-Array von Strings. "
    "Kein einleitender Text, keine Erklärungen, kein Markdown.\n\n"
    "Anfrage: "
)
_DECOMPOSE_PROMPT_SUFFIX = "\n\nAntwort (nur JSON-Array):"


def decompose_query(query: str) -> list[str]:
    """Zerlegt die Nutzeranfrage via Phi-4 (Ollama) in 2–3 atomare Sub-Queries.

    VRAM-Strategie:
        - Wird VOR acquire_gpu_lock() aufgerufen: Phi-4 lädt via Ollama,
          BGE-M3 ist noch nicht im VRAM.
        - keep_alive=0: Ollama entlädt Phi-4 sofort nach der Antwort.
          BGE-M3 findet danach ~9 GB freien VRAM vor.
        - temperature=0.0: maximale Determiniertheit, kein kreatives Rauschen.
        - num_predict=256: Sub-Queries brauchen wenige Tokens → kurze Latenz.

    Fehlerverhalten:
        Bei Verbindungsfehler, Timeout oder ungültigem JSON: Fallback auf
        [query] (originale Query). Die Pipeline läuft immer durch.

    Args:
        query: Die ursprüngliche Suchanfrage.

    Returns:
        Liste mit 2–3 atomaren Sub-Queries, oder [query] als Fallback.
    """
    safe_query = sanitize(query, max_len=500)
    prompt = _DECOMPOSE_PROMPT_PREFIX + safe_query + _DECOMPOSE_PROMPT_SUFFIX

    payload: dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": 0,
        "options": {
            "temperature": 0.0,
            "num_predict": 256,
        },
    }

    try:
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=30)
        resp.raise_for_status()
        raw: str = resp.json().get("response", "")

        array_match = re.search(r"\[.*\]", raw, re.DOTALL)
        dict_match = re.search(r"\{.*\}", raw, re.DOTALL)

        if array_match:
            parsed: Any = json.loads(array_match.group())
        elif dict_match:
            parsed = json.loads(dict_match.group())
        else:
            raise ValueError(f"Kein JSON in Ollama-Response gefunden: {raw!r}")

        if isinstance(parsed, list):
            sub_queries = [str(q).strip() for q in parsed if str(q).strip()]
        elif isinstance(parsed, dict):
            sub_queries = []
            for key in ("queries", "sub_queries", "results"):
                if key in parsed and isinstance(parsed[key], list):
                    sub_queries = [str(q).strip() for q in parsed[key] if str(q).strip()]
                    break
            if not sub_queries:
                sub_queries = [str(v).strip() for v in parsed.values() if str(v).strip()]
        else:
            sub_queries = []

        if not sub_queries:
            raise ValueError("Leeres Sub-Query-Array erhalten.")

        log.info("Query-Zerlegung: %d Sub-Queries → %s", len(sub_queries), sub_queries)
        return sub_queries

    except requests.exceptions.ConnectionError:
        log.warning(
            "Ollama nicht erreichbar (%s) – "
            "Query-Zerlegung übersprungen, originale Query wird verwendet.",
            OLLAMA_URL,
        )
    except requests.exceptions.Timeout:
        log.warning("Ollama-Timeout (30 s) bei Query-Zerlegung – originale Query wird verwendet.")
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Query-Zerlegung fehlgeschlagen (%s) – originale Query wird verwendet.", exc)

    return [query]


# ════════════════════════════════════════════════════════════════════════════
# EPIC 2b – Reciprocal Rank Fusion
# ════════════════════════════════════════════════════════════════════════════


def rrf_fusion(ranked_lists: list[list[dict[str, Any]]], k: int = 60) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion (Robertson et al., 2009).

    Formel: Score(d) = Σ 1 / (k + rank(d, list_i))
            für alle Listen list_i, in denen Dokument d vorkommt.

    k=60 ist die Standard-Stabilisierungskonstante. Dokumente, die in mehreren
    Sub-Query-Ergebnissen auftauchen, akkumulieren höhere Scores.

    Args:
        ranked_lists: Liste von Ergebnislisten (je Sub-Query).
        k: Stabilisierungskonstante (Standard 60).

    Returns:
        Fusionierte und nach RRF-Score sortierte Ergebnisliste.
    """
    rrf_scores: dict[str, float] = defaultdict(float)
    payloads: dict[str, dict[str, Any]] = {}

    for ranked_list in ranked_lists:
        for rank, result in enumerate(ranked_list, start=1):
            doc_id = result["id"]
            rrf_scores[doc_id] += 1.0 / (k + rank)
            if doc_id not in payloads:
                payloads[doc_id] = result

    fused_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)

    return [
        {**payloads[doc_id], "rank": new_rank, "score": round(rrf_scores[doc_id], 6)}
        for new_rank, doc_id in enumerate(fused_ids, start=1)
    ]


# ════════════════════════════════════════════════════════════════════════════
# PHASE 5a – Query-Embedding via BGE-M3 (CUDA)
# ════════════════════════════════════════════════════════════════════════════


def load_bge_m3_model() -> Any:
    """CLI-Wrapper um titan.infra.load_bge_m3_model (sys.exit statt Exception).

    Bei gecachtem Modell startet der Prozess in ~2 s statt ~20 s
    (HuggingFace-Cache).

    Returns:
        BGEM3FlagModel-Instanz, bereit für encode().

    Raises:
        SystemExit: Wenn FlagEmbedding nicht installiert ist.
    """
    from titan.infra import load_bge_m3_model as _load

    try:
        return _load()
    except RuntimeError as exc:
        log.critical(str(exc))
        sys.exit(1)


def embed_queries(model: Any, queries: list[str]) -> list[dict[str, Any]]:
    """Vektorisiert alle Sub-Queries in EINEM encode()-Batch.

    Ein GPU-Roundtrip statt einem pro Sub-Query — bei 3–4 Sub-Queries spart
    das den Großteil der Embedding-Latenz einer Suche.

    Args:
        model: BGEM3FlagModel-Instanz.
        queries: Die zu vektorisierenden Queries.

    Returns:
        Liste von Dicts (eine pro Query) mit keys:
            dense:   list[float] – 1024-dim Vektor
            sparse:  dict[str, float] – {token_id: gewicht}
            colbert: list[list[float]] – (seq_len × 128) Matrix
    """
    log.info(
        "Vektorisiere %d Quer%s in einem Batch", len(queries), "ys" if len(queries) > 1 else "y"
    )
    with torch.no_grad():
        output = model.encode(
            queries,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=True,
            batch_size=len(queries),
        )
    return [
        {
            "dense": output["dense_vecs"][i].tolist(),
            "sparse": output["lexical_weights"][i],
            "colbert": output["colbert_vecs"][i].tolist(),
        }
        for i in range(len(queries))
    ]


def embed_query(model: Any, query: str) -> dict[str, Any]:
    """Vektorisiert eine einzelne Query (Wrapper um embed_queries)."""
    return embed_queries(model, [query])[0]


# ════════════════════════════════════════════════════════════════════════════
# EPIC 5B – Semantic Caching
# ════════════════════════════════════════════════════════════════════════════


def _normalize_query(text: str) -> str:
    """Lowercase + Whitespace-Normalisierung für Exact Match."""
    return " ".join(text.lower().split())


def bootstrap_cache_collection(client: Any) -> None:
    """Erstellt query_cache Collection falls nicht vorhanden."""
    from qdrant_client.models import Distance, VectorParams

    try:
        collections = client.get_collections()
        known = [c.name for c in collections.collections]
        if CACHE_COLLECTION_NAME not in known:
            log.info("Erstelle Cache-Collection '%s'...", CACHE_COLLECTION_NAME)
            client.create_collection(
                collection_name=CACHE_COLLECTION_NAME,
                vectors_config={"dense": VectorParams(size=1024, distance=Distance.COSINE)},
                hnsw_config={"m": 16, "ef_construct": 100},
                on_disk_payload=False,
            )
            client.create_payload_index(CACHE_COLLECTION_NAME, "domain", "keyword")
            client.create_payload_index(CACHE_COLLECTION_NAME, "sub_query_normalized", "keyword")
            client.create_payload_index(CACHE_COLLECTION_NAME, "created_at", "float")
            log.info("Cache-Collection erfolgreich erstellt.")
    except Exception as e:
        log.warning("Fehler beim Bootstrap der Cache-Collection: %s", e)


def cache_lookup(
    client: Any,
    sub_query: str,
    dense_vec: list[float],
    domain: str | None,
) -> list[dict[str, Any]] | None:
    """Zwei-Stufen-Lookup (Exact + Vector). Gibt Chunk-Liste zurück oder None.

    Args:
        client: Qdrant-Client.
        sub_query: Die Sub-Query für die gecachte Ergebnisse gesucht werden.
        dense_vec: Dense-Embedding der Sub-Query für Vektor-Suche.
        domain: Domain-Filter oder None.

    Returns:
        Gecachte Chunk-Liste bei Hit, None bei Miss oder Fehler.
    """
    from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

    try:
        domain_val = domain or "_global"
        normalized = _normalize_query(sub_query)
        min_created_at = time.time() - CACHE_TTL_SECONDS

        exact_filter = Filter(
            must=[
                FieldCondition(key="sub_query_normalized", match=MatchValue(value=normalized)),
                FieldCondition(key="domain", match=MatchValue(value=domain_val)),
                FieldCondition(key="created_at", range=Range(gte=min_created_at)),
            ]
        )

        scroll_res, _ = client.scroll(
            collection_name=CACHE_COLLECTION_NAME,
            scroll_filter=exact_filter,
            limit=1,
            with_payload=True,
        )
        if scroll_res:
            log.info("Cache-HIT (Stage 1: Exact) für '%s'", sub_query[:50])
            _async_increment_hit_count(client, scroll_res[0].id, scroll_res[0].payload)
            return scroll_res[0].payload.get("chunks", [])  # type: ignore[no-any-return]

        vec_filter = Filter(
            must=[
                FieldCondition(key="domain", match=MatchValue(value=domain_val)),
                FieldCondition(key="created_at", range=Range(gte=min_created_at)),
            ]
        )

        query_res = client.query_points(
            collection_name=CACHE_COLLECTION_NAME,
            query=dense_vec,
            using="dense",
            limit=1,
            score_threshold=CACHE_THRESHOLD,
            query_filter=vec_filter,
            with_payload=True,
        )

        if query_res.points:
            hit = query_res.points[0]
            log.info(
                "Cache-HIT (Stage 2: Vector, score=%.4f) für '%s'",
                hit.score,
                sub_query[:50],
            )
            _async_increment_hit_count(client, hit.id, hit.payload)
            return hit.payload.get("chunks", [])  # type: ignore[no-any-return]

    except Exception as e:
        log.warning("Cache-Lookup fehlgeschlagen, fahre live fort: %s", e)

    return None


def _async_increment_hit_count(client: Any, point_id: Any, payload: dict[str, Any]) -> None:
    try:
        client.set_payload(
            collection_name=CACHE_COLLECTION_NAME,
            payload={"hit_count": payload.get("hit_count", 0) + 1},
            points=[point_id],
            wait=False,
        )
    except Exception as e:
        log.warning("Konnte hit_count nicht asynchron erhöhen: %s", e)


def cache_write(
    client: Any,
    sub_query: str,
    dense_vec: list[float],
    domain: str | None,
    chunks: list[dict[str, Any]],
) -> None:
    """Schreibt ein Query-Ergebnis in den Semantic Cache."""
    from qdrant_client.models import PointStruct

    try:
        domain_val = domain or "_global"
        point_id = cache_uuid(sub_query, domain_val)
        point = PointStruct(
            id=point_id,
            vector={"dense": dense_vec},
            payload={
                "sub_query": sub_query,
                "sub_query_normalized": _normalize_query(sub_query),
                "domain": domain_val,
                "created_at": time.time(),
                "hit_count": 0,
                "chunks": chunks,
            },
        )
        client.upsert(collection_name=CACHE_COLLECTION_NAME, points=[point], wait=False)
    except Exception as e:
        log.warning("Cache-Write fehlgeschlagen: %s", e)


def cache_cleanup(client: Any, domain: str | None = None) -> None:
    """Löscht TTL-abgelaufene Cache-Einträge."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

    try:
        min_created_at = time.time() - CACHE_TTL_SECONDS
        must_conds: list[Any] = [FieldCondition(key="created_at", range=Range(lt=min_created_at))]
        if domain:
            must_conds.append(FieldCondition(key="domain", match=MatchValue(value=domain)))

        client.delete(
            collection_name=CACHE_COLLECTION_NAME,
            points_selector=Filter(must=must_conds),
        )
        log.info("Cache-Cleanup für alte Einträge erfolgreich ausgeführt.")
    except Exception as e:
        log.warning("Cache-Cleanup fehlgeschlagen: %s", e)


def invalidate_domain_cache(qdrant_client: Any, domain: str) -> None:
    """Löscht alle Cache-Einträge für eine Domain (aggressiv, domain-granular).

    Wird nach Re-Ingest einer Note aufgerufen, damit veraltete Cache-Treffer
    für die betroffene Domain nicht mehr ausgespielt werden.

    Lebt hier in titan.search (nicht in routes), damit die Funktion testbar
    ist ohne FastAPI und ohne Mocking des ServiceState-Singletons.
    """
    if not CACHE_ENABLED or qdrant_client is None:
        return
    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        qdrant_client.delete(
            collection_name=CACHE_COLLECTION_NAME,
            points_selector=Filter(
                must=[FieldCondition(key="domain", match=MatchValue(value=domain))]
            ),
        )
        log.info("Cache für Domain '%s' invalidiert.", domain)
    except Exception as exc:
        log.warning("Cache-Invalidierung für Domain '%s' fehlgeschlagen: %s", domain, exc)


def print_cache_stats(client: Any) -> None:
    """Gibt Statistiken über den Semantic Cache aus."""
    try:
        total = 0
        total_hits = 0
        offset: Any = None
        while True:
            res, offset = client.scroll(
                collection_name=CACHE_COLLECTION_NAME,
                limit=1000,
                offset=offset,
                with_payload=True,
            )
            if not res:
                break
            total += len(res)
            total_hits += sum(r.payload.get("hit_count", 0) for r in res)
            if offset is None:
                break
        log.info("Cache-Stats: %d Einträge, %d kumulierte Hits.", total, total_hits)
    except Exception as e:
        log.warning("Fehler beim Lesen der Cache-Stats: %s", e)


# ════════════════════════════════════════════════════════════════════════════
# PHASE 5b – Qdrant Hybrid Prefetch (Dense + Sparse) → Top-N
# ════════════════════════════════════════════════════════════════════════════

_qdrant_client: Any = None


def build_qdrant_client() -> Any:
    """Verbindet mit Qdrant via gRPC.

    Singleton: Die Verbindung wird beim ersten Aufruf aufgebaut und danach
    wiederverwendet – spart ~10–50 ms TCP-Overhead pro Suchanfrage.

    Returns:
        QdrantClient-Instanz.

    Raises:
        SystemExit: Wenn Qdrant nicht erreichbar ist oder die Collection fehlt.
    """
    global _qdrant_client
    if _qdrant_client is not None:
        return _qdrant_client

    from titan.infra import make_qdrant_client

    client = make_qdrant_client()
    try:
        client.get_collection(COLLECTION_NAME)
        log.info("Collection '%s' gefunden.", COLLECTION_NAME)
    except Exception:
        log.critical(
            "Collection '%s' nicht erreichbar. Läuft Qdrant? Wurde Phase 1 & 3 ausgeführt?",
            COLLECTION_NAME,
            exc_info=True,
        )
        sys.exit(1)
    _qdrant_client = client
    return _qdrant_client


def hybrid_search_with_rerank(
    client: Any,
    query_vecs: dict[str, Any],
    prefetch_limit: int = 100,
    final_top_k: int = 5,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """Zweistufiger Hybrid-Search: Prefetch + ColBERT MaxSim Re-Ranking.

    Stufe 1 – Prefetch (parallel, innerhalb Qdrant):
        Dense-Suche + Sparse-Suche → Top-N Kandidaten (Union).
    Stufe 2 – ColBERT Late Interaction (nativer Qdrant MaxSim):
        score(q, d) = Σ_{qi} max_{dj} cos(qi, dj)

    Epic 1A: Wenn domain gesetzt, wird FieldCondition-Filter auf allen Stufen
    angewendet – keine False Positives aus anderen Domains.

    Args:
        client: QdrantClient-Instanz.
        query_vecs: Dict mit dense, sparse, colbert Vektoren.
        prefetch_limit: Kandidaten pro Vektortyp für den Prefetch.
        final_top_k: Finale Ergebnis-Anzahl nach ColBERT-Reranking.
        domain: Optionaler Domain-Filter.

    Returns:
        Liste von Result-Dicts, sortiert nach ColBERT-Score (absteigend).
    """
    from qdrant_client.models import FieldCondition, Filter, MatchValue, Prefetch, SparseVector

    sparse_raw: dict[str, Any] = query_vecs["sparse"]
    # int(float(k)): FlagEmbedding gibt Keys manchmal als "1024.0" zurück –
    # int("1024.0") wirft ValueError, int(float("1024.0")) ist korrekt.
    sparse_vec = SparseVector(
        indices=[int(float(k)) for k in sparse_raw],
        values=[float(v) for v in sparse_raw.values()],
    )

    domain_filter = None
    if domain:
        domain_filter = Filter(must=[FieldCondition(key="domain", match=MatchValue(value=domain))])
        log.info("Domain-Filter aktiv: '%s'", domain)

    log.info(
        "Starte Hybrid-Search: prefetch=%d (Dense+Sparse), ColBERT-Reranking auf top-%d …",
        prefetch_limit,
        final_top_k,
    )

    t0 = time.perf_counter()
    results = client.query_points(
        collection_name=COLLECTION_NAME,
        prefetch=[
            Prefetch(
                query=query_vecs["dense"],
                using="dense",
                limit=prefetch_limit,
                filter=domain_filter,
            ),
            Prefetch(
                query=sparse_vec,
                using="sparse",
                limit=prefetch_limit,
                filter=domain_filter,
            ),
        ],
        query=query_vecs["colbert"],
        using="colbert",
        limit=final_top_k,
        with_payload=True,
        query_filter=domain_filter,
    )
    log.info("Retrieval-Latenz: %.1f ms", (time.perf_counter() - t0) * 1000)

    hits = results.points
    log.info("Suche abgeschlossen: %d Ergebnis(se) zurückgegeben", len(hits))

    return [
        {
            "rank": rank + 1,
            "score": round(hit.score, 6),
            "id": str(hit.id),
            "source": hit.payload.get("source", ""),
            "source_path": hit.payload.get("source", ""),  # Service-Alias
            "header": hit.payload.get("header", ""),
            "text": hit.payload.get("text", ""),
            "domain": hit.payload.get("domain", ""),
            "chunk_offset": hit.payload.get("chunk_id", 0),
            # Herkunft mitgeben. Ohne diese drei Zeilen sieht ein Treffer aus
            # wie jeder andere — ein ungepruefter Agenten-Entwurf ist dann nicht
            # von einer gemessenen Notiz zu unterscheiden. Genau die
            # Kontaminationsschleife, vor der plan-second-brain 3.3 warnt:
            # Agent schreibt -> wird indexiert -> liest es als Wahrheit zurueck.
            "quelle": hit.payload.get("quelle"),
            "geprueft": hit.payload.get("geprueft"),
            "updated": hit.payload.get("updated"),
        }
        for rank, hit in enumerate(hits)
    ]


# ════════════════════════════════════════════════════════════════════════════
# Ausgabe
# ════════════════════════════════════════════════════════════════════════════


def print_results(
    results: list[dict[str, Any]],
    as_json: bool = False,
    original_query: str = "",
) -> None:
    """Gibt Suchergebnisse formatiert auf stdout aus."""
    if as_json:
        output = {"query": original_query, "chunks": results}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    print(f"\n{'═' * 70}")
    print(f"  {len(results)} Ergebnis(se)")
    print(f"{'═' * 70}\n")
    for r in results:
        print(f"[{r['rank']}] Score: {r['score']:.4f}  |  {r['source']}  |  {r['header']}")
        print(f"{'─' * 70}")
        preview = r["text"][:400].replace("\n", " ")
        if len(r["text"]) > 400:
            preview += " …"
        print(f"{preview}\n")


# ════════════════════════════════════════════════════════════════════════════
# Einstiegspunkt
# ════════════════════════════════════════════════════════════════════════════


def search(
    query: str,
    domain: str | None = None,
    top_k: int = 10,
    prefetch_limit: int = 100,
    use_decompose: bool = True,
    use_cache: bool = True,
    model: Any | None = None,
    qdrant_client: Any | None = None,
) -> dict[str, Any]:
    """Führt eine vollständige Hybrid-Suche durch (CLI- und Service-Pfad).

    Wenn ``model`` oder ``qdrant_client`` None sind, werden sie intern geladen
    (CLI-Pfad inkl. GPU-Lock). Werden sie übergeben, übernimmt der Caller die
    Verwaltung des GPU-Locks und des Modells (Service-Pfad).

    Args:
        query: Suchanfrage.
        domain: Optionaler Domain-Filter.
        top_k: Maximale Anzahl Ergebnis-Chunks.
        prefetch_limit: Vorfilter-Kandidaten für ColBERT-Reranking.
        use_decompose: Query-Decomposition via Phi-4 aktivieren.
        use_cache: Epic-5B Semantic Cache nutzen.
        model: Optional vorgeladene BGEM3FlagModel-Instanz (Service-Pfad).
        qdrant_client: Optional vorgeladener QdrantClient (Service-Pfad).

    Returns:
        Dict mit keys:
            chunks:      list[dict] – finales Ranking
            sub_queries: list[str] – generierte Sub-Queries
            cache_hit:   bool – True wenn mind. eine Sub-Query aus Cache kam
    """
    _own_lock: Any = None
    _own_model = model is None
    _own_client = qdrant_client is None

    # CLI-Pfad: Lock und Ressourcen selbst verwalten
    if _own_lock is None and _own_model:
        try:
            _own_lock = acquire_gpu_lock()
        except RuntimeError as e:
            raise RuntimeError(f"GPU-Lock nicht erreichbar: {e}") from e

    if _own_model:
        model = load_bge_m3_model()
    if _own_client:
        qdrant_client = build_qdrant_client()

    # Query-Decomposition (VOR GPU-Operationen, keep_alive=0 → kein VRAM-Konflikt)
    if use_decompose:
        sub_queries = decompose_query(query)
        if query not in sub_queries:
            sub_queries.append(query)
    else:
        sub_queries = [query]

    if use_cache and CACHE_ENABLED:
        bootstrap_cache_collection(qdrant_client)

    rrf_candidates = top_k * max(len(sub_queries), 3)
    ranked_lists: list[list[dict[str, Any]]] = []
    cache_hit = False

    # Alle Sub-Queries in einem GPU-Batch vektorisieren (statt einzeln).
    all_vecs = embed_queries(model, sub_queries)

    for sq, vecs in zip(sub_queries, all_vecs, strict=True):
        if use_cache and CACHE_ENABLED:
            cached = cache_lookup(qdrant_client, sq, vecs["dense"], domain)
            if cached is not None and cached:
                ranked_lists.append(cached)
                cache_hit = True
                continue

        results = hybrid_search_with_rerank(
            qdrant_client,
            vecs,
            prefetch_limit=prefetch_limit,
            final_top_k=rrf_candidates,
            domain=domain,
        )

        if use_cache and CACHE_ENABLED and results:
            cache_write(qdrant_client, sq, vecs["dense"], domain, results)

        ranked_lists.append(results)

    if not ranked_lists:
        final: list[dict[str, Any]] = []
    elif len(ranked_lists) > 1:
        final = rrf_fusion(ranked_lists)[:top_k]
    else:
        final = ranked_lists[0][:top_k]

    return {
        "chunks": final,
        "sub_queries": sub_queries,
        "cache_hit": cache_hit,
    }


def main() -> None:
    """CLI entry point für python -m titan.search."""
    parser = argparse.ArgumentParser(
        description="Titan Search – Phase 5: Hybrid Retrieval + ColBERT Re-Ranking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "query",
        type=str,
        nargs="?",
        default=None,
        help="Suchanfrage (entfällt bei --cache-stats/--cache-cleanup/--cache-clear)",
    )
    parser.add_argument(
        "--prefetch",
        type=int,
        default=100,
        help="Kandidaten pro Vektortyp für Prefetch (Standard: 100)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Finale Ergebnis-Anzahl nach ColBERT-Reranking (Standard: 5)",
    )
    parser.add_argument(
        "--domain",
        type=str,
        default=None,
        help="Suche nur innerhalb dieser Domain (z.B. 'trading'). Ohne: gesamte Collection.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Ausgabe als JSON (für Weiterverarbeitung)",
    )
    parser.add_argument("--no-cache", action="store_true", help="Cache für diesen Lauf umgehen")
    parser.add_argument("--cache-stats", action="store_true", help="nur Cache-Statistik ausgeben")
    parser.add_argument(
        "--cache-cleanup", action="store_true", help="TTL-abgelaufene Einträge löschen"
    )
    parser.add_argument(
        "--cache-clear", action="store_true", help="komplette Cache-Collection leeren"
    )
    args = parser.parse_args()

    if not args.query and not (args.cache_stats or args.cache_cleanup or args.cache_clear):
        parser.error(
            "query ist erforderlich (außer bei --cache-stats/--cache-cleanup/--cache-clear)"
        )

    # ── Epic 5B: Standalone Cache Commands ───────────────────────────────────
    if args.cache_clear or args.cache_cleanup or args.cache_stats:
        client = build_qdrant_client()
        if args.cache_clear:
            if CACHE_ENABLED:
                confirm = input(
                    f"Cache-Collection '{CACHE_COLLECTION_NAME}' wirklich komplett löschen? [j/N]: "
                )
                if confirm.lower() in ("j", "ja", "y", "yes"):
                    client.delete_collection(collection_name=CACHE_COLLECTION_NAME)
                    log.info("Cache-Collection vollständig geleert.")
                else:
                    log.info("Abgebrochen.")
            else:
                log.warning("Cache ist in .env deaktiviert.")
        elif args.cache_cleanup:
            if CACHE_ENABLED:
                cache_cleanup(client, args.domain)
        elif args.cache_stats and CACHE_ENABLED:
            print_cache_stats(client)
        sys.exit(0)

    # Die komplette Pipeline (Decompose → GPU-Lock → Modell → Suche → RRF)
    # lebt in search() — main() ist nur noch CLI-Adapter. Vorher war die
    # Pipeline hier ein zweites Mal ausprogrammiert (Drift-Gefahr zwischen
    # Service- und CLI-Pfad).
    try:
        result = search(
            query=args.query,
            domain=args.domain,
            top_k=args.top_k,
            prefetch_limit=args.prefetch,
            use_decompose=True,
            use_cache=not args.no_cache,
        )
    except RuntimeError as e:
        log.critical(str(e))
        sys.exit(1)

    print_results(result["chunks"], as_json=args.json, original_query=args.query)


if __name__ == "__main__":
    main()
