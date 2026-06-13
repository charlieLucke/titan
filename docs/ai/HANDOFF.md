# Handoff – 2026-05-17
Model: Claude Opus 4.7

## Done in this session

Added `GET /notes` — an endpoint for the list of all indexed notes (grouped by file,
with domain + chunk count). Serves the new `list_notes` tool in brain-mcp.

**Code changes:**
- `src/titan/service/schemas.py` — new schemas `NoteInfo`, `NotesResponse`
- `src/titan/service/routes.py` — `GET /notes` (full scroll + aggregation)
- `tests/integration/test_service.py` — `/notes` integration tests + repair of the
  until-then fully red suite (details: DECISIONS.md 2026-05-17)

**Quality status:** `ruff` green, `mypy` green, unit tests 12 passed, integration suite
19/20 green (1 flaky teardown).

## Context

Part of "Stage 2 — vault-admin": brain-mcp gains the tools `list_notes` and
`delete_note`. titan only provides the new `/notes` endpoint for this; `delete_note`
uses the existing `DELETE /chunks`. See brain-mcp `docs/ai/` (2026-05-17).

## Open / Next steps

- Flaky grpc error on the collection teardown of the last integration test.
- Integration tests only run with the titan-service stopped (BGE-M3 GPU lock).

---

# Handoff – 2026-05-16

## Done in this session

**Milestone reached!**
Phase 1 (titan service layer) and Phase 2 (brain-mcp) are both fully completed and
audited. The system is now **in production**.
The first full manual end-to-end test (E2E) was successful with a strong score of **5.71**.

## In progress

Nothing open — all phases of the initial development (1 & 2) are completed.

## Next concrete step

Use the system day-to-day.
Monitor cache behavior, latencies and the relevance of the retrieved chunks.

## Open questions / decisions needed

- None.

## Notes / gotchas discovered

- **Important setup detail (documented in DECISIONS.md):** the system requires WSL2
  mirrored networking, `QDRANT_HOST=localhost` and a running Docker Desktop. On Qdrant
  connection problems, this is the first thing to check.
- **Most common Qdrant failure source:** if Docker Desktop isn't running, the Qdrant
  container is gone. `Test-NetConnection` on Windows can still report "port open"
  (phantom socket) — the gRPC connect then runs into "Deadline Exceeded". Check Docker
  Desktop / the container first, then the network.

---

## Addendum (2026-05-16): Claude Desktop integration

The Claude Desktop integration is set up — it runs **not** through titan directly, but
through the brain-mcp server.

- The installed Claude Desktop build has no Developer Mode; local stdio MCP servers via
  `claude_desktop_config.json` are not supported (the app removes the `mcpServers` block
  again).
- Status: **implemented.** brain-mcp runs as an HTTP daemon (`127.0.0.1:9100`) behind
  `tailscale funnel` (public), secured by a GitHub OAuth proxy with a login allowlist.
  Wired into Claude as the custom connector `https://<your-tailnet-host>.ts.net/mcp`;
  verified end-to-end (`query_knowledge` returns vault hits). Details in the brain-mcp
  repo: `docs/ai/DECISIONS.md` and `docs/ai/HANDOFF.md` (both 2026-05-16).
- **Nothing was changed** in titan itself for this. titan-service stays on
  `127.0.0.1:8765`; brain-mcp calls it via `BRAIN_TITAN_URL`.
- Service order for a working system:
  Docker Desktop → Qdrant container → `titan-service` → `brain-mcp` / `brain-watcher`.
