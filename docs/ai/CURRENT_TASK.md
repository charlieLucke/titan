# Current Task

> Keep this short. One screen max. Update as you progress.

## Goal

System produktiv. Laufender Ausbau rund um titan + brain-mcp.

## Status

- [x] Phase 1 (titan Service-Layer) + Phase 2 (brain-mcp) abgeschlossen, auditiert
- [x] Claude-Anbindung live (brain-mcp HTTP + OAuth + Tailscale Funnel)
- [x] 2026-05-17: `GET /notes` ergänzt (für brain-mcp `list_notes`)
- [x] 2026-05-17: Integrationssuite repariert (Versions-Drift, 19/20 grün)

## Offen

- [ ] Flaky grpc-Fehler beim Teardown des letzten Integrationstests
- [ ] optional: gecachter Notes-Counter statt Voll-Scroll bei `/notes`

## Notes

- Dienst-Reihenfolge: Docker Desktop → Qdrant → titan-service → brain-mcp/brain-watcher
- Integrationstests nur bei gestopptem `titan-service` (BGE-M3-GPU-Lock)
- Start/Stopp: Desktop-Skript `RAG-System.bat` oder das neue `brain-dashboard` (Port 9200)
