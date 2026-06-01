# Ideas

> Out-of-scope ideas captured during work, to revisit later.
> Nothing here is committed. This is a parking lot.

## Format
- [ ] **YYYY-MM-DD:** Idea description. Why it matters. Rough effort estimate.

---

## Pending

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
