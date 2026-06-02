# Current Task

> Keep this short. One screen max. Update as you progress.

## Goal

System in production. Ongoing build-out around titan + brain-mcp.

## Status

- [x] Phase 1 (titan service layer) + Phase 2 (brain-mcp) completed, audited
- [x] Claude integration live (brain-mcp HTTP + OAuth + Tailscale Funnel)
- [x] 2026-05-17: added `GET /notes` (for brain-mcp `list_notes`)
- [x] 2026-05-17: repaired the integration suite (version drift, 19/20 green)
- [/] 2026-06-02: Stage 1 — `content_hash` on ingest + `/notes` + `/domains/{domain}/notes`
      Workspace plan: `docs/ai/plans/2026-06-02_vault-index-startup-reconcile.md`
      Implementation: Sonnet. Status: make check pending.

## Open

- [ ] Flaky grpc error on the teardown of the last integration test
- [ ] optional: a cached notes counter instead of a full scroll for `/notes`

## Notes

- Service order: Docker Desktop → Qdrant → titan-service → brain-mcp/brain-watcher
- Integration tests only with `titan-service` stopped (BGE-M3 GPU lock)
- Start/stop: desktop script `RAG-System.bat` or the new `brain-dashboard` (port 9200)
