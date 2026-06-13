# Aktuelle Aufgabe

> Kurz halten. Maximal ein Bildschirm. Mit dem Fortschritt aktualisieren.

## Ziel

System in Produktion. Laufender Ausbau rund um titan + brain-mcp.

## Status

- [x] Phase 1 (titan-Service-Layer) + Phase 2 (brain-mcp) abgeschlossen, auditiert
- [x] Claude-Integration live (brain-mcp HTTP + OAuth + Tailscale Funnel)
- [x] 2026-05-17: `GET /notes` ergänzt (für brain-mcp `list_notes`)
- [x] 2026-05-17: Integrations-Suite repariert (Versions-Drift, 19/20 grün)
- [x] 2026-06-02: Stage 1 — `content_hash` beim Ingest + `/notes` + `/domains/{domain}/notes`
      Workspace-Plan: `docs/ai/plans/2026-06-02_vault-index-startup-reconcile.md`
      Committet: `149a47c` (titan feat) + `b208bd0` (workspace docs)
      Quality-Gate: 40/40 Tests grün (inkl. 2 neue Integrationstests); `./workspace.sh check` grün.

## Offen

- [ ] Flaky grpc-Fehler beim Teardown des letzten Integrationstests
- [ ] optional: ein gecachter Notes-Zähler statt eines vollständigen Scrolls für `/notes`

## Notizen

- Service-Reihenfolge: Docker Desktop → Qdrant → titan-service → brain-mcp/brain-watcher
- Integrationstests nur bei gestopptem `titan-service` (BGE-M3 GPU-Lock)
- Start/Stopp: Desktop-Skript `RAG-System.bat` oder das neue `brain-dashboard` (Port 9200)
