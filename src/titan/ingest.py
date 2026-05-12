"""
ingest.py – Phase 2, 3 & Epic 3: PDF-Parsing, Late Chunking & Multi-Vector Embedding
======================================================================================
Note: Epic 5A (Contextual Retrieval via Phi-4-Summaries) wurde beim Port aus RAG_System
      bewusst entfernt. Referenz: ~/projects/RAG_System/execution/ingest.py
      Begründung: siehe DECISIONS.md 2026-05-12.

Pipeline:
  Phase 2a – PDF-Parsing via Docling (Multiprocessing, CPU-bound)
  Phase 2b – Header-basiertes Chunking (semantische Abschnitte)
  Phase 3a – BGE-M3 laden (CUDA, FP16)
  Phase 3b – Late Chunking Embedding (Epic 3, kontextualisierte Vektoren)
  Phase 3c – Upsert in Qdrant via gRPC (Batch, maximaler Durchsatz)

Aufruf:
    python -m titan.ingest <pdf_datei_oder_verzeichnis> --domain <domain>

Umgebungsvariablen (via .env):
    QDRANT_HOST              – Standard: localhost
    QDRANT_GRPC_PORT         – Standard: 6334
    QDRANT_API_KEY           – Qdrant API-Key
    COLLECTION_NAME          – Standard: mein_wissen
    MAX_WORKERS              – Docling-Prozesse (Standard: 12)
    EMBED_BATCH_SIZE         – Qdrant-Upsert-Batch-Größe (Standard: 32)
    LATE_CHUNK_WINDOW_TOKENS – Max. Tokens pro Late-Chunking-Fenster (Standard: 7800)
    INGEST_BASE_DIR          – Erlaubtes Eingabeverzeichnis (Path-Traversal-Schutz)
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import torch
from dotenv import load_dotenv

from titan.utils import acquire_gpu_lock, stable_uuid

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(processName)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Konfiguration aus .env ──────────────────────────────────────────────────
load_dotenv()

QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
QDRANT_GRPC_PORT: int = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
QDRANT_API_KEY: str = os.getenv("QDRANT_API_KEY", "")
COLLECTION_NAME: str = os.getenv("COLLECTION_NAME", "mein_wissen")
MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "12"))
EMBED_BATCH_SIZE: int = int(os.getenv("EMBED_BATCH_SIZE", "32"))

# Path-Traversal-Schutz: Ingest nur aus diesem Verzeichnis erlaubt
INGEST_BASE_DIR: Path = Path(os.getenv("INGEST_BASE_DIR", "/mnt/f/data/titan-input")).resolve()

# Epic 3: Late Chunking
# BGE-M3 Hard-Limit: 8192 Tokens. Fenster-Ziel mit ~400-Token-Puffer.
BGE_MAX_TOKENS: int = 8192
LATE_CHUNK_WINDOW_TOKENS: int = int(os.getenv("LATE_CHUNK_WINDOW_TOKENS", "7800"))
# Trennzeichen zwischen Chunks im Fenstertext
_WINDOW_SEP = "\n\n[SEP]\n\n"

# Maximalgröße eines Sub-Chunks (~6000 Tokens, sicher unter BGE-M3-Limit)
MAX_SUPER_CHUNK_CHARS: int = 24_000
OVERLAP_CHARS: int = 4_000


# ════════════════════════════════════════════════════════════════════════════
# PHASE 2a – PDF-Parsing via Docling (Multiprocessing, CPU-bound)
# ════════════════════════════════════════════════════════════════════════════


def _parse_single_pdf(pdf_path: Path) -> tuple[Path, str]:
    """Worker-Funktion – läuft in einem separaten OS-Prozess.

    Konvertiert eine PDF-Datei in Markdown via Docling.
    Import im Worker-Prozess, damit das schwere Docling-Modell
    nur einmal pro Worker geladen und nicht durch den GIL limitiert wird.

    Args:
        pdf_path: Pfad zur PDF-Datei.

    Returns:
        Tuple (Pfad, Markdown-String).
    """
    import logging as _logging

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    _log = _logging.getLogger(__name__)
    _log.info("Parsing: %s", pdf_path.name)

    pipeline_opts = PdfPipelineOptions()
    pipeline_opts.do_ocr = False
    pipeline_opts.do_table_structure = True

    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)},
    )
    result = converter.convert(str(pdf_path))
    markdown = result.document.export_to_markdown()
    _log.info("Fertig: %s – %d Zeichen", pdf_path.name, len(markdown))
    return pdf_path, markdown


def parse_pdfs_parallel(pdf_paths: list[Path]) -> list[tuple[Path, str]]:
    """Parst alle PDFs parallel mit bis zu MAX_WORKERS Prozessen.

    Nutzt ProcessPoolExecutor, um den GIL zu umgehen und alle CPU-Kerne auszulasten.

    Args:
        pdf_paths: Liste der zu parsenden PDF-Dateien.

    Returns:
        Liste von (Pfad, Markdown-String) Tupeln für erfolgreich geparste Dateien.
    """
    n = len(pdf_paths)
    workers = min(MAX_WORKERS, n)
    log.info("Starte Docling-Parsing: %d Datei(en), %d Worker-Prozesse", n, workers)

    results: list[tuple[Path, str]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(_parse_single_pdf, p): p for p in pdf_paths}
        for future in as_completed(future_map, timeout=300):
            source_path = future_map[future]
            try:
                path, md = future.result(timeout=120)
                results.append((path, md))
            except TimeoutError:
                log.error(
                    "Docling-Timeout (>120s): %s – übersprungen. Korruptes oder großes PDF?",
                    source_path.name,
                )
                future.cancel()
            except Exception:
                log.error("Parsing fehlgeschlagen: %s", source_path.name, exc_info=True)

    log.info("Parsing abgeschlossen: %d/%d Dateien erfolgreich", len(results), n)
    return results


# ════════════════════════════════════════════════════════════════════════════
# PHASE 2b – Header-basiertes Chunking
# ════════════════════════════════════════════════════════════════════════════


def _compute_section_id(source: str, header: str) -> str:
    return hashlib.sha256(f"{source}::{header}".encode()).hexdigest()[:16]


def _compute_section_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


_HEADER_RE = re.compile(r"^(#{1,2}\s+.+)", re.MULTILINE)


def chunk_markdown(markdown: str, source_path: Path) -> list[dict[str, Any]]:
    """Zerschneidet Markdown-Text an Headern (h1–h2) in semantische Chunks.

    Strategie:
    - Jeder Header eröffnet einen neuen Chunk.
    - Leere Chunks (nur Header, kein Inhalt) werden verworfen.
    - Chunks <= MAX_SUPER_CHUNK_CHARS Zeichen werden direkt übernommen.
    - Chunks > MAX_SUPER_CHUNK_CHARS werden mit OVERLAP_CHARS Überlappung in
      Sub-Chunks aufgeteilt. Schnittpunkte an Wortgrenzen (\\n\\n → \\n → Leerzeichen).

    chunk_id ist ein fortlaufender Counter über alle Chunks der Datei.

    Args:
        markdown: Der Markdown-Volltext des Dokuments.
        source_path: Pfad zur Quelldatei (wird als Metadaten gespeichert).

    Returns:
        Liste von Chunk-Dicts mit text, source, chunk_id, header, etc.
    """
    matches = list(_HEADER_RE.finditer(markdown))

    global_chunk_id = 0

    first_h1 = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    document_title = first_h1.group(1).strip() if first_h1 else source_path.stem

    def _split_text(text: str, header: str) -> list[dict[str, Any]]:
        nonlocal global_chunk_id
        sub_chunks: list[dict[str, Any]] = []
        pos = 0
        sub_idx = 0

        while pos < len(text):
            end = min(pos + MAX_SUPER_CHUNK_CHARS, len(text))

            if end < len(text):
                split = end
                for sep, window in (("\n\n", 500), ("\n", 200), (" ", 100)):
                    p = text.rfind(sep, max(0, end - window), end)
                    if p != -1:
                        split = p + len(sep)
                        break
            else:
                split = end

            sub_text = text[pos:split].strip()
            if sub_text:
                sub_header = f"{header} (Teil {sub_idx + 1})" if sub_idx > 0 else header
                sub_chunks.append(
                    {
                        "text": sub_text,
                        "source": source_path.name,
                        "chunk_id": global_chunk_id,
                        "header": sub_header,
                        "section_id": _compute_section_id(source_path.name, header),
                        "section_full_text": text,
                        "document_title": document_title,
                    }
                )
                global_chunk_id += 1
                sub_idx += 1

            next_pos = split - OVERLAP_CHARS
            if split >= len(text) or next_pos <= pos:
                break
            pos = next_pos

        return sub_chunks

    if not matches:
        text = markdown.strip()
        if not text:
            return []
        if len(text) <= MAX_SUPER_CHUNK_CHARS:
            return [
                {
                    "text": text,
                    "source": source_path.name,
                    "chunk_id": global_chunk_id,
                    "header": source_path.stem,
                    "section_id": _compute_section_id(source_path.name, source_path.stem),
                    "section_full_text": text,
                    "document_title": document_title,
                }
            ]
        log.warning(
            "Dokument '%s' hat keinen Header und ist zu groß (%d Zeichen) – "
            "wird in Sub-Chunks aufgeteilt.",
            source_path.name,
            len(text),
        )
        return _split_text(text, source_path.stem)

    chunks: list[dict[str, Any]] = []
    starts = [m.start() for m in matches]
    ends = [*starts[1:], len(markdown)]

    for match, start, end in zip(matches, starts, ends, strict=True):
        text = markdown[start:end].strip()
        if not text:
            continue
        header_title = match.group(1).lstrip("#").strip()

        if len(text) <= MAX_SUPER_CHUNK_CHARS:
            chunks.append(
                {
                    "text": text,
                    "source": source_path.name,
                    "chunk_id": global_chunk_id,
                    "header": header_title,
                    "section_id": _compute_section_id(source_path.name, header_title),
                    "section_full_text": text,
                    "document_title": document_title,
                }
            )
            global_chunk_id += 1
        else:
            log.warning(
                "Chunk '%s' zu groß (%d Zeichen) – wird in überlappende Sub-Chunks aufgeteilt "
                "(max %d Zeichen, %d Overlap).",
                header_title,
                len(text),
                MAX_SUPER_CHUNK_CHARS,
                OVERLAP_CHARS,
            )
            chunks.extend(_split_text(text, header_title))

    log.info("%s: %d Chunks erzeugt", source_path.name, len(chunks))
    return chunks


# ════════════════════════════════════════════════════════════════════════════
# PHASE 3a – BGE-M3 Modell laden
# ════════════════════════════════════════════════════════════════════════════


def load_bge_m3_model() -> Any:
    """Lädt das BGE-M3 Modell via FlagEmbedding mit zwingender CUDA-Beschleunigung.

    FlagEmbedding erzeugt in einem encode()-Aufruf:
      - dense_vecs       – 1024-dim Dense-Vektor
      - lexical_weights  – Sparse Token-Gewichte
      - colbert_vecs     – Token-Level Matrizen für Late Interaction / MaxSim

    use_fp16=True: Halbiert VRAM-Verbrauch, vernachlässigbare Qualitätseinbuße.

    Returns:
        BGEM3FlagModel-Instanz.

    Raises:
        SystemExit: Wenn FlagEmbedding nicht installiert ist.
    """
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError:
        log.critical("FlagEmbedding fehlt. Installation: uv add flagembedding")
        sys.exit(1)

    log.info("Lade BAAI/bge-m3 auf CUDA (FP16) …")
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device="cuda")
    log.info("BGE-M3 geladen und CUDA-Kontext bereit.")
    return model


# ════════════════════════════════════════════════════════════════════════════
# PHASE 3b – Late Chunking Embedding (Epic 3)
# ════════════════════════════════════════════════════════════════════════════


def _group_into_windows(chunks: list[dict[str, Any]], tokenizer: Any) -> list[list[dict[str, Any]]]:
    """Gruppiert Chunks in Fenster von max. LATE_CHUNK_WINDOW_TOKENS Tokens.

    Verwendet tokenizer.encode() für genaue Token-Zählung. Ingestion ist ein
    Offline-Prozess, daher ist die doppelte Tokenisierung latenzunkritisch.

    Args:
        chunks: Liste von Chunk-Dicts.
        tokenizer: HuggingFace Fast Tokenizer aus dem BGE-M3-Modell.

    Returns:
        Liste von Fenstern, jedes eine Chunk-Liste.
    """
    windows: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens: int = 0

    for chunk in chunks:
        n = len(tokenizer.encode(chunk["text"], add_special_tokens=False))

        if n > LATE_CHUNK_WINDOW_TOKENS:
            log.warning(
                "Chunk '%s' hat ~%d Tokens (Limit: %d) – eigenes Fenster, "
                "BGE-M3 trunciert auf 8192 Tokens.",
                chunk.get("header", "?"),
                n,
                LATE_CHUNK_WINDOW_TOKENS,
            )
            if current:
                windows.append(current)
                current = []
                current_tokens = 0
            windows.append([chunk])

        elif current and current_tokens + n > LATE_CHUNK_WINDOW_TOKENS:
            windows.append(current)
            current = [chunk]
            current_tokens = n

        else:
            current.append(chunk)
            current_tokens += n

    if current:
        windows.append(current)

    return windows


def _embed_window(
    window_chunks: list[dict[str, Any]],
    transformer: Any,
    tokenizer: Any,
    sparse_linear: Any,
    colbert_linear: Any,
    win_idx: int,
    total_windows: int,
) -> None:
    """Late-Chunking-Embedding für ein einzelnes Fenster.

    Ablauf:
    1. Chunk-Texte zu Fenstertext verketten, Zeichenoffsets merken.
    2. Tokenisierung mit return_offsets_mapping=True → Char→Token-Mapping.
    3. Forward-Pass: bidirektionale Attention über gesamtes Fenster.
    4. Dense:   Mean-Pool der Chunk-Tokens + L2-Norm.
    5. Sparse:  relu(sparse_linear) → {token_id → max_weight} Dict.
    6. ColBERT: colbert_linear auf hidden[1:] + L2-Norm → Token-Slice.

    Schreibt dense/sparse/colbert direkt in die Chunk-Dicts (in-place).

    Args:
        window_chunks: Chunks in diesem Fenster.
        transformer: XLM-RoBERTa Transformer aus BGE-M3.
        tokenizer: HuggingFace Fast Tokenizer.
        sparse_linear: nn.Linear für Sparse-Gewichte.
        colbert_linear: nn.Linear für ColBERT-Projektion.
        win_idx: Aktueller Fenster-Index (für Logging).
        total_windows: Gesamtzahl der Fenster (für Logging).
    """
    log.info(
        "  Late-Chunk-Fenster %d/%d: %d Chunk(s), %d Zeichen",
        win_idx + 1,
        total_windows,
        len(window_chunks),
        sum(len(c["text"]) for c in window_chunks),
    )

    # ── 1. Fenstertext + Zeichenoffsets aufbauen ──────────────────────────────
    window_text = ""
    char_ranges: list[tuple[int, int]] = []
    for i, chunk in enumerate(window_chunks):
        start = len(window_text)
        window_text += chunk["text"]
        char_ranges.append((start, len(window_text)))
        if i < len(window_chunks) - 1:
            window_text += _WINDOW_SEP

    # ── 2. Tokenisierung mit Offset-Mapping ───────────────────────────────────
    encoded = tokenizer(
        window_text,
        return_tensors="pt",
        return_offsets_mapping=True,
        truncation=True,
        max_length=BGE_MAX_TOKENS,
        padding=False,
    )
    offset_mapping = encoded.pop("offset_mapping")[0]
    off_start = offset_mapping[:, 0]
    off_end = offset_mapping[:, 1]

    # ── 3. Forward-Pass ───────────────────────────────────────────────────────
    encoded_gpu = {k: v.to("cuda") for k, v in encoded.items()}
    hidden: Any = None
    tok_weights: Any = None
    colbert_all: Any = None
    try:
        with torch.no_grad():
            outputs = transformer(**encoded_gpu)
            hidden = outputs.last_hidden_state[0]  # (seq_len, 1024)
            tok_weights = torch.relu(sparse_linear(hidden)).squeeze(-1)
            colbert_all = colbert_linear(hidden[1:])
            colbert_all = torch.nn.functional.normalize(colbert_all, p=2, dim=-1)
    finally:
        del encoded_gpu
        torch.cuda.empty_cache()

    # ── 4–6. Chunk-spezifische Aggregation ───────────────────────────────────
    for chunk, (char_start, char_end) in zip(window_chunks, char_ranges, strict=True):
        tok_mask: torch.Tensor = (off_start < char_end) & (off_end > char_start) & (off_end > 0)

        if not tok_mask.any():
            log.warning(
                "Keine Tokens für Chunk '%s' – Fallback auf alle Content-Tokens.",
                chunk.get("header", "?"),
            )
            tok_mask = off_end > 0

        tok_mask_gpu = tok_mask.to("cuda")

        # Dense: Mean-Pool + L2-Normalisierung
        chunk_hidden = hidden[tok_mask_gpu]
        dense_vec = chunk_hidden.mean(dim=0)
        dense_vec = torch.nn.functional.normalize(dense_vec.unsqueeze(0), p=2, dim=-1).squeeze(0)
        chunk["dense"] = dense_vec.cpu().tolist()

        # Sparse: {token_id_str → max_weight} Dict für Qdrant SparseVector
        chunk_ids = encoded["input_ids"][0][tok_mask]  # CPU
        chunk_weights = tok_weights[tok_mask_gpu].cpu()
        sparse_dict: dict[str, float] = {}
        for tid, w in zip(chunk_ids.tolist(), chunk_weights.tolist(), strict=True):
            if w > 0.0:
                key = str(tid)
                if key not in sparse_dict or w > sparse_dict[key]:
                    sparse_dict[key] = w
        chunk["sparse"] = sparse_dict

        # ColBERT: Token-Slice der projizierten Vektoren
        colbert_mask = tok_mask[1:].to("cuda")
        if colbert_mask.any():
            chunk["colbert"] = colbert_all[colbert_mask].cpu().tolist()
        else:
            # Fallback: Dense-Vec durch colbert_linear
            fallback = colbert_linear(dense_vec)
            fallback = torch.nn.functional.normalize(fallback.unsqueeze(0), p=2, dim=-1)
            chunk["colbert"] = fallback.cpu().tolist()

    del hidden, tok_weights, colbert_all, off_start, off_end, offset_mapping
    torch.cuda.empty_cache()


def late_chunk_embed(model: Any, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Epic 3: Late Chunking – kontextualisiertes Multi-Vector Embedding.

    Kernidee (Jina AI, 2024):
        Fenster von bis zu LATE_CHUNK_WINDOW_TOKENS Tokens werden als
        zusammenhängender Text an BGE-M3 übergeben. Die bidirektionale
        Attention kontextualisiert jeden Token gegen seinen Dokumentkontext.

    VRAM-Budget:
        BGE-M3 (FP16) ~1,1 GB. Sequentielle Fensterverarbeitung → deterministischer
        Peak-VRAM.

    Interne Zugriffe auf BGEM3FlagModel-Attribute:
        model.model          → BGEM3Model (nn.Module)
        bgem3.model          → XLM-RoBERTa Transformer
        bgem3.tokenizer      → Fast Tokenizer
        bgem3.sparse_linear  → nn.Linear(1024, 1)
        bgem3.colbert_linear → nn.Linear(1024, 1024) (FlagEmbedding >= 1.3)

    Args:
        model: BGEM3FlagModel-Instanz.
        chunks: Chunk-Dicts (werden in-place mit dense/sparse/colbert befüllt).

    Returns:
        Dieselbe chunks-Liste mit eingebetteten Vektoren.
    """
    import importlib.metadata

    from packaging import version

    try:
        fe_version = version.parse(importlib.metadata.version("FlagEmbedding"))
    except importlib.metadata.PackageNotFoundError:
        fe_version = version.parse("0.0.0")
    if fe_version < version.parse("1.2.0"):
        log.critical("Late Chunking erfordert FlagEmbedding >= 1.2.0.")
        sys.exit(1)

    bgem3_inner = model.model  # BGEM3Model

    for attr in ("model", "tokenizer", "sparse_linear", "colbert_linear"):
        if not hasattr(bgem3_inner, attr):
            log.critical(
                "FlagEmbedding-Interna '%s' nicht in BGEM3Model gefunden. "
                "Erfordert FlagEmbedding >= 1.2. Aktualisieren mit: uv add 'flagembedding>=1.2'",
                attr,
            )
            sys.exit(1)

    transformer = bgem3_inner.model.to("cuda")
    tokenizer = bgem3_inner.tokenizer
    sparse_linear = bgem3_inner.sparse_linear.to("cuda")
    colbert_linear = bgem3_inner.colbert_linear.to("cuda")

    windows = _group_into_windows(chunks, tokenizer)
    log.info(
        "Late Chunking: %d Chunk(s) → %d Fenster (Ziel: max %d Tokens/Fenster)",
        len(chunks),
        len(windows),
        LATE_CHUNK_WINDOW_TOKENS,
    )

    for win_idx, window_chunks in enumerate(windows):
        _embed_window(
            window_chunks,
            transformer,
            tokenizer,
            sparse_linear,
            colbert_linear,
            win_idx,
            len(windows),
        )

    log.info("Late-Chunking-Embedding abgeschlossen.")
    return chunks


# ════════════════════════════════════════════════════════════════════════════
# PHASE 3c – Qdrant Upsert via gRPC
# ════════════════════════════════════════════════════════════════════════════


def build_qdrant_client() -> Any:
    """Verbindet mit der lokalen Qdrant-Instanz via gRPC.

    gRPC ist schneller als REST bei großen Batch-Uploads.

    Returns:
        QdrantClient-Instanz.

    Raises:
        SystemExit: Wenn Qdrant nicht erreichbar oder die Collection fehlt.
    """
    from qdrant_client import QdrantClient
    from qdrant_client.models import HnswConfigDiff, PayloadSchemaType

    _masked = (QDRANT_API_KEY[:4] + "***") if QDRANT_API_KEY else "—"
    log.info("Verbinde mit Qdrant gRPC: %s:%d (key: %s)", QDRANT_HOST, QDRANT_GRPC_PORT, _masked)
    client = QdrantClient(
        host=QDRANT_HOST,
        grpc_port=QDRANT_GRPC_PORT,
        prefer_grpc=True,
        api_key=QDRANT_API_KEY if QDRANT_API_KEY else None,
        https=False,
        check_compatibility=False,
    )

    try:
        collections = client.get_collections()
        known = [c.name for c in collections.collections]
        if COLLECTION_NAME not in known:
            log.error(
                "Collection '%s' nicht gefunden! Bekannte: %s. Bitte Phase 1 zuerst ausführen.",
                COLLECTION_NAME,
                known,
            )
            sys.exit(1)
        log.info("Qdrant verbunden. Collection '%s' bereit.", COLLECTION_NAME)
    except Exception:
        log.critical(
            "Qdrant nicht erreichbar. Läuft Qdrant auf localhost:6333/6334?", exc_info=True
        )
        sys.exit(1)

    # Epic 1B: HNSW-Tuning (idempotent)
    try:
        client.update_collection(
            collection_name=COLLECTION_NAME,
            hnsw_config=HnswConfigDiff(m=0, payload_m=16),
        )
        log.info("HNSW-Tuning gesetzt: m=0 (kein globaler Graph), payload_m=16.")
    except Exception:
        log.warning(
            "HNSW-Tuning konnte nicht gesetzt werden (ggf. bereits korrekt).", exc_info=True
        )

    try:
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="domain",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        log.info("Payload-Index auf 'domain' bereit.")
    except Exception:
        log.warning(
            "Payload-Index für 'domain' konnte nicht angelegt werden (ggf. existiert).",
            exc_info=True,
        )

    return client


def upsert_to_qdrant(client: Any, chunks: list[dict[str, Any]], domain: str) -> None:
    """Sendet alle Chunks mit Dense-, Sparse- und ColBERT-Vektoren an Qdrant.

    Nutzt Batch-Upserts für maximalen Durchsatz via gRPC.
    Das domain-Tag wird als Payload gespeichert für FieldCondition-Filter (Epic 1A).

    Args:
        client: QdrantClient-Instanz.
        chunks: Chunks mit dense, sparse, colbert Vektoren.
        domain: Domain-Label für alle Chunks dieses Ingests.
    """
    from qdrant_client.models import PointStruct, SparseVector

    points = []
    for chunk in chunks:
        sparse_raw: dict[str, Any] = chunk["sparse"]
        # int(float(k)): FlagEmbedding gibt Keys manchmal als "1024.0" zurück
        sparse_vec = SparseVector(
            indices=[int(float(k)) for k in sparse_raw],
            values=[float(v) for v in sparse_raw.values()],
        )

        points.append(
            PointStruct(
                id=stable_uuid(chunk["source"], chunk["chunk_id"]),
                vector={
                    "dense": chunk["dense"],
                    "sparse": sparse_vec,
                    "colbert": chunk["colbert"],
                },
                payload={
                    "text": chunk["text"],
                    "source": chunk["source"],
                    "chunk_id": chunk["chunk_id"],
                    "header": chunk.get("header", ""),
                    "domain": domain,
                },
            )
        )

    total = len(points)
    n_batches = (total + EMBED_BATCH_SIZE - 1) // EMBED_BATCH_SIZE
    log.info("Starte Qdrant-Upsert: %d Punkte, %d Batch(es)", total, n_batches)

    for i in range(n_batches):
        batch = points[i * EMBED_BATCH_SIZE : (i + 1) * EMBED_BATCH_SIZE]
        client.upsert(collection_name=COLLECTION_NAME, points=batch)
        log.info("  Upsert Batch %d/%d OK (%d Punkte)", i + 1, n_batches, len(batch))

    log.info("Upsert abgeschlossen: %d Punkte in '%s'", total, COLLECTION_NAME)


# ════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ════════════════════════════════════════════════════════════════════════════


def collect_pdfs(input_path: Path) -> list[Path]:
    """Sammelt PDF-Dateipfade aus einer Datei oder einem Verzeichnis.

    Path-Traversal-Schutz: Pfad muss innerhalb INGEST_BASE_DIR liegen.

    Args:
        input_path: Pfad zur Eingabedatei oder zum Eingabeverzeichnis.

    Returns:
        Liste der gefundenen PDF-Dateien.

    Raises:
        SystemExit: Bei Sicherheits- oder Dateifehler.
    """
    if not input_path.is_relative_to(INGEST_BASE_DIR):
        log.error(
            "Sicherheitsfehler: Pfad '%s' liegt außerhalb '%s'. "
            "INGEST_BASE_DIR in .env anpassen falls nötig.",
            input_path,
            INGEST_BASE_DIR,
        )
        sys.exit(1)

    if input_path.is_file():
        if input_path.suffix.lower() != ".pdf":
            log.error("Eingabedatei ist kein PDF: %s", input_path)
            sys.exit(1)
        return [input_path]

    if input_path.is_dir():
        pdfs = sorted(input_path.glob("**/*.pdf"))
        if not pdfs:
            log.error("Keine PDF-Dateien in %s gefunden.", input_path)
            sys.exit(1)
        log.info("%d PDF(s) gefunden in: %s", len(pdfs), input_path)
        return pdfs

    log.error("Pfad existiert nicht: %s", input_path)
    sys.exit(1)


# ════════════════════════════════════════════════════════════════════════════
# Einstiegspunkt
# ════════════════════════════════════════════════════════════════════════════


def main() -> None:
    """CLI entry point für python -m titan.ingest."""
    parser = argparse.ArgumentParser(
        description="Titan Ingest – Phase 2, 3 & Epic 3: PDF → Markdown → Late Chunking → Qdrant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input",
        type=str,
        help="Pfad zu einer PDF-Datei oder einem Verzeichnis mit PDF-Dateien",
    )
    parser.add_argument(
        "--domain",
        type=str,
        default="general",
        help="Themengebiet für diesen Ingest (z.B. 'trading', 'titan'). Standard: 'general'.",
    )
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    pdf_paths = collect_pdfs(input_path)

    # ── Phase 2a: Parallel-Parsing (CPU-bound, Multiprocessing) ──────────────
    parsed = parse_pdfs_parallel(pdf_paths)
    if not parsed:
        log.error("Kein Dokument erfolgreich geparst. Abbruch.")
        sys.exit(1)

    # ── Phase 2b: Header-basiertes Chunking ───────────────────────────────────
    all_chunks: list[dict[str, Any]] = []
    for path, markdown in parsed:
        chunks = chunk_markdown(markdown, path)
        all_chunks.extend(chunks)

    log.info("Chunking gesamt: %d Chunks aus %d Dokument(en)", len(all_chunks), len(parsed))
    if not all_chunks:
        log.warning("Keine Chunks erzeugt (leere Dokumente?). Abbruch.")
        sys.exit(0)

    # ── GPU-Lock vor Modell-Load erwerben ─────────────────────────────────────
    try:
        _gpu_lock = acquire_gpu_lock()  # kept open until process exit
    except RuntimeError as e:
        log.critical(str(e))
        sys.exit(1)

    # ── Phase 3a: BGE-M3 laden ────────────────────────────────────────────────
    model = load_bge_m3_model()

    # ── Phase 3b: Late-Chunking-Embedding (Epic 3) ────────────────────────────
    all_chunks = late_chunk_embed(model, all_chunks)

    # ── Phase 3c: Upsert in Qdrant via gRPC ──────────────────────────────────
    client = build_qdrant_client()
    upsert_to_qdrant(client, all_chunks, domain=args.domain)

    log.info("Pipeline Phase 2, 3 & Epic 3 erfolgreich abgeschlossen.")


# Multiprocessing-Safety: Worker-Prozesse dürfen main() nicht erneut ausführen
if __name__ == "__main__":
    main()
