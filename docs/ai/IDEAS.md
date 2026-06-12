# Ideas

> Out-of-scope ideas captured during work, to revisit later.
> Nothing here is committed. This is a parking lot.

## Format
- [ ] **YYYY-MM-DD:** Idea description. Why it matters. Rough effort estimate.

---

## Pending

- [ ] **2026-06-12: /notes-Aggregation auf Qdrant-Facets oder Registry umstellen (Review P2.4).**
      `QdrantRepository.aggregate_notes()` scrollt die gesamte Collection und aggregiert
      in Python — O(N) pro Aufruf. Bei heutiger Vault-Größe unkritisch; ab einigen
      zehntausend Chunks auf die Qdrant-Facet-API umstellen oder eine kleine
      Notes-Registry (SQLite) pflegen — Letzteres löst auch den domain_counts-Drift,
      wenn CLI-Ingests am Service vorbei laufen. Referenz:
      `~/projects/rag-workspace/docs/ai/plans/2026-06-12_architecture-review.md` (P2.4).
      *Effort: Medium. Trigger: Vault-Wachstum, nicht Kalender.*

- [ ] **2026-06-12: Property-based Tests für _split_text via hypothesis (Review P3.2).**
      `tests/titan/test_chunking.py` deckt die Overlap-Invarianten mit festen Beispielen
      ab; hypothesis würde sie generativ prüfen ("Sub-Chunks ohne Overlap ergeben den
      Originaltext", "kein Sub-Chunk > MAX_SUPER_CHUNK_CHARS") und Randfälle an
      Wortgrenzen/Separator-Fallbacks finden. Neue dev-Dependency `hypothesis`.
      Referenz: Plan P3.2. *Effort: Low.*

- [ ] **2026-06-12: Request-ID-Korrelation titan ↔ brain-mcp (Review P3.3).**
      X-Request-ID-Middleware in titan + Durchreichen im TitanClient, damit sich ein
      MCP-Call durch beide Journals verfolgen lässt. Erst sinnvoll, wenn Debugging über
      Service-Grenzen hinweg real Zeit kostet. Referenz: Plan P3.3. *Effort: Medium.*

- [x] **2026-06-06: Content-hash skip — short-circuit unchanged re-ingests.**
      ✅ Umgesetzt 2026-06-12 (Commit f0602ce, Review-Session): `skipped_reason:
      "unchanged"`, `force=true` umgeht den Skip, `indexed: false` bleibt geehrt.
      `ingest_file_endpoint` re-embeds the full file on every call, even when the raw
      bytes are identical to what's already indexed. `content_hash` (sha256 of the raw
      file) is already computed and stored in the payload but only used for reconcile —
      not to skip work. Add an early-return at the top of the ingest path: cheaply read
      the stored hash for that `source_path` (one `scroll`, `with_payload=["content_hash"]`,
      `limit=1`), compare against the freshly computed hash, and return early (chunks
      unchanged) if they match. Why it matters: the watcher re-fires on touch/metadata
      events (Syncthing rename-delivery, editor saves that don't change content), and
      each spurious trigger currently costs a full Docling→chunk→GPU-embed pass under
      the GPU lock. Bonus: this finally gives the `force` flag a real meaning
      (`force=True` bypasses the skip). Must still honour `indexed: false`. Effort: Low.

- [ ] **2026-06-06: Near-duplicate suppression at retrieval time.** RRF already
      dedups *exact* same-chunk hits across sub-queries (keyed on point ID), but there's
      no suppression of *near*-duplicates — two different IDs whose text is almost
      identical can both land in the top-k. The one real source of this in the current
      pipeline is `_split_text`: sections over `MAX_SUPER_CHUNK_CHARS` (24k) are split
      into sub-chunks with `OVERLAP_CHARS` (4k) of overlap, so adjacent sub-chunks share
      text and can both rank when a query hits the overlap region. Cheapest fix first (do
      NOT jump straight to MMR): a post-filter on the final result set that collapses
      adjacent chunks of the same `source` with consecutive `chunk_id`, keeping the
      higher-scored one (or merging their text). Only escalate to real diversity
      reranking — MMR (maximal marginal relevance) or a cosine-similarity threshold on
      the retrieved set — if the cheap post-filter proves insufficient. Why not now: at
      ~14 notes with header-based chunking, chunks are mostly distinct sections and
      near-dups are rare; this is complexity for a problem that barely exists yet.
      Revisit only if duplicate-ish hits actually show up in results (most likely from
      large, overlap-split documents). Effort: Low (post-filter) → Medium (MMR).

- [ ] **2026-05-31: Wikilink-aware retrieval (link graph).** Parse `[[wikilinks]]`
      from notes and use the vault's manual link structure to improve retrieval.
      Manual links are high-quality human signal ("these belong together") that pure
      vector search misses, and they enable structural/multi-hop questions
      ("what depends on X?") and Map-of-Content hub notes. Staged, lowest-risk first:
      1. **Cheap/now:** at ingest, store outbound wikilinks in the Qdrant payload
         (`links: [...]`). Near-zero cost, future-proofs the data, no retrieval change.
      2. **Later, behind a flag:** a `find_linked` tool (a note's neighbours) and an
         optional, capped graph-expansion step in `search` (pull in linked neighbours
         after the vector hit, carefully weighted so `top_k` isn't diluted).
      3. **Full GraphRAG-style expansion** only once the vault grows to hundreds of
         notes and MOC/hub-note habits exist.
      Don't build now: at the current ~14 notes semantic search + `find_related`
      already cover it, link resolution is fiddly (aliases, `#headings`, renames),
      and it cuts against titan's lean single-user ethos. Effort: 1 (step 1) →
      Medium/High (step 3).

- [ ] **2026-05-31: Cross-encoder reranker as the final retrieval stage.** After RRF,
      re-score the top-N candidates (e.g. 30) with a cross-encoder (e.g.
      BGE-reranker-v2) and return the re-sorted `top_k`. Typically the single biggest
      quality lever in a RAG pipeline — it judges query↔chunk relevance directly
      instead of via independent embeddings. Runs on the workstation GPU. Cost: one
      more model in VRAM + added latency per query (mind the GPU lock / VRAM budget).
      Effort: Medium.

- [ ] **2026-05-31: Contextual Retrieval (Epic 5A v2.0).** Have Phi-4 generate a 1–2
      sentence context blurb per chunk and prepend it before embedding (Anthropic's
      "Contextual Retrieval"). Recovers context lost at chunk boundaries → better
      recall, especially for short/ambiguous chunks. Phi-4 is already in the stack.
      The v1.0 attempt was dropped for poor cost/benefit, so the redo must be measured
      against the eval harness (`ab_eval`) before keeping it. Cost: slower, more
      expensive ingest. Effort: Medium–High.

- [ ] **2026-05-31: Answer provenance / inline citations.** Make `generate` attribute
      claims to the chunks/`source_path` that back them (e.g. inline `[n]` markers + a
      sources list). Turns answers into verifiable, traceable output — high value for a
      personal knowledge RAG. Effort: Low–Medium.

- [ ] **2026-05-31: Qdrant snapshot backup/restore CLI.** A one-command snapshot (and
      restore) of the collection. The index is derived but expensive to rebuild (full
      re-embed); a snapshot is cheap insurance against corruption, a bad migration, or
      a lost container volume. Effort: Low.

- [x] **2026-05-31: `/stats` endpoint + lightweight metrics.** ✅ Umgesetzt
      (`GET /stats`): uptime, total chunks + domain count, cache hit rate, recent
      search-latency p50/p95/max, cache entry count, last-ingest age. In-memory
      counters in `ServiceState`, pure assembly in `service/stats.py`.

- [ ] **2026-05-31: Multi-collection / namespaces (the lean multi-user path).** A
      `collection`/`namespace` request param so distinct contexts (e.g. personal vs.
      work, or per project) get isolated search while sharing the one BGE-M3 model.
      Gives ~80% of the practical benefit of multi-user (data separation) at ~10% of
      the cost — no per-user auth, TLS, or GPU-concurrency rework, and it keeps the
      single-user `127.0.0.1` design intact. True multi-tenant (multiple people,
      isolated + authenticated) stays out of scope: front titan with a gateway then,
      rather than baking tenancy into it. Effort: Medium.
