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
import re
import sys
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import torch

from titan.config import settings
from titan.models import Chunk
from titan.utils import acquire_gpu_lock

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(processName)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Konfiguration (zentral in titan.config, hier nur Aliase) ────────────────
COLLECTION_NAME: str = settings.collection_name
MAX_WORKERS: int = settings.max_workers
EMBED_BATCH_SIZE: int = settings.embed_batch_size

# Path-Traversal-Schutz: Ingest nur aus diesem Verzeichnis erlaubt
INGEST_BASE_DIR: Path = settings.ingest_base_dir.resolve()

# Optionale Frontmatter-Felder, die den Kuratierungszustand einer Note
# beschreiben. Sie wandern in die Qdrant-Payload, damit sich danach filtern
# laesst — vor allem "was behauptet der Vault, das seit Monaten niemand
# nachgesehen hat".
#   updated   – wann die Note zuletzt inhaltlich geaendert wurde
#   geprueft  – wann sie zuletzt gegen die Wirklichkeit gehalten wurde;
#               fehlt bewusst, wenn das nie passiert ist
#   quelle    – gemessen | recherchiert | ueberlegt | agent-entwurf
CURATION_FIELDS: tuple[str, ...] = ("updated", "geprueft", "quelle")

# Epic 3: Late Chunking
# BGE-M3 Hard-Limit: 8192 Tokens. Fenster-Ziel mit ~400-Token-Puffer.
BGE_MAX_TOKENS: int = 8192
LATE_CHUNK_WINDOW_TOKENS: int = settings.late_chunk_window_tokens
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
        # Kein Timeout auf as_completed: der Gesamt-Timeout wirft beim ITERIEREN
        # und würde den restlichen Batch (inkl. bereits fertiger Ergebnisse)
        # verwerfen, sobald der ganze Lauf länger dauert. Ein Per-PDF-Timeout ist
        # mit ProcessPool nicht sauber möglich (cancel() wirkt nicht auf laufende
        # Worker) — ein hängendes PDF blockiert nur seinen eigenen Worker-Slot.
        for future in as_completed(future_map):
            source_path = future_map[future]
            try:
                path, md = future.result()
                results.append((path, md))
            except Exception:
                log.error("Parsing fehlgeschlagen: %s", source_path.name, exc_info=True)

    log.info("Parsing abgeschlossen: %d/%d Dateien erfolgreich", len(results), n)
    return results


# ════════════════════════════════════════════════════════════════════════════
# PHASE 2b – Header-basiertes Chunking
# ════════════════════════════════════════════════════════════════════════════


def _compute_section_id(source: str, header: str) -> str:
    return hashlib.sha256(f"{source}::{header}".encode()).hexdigest()[:16]


_HEADER_RE = re.compile(r"^(#{1,2}\s+.+)", re.MULTILINE)


def chunk_markdown(markdown: str, source_path: Path) -> list[Chunk]:
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
        Liste von Chunk-Objekten (ohne Embeddings).
    """
    matches = list(_HEADER_RE.finditer(markdown))

    global_chunk_id = 0

    first_h1 = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    document_title = first_h1.group(1).strip() if first_h1 else source_path.stem

    def _split_text(text: str, header: str) -> list[Chunk]:
        nonlocal global_chunk_id
        sub_chunks: list[Chunk] = []
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
                    Chunk(
                        text=sub_text,
                        source=source_path.name,
                        chunk_id=global_chunk_id,
                        header=sub_header,
                        section_id=_compute_section_id(source_path.name, header),
                        document_title=document_title,
                    )
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
                Chunk(
                    text=text,
                    source=source_path.name,
                    chunk_id=global_chunk_id,
                    header=source_path.stem,
                    section_id=_compute_section_id(source_path.name, source_path.stem),
                    document_title=document_title,
                )
            ]
        log.warning(
            "Dokument '%s' hat keinen Header und ist zu groß (%d Zeichen) – "
            "wird in Sub-Chunks aufgeteilt.",
            source_path.name,
            len(text),
        )
        return _split_text(text, source_path.stem)

    chunks: list[Chunk] = []
    starts = [m.start() for m in matches]
    ends = [*starts[1:], len(markdown)]

    for match, start, end in zip(matches, starts, ends, strict=True):
        text = markdown[start:end].strip()
        if not text:
            continue
        header_title = match.group(1).lstrip("#").strip()

        if len(text) <= MAX_SUPER_CHUNK_CHARS:
            chunks.append(
                Chunk(
                    text=text,
                    source=source_path.name,
                    chunk_id=global_chunk_id,
                    header=header_title,
                    section_id=_compute_section_id(source_path.name, header_title),
                    document_title=document_title,
                )
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
    """CLI-Wrapper um titan.infra.load_bge_m3_model (sys.exit statt Exception).

    FlagEmbedding erzeugt in einem encode()-Aufruf:
      - dense_vecs       – 1024-dim Dense-Vektor
      - lexical_weights  – Sparse Token-Gewichte
      - colbert_vecs     – Token-Level Matrizen für Late Interaction / MaxSim

    Returns:
        BGEM3FlagModel-Instanz.

    Raises:
        SystemExit: Wenn FlagEmbedding nicht installiert ist.
    """
    from titan.infra import load_bge_m3_model as _load

    try:
        return _load()
    except RuntimeError as exc:
        log.critical(str(exc))
        sys.exit(1)


# ════════════════════════════════════════════════════════════════════════════
# PHASE 3b – Late Chunking Embedding (Epic 3)
# ════════════════════════════════════════════════════════════════════════════


def _group_into_windows(chunks: list[Chunk], tokenizer: Any) -> list[list[Chunk]]:
    """Gruppiert Chunks in Fenster von max. LATE_CHUNK_WINDOW_TOKENS Tokens.

    Verwendet tokenizer.encode() für genaue Token-Zählung. Ingestion ist ein
    Offline-Prozess, daher ist die doppelte Tokenisierung latenzunkritisch.

    Args:
        chunks: Liste von Chunk-Objekten.
        tokenizer: HuggingFace Fast Tokenizer aus dem BGE-M3-Modell.

    Returns:
        Liste von Fenstern, jedes eine Chunk-Liste.
    """
    windows: list[list[Chunk]] = []
    current: list[Chunk] = []
    current_tokens: int = 0

    for chunk in chunks:
        n = len(tokenizer.encode(chunk.text, add_special_tokens=False))

        if n > LATE_CHUNK_WINDOW_TOKENS:
            log.warning(
                "Chunk '%s' hat ~%d Tokens (Limit: %d) – eigenes Fenster, "
                "BGE-M3 trunciert auf 8192 Tokens.",
                chunk.header,
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
    window_chunks: list[Chunk],
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

    Schreibt dense/sparse/colbert direkt in die Chunk-Objekte (in-place).

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
        sum(len(c.text) for c in window_chunks),
    )

    # ── 1. Fenstertext + Zeichenoffsets aufbauen ──────────────────────────────
    window_text = ""
    char_ranges: list[tuple[int, int]] = []
    for i, chunk in enumerate(window_chunks):
        start = len(window_text)
        window_text += chunk.text
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
                chunk.header,
            )
            tok_mask = off_end > 0

        tok_mask_gpu = tok_mask.to("cuda")

        # Dense: Mean-Pool + L2-Normalisierung
        chunk_hidden = hidden[tok_mask_gpu]
        dense_vec = chunk_hidden.mean(dim=0)
        dense_vec = torch.nn.functional.normalize(dense_vec.unsqueeze(0), p=2, dim=-1).squeeze(0)
        chunk.dense = dense_vec.cpu().tolist()

        # Sparse: {token_id_str → max_weight} Dict für Qdrant SparseVector
        chunk_ids = encoded["input_ids"][0][tok_mask]  # CPU
        chunk_weights = tok_weights[tok_mask_gpu].cpu()
        sparse_dict: dict[str, float] = {}
        for tid, w in zip(chunk_ids.tolist(), chunk_weights.tolist(), strict=True):
            if w > 0.0:
                key = str(tid)
                if key not in sparse_dict or w > sparse_dict[key]:
                    sparse_dict[key] = w
        chunk.sparse = sparse_dict

        # ColBERT: Token-Slice der projizierten Vektoren
        colbert_mask = tok_mask[1:].to("cuda")
        if colbert_mask.any():
            chunk.colbert = colbert_all[colbert_mask].cpu().tolist()
        else:
            # Fallback: Dense-Vec durch colbert_linear
            fallback = colbert_linear(dense_vec)
            fallback = torch.nn.functional.normalize(fallback.unsqueeze(0), p=2, dim=-1)
            chunk.colbert = fallback.cpu().tolist()

    del hidden, tok_weights, colbert_all, off_start, off_end, offset_mapping
    torch.cuda.empty_cache()


def late_chunk_embed(model: Any, chunks: list[Chunk]) -> list[Chunk]:
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
        chunks: Chunk-Objekte (werden in-place mit dense/sparse/colbert befüllt).

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
    """Verbindet mit der lokalen Qdrant-Instanz via gRPC (Ingest-Setup).

    Konstruktion via titan.infra; hier zusätzlich Collection-Check,
    HNSW-Tuning und Payload-Index (idempotent).

    Returns:
        QdrantClient-Instanz.

    Raises:
        SystemExit: Wenn Qdrant nicht erreichbar oder die Collection fehlt.
    """
    from qdrant_client.models import HnswConfigDiff, PayloadSchemaType

    from titan.infra import make_qdrant_client

    client = make_qdrant_client()

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


def upsert_files_to_qdrant(
    client: Any,
    files_with_chunks: list[tuple[Path, list[Chunk]]],
    domain: str,
) -> None:
    """Schreibt Chunks pro Quelldatei nach Qdrant — gleiches Payload-Schema wie der Service.

    Nutzt make_point() (source_path, run_id, content_hash) statt eines eigenen
    CLI-Payloads: zwei Schemata in einer Collection führten dazu, dass
    PDF-Chunks in /notes unsichtbar und per DELETE /chunks unlöschbar waren.

    Upsert-before-Delete mit run_id wie in POST /ingest/file: erst die neuen
    Punkte upserten, dann alle Punkte derselben Datei mit fremder run_id
    löschen. Das räumt Orphans auf, wenn ein Re-Ingest weniger Chunks erzeugt,
    und entfernt Altbestände des früheren CLI-Schemas (Payload mit
    source=Dateiname, ohne run_id).

    Args:
        client: QdrantClient-Instanz.
        files_with_chunks: Pro Quelldatei die embeddeten Chunks.
        domain: Domain-Label für alle Chunks dieses Ingests.
    """
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    for file_path, chunks in files_with_chunks:
        run_id = str(uuid.uuid4())
        content_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        points = [make_point(c, file_path, domain, run_id, content_hash) for c in chunks]

        total = len(points)
        n_batches = (total + EMBED_BATCH_SIZE - 1) // EMBED_BATCH_SIZE
        log.info(
            "Starte Qdrant-Upsert: %s – %d Punkte, %d Batch(es)", file_path.name, total, n_batches
        )
        for i in range(n_batches):
            batch = points[i * EMBED_BATCH_SIZE : (i + 1) * EMBED_BATCH_SIZE]
            client.upsert(collection_name=COLLECTION_NAME, points=batch)
            log.info("  Upsert Batch %d/%d OK (%d Punkte)", i + 1, n_batches, len(batch))

        # Stale Chunks dieser Datei löschen: aktuelles Schema (source_path) und
        # Legacy-CLI-Schema (source=Dateiname). must_not auf run_id matcht auch
        # Punkte ohne run_id-Feld — Legacy-Punkte werden damit miterfasst.
        for key, value in (("source_path", str(file_path)), ("source", file_path.name)):
            client.delete(
                collection_name=COLLECTION_NAME,
                points_selector=Filter(
                    must=[FieldCondition(key=key, match=MatchValue(value=value))],
                    must_not=[FieldCondition(key="run_id", match=MatchValue(value=run_id))],
                ),
            )

        log.info(
            "Upsert abgeschlossen: %s → %d Punkte in '%s'", file_path.name, total, COLLECTION_NAME
        )


# ════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ════════════════════════════════════════════════════════════════════════════


def read_markdown(file_path: Path) -> tuple[str, dict[str, Any]]:
    """Liest eine Markdown-Datei mit YAML-Frontmatter.

    Args:
        file_path: Absoluter Pfad zur .md-Datei.

    Returns:
        Tuple (content, metadata):
            content  – Markdown-Inhalt ohne Frontmatter.
            metadata – Frontmatter-Dict. Enthält ``_skip: True`` wenn
                       ``indexed: false`` gesetzt ist. Domain und die
                       Kuratierungsfelder (CURATION_FIELDS) werden mit
                       sanitize() bereinigt (Prompt-Injection-Schutz); nicht
                       gesetzte Kuratierungsfelder stehen explizit auf ``None``.

    Raises:
        ValueError: Wenn domain fehlt oder leer ist (und indexed nicht false).
        FileNotFoundError: Wenn die Datei nicht existiert.
    """
    import frontmatter  # python-frontmatter

    from titan.utils import sanitize

    post = frontmatter.load(str(file_path))
    meta: dict[str, Any] = dict(post.metadata)
    content: str = post.content

    # indexed:false → Sentinel zurückgeben
    if meta.get("indexed") is False or str(meta.get("indexed", "")).lower() == "false":
        return content, {**meta, "_skip": True}

    # Domain ist Pflicht für indexierbare Notes
    raw_domain = meta.get("domain", "")
    if not raw_domain or not str(raw_domain).strip():
        raise ValueError(
            f"Frontmatter-Feld 'domain' fehlt oder ist leer in: {file_path}. "
            "Setze 'indexed: false' um die Note zu überspringen."
        )

    meta["domain"] = sanitize(str(raw_domain).strip())

    # Kuratierungsfelder normalisieren. Sie landen in der Payload und damit in
    # Suchtreffern, bekommen also dieselbe sanitize()-Behandlung wie die Domain.
    # PyYAML liefert ein unquotiertes Datum als date-Objekt, ein quotiertes als
    # String — str() vereinheitlicht beides auf ISO-8601.
    # Fehlend bleibt bewusst None statt "": Ein leerer String liesse sich nicht
    # von "nie geprüft" unterscheiden, und genau diese Unterscheidung ist der
    # ganze Zweck von 'geprueft'.
    # Diese Felder sind einwertig: ein Datum, oder eines von vier Woertern. Ein
    # Zeilenumbruch darin ist immer ein Fehler, also wird Whitespace kollabiert,
    # bevor sanitize() die Separatoren entschaerft.
    for field in CURATION_FIELDS:
        raw = meta.get(field)
        if raw in (None, ""):
            meta[field] = None
            continue
        meta[field] = sanitize(" ".join(str(raw).split()))

    return content, meta


def late_chunk_and_embed(content: str, model: Any) -> list[Chunk]:
    """Kombiniert chunk_markdown + late_chunk_embed für Markdown-Content.

    Convenience-Wrapper für den Service-Pfad. Das Modell wird übergeben
    (kein eigenes Loading/Lock).

    Args:
        content: Markdown-Inhalt (ohne Frontmatter).
        model: Vorgeladene BGEM3FlagModel-Instanz.

    Returns:
        Chunks mit dense/sparse/colbert-Vektoren.
    """
    # Für Markdown ohne echten source-Pfad nutzen wir einen Placeholder.
    # Der Caller setzt source_path im Payload beim Upsert.
    placeholder_path = Path("<service-ingest>")
    raw_chunks = chunk_markdown(content, placeholder_path)
    return late_chunk_embed(model, raw_chunks)


def make_point(
    chunk: Chunk,
    file_path: Path,
    domain: str,
    run_id: str,
    content_hash: str,
    *,
    curation: dict[str, str | None] | None = None,
) -> Any:
    """Erstellt ein Qdrant-PointStruct aus einem embedded Chunk.

    Setzt source_path, domain, run_id, content_hash und – sofern vorhanden –
    die Kuratierungsfelder (CURATION_FIELDS) im Payload.

    Args:
        chunk:        Chunk mit befüllten dense/sparse/colbert-Vektoren.
        file_path:    Absoluter Pfad der ingestierten Datei (source_path im Payload).
        domain:       Domain-Label.
        run_id:       UUID dieses Ingest-Runs (für Upsert-before-Delete-Pattern).
        content_hash: sha256-Hex-Digest der rohen Datei-Bytes (für Vault-Reconcile).
        curation:     Optional die Werte aus CURATION_FIELDS. Keyword-only und
                      optional, weil der CLI-/PDF-Pfad kein Frontmatter hat —
                      dort bleiben die Felder ungesetzt statt leer erfunden.
                      ``None``-Werte werden **nicht** ins Payload geschrieben:
                      ein fehlendes Feld ist in Qdrant filterbar ("is_empty"),
                      ein leerer String wäre nur ein weiterer Wert.

    Returns:
        PointStruct bereit für qdrant_client.upsert().

    Raises:
        ValueError: Wenn der Chunk noch keine Embeddings trägt.
    """
    from qdrant_client.models import PointStruct, SparseVector

    from titan.utils import stable_uuid

    if chunk.dense is None or chunk.sparse is None or chunk.colbert is None:
        raise ValueError("Chunk ohne Embeddings — late_chunk_embed() zuerst aufrufen.")

    # int(float(k)): FlagEmbedding gibt Keys manchmal als "1024.0" zurück
    sparse_vec = SparseVector(
        indices=[int(float(k)) for k in chunk.sparse],
        values=[float(v) for v in chunk.sparse.values()],
    )

    source_str = str(file_path)
    payload: dict[str, Any] = {
        "text": chunk.text,
        "source": source_str,
        "source_path": source_str,  # Alias für Service-Queries
        "chunk_id": chunk.chunk_id,
        "chunk_offset": chunk.chunk_id,  # Alias für Service-Schema
        "header": chunk.header,
        "domain": domain,
        "run_id": run_id,
        "content_hash": content_hash,
    }
    for field in CURATION_FIELDS:
        value = (curation or {}).get(field)
        if value:
            payload[field] = value

    return PointStruct(
        id=stable_uuid(source_str, chunk.chunk_id),
        vector={
            "dense": chunk.dense,
            "sparse": sparse_vec,
            "colbert": chunk.colbert,
        },
        payload=payload,
    )


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

    # ── Phase 2b: Header-basiertes Chunking (Datei-Zuordnung für run_id-Delete) ──
    files_with_chunks: list[tuple[Path, list[Chunk]]] = []
    all_chunks: list[Chunk] = []
    for path, markdown in parsed:
        chunks = chunk_markdown(markdown, path)
        if chunks:
            files_with_chunks.append((path, chunks))
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
    # late_chunk_embed mutiert die Chunk-Dicts in-place — die Datei-Zuordnung
    # in files_with_chunks bleibt dadurch gültig.
    late_chunk_embed(model, all_chunks)

    # ── Phase 3c: Upsert in Qdrant via gRPC ──────────────────────────────────
    client = build_qdrant_client()
    upsert_files_to_qdrant(client, files_with_chunks, domain=args.domain)

    log.info("Pipeline Phase 2, 3 & Epic 3 erfolgreich abgeschlossen.")


# Multiprocessing-Safety: Worker-Prozesse dürfen main() nicht erneut ausführen
if __name__ == "__main__":
    main()
