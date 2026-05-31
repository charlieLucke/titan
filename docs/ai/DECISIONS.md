# Decisions Log

> Architecture Decision Records. Append-only. One entry per significant decision.
> This prevents re-litigating the same questions in every new AI session.

## Format

```
## YYYY-MM-DD: Short title
**Decision:** What we decided
**Reasoning:** Why
**Alternatives considered:** What we rejected and why
**Consequences:** What this implies going forward
```

---

## Initial decisions (template defaults)

## 2026-XX-XX: Use uv as package manager
**Decision:** uv (over pip+venv, poetry, pdm).
**Reasoning:** 10-100x faster than pip; unified tool replacing pip, pip-tools, virtualenv, pyenv; lockfile by default; backed by Astral (same team as ruff).
**Alternatives considered:** Poetry (slower, more config overhead, separate from venv tooling). pip+venv (no lockfile by default, manual workflow).
**Consequences:** All dependency operations go through `uv add` / `uv remove` / `uv sync`. Never edit pyproject.toml dependencies manually.

## 2026-XX-XX: Use ruff for lint and format
**Decision:** ruff replaces black + flake8 + isort + pyupgrade.
**Reasoning:** Single tool, much faster, consistent config, actively maintained.
**Consequences:** Don't add black, flake8, or isort as separate tools.

## 2026-XX-XX: Mypy strict mode
**Decision:** Mypy in strict mode from day one.
**Reasoning:** Strictness is much easier to enforce from the start than retrofit. Catches whole categories of bugs at write-time.
**Consequences:** Every function needs full type hints. `# type: ignore` requires an inline comment explaining why.

---

## 2026-05-12: Big-bang migration from RAG_System, Epic 5A v1.0 not ported
**Decision:** Code from `~/projects/RAG_System/execution/` is ported 1:1 into `src/titan/`.
Epic 5A (Contextual Retrieval via Phi-4 summaries) is deliberately removed during the port.
**Reasoning:** Epic 5A v1.0 had too high latency and too little retrieval gain for the effort.
The reimplementation (Epic 5A v2.0) should start cleanly on the new code base, not on old code.
**Alternatives considered:** A selective port with a feature flag for 5A — rejected, since a dead
feature in the code causes confusion and complicates mypy hygiene.
**Consequences:** `~/projects/RAG_System/` stays unchanged as a reference. Whoever needs 5A code
reads it there. All `--contextual`, `--clear-summary-cache`, `--summary-cache-stats` flags are dropped.

## 2026-05-12: Flat package structure under src/titan/
**Decision:** Flat structure: `src/titan/utils.py`, `src/titan/ingest.py` etc. instead of
`src/titan/rag_system/execution/utils.py` (the old layout).
**Reasoning:** The old `rag_system/execution/` prefix was a workaround without a package namespace.
With `titan` as the package name, the extra nesting is pure overhead.
**Consequences:** Import paths read `from titan.utils import ...` — short and unambiguous.

## 2026-05-12: mypy ignore_missing_imports for ML libraries
**Decision:** `ignore_missing_imports = true` in `[[tool.mypy.overrides]]` for `flagembedding`,
`docling`, and possibly `torch` (depending on stub availability).
**Reasoning:** These libraries have no type stubs. mypy strict would otherwise fail on every import.
That's not a real type problem, just missing third-party stubs.
**Consequences:** These modules are exempt from type checking — an acceptable compromise as long as
no community stubs are available.

## 2026-05-12: ColBERT vector dimension 1024 (FlagEmbedding ≥ 1.3)
**Decision:** The Qdrant collection is created with `colbert.size=1024`.
**Reasoning:** FlagEmbedding changed the ColBERT output dimension from 128 to 1024 as of version 1.3.
The collection must match the installed library version exactly — a wrong value leads to upsert
errors or unusable retrieval results without an obvious error message.
**Alternatives considered:** 128 (previous version) — rejected, since it is effectively wrong from FE 1.3 on.
**Consequences:** `flagembedding>=1.3` is an implicit minimum requirement (pyproject.toml declares
`>=1.4.0`). On a FlagEmbedding upgrade: check the release notes for ColBERT dim changes. On a
downgrade below 1.3: recreate the collection with `init_col.py --recreate` using `colbert.size=128`.

## 2026-05-12: GPU_LOCK_PATH default changed to /tmp/bge_m3.lock
**Decision:** The GPU lock file lives at `/tmp/bge_m3.lock` (was `/tmp/rag_gpu.lock` in the old
RAG_System repo).
**Reasoning:** The new lock path reflects the new package name and avoids collisions with still-running
processes from the old RAG_System setup.
**Consequences:** Whoever migrates from the old RAG_System: run `rm -f /tmp/rag_gpu.lock` once so a
stale lock file doesn't block a process start. The new path can be overridden via `.env`
(`GPU_LOCK_PATH=/tmp/bge_m3.lock`).

---

## 2026-05-12: run_id payload convention for upsert-before-delete
**Decision:** Every ingest run sets a UUID (`run_id`) in the chunk payload.
On re-ingest: insert new chunks with a new `run_id`, then delete old chunks with a
different `run_id` for the same `source_path`.
**Reasoning:** Prevents a downtime window: as long as step 2 (upsert) is running, the old chunks are
still searchable. If step 2 crashes, the old state is preserved. Duplicate chunks (old + new run) are
harmless — RRF weights them equally and the next run cleans up.
**Alternatives considered:** Delete-before-insert (leads to gaps during re-indexing).
**Consequences:** `run_id` is a mandatory payload field for all ingest calls via the service.
CLI ingest (`python -m titan.ingest`) doesn't use this convention yet — that's acceptable since the
CLI path is typically used for the first ingest.

## 2026-05-12: indexed:false semantics — the service decides, the client trusts
**Decision:** The frontmatter field `indexed: false` is evaluated exclusively by the service.
The watcher (brain-mcp Phase 2) sends the path without evaluating frontmatter.
**Reasoning:** A single place that knows the semantics → no sync problem when the semantics change.
**Consequences:** `POST /ingest/file` with `indexed:false`: deletes existing chunks, returns
`skipped_reason: "indexed:false"`, creates no new chunks.

## 2026-05-12: Aggressive cache invalidation (domain-granular)
**Decision:** On re-ingest of a note, ALL cache entries of its domain are cleared.
**Reasoning:** The simplest correct implementation. Avoids a cache hit for the "old" state without
chunk→cache dependency tracking.
**Alternatives considered:** Fine-grained invalidation only for entries that contained the changed
note — needs cache→chunk tracking that Epic 5B doesn't implement.
**Consequences:** With frequent edits in one domain the cache hit rate can drop. If that becomes
measurably problematic: add finer tracking.

## 2026-05-13: Cache invalidation in titan.search, not in routes

**Decision:** `invalidate_domain_cache(qdrant_client, domain)` lives in `titan.search`, not in
`titan.service.routes`.
**Reasoning:** Audit T-MED-2: the function only needs `qdrant_client` and the global cache constants
(`CACHE_COLLECTION_NAME`, `CACHE_ENABLED`) — both accessible from `search.py`. In `routes` it had
ended up there because of `state.qdrant_client`, but that's not a valid argument: `search()` also takes
the client as a parameter. This makes the function testable without FastAPI.
**Consequences:** Routes import `invalidate_domain_cache` from `titan.search`.

## 2026-05-13: The service binds only to 127.0.0.1 (no remote access)

**Decision:** uvicorn runs on `host="127.0.0.1"`, with no external configurability.
**Reasoning:** The service is designed for single-user operation on the local machine. Exposing it on
public IPs would require auth, rate limiting and TLS.
Audit assessment (security table): ✅ acceptable hardening level for a single-user setup.
**Consequences:** Whoever needs multi-user or remote: put a reverse proxy with auth in front, then
review `VAULT_ROOT` and all path checks.

## 2026-05-12: source_path + source as payload aliases
**Decision:** New ingest uploads via the service set both `source_path` and `source` in the Qdrant
payload. Old CLI ingests have only `source`.
**Reasoning:** Backward compatibility: existing chunks in the index stay usable. The service schema
(`Chunk.source_path`) is the new standard.
**Consequences:** Search results return both keys. The routes code uses `source_path`; if empty, the
note was ingested via the old CLI path.

## 2026-05-16: Setup assumption — WSL2 mirrored networking & Docker Desktop
**Decision:** The setup assumes that Docker Desktop (for containers) is running, WSL2 mirrored
networking is active, and `QDRANT_HOST=localhost` is configured.
**Reasoning:** Undocumented network setups inevitably lead to lengthy debugging of the Qdrant
connection over time. Since Titan accesses the Qdrant container via `localhost`, mirrored networking
must be enabled in WSL2 for the port mapping to work.
**Consequences:** On a `ConnectError` to Qdrant always check first: is Docker Desktop running? Is
mirrored networking active in `.wslconfig`?

## 2026-05-17: GET /notes — list all indexed notes

**Decision:** A new endpoint `GET /notes` lists all indexed notes, grouped by `source_path`, with
domain and chunk count. It scrolls the entire collection (pagination of 256) and aggregates in-memory.
**Reasoning:** brain-mcp needs an overview of all indexed files for its new `list_notes` tool.
`GET /domains` only returns domain counts, not individual notes. A full scroll is uncritical for a
personal vault (a few hundred/thousand chunks).
**Consequences:** New schemas `NoteInfo` / `NotesResponse`. For very large collections a cached counter
(like `domain_counts`) would be more efficient — add it if needed.

## 2026-05-17: Integration tests repaired (version drift + test isolation)

**Decision:** `tests/integration/test_service.py` brought up to the current state.
**Reasoning:** The integration suite was completely red and no longer runnable — four overlapping defects:
1. `VectorsConfig(root=...)` — in qdrant-client 1.18 `VectorsConfig` is a `typing.Union` alias, not
   instantiable. Fix: pass `vectors_config` directly as a dict (like the production code in
   `init_col.py`).
2. Qdrant now requires an API key — the `qdrant_client` fixture didn't pass one. Fix: `load_dotenv()`
   + `api_key=os.getenv("QDRANT_API_KEY")`.
3. `TestClient(app)` as a context manager ran the `lifespan`, which reloads BGE-M3 and Qdrant and
   overwrote the pre-injected test state. Fix: without `with`.
4. `test_health_degraded_without_qdrant` mutated the `state` singleton without restoring it — all
   subsequent tests saw `None` (503). Fix: save/restore.
**Consequences:** The suite runs again (19/20 green). An occasional grpc error on the collection
teardown of the last test remains — separate teardown robustness, open. Integration tests must run
with `titan-service` stopped (BGE-M3 GPU lock).
