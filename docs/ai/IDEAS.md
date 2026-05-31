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
