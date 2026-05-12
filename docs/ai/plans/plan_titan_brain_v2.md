# Plan: Titan-Brain v2 — Service-Layer + MCP-Server

> **Autor:** Claude Opus 4.7
> **Datum:** Mai 2026
> **Vorgänger:** Phase 0a (Inventur), Phase 0b (Migration) abgeschlossen
> **Workflow:** Opus-Plan → Sonnet pro Task → Audit-Runde am Ende jeder Phase
> **Voraussetzung:** Phase 0b mit allen Cleanup-Punkten geschlossen (ColBERT-ADR, GPU_LOCK-ADR, HANDOFF, CI-Konsistenz)

---

## 0. Was sich gegenüber Plan v1 ändert

Sonnets Architektur-Review hatte sieben Befunde. Alle sind in diesem Plan adressiert:

| # | Befund v1 | Lösung in v2 |
|---|---|---|
| 1 | `GET /domains` und `POST /find_related` fehlten als Tasks | Eigene Tasks A7 und A8, plus Backend-Helfer in `titan.search` |
| 2 | API-Contract für `indexed: false` unklar | Eigener Abschnitt 2.3 mit klarer Semantik: Service entscheidet, Client trusts |
| 3 | Update-Semantik-Widerspruch (delete-before vs upsert-before) | Eindeutig: **Upsert-before-Delete**, mit detailliertem Begründungsabschnitt |
| 4 | VRAM-Test ohne Verantwortlichen | Eigene Task A0 vor allen anderen Endpoints |
| 5 | E2E-Test mit `time.sleep(35)` | Test-Hook in Watcher (Debouncing-Window injectierbar), Tests laufen mit 1 Sekunde |
| 6 | `Requires=` macht Watcher abhängig von Titan-Service | `Wants=` plus Reconnect-Logik mit Exponential Backoff im Watcher selbst |
| 7 | `rag_system/`-Struktur statt Template-Konvention | Service als `src/titan/service/`, MCP als eigenes Repo mit Template-Standard |

Zusätzliche Lehren aus der Migration:
- Modulnamen sind jetzt fixiert (`titan.utils`, `titan.search`, `titan.ingest`, etc.)
- ColBERT-Dimension 1024 ist an FlagEmbedding 1.3+ gekoppelt — Service muss mit derselben Library-Version laufen
- `GPU_LOCK_PATH` ist `/tmp/bge_m3.lock`
- `from __future__ import annotations` und Type Hints überall — Service-Code muss diese Konvention fortführen

---

## 1. Zielbild

Drei Daemons laufen permanent, ein Prozess startet pro Claude-Session:

```
┌────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│  Obsidian (Windows) ──► Vault (F:\vault\) ──► File-Events               │
│                                              │                          │
│                                              ▼                          │
│                                  ┌──────────────────────┐               │
│                                  │  brain-watcher       │               │
│                                  │  Daemon (systemd)    │               │
│                                  │  - watchdog          │               │
│                                  │  - Debouncing 30s    │               │
│                                  │  - Reconnect-Logik   │               │
│                                  └────────┬─────────────┘               │
│                                           │ HTTP                        │
│                                           ▼                            │
│                                  ┌──────────────────────┐               │
│                                  │  titan service       │               │
│                                  │  Daemon (systemd)    │               │
│                                  │  python -m titan     │               │
│                                  │  service             │               │
│                                  │  FastAPI :8765       │               │
│                                  │  BGE-M3 im VRAM      │               │
│                                  └────────┬─────────────┘               │
│                                           │ gRPC                        │
│                                           ▼                            │
│                                  ┌──────────────────────┐               │
│                                  │  Qdrant (Docker)     │               │
│                                  └──────────────────────┘               │
│                                           ▲                            │
│  Claude Desktop / Antigravity             │                            │
│            │                              │                            │
│            │ MCP stdio                    │                            │
│            ▼                              │                            │
│  ┌──────────────────────┐                 │                            │
│  │  brain-mcp           │  HTTP /search   │                            │
│  │  (per Session)       │─────────────────┘                            │
│  │  FastMCP + httpx     │                                              │
│  └──────────────────────┘                                              │
└────────────────────────────────────────────────────────────────────────┘
```

Port 8765 statt 8000 — vermeidet Konflikte mit häufigen Dev-Servern. Wert ist beliebig, aber konsistent im ganzen Plan.

---

## 2. Architektur-Entscheidungen (vorab festgehalten)

### 2.1 Service lebt im Titan-Repo, nicht als eigenes Repo

`src/titan/service/` als neues Sub-Package — analog zu `eval/` und `tools/`. Begründung:
- Service braucht direkten Zugriff auf BGE-M3-Loading, Qdrant-Wrapper, `acquire_gpu_lock`
- Cross-Repo-Dependency (`titan-service` importiert `titan`) wäre Overhead für null Nutzen
- Tests gegen `titan.search.search()` werden Service-Tests, ohne ein zweites Repo zu pflegen

Damit ist die Repo-Landschaft am Ende:
- `titan` (existiert) — bekommt `src/titan/service/`
- `brain-mcp` (neu) — eigenständig, dependiert nur auf HTTP-Schnittstelle

### 2.2 BGE-M3 als Modul-globale Singleton-Variable

Aktuell laden `ingest.py` und `search.py` BGE-M3 jeweils in einer lokalen Funktion. Für den Service muss das Modell **einmal** geladen werden und über alle Requests verfügbar sein.

Strategie: Refactor in `titan.search`, der BGE-M3 als optional injectierbares Argument akzeptiert. Service hält das Modell als Singleton.

```python
# Vorher (titan.search):
def search(query, domain, top_k):
    model = load_bge_m3()  # jedes Mal neu
    ...

# Nachher (titan.search):
def search(query, domain, top_k, model=None):
    if model is None:
        model = load_bge_m3()  # CLI-Fallback wie bisher
    ...
```

So bleibt `python -m titan.search "query"` als CLI-Pfad voll funktional, ohne dass der Service das Modell jedes Mal neu lädt.

### 2.3 API-Contract für `indexed: false`

Klare Regel: **Der Service entscheidet, der Client trusts.**

- Watcher liest Frontmatter nicht selbst. Er ruft `POST /ingest/file` mit dem Pfad auf.
- `POST /ingest/file` liest das Frontmatter, prüft `indexed: false`, und:
  - Wenn `false`: löscht intern alle existierenden Chunks dieser Datei (für den Fall, dass sie vorher `true` war), gibt `200` mit `chunks_created: 0, chunks_deleted: N` zurück
  - Wenn `true` oder Feld fehlt: normaler Ingest

Damit gibt es nur eine Stelle, die die Semantik kennt. Der Watcher loggt das Ergebnis, agiert aber nicht aufgrund von Frontmatter-Inhalt.

### 2.4 Update-Semantik: Upsert-before-Delete (definitiv)

Wenn eine Note re-indexed wird:

```python
# 1. Neue Chunks generieren (Embedding, Chunking) — kann mehrere Sekunden dauern
new_chunks = late_chunk_and_embed(content)

# 2. Neue Chunks in Qdrant einfügen mit NEUEN UUIDs (run_id im Payload)
qdrant.upsert(new_chunks, payload={"run_id": current_run_id, ...})

# 3. ALTE Chunks dieser source mit anderem run_id löschen
qdrant.delete(filter={
    "must": [{"key": "source_path", "match": file_path}],
    "must_not": [{"key": "run_id", "match": current_run_id}]
})
```

**Warum so:** Wenn Schritt 1 oder 2 crashen, sind die alten Chunks noch da — Note bleibt durchsuchbar. Wenn Schritt 3 crash, gibt es kurzzeitig doppelte Chunks (alte + neue), was RRF als gleichwertige Treffer wertet — harmlos, wird beim nächsten Lauf bereinigt. Der gefährliche Zustand „Note ist plötzlich nicht mehr im Index, weil Re-Indexierung gestolpert ist" tritt nicht auf.

**Implementation-Detail:** `run_id` ist eine pro-Aufruf-UUID (oder `time_ns()`-Wert), wird im Chunk-Payload mitgespeichert. Ohne dieses Feld funktioniert „delete-alle-außer-aktueller-run_id" nicht.

### 2.5 Embedding-Dimensions-Lock

Service prüft beim Start, dass die installierte FlagEmbedding-Version zur Qdrant-Collection passt. Konkret: Embedding ein Dummy-String, vergleiche `len(colbert_vec[0])` mit `collection.vectors_config["colbert"].size`. Mismatch → Service refused to start mit klarer Fehlermeldung. Verhindert das Problem aus der ColBERT-ADR.

---

## 3. Phase 1 — Titan-Service-Layer

**Lokation:** `~/projects/titan/`, neuer Branch `feat/service-layer`
**Neues Sub-Package:** `src/titan/service/`

### 3.1 Aufgaben-Übersicht

| ID | Task | Aufwand | Modell | Abhängigkeit |
|---|---|---|---|---|
| A0 | VRAM-Validierung (BGE-M3 + Phi-4 parallel) | 2 h | Sonnet | — |
| A1 | FastAPI-Skeleton + Lifespan + Logging | 2 h | Sonnet | A0 |
| A2 | Pydantic-Schemas + `GET /health` | 1.5 h | Sonnet | A1 |
| A3 | Refactor `titan.search` für injectables BGE-M3 | 2 h | Sonnet | A1 |
| A4 | `POST /search` Endpoint | 2 h | Sonnet | A2, A3 |
| A5 | Markdown-Reader in `titan.ingest` | 2 h | Sonnet | — |
| A6 | `POST /ingest/file` mit Upsert-before-Delete | 3 h | Sonnet | A5 |
| A7 | `GET /domains` Endpoint | 1 h | Sonnet | A1 |
| A8 | `POST /find_related` Endpoint | 2 h | Sonnet | A3, A4 |
| A9 | `DELETE /chunks` Endpoint | 1 h | Sonnet | A6 |
| A10 | Cache-Invalidierung bei Re-Ingest | 1.5 h | Sonnet | A6 |
| A11 | systemd-Service-Datei | 30 min | Lokal | A1–A9 |
| A12 | Integration-Tests | 3 h | Sonnet | A1–A10 |
| A13 | Audit-Runde + Fixes | nach Bedarf | Opus | A1–A12 |

**Summe geplant:** ~25 h, realistisch 6–8 Abende.

### 3.2 Task A0 — VRAM-Validierung

**Ziel:** Empirische Antwort auf die Frage: Passen BGE-M3 (FP16) und ein Phi-4-Inference-Call mit `keep_alive=0` parallel in 16 GB VRAM?

**Neue Datei:** `src/titan/service/_vram_probe.py`

**Logik:**
1. BGE-M3 laden, ein Dummy-Embedding rechnen, `torch.cuda.memory_allocated()` loggen
2. Phi-4 via Ollama HTTP aufrufen (ein kurzer Prompt mit `keep_alive: 0`)
3. Während Phi-4 antwortet: `torch.cuda.memory_allocated()` und Ollama's `/api/ps` checken
4. Output: Tabelle mit Peak-VRAM (BGE-M3 idle, BGE-M3 + Phi-4 loading, BGE-M3 + Phi-4 inference)
5. Verdict: `"VRAM_MODE=relaxed feasible"` oder `"VRAM_MODE=strict required"`

**CLI-Aufruf:** `uv run python -m titan.service._vram_probe`

**Output speichern:** `docs/ai/vram_probe_results.md` mit Datum und gemessenen Werten. Beim Audit-Punkt in `DECISIONS.md` referenzieren.

**Wichtig:** Dies ist KEIN Test im `tests/`-Verzeichnis. Es ist ein einmalig manuell laufendes Validierungs-Skript. Es darf existierende Qdrant-Daten nicht anfassen.

### 3.3 Task A1 — FastAPI-Skeleton + Lifespan

**Neue Dateien:**
- `src/titan/service/__init__.py` (leer)
- `src/titan/service/app.py`
- `src/titan/service/state.py` (singleton holder für BGE-M3, Qdrant-Client)

**`state.py` Pattern:**
```python
class ServiceState:
    bge_model: BGEM3FlagModel | None = None
    qdrant_client: QdrantClient | None = None
    gpu_lock: object | None = None

state = ServiceState()
```

**`app.py` Lifespan:**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    state.gpu_lock = acquire_gpu_lock()       # systemweiter Lock
    state.bge_model = load_bge_m3()
    state.qdrant_client = make_qdrant_client()
    _validate_embedding_dimension(state)      # ColBERT-Dim-Check
    log.info("Service ready")
    yield
    # Shutdown
    del state.bge_model
    torch.cuda.empty_cache()
    if state.gpu_lock is not None:
        state.gpu_lock.close()
```

**CLI-Integration:** Erweitere `src/titan/main.py` um einen Service-Mode:
```bash
uv run python -m titan service              # startet Service
uv run python -m titan service --port 8765  # mit Port
```

Bedeutet: das Help-Dispatcher-Pattern aus Phase 0b's Cleanup wird hier konkret.

**Logging:** strukturiert (JSON-Lines), nicht print. Logger-Setup analog zu den anderen Modulen.

**Bind-Address:** Hart auf `127.0.0.1`. Nicht parametrisierbar. Wenn jemand das remote nutzen will, muss er das explizit per Reverse-Proxy machen. Service-Defaults sind sicher.

### 3.4 Task A2 — Pydantic-Schemas und `/health`

**Neue Datei:** `src/titan/service/schemas.py`

**Core-Schemas:**
```python
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    bge_loaded: bool
    qdrant_reachable: bool
    vram_used_mb: int | None
    collection_name: str
    colbert_dim: int                # für Diagnose

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=10_000)
    domain: str | None = None
    top_k: int = Field(default=10, ge=1, le=50)
    use_decompose: bool = True
    use_cache: bool = True

class Chunk(BaseModel):
    text: str
    source_path: str
    domain: str
    chunk_offset: int
    score: float
    metadata: dict[str, Any]

class SearchResponse(BaseModel):
    query: str
    chunks: list[Chunk]
    sub_queries: list[str]
    cache_hit: bool
    latency_ms: int

class IngestRequest(BaseModel):
    file_path: str                  # absolut, muss unter VAULT_ROOT liegen
    force: bool = False

class IngestResponse(BaseModel):
    file_path: str
    domain: str | None              # None wenn indexed:false
    chunks_deleted: int
    chunks_created: int
    skipped_reason: str | None      # "indexed:false" wenn Skip
    latency_ms: int

class DomainsResponse(BaseModel):
    domains: list[str]              # alphabetisch sortiert
    counts: dict[str, int]          # Domain → Anzahl Chunks

class FindRelatedRequest(BaseModel):
    file_path: str
    top_k: int = Field(default=5, ge=1, le=20)
    exclude_self: bool = True       # zur Sicherheit explizit

class FindRelatedResponse(BaseModel):
    source_path: str
    related: list[Chunk]
    latency_ms: int
```

**`/health` Endpoint:**
- `status: "ok"` wenn BGE geladen UND Qdrant reachable
- `status: "degraded"` wenn Qdrant nicht erreichbar (Service läuft, kann aber nichts liefern)
- Niemals `status: "down"` — wenn der Service nicht antworten kann, kommt der HTTP-Aufruf gar nicht erst durch

### 3.5 Task A3 — `titan.search` Refactor

**Ziel:** `titan.search.search()` akzeptiert ein optionales BGE-M3-Modell.

**Vorher (Migration-State):**
```python
def search(query: str, domain: str | None, top_k: int) -> dict:
    lock = acquire_gpu_lock()
    model = load_bge_m3()
    ...
```

**Nachher:**
```python
def search(
    query: str,
    domain: str | None,
    top_k: int,
    model: BGEM3FlagModel | None = None,
    qdrant_client: QdrantClient | None = None,
) -> dict:
    if model is None:
        # CLI-Pfad: alter Code, eigener Lock
        lock = acquire_gpu_lock()
        model = load_bge_m3()
        ...
    else:
        # Service-Pfad: Lock und Modell vom Service verwaltet
        ...
```

**Wichtig:**
- Die `if model is None`-Verzweigung soll nicht zwei komplette Code-Pfade duplizieren. Der Embedding-Schritt nutzt immer `model.encode(...)`. Nur das Loading/Lock-Management ist konditional.
- CLI-Aufruf `python -m titan.search "query"` muss weiter funktionieren — Smoke-Test dafür existiert nach Phase 0b.

**Test-Strategie:** Beim Refactor mindestens ein neuer Test, der `search()` mit einem Mock-Model aufruft (kein echtes BGE-M3 nötig, nur ein Objekt mit `.encode()`-Methode).

### 3.6 Task A4 — `POST /search`

**Endpoint-Logik:**
```python
@app.post("/search", response_model=SearchResponse)
async def search_endpoint(req: SearchRequest) -> SearchResponse:
    t0 = time.perf_counter()
    result = titan.search.search(
        query=req.query,
        domain=req.domain,
        top_k=req.top_k,
        model=state.bge_model,
        qdrant_client=state.qdrant_client,
    )
    return SearchResponse(
        query=req.query,
        chunks=[Chunk(**c) for c in result["chunks"]],
        sub_queries=result.get("sub_queries", []),
        cache_hit=result.get("cache_hit", False),
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
```

**Wichtig:**
- `async def`, aber `titan.search.search()` ist sync. Das ist OK — FastAPI verwaltet das Threadpool-Offloading automatisch (sync function calls in async endpoints werden in einen Worker-Thread verlegt). Kein Block des Event-Loops.
- Pydantic-Validation passiert vor dem Handler — Garbage-Requests werden 422 bevor `search()` aufgerufen wird.

**Rate-Limiting:** Nicht in v1. Service hört auf 127.0.0.1, einziger Caller ist der MCP-Server. Wenn das mal anders wird: `slowapi` einbauen.

### 3.7 Task A5 — Markdown-Reader

**Erweiterung in:** `src/titan/ingest.py`

**Neue Funktion:**
```python
def read_markdown(file_path: Path) -> tuple[str, dict[str, Any]]:
    """Liest .md mit Frontmatter, gibt (content, metadata) zurück."""
```

**Dependencies:** `uv add python-frontmatter`

**Logik:**
1. `frontmatter.load(file_path)`
2. Validiere `metadata["domain"]` ist string und nicht leer. Sonst: `ValueError`.
3. Wenn `metadata.get("indexed", True) is False`: spezielle Sentinel-Rückgabe `(content, {**metadata, "_skip": True})`
4. Sanitize `metadata["domain"]` mit `titan.utils.sanitize()` (verhindert Prompt-Injection über Frontmatter)
5. Return `(post.content, dict(post.metadata))`

**File-Type-Dispatch in der Hauptpipeline:**
```python
def ingest_file(path: Path) -> dict:
    if path.suffix.lower() == ".md":
        content, meta = read_markdown(path)
        if meta.get("_skip"):
            return {"skipped_reason": "indexed:false", "chunks_created": 0, ...}
        domain = meta["domain"]
    elif path.suffix.lower() == ".pdf":
        content = docling_extract(path)
        domain = "general"  # PDFs ohne Frontmatter brauchen CLI-Argument
        # ... oder explizit als Parameter
    else:
        raise UnsupportedFileType(path.suffix)

    # gemeinsamer Pfad: chunken, embedden, upserten
```

**Wichtig:** Markdown geht NICHT durch Docling. Markdown ist trivial parseable, Docling wäre Overhead. Markdown geht WEITERHIN durch Late Chunking — der Algorithmus ist content-agnostisch.

### 3.8 Task A6 — `POST /ingest/file` mit Upsert-before-Delete

**Endpoint-Logik (Pseudo-Code):**
```python
@app.post("/ingest/file", response_model=IngestResponse)
async def ingest_file_endpoint(req: IngestRequest) -> IngestResponse:
    t0 = time.perf_counter()

    # 1. Pfad-Sicherheit: muss unter VAULT_ROOT liegen
    file_path = Path(req.file_path).resolve()
    if not file_path.is_relative_to(VAULT_ROOT):
        raise HTTPException(400, "path outside VAULT_ROOT")

    # 2. Dispatch nach Filetype
    if file_path.suffix == ".md":
        content, meta = read_markdown(file_path)
        if meta.get("_skip"):
            # indexed:false → existierende Chunks löschen, sonst nichts
            n_deleted = delete_chunks_for_path(file_path)
            return IngestResponse(
                file_path=str(file_path),
                domain=None,
                chunks_deleted=n_deleted,
                chunks_created=0,
                skipped_reason="indexed:false",
                latency_ms=...,
            )
        domain = meta["domain"]
    elif file_path.suffix == ".pdf":
        # PDFs sind via CLI-Pfad, nicht via Service-Ingest
        raise HTTPException(400, "PDFs via CLI: python -m titan.ingest")
    else:
        raise HTTPException(400, f"unsupported: {file_path.suffix}")

    # 3. Upsert-before-Delete
    run_id = str(uuid.uuid4())
    new_chunks = late_chunk_and_embed(content, model=state.bge_model)

    state.qdrant_client.upsert(
        collection_name=COLLECTION_NAME,
        points=[make_point(c, file_path, domain, run_id) for c in new_chunks],
    )

    n_deleted = state.qdrant_client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="source_path", match=MatchValue(value=str(file_path)))],
            must_not=[FieldCondition(key="run_id", match=MatchValue(value=run_id))],
        ),
    ).operation_id  # oder Count, je nach Qdrant-Version

    return IngestResponse(
        file_path=str(file_path),
        domain=domain,
        chunks_deleted=n_deleted,
        chunks_created=len(new_chunks),
        skipped_reason=None,
        latency_ms=...,
    )
```

**Wichtig:**
- `run_id` muss im Chunk-Payload mitgespeichert werden. Das ist eine neue Payload-Konvention — in `DECISIONS.md` festhalten.
- PDFs werden im Service-Pfad NICHT unterstützt. Begründung: PDFs sind ein CPU-bound Multiprozess-Workflow mit Docling, das passt nicht in einen synchronen HTTP-Request. PDFs bleiben via `python -m titan.ingest` als CLI-Workflow.
- Path-Traversal-Schutz wie in `titan.ingest` (Epic 1) übernehmen — `is_relative_to(VAULT_ROOT)` ist die saubere Variante.

### 3.9 Task A7 — `GET /domains`

**Logik:**
```python
@app.get("/domains", response_model=DomainsResponse)
async def list_domains() -> DomainsResponse:
    # Qdrant aggregation: alle distinct values von payload.domain
    # plus count pro domain
    # Implementation via scroll oder facets (je nach Qdrant-Version)
    ...
```

**Implementierungs-Note:** Qdrant hat keine native `GROUP BY`-Aggregation. Praktische Wege:
- **Variante 1:** `scroll()` durch alle Punkte, manuell zählen. OK für <100k Punkte, langsam darüber.
- **Variante 2:** Cache: bei jedem Ingest/Delete ein in-memory `Counter[str, int]`, der Domain-Counts trackt. Service-Restart triggert einmaligen Voll-Scan zur Initialisierung. Empfehlung: dieser Pfad.
- **Variante 3:** Separate Qdrant-Collection nur für Domain-Statistik. Overkill.

**Empfehlung Variante 2.** State-Modul bekommt eine `domain_counts: Counter[str, int]`-Eigenschaft, die im Lifespan einmal initialisiert wird und bei Ingest/Delete inkrementell aktualisiert wird.

### 3.10 Task A8 — `POST /find_related`

**Logik:**
1. Pfad-Validierung (analog A6)
2. Aus Qdrant alle Chunks dieser source_path holen, mit `with_vectors=True`. Falls keine Chunks: 404.
3. **Strategie für die Suche:** Den ersten Chunk der Note nehmen (chunk_offset == 0), seinen Dense-Vektor verwenden, Qdrant-Search mit `must_not source_path == own_path` und denselben Domain-Filter wie der Source-Chunk.
4. Top-K Ergebnisse als `FindRelatedResponse` zurückgeben.

**Strategiebegründung:** Erster Chunk ist heuristisch der „Anker" einer Note. Komplexere Strategien (RRF über mehrere Chunks der Quell-Note, gemittelter Embedding-Vektor) sind v2-Features.

**Code-Sketch:**
```python
@app.post("/find_related", response_model=FindRelatedResponse)
async def find_related_endpoint(req: FindRelatedRequest) -> FindRelatedResponse:
    src_path = Path(req.file_path).resolve()
    # ... path-checks

    # Source-Chunks holen
    source_chunks = state.qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=[FieldCondition(
            key="source_path", match=MatchValue(value=str(src_path)))]),
        limit=1,  # erster Chunk reicht
        with_vectors=True,
        with_payload=True,
    )
    if not source_chunks[0]:
        raise HTTPException(404, "no chunks for this path")

    anchor = source_chunks[0][0]

    # Such mit Anchor-Vektor, exclude self
    results = state.qdrant_client.query_points(
        collection_name=COLLECTION_NAME,
        query=anchor.vector["dense"],
        using="dense",
        limit=req.top_k,
        query_filter=Filter(
            must_not=[FieldCondition(
                key="source_path", match=MatchValue(value=str(src_path)))],
        ),
    )

    return FindRelatedResponse(...)
```

### 3.11 Task A9 — `DELETE /chunks`

Trivial-Endpoint, nur für Watcher-Delete-Events.

```python
@app.delete("/chunks")
async def delete_chunks(source_path: str) -> dict:
    path = Path(source_path).resolve()
    if not path.is_relative_to(VAULT_ROOT):
        raise HTTPException(400, "path outside VAULT_ROOT")
    n = state.qdrant_client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(must=[FieldCondition(
            key="source_path", match=MatchValue(value=str(path)))]),
    )
    # Domain-Counter aktualisieren
    return {"chunks_deleted": n}
```

Pfad-Sicherheit identisch zu A6.

### 3.12 Task A10 — Cache-Invalidierung bei Re-Ingest

Epic 5B speichert Query-Antworten im `query_cache`-Collection. Wenn Notes einer Domain neu indexed werden, sind alte Cache-Antworten potenziell stale.

**Lösung:** Bei jedem `/ingest/file`-Call mit `chunks_created > 0`:
```python
# Lösche alle Cache-Einträge, deren Domain dieser Datei entspricht
state.qdrant_client.delete(
    collection_name=CACHE_COLLECTION_NAME,
    points_selector=Filter(must=[FieldCondition(
        key="domain", match=MatchValue(value=domain))]),
)
```

**Trade-off:** Das ist aggressive Invalidierung — eine geänderte Note kippt ALLE Cache-Einträge derselben Domain. Alternative wäre nur Einträge zu invalidieren, deren retrievte Chunks die geänderte Note enthielten — das wäre komplexer und braucht Tracking der Cache→Chunk-Abhängigkeit, was Epic 5B aktuell nicht macht.

Aggressive Variante ist OK für v1. Wenn der Cache-Hit-Rate zu sehr leidet: später Tracking nachrüsten.

**Wichtig:** Diese Logik ist in `titan.search`-Modul, nicht im Service-Endpoint. So bleibt sie testbar ohne FastAPI.

### 3.13 Task A11 — systemd-Service

**Datei:** `~/.config/systemd/user/titan-service.service`

```ini
[Unit]
Description=Titan RAG Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/charl/projects/titan
ExecStart=/home/charl/projects/titan/.venv/bin/python -m titan service --port 8765
Restart=on-failure
RestartSec=5
Environment="PYTHONUNBUFFERED=1"
StandardOutput=append:/home/charl/projects/titan/logs/service.log
StandardError=append:/home/charl/projects/titan/logs/service.err.log

[Install]
WantedBy=default.target
```

**Aktivierung:**
```bash
mkdir -p ~/projects/titan/logs
systemctl --user daemon-reload
systemctl --user enable --now titan-service
systemctl --user status titan-service
```

**Hinweis WSL:** `/etc/wsl.conf` muss `[boot]\nsystemd=true` enthalten. Falls noch nicht: einmal setzen, `wsl --shutdown` aus PowerShell.

### 3.14 Task A12 — Integration-Tests

**Neue Datei:** `tests/integration/test_service.py`

**Setup:** Pytest-Fixture, die für die Test-Suite eine separate Test-Collection in Qdrant anlegt (`titan_test_<random>`), nach Tests wieder löscht. Tests laufen NICHT gegen produktives `mein_wissen`.

**Test-Matrix:**
| # | Was | Erwartet |
|---|---|---|
| 1 | `/health` ohne Qdrant | `status: "degraded"` |
| 2 | `/health` mit Qdrant | `status: "ok"`, `bge_loaded: True` |
| 3 | `/search` mit existierender Query | Chunks zurück, `latency_ms > 0` |
| 4 | `/search` mit Domain-Filter | nur passende Domain |
| 5 | `/search` mit `top_k=51` | 422 (validation error) |
| 6 | `/ingest/file` neue Note | `chunks_created > 0`, `chunks_deleted == 0` |
| 7 | `/ingest/file` selbe Note nochmal | `chunks_created > 0`, `chunks_deleted == previous chunks_created` |
| 8 | `/ingest/file` mit `indexed: false` | `skipped_reason: "indexed:false"`, `chunks_created: 0` |
| 9 | `/ingest/file` mit Pfad außerhalb VAULT_ROOT | 400 |
| 10 | Note mit `indexed:true` → ändern auf `indexed:false` → re-ingest | alte Chunks weg |
| 11 | `GET /domains` nach mehreren Ingests | korrekte Counts |
| 12 | `/find_related` für existierende Note | Top-K von ANDEREN Notes (nicht self) |
| 13 | `/find_related` für nicht-existierende Note | 404 |
| 14 | `DELETE /chunks` | Chunks weg, Domain-Counter aktualisiert |
| 15 | Domain-Isolation: Note in A, Suche in B | 0 Treffer |
| 16 | Cache-Invalidierung: Cache fillen, Note der Domain ingesten, Cache leer | ✓ |

**Mock-Strategie:**
- BGE-M3 wird in Tests **nicht gemockt** — die echten Embeddings sind essenziell für Korrektheit von 4, 12, 15
- Phi-4-Decompose wird gemockt (Ollama-HTTP-Mock), weil sonst Tests Ollama brauchen
- Qdrant ist echt (Test-Collection)

**Marker:** Alle Tests bekommen `@pytest.mark.integration`. So bleibt `make test-fast` schnell und CI kann `not integration` setzen, wenn nötig.

### 3.15 Task A13 — Audit-Runde

**Checkliste:**

- [ ] Pfad-Traversal-Schutz auf `/ingest/file`, `/find_related`, `DELETE /chunks` aktiv?
- [ ] Pydantic-Limits sinnvoll (`max_length`, `ge/le`, `min_length`)?
- [ ] `sanitize()` auf alle Frontmatter-Werte vor Payload-Insert?
- [ ] BGE-M3 wird im `lifespan` geladen UND freigegeben?
- [ ] CUDA-OOM-Handling mit `try/finally` und `empty_cache()`?
- [ ] Service akzeptiert nur 127.0.0.1, nicht extern?
- [ ] Logs maskieren API-Keys und Datei-Inhalte?
- [ ] Update-Semantik ist Upsert-before-Delete (mit `run_id`)?
- [ ] Embedding-Dimensions-Check im Lifespan aktiv (ColBERT-Mismatch wird erkannt)?
- [ ] `indexed:false`-Übergang räumt alte Chunks weg?
- [ ] Cache-Invalidierung bei Re-Ingest implementiert?
- [ ] Domain-Counter wird inkrementell aktualisiert UND beim Restart neu initialisiert?
- [ ] PDFs werden im Service abgelehnt, klare Fehlermeldung?
- [ ] `python -m titan.search "..."` als CLI-Pfad funktioniert weiterhin (Smoke-Test)?

### 3.16 Definition of Done für Phase 1

- [ ] Service läuft als systemd-user-Daemon, überlebt WSL-Neustart
- [ ] `curl localhost:8765/health` antwortet sauber
- [ ] `curl -X POST localhost:8765/search -H "Content-Type: application/json" -d '{"query":"...","domain":"titan"}'` gibt Chunks zurück
- [ ] Eine Markdown-Note kann via `/ingest/file` indexed werden
- [ ] Re-Ingest derselben Note ersetzt alte Chunks (Upsert-before-Delete bewiesen)
- [ ] `/domains` zeigt korrekte Statistik
- [ ] `/find_related` für eine bekannte Note liefert plausibel ähnliche Notes
- [ ] Integration-Tests grün (`make test`)
- [ ] CI grün
- [ ] Audit-Runde mit Opus durchgeführt, alle Findings ≥ Warnung behoben
- [ ] `docs/ai/HANDOFF.md` aktualisiert
- [ ] `docs/ai/DECISIONS.md` enthält: `run_id`-Payload-Konvention, `indexed:false`-Semantik, VRAM-Mode-Wahl, Cache-Invalidierungs-Strategie

---

## 4. Phase 2 — brain-mcp

**Lokation:** neues Repo `~/projects/brain-mcp/` aus python-template
**Package:** `brain_mcp` (Unterstrich)

### 4.1 Aufgaben-Übersicht

| ID | Task | Aufwand | Modell |
|---|---|---|---|
| B0 | Repo-Setup aus Template | 30 min | Lokal |
| B1 | `titan_client.py` (HTTP-Wrapper) | 1.5 h | Sonnet |
| B2 | MCP-Server-Skeleton (FastMCP) | 1 h | Sonnet |
| B3 | Tool: `query_knowledge` | 1.5 h | Sonnet |
| B4 | Tool: `ingest_note` | 1 h | Sonnet |
| B5 | Tools: `list_domains`, `find_related` | 1.5 h | Sonnet |
| B6 | Watcher mit Debouncing (Test-Hook!) | 3 h | Sonnet |
| B7 | Watcher: Reconnect-Logik bei Titan-Down | 1.5 h | Sonnet |
| B8 | systemd-Services für Watcher und MCP | 1 h | Lokal |
| B9 | Claude Desktop einbinden | 1 h | Lokal |
| B10 | E2E-Test (deterministisch) | 2 h | Sonnet |
| B11 | Audit-Runde + Fixes | nach Bedarf | Opus |

### 4.2 Task B0 — Repo-Setup

```bash
cd ~/projects
gh repo create brain-mcp --template charlie-vincent/python-template --private --clone
cd brain-mcp
./init-project.sh brain-mcp "MCP server and vault watcher that make my Obsidian notes searchable by Claude"
uv add fastmcp watchdog httpx python-frontmatter pydantic-settings
make install
make check
```

**Package-Struktur:**
```
brain-mcp/
├── src/brain_mcp/
│   ├── __init__.py
│   ├── config.py           # Pydantic-Settings: VAULT_ROOT, TITAN_URL
│   ├── titan_client.py     # HTTP-Client
│   ├── mcp_server.py       # FastMCP-Entry-Point
│   └── watcher.py          # Watchdog-Daemon
├── tests/
│   └── brain_mcp/
│       ├── test_titan_client.py
│       ├── test_watcher.py
│       └── test_mcp_tools.py
└── pyproject.toml
```

**`pyproject.toml` Entry-Points:**
```toml
[project.scripts]
brain-mcp = "brain_mcp.mcp_server:main"
brain-watcher = "brain_mcp.watcher:main"
```

### 4.3 Task B1 — TitanClient

**`src/brain_mcp/titan_client.py`:**

```python
class TitanClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", timeout: int = 30):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def health(self) -> HealthResponse: ...
    def search(self, query: str, domain: str | None = None, top_k: int = 10) -> SearchResponse: ...
    def ingest_file(self, file_path: Path, force: bool = False) -> IngestResponse: ...
    def delete_chunks(self, file_path: Path) -> int: ...
    def list_domains(self) -> DomainsResponse: ...
    def find_related(self, file_path: Path, top_k: int = 5) -> FindRelatedResponse: ...
    def close(self) -> None:
        self._client.close()
```

**Pydantic-Schemas:**

Optionen:
1. Aus titan kopieren (Code-Duplikation, aber sauber entkoppelt)
2. Als shared package `titan-schemas` extrahieren (drittes Repo)
3. Aus titan importieren (dependency-Pfeil brain-mcp → titan)

**Empfehlung Variante 1.** Sieben Schemas sind nicht so viel Duplikation, dass eine dritte Repo-Pflege sich lohnt. Wenn die Schemas mal divergieren wollen (z.B. brain-mcp will andere Felder), bist du nicht in eine Cross-Repo-Sync gefangen.

**Retry-Logik:**
```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=4))
def search(...): ...
```

Mit `tenacity` (`uv add tenacity`). Connection-Errors → 3 Versuche mit 1s/2s/4s Backoff. Andere Errors → kein Retry.

### 4.4 Task B2 — MCP-Server-Skeleton

```python
# src/brain_mcp/mcp_server.py
from fastmcp import FastMCP
from brain_mcp.config import settings
from brain_mcp.titan_client import TitanClient

mcp = FastMCP("brain")
client = TitanClient(base_url=settings.titan_url)

# Tools werden in B3–B5 registriert

def main() -> None:
    mcp.run()  # stdio transport

if __name__ == "__main__":
    main()
```

**`config.py`:**
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    titan_url: str = "http://127.0.0.1:8765"
    vault_root: Path = Path("/mnt/f/vault")
    debounce_seconds: int = 30  # Test-Hook!

    class Config:
        env_prefix = "BRAIN_"

settings = Settings()
```

Wichtig: `debounce_seconds` als Setting — Tests setzen das auf 1.

### 4.5 Task B3 — `query_knowledge`

```python
@mcp.tool()
def query_knowledge(
    query: str,
    domain: str | None = None,
    top_k: int = 10,
) -> str:
    """Search my personal knowledge vault.

    Args:
        query: Natural-language question or keywords.
        domain: Optional filter. Use list_domains() first to see available domains.
        top_k: Number of chunks to return (1-30). Default 10.

    Returns:
        Markdown-formatted list of relevant chunks with source paths.
    """
    top_k = min(top_k, 30)  # Schutz vor Token-Explosion
    try:
        result = client.search(query, domain=domain, top_k=top_k)
    except httpx.ConnectError:
        return "Error: Titan service is not reachable. Check `systemctl --user status titan-service`."

    return _format_chunks_as_markdown(result.chunks, result.cache_hit, result.latency_ms)
```

**Return-Format:** Markdown-String, kein JSON. Markdown ist für Claude die natürlichere Form.

```markdown
## Result 1 (score: 0.87, source: notes/learning/rrf.md)
Reciprocal Rank Fusion mit k=60 fusioniert mehrere Rangier-Listen...

## Result 2 (score: 0.82, source: titan/docs/ARCHITECTURE.md)
...

*Cache hit: false, latency: 156ms*
```

**Wichtig:** Tool-Description ist der Hauptkommunikationskanal zu Claude. Sie muss erklären:
- Was das Tool macht
- Wann es zu benutzen ist
- Welche Domains existieren (über `list_domains()` referenzieren)
- Was bei Fehlern passiert

### 4.6 Task B4 — `ingest_note`

```python
@mcp.tool()
def ingest_note(file_path: str, force: bool = False) -> str:
    """Trigger immediate re-indexing of a note (bypassing the watcher's 30s delay).

    Use when:
    - You just edited a note and want it searchable immediately
    - You suspect the index is stale for a specific note
    """
    path = Path(file_path).resolve()
    # Client-side path safety: muss unter VAULT_ROOT liegen
    if not path.is_relative_to(settings.vault_root):
        return f"Error: {file_path} is outside the vault root."

    try:
        result = client.ingest_file(path, force=force)
    except httpx.HTTPError as e:
        return f"Error: {e}"

    if result.skipped_reason:
        return f"Skipped: {result.skipped_reason}. {result.chunks_deleted} old chunks removed."
    return f"Indexed: {result.chunks_created} chunks created, {result.chunks_deleted} replaced. Domain: {result.domain}."
```

**Doppelte Pfad-Sicherheit:** Client checked, Service checked. Sicherer als sich auf eine Seite zu verlassen.

### 4.7 Task B5 — `list_domains` und `find_related`

```python
@mcp.tool()
def list_domains() -> str:
    """List all domains in the vault with chunk counts."""
    try:
        result = client.list_domains()
    except httpx.ConnectError:
        return "Error: Titan service is not reachable."

    lines = [f"- **{d}**: {result.counts[d]} chunks" for d in result.domains]
    return "Available domains:\n" + "\n".join(lines)

@mcp.tool()
def find_related(file_path: str, top_k: int = 5) -> str:
    """Find notes semantically related to a given note."""
    path = Path(file_path).resolve()
    if not path.is_relative_to(settings.vault_root):
        return f"Error: {file_path} is outside the vault root."
    try:
        result = client.find_related(path, top_k=top_k)
    except httpx.HTTPError as e:
        return f"Error: {e}"
    return _format_chunks_as_markdown(result.related, cache_hit=False, latency_ms=result.latency_ms)
```

### 4.8 Task B6 — Watcher mit Test-Hook für Debouncing

**Architektur:**
- `watchdog.Observer` beobachtet `VAULT_ROOT` rekursiv
- Worker-Thread konsumiert Events aus einer `queue.Queue`
- Pro Pfad ein letzter-Event-Timestamp in `dict[Path, float]`
- Worker schläft kurz, prüft welche Pfade ihre Debounce-Frist abgelaufen haben, verarbeitet die

**Test-Hook:**
```python
class VaultWatcher:
    def __init__(
        self,
        vault_root: Path,
        titan_client: TitanClient,
        debounce_seconds: float = 30.0,  # injectierbar!
        poll_interval: float = 1.0,
    ):
        self.debounce_seconds = debounce_seconds
        self.poll_interval = poll_interval
        ...
```

Tests setzen `debounce_seconds=0.1, poll_interval=0.05`. Echter Daemon nutzt 30s/1s.

**Event-Filter:**
- Nur `*.md` triggern. PDFs werden via CLI ingested.
- Ignore: `.obsidian/`, `.trash/`, versteckte Dateien, alles unter `**/.git/`

**Event-Handling:**
```python
def on_modified(self, event):
    if not event.src_path.endswith(".md"):
        return
    path = Path(event.src_path)
    if self._should_ignore(path):
        return
    self.pending[path] = time.monotonic()  # Reset Timer

def on_deleted(self, event):
    # sofort feuern, kein Debouncing
    if event.src_path.endswith(".md"):
        self._delete(Path(event.src_path))

def on_moved(self, event):
    # Delete + Modified
    ...
```

**Worker-Loop:**
```python
def _worker(self):
    while not self._stop.is_set():
        now = time.monotonic()
        ready = [p for p, t in self.pending.items() if now - t >= self.debounce_seconds]
        for path in ready:
            self.pending.pop(path)
            try:
                self.titan_client.ingest_file(path)
            except Exception as e:
                log.error(f"Ingest failed for {path}: {e}")
        self._stop.wait(self.poll_interval)
```

### 4.9 Task B7 — Reconnect-Logik

Wenn Titan-Service down ist, soll der Watcher nicht crashen, sondern warten.

**Strategie:** Bei `httpx.ConnectError` im Ingest-Call:
1. Loggen, kein Crash
2. Pfad zurück in `self.pending` legen, Timestamp aktualisieren
3. Mit Exponential Backoff prüfen, ob Service wieder da ist (`client.health()`)
4. Wenn ja: weiter normal

```python
def _ensure_titan_available(self) -> bool:
    """Returns True if Titan reachable, else False after backoff sequence."""
    for delay in (1, 2, 4, 8, 16):
        try:
            self.titan_client.health()
            return True
        except httpx.ConnectError:
            log.warning(f"Titan unreachable, retry in {delay}s")
            time.sleep(delay)
    return False
```

Wenn nach 5 Versuchen immer noch tot: weiter normal arbeiten, beim nächsten Ingest-Versuch wird's neu probiert. Keinen permanenten „dead"-State setzen.

### 4.10 Task B8 — systemd-Services

**`titan-service.service`** wurde in A11 angelegt.

**`brain-watcher.service`** (`~/.config/systemd/user/brain-watcher.service`):

```ini
[Unit]
Description=Brain Vault Watcher
After=titan-service.service
Wants=titan-service.service       # NICHT Requires=

[Service]
Type=simple
WorkingDirectory=/home/charl/projects/brain-mcp
ExecStart=/home/charl/projects/brain-mcp/.venv/bin/brain-watcher
Restart=on-failure
RestartSec=10
Environment="BRAIN_VAULT_ROOT=/mnt/f/vault"
Environment="BRAIN_TITAN_URL=http://127.0.0.1:8765"
Environment="BRAIN_DEBOUNCE_SECONDS=30"

[Install]
WantedBy=default.target
```

**Wichtig:** `Wants=` statt `Requires=`. Damit überlebt der Watcher einen Titan-Service-Restart, statt mitgekillt zu werden. Die Reconnect-Logik aus B7 fängt das ab.

**Brain-MCP läuft NICHT als Daemon.** MCP-Server ist ein Per-Session-Subprozess, gestartet von Claude Desktop. Kein systemd nötig.

### 4.11 Task B9 — Claude Desktop einbinden

**Config:** `%APPDATA%\Claude\claude_desktop_config.json` (Windows-Pfad, nicht WSL!)

```json
{
  "mcpServers": {
    "brain": {
      "command": "wsl",
      "args": [
        "-d", "Ubuntu",
        "--",
        "/home/charl/projects/brain-mcp/.venv/bin/brain-mcp"
      ],
      "env": {
        "BRAIN_TITAN_URL": "http://127.0.0.1:8765",
        "BRAIN_VAULT_ROOT": "/mnt/f/vault"
      }
    }
  }
}
```

**Test:** Claude Desktop neu starten, in neuer Konversation prüfen ob das `brain`-Tool-Set verfügbar ist. Tools werden mit dem Namespace-Präfix angezeigt: `brain__query_knowledge`, `brain__list_domains` etc.

### 4.12 Task B10 — E2E-Test (deterministisch)

**Datei:** `tests/integration/test_full_pipeline.py`

```python
@pytest.mark.integration
def test_full_pipeline(tmp_vault, titan_service, watcher_with_short_debounce):
    """End-to-end: Note schreiben → Watcher sieht → Titan indexed → Search findet."""

    # Setup-Fixtures liefern:
    # - tmp_vault: temp directory mit korrekter Struktur, als VAULT_ROOT konfiguriert
    # - titan_service: laufender Service auf Test-Collection
    # - watcher_with_short_debounce: VaultWatcher(debounce_seconds=0.1)

    note = tmp_vault / "notes" / "test.md"
    note.write_text(
        "---\ndomain: test\n---\n"
        "# Test Note\n"
        "Unique-Phrase XYZ987654321 für E2E-Test."
    )

    # Warten auf Watcher (deterministisch, nicht time.sleep!)
    _wait_for_indexed(titan_service, note, timeout=5.0)

    response = httpx.post(
        f"{titan_service}/search",
        json={"query": "XYZ987654321", "domain": "test"},
    ).json()

    assert response["chunks"], "no chunks found"
    assert "XYZ987654321" in response["chunks"][0]["text"]

def _wait_for_indexed(service_url: str, path: Path, timeout: float) -> None:
    """Poll until search finds the unique phrase, or timeout."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        # ... search and check
        if found:
            return
        time.sleep(0.1)
    pytest.fail(f"Note not indexed within {timeout}s")
```

**Kein `time.sleep(35)` mehr.** Das war Sonnets Befund — der Test ist jetzt deterministisch über Polling mit kurzem Timeout.

### 4.13 Task B11 — Audit-Runde

**Checkliste:**
- [ ] MCP-Tool-Descriptions präzise, verweisen aufeinander (`list_domains` als Helper für `query_knowledge`)
- [ ] `query_knowledge` clampt `top_k` auf 30
- [ ] Watcher hat Debouncing-Tests die in <2 Sekunden durchlaufen
- [ ] Watcher fängt alle Exceptions in `_worker`, läuft weiter
- [ ] Pfad-Validierung Client-seitig gegen VAULT_ROOT
- [ ] `Wants=` (nicht `Requires=`) im systemd
- [ ] Reconnect-Logik mit Backoff in Watcher
- [ ] `indexed:false`-Übergang räumt Chunks
- [ ] Tests gegen Test-Collection, nicht Produktiv
- [ ] Keine Secrets in Logs
- [ ] Tool-Output ist immer Markdown-String (kein JSON-Leak)
- [ ] E2E-Test deterministisch (Polling, kein `time.sleep` für Synchronisation)

### 4.14 Definition of Done für Phase 2

- [ ] `brain-mcp` und `brain-watcher` als systemd-user-Services aktiv (nur Watcher als Daemon)
- [ ] Claude Desktop erkennt `brain`-MCP, listet 4 Tools
- [ ] Note schreiben → 30s warten → in Claude abfragen funktioniert
- [ ] Note löschen → Chunks verschwinden aus Index
- [ ] `indexed: false`-Setzen räumt alte Chunks weg
- [ ] Watcher überlebt Titan-Service-Restart (Reconnect-Logik greift)
- [ ] Integration-Tests + E2E-Test grün, alle deterministisch
- [ ] CI grün
- [ ] Audit-Runde durchgeführt
- [ ] `docs/ai/HANDOFF.md`, `docs/ai/DECISIONS.md` aktualisiert

---

## 5. Reihenfolge & Pacing

| Woche | Fokus | Tasks |
|---|---|---|
| 1 | Phase 0b Cleanup, dann A0 + A1 | Cleanup, VRAM-Probe, FastAPI-Skeleton |
| 2 | A2–A5 | Schemas, Health, Search-Refactor, Markdown-Reader |
| 3 | A6–A10 | Ingest, Domains, FindRelated, Cache-Invalidierung |
| 4 | A11–A13 | systemd, Tests, Audit. **Phase 1 abgeschlossen.** |
| 5 | B0–B5 | Repo, Client, MCP-Tools |
| 6 | B6–B8 | Watcher, systemd |
| 7 | B9–B11 | Claude-Desktop-Integration, E2E, Audit |

Realistisch: 7 Wochen mit Abend-Pacing. Sprint-Optimismus: 4–5 Wochen.

**Modell-Budget-Strategie:**
- Opus: dieser Plan, Audit-Runden, Architektur-Entscheidungen die unterwegs auftauchen
- Sonnet: alle Implementierungs-Tasks
- Lokales Modell: Boilerplate (systemd-Files, Pydantic-Schemas aus dieser Doku abtippen, Test-Skeletons)
- Gemini: nicht in diesem Plan — Codebase bleibt überschaubar

---

## 6. Risiken & Stolpersteine

| Risiko | Wahrscheinlichkeit | Mitigation |
|---|---|---|
| BGE-M3 + Phi-4 passen nicht parallel in 16 GB | Mittel | A0 misst das empirisch, VRAM_MODE-Switch als Fallback |
| FlagEmbedding-Upgrade ändert ColBERT-Dim wieder | Niedrig | Embedding-Dim-Check im Lifespan, Fail-Fast |
| WSL2 systemd-user läuft instabil | Niedrig | Notfall: `tmux`-Session beim WSL-Start |
| Junction Points werden von Obsidian falsch erkannt | Niedrig | Test mit einer Junction vor Bulk-Setup |
| Watcher übersieht rapid-fire Saves | Mittel | Debouncing 30s großzügig, Tests verifizieren mit 10× save in 5s |
| Frontmatter-Parsing failed bei Tippfehlern | Hoch | Klare Error-Messages, Watcher loggt statt zu crashen |
| Re-Index zerstört Index zwischenzeitlich | Hoch ohne Mitigation | Upsert-before-Delete strikt einhalten (A6) |
| Claude Desktop findet WSL-Binary nicht | Mittel | Absolute Pfade in JSON-Config, manuell testen |
| `find_related` ist zu langsam | Niedrig | Erstmal nicht optimieren, später Cache wenn nötig |
| Cache-Invalidierung zu aggressiv (Hit-Rate kollabiert) | Mittel | Monitoren, ggf. später feinere Granularität |
| Markdown mit komplexer Frontmatter (nested YAML) bricht | Niedrig | python-frontmatter handhabt das, aber Tests dafür schreiben |
| Race-Condition: zwei parallele Ingests derselben Datei | Niedrig | `run_id`-Pattern schützt davor (letzter gewinnt sauber) |

---

## 7. Offene Fragen (zu klären während der Implementation)

1. **Tagesnotizen automatisch indizieren?** Empfehlung: Default `indexed: false` in Daily-Note-Templates, gezielt umstellen.
2. **Voll-Rescan beim Watcher-Start?** Optional via `brain-watcher --full-rescan`. Default: nein.
3. **Qdrant-Backup-Strategie?** Recovery-Pfad ist `--full-rescan`. In `DECISIONS.md` festhalten.
4. **Decompose im MCP-Pfad nötig?** Claude (Opus/Sonnet) kann das selbst. Default `use_decompose=False` im MCP-Client. Auf API-Ebene weiter `True` als Default für CLI-Aufrufer.
5. **`/find_related` Strategie:** v1 nutzt ersten Chunk. v2 könnte RRF über mehrere Chunks der Quell-Note. In `IDEAS.md` parken.
6. **Multi-User?** Nicht in v1 vorgesehen. Wenn jemals: Domain-Filter trennt sauber zwischen Personen-Wissen.

---

## 8. Was nach diesem Plan kommt

Mit Phase 1 + 2 produktiv eröffnet sich:

- **Memory-Layer-Tools:** `update_handoff(project, content)`, `log_decision(project, ...)` — deterministische Schreib-Tools in die `docs/ai/`-Struktur eines Projekts. Stufe 2 aus dem ursprünglichen Ideen-Brief. Jetzt machbar.
- **Web-UI über Titan-Service:** Neuer Sub-Package `src/titan/web/` mit FastAPI-Routes für HTML-Suchoberfläche. Lerneffekt: Server-Side-Rendering oder htmx.
- **Multi-Source:** Watcher erweitert auf zusätzliche Quellen (z.B. ein zweiter Vault, oder synchronisierte Notion-Exports).
- **Lokaler Inferenz-Bypass:** Statt Claude über MCP, ein lokales Modell (Qwen 3.6-35B-A3B auf der 4070 Ti Super) kann Titan ebenso über HTTP nutzen. Aider/Cline o.ä. mit eigener MCP-Implementation.

---

## 9. Anhang: Beispiel-Frontmatter

**Lernnotiz (indexed):**
```markdown
---
domain: lernen
tags: [python, mcp]
created: 2026-05-12
indexed: true
---
```

**Projekt-ADR (indexed unter projekt-domain):**
```markdown
---
domain: titan
tags: [adr, architecture]
created: 2026-05-12
indexed: true
adr_status: accepted
---
```

**Tagesnotiz (default nicht indexed):**
```markdown
---
domain: daily
indexed: false
---
```

**Privater Eintrag:**
```markdown
---
indexed: false
---
```

---

*Ende des Plans. Ablegen unter `~/projects/titan/docs/ai/plans/2026-05-12_brain-v2.md`. Start mit Phase 0b Cleanup, dann A0 (VRAM-Probe). Erste DECISIONS-Einträge nach A0 schreiben.*
