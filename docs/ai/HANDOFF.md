# Handoff – 2026-05-16

## Done in this session

**Meilenstein erreicht!**
Phase 1 (titan Service-Layer) und Phase 2 (brain-mcp) sind beide vollständig abgeschlossen und auditiert. Das System ist nun **produktiv**.
Der erste vollständige manuelle End-to-End Test (E2E) war erfolgreich mit einem starken Score von **5.71**.

## In progress

Nichts offen — alle Phasen der Initialentwicklung (1 & 2) sind abgeschlossen.

## Next concrete step

Nutzung des Systems im Alltag.
Überwachung von Cache-Verhalten, Latenzen und Relevanz der abgerufenen Chunks.

## Open questions / decisions needed

- Keine.

## Notes / gotchas discovered

- **Wichtiges Setup-Detail (dokumentiert in DECISIONS.md):** Das System erfordert WSL2 Mirrored Networking, `QDRANT_HOST=localhost` und einen laufenden Docker Desktop. Bei Verbindungsproblemen zu Qdrant ist dies die erste zu prüfende Fehlerquelle.
- **Häufigste Qdrant-Fehlerquelle:** Wenn Docker Desktop nicht läuft, ist der Qdrant-Container weg. `Test-NetConnection` unter Windows kann dann trotzdem „Port offen" melden (Phantom-Socket) — der gRPC-Connect läuft anschließend in „Deadline Exceeded". Erst Docker Desktop / Container prüfen, dann das Netzwerk.

---

## Nachtrag (2026-05-16): Claude-Desktop-Anbindung

Die Anbindung an Claude Desktop ist eingerichtet — sie läuft **nicht** über titan direkt,
sondern über den brain-mcp-Server.

- Der installierte Claude-Desktop-Build hat keinen Developer Mode; lokale stdio-MCP-Server
  über `claude_desktop_config.json` werden nicht unterstützt (App löscht den
  `mcpServers`-Block wieder).
- Stand: brain-mcp läuft jetzt zusätzlich als HTTP-Daemon (`127.0.0.1:9100`). Die
  Anbindung an Claude über einen *Custom Connector* ist aber noch **offen**: Custom
  Connectors verbindet Anthropic serverseitig aus der Cloud, der MCP-Endpoint muss also
  öffentlich aus dem Internet erreichbar sein. Geplant ist `tailscale funnel` + eine
  Auth-Schicht — bewusst vertagt. Details im brain-mcp-Repo: `docs/ai/DECISIONS.md` und
  `docs/ai/HANDOFF.md` (jeweils 2026-05-16).
- An titan selbst wurde dafür **nichts geändert**. titan-service bleibt auf
  `127.0.0.1:8765`; brain-mcp ruft es über `BRAIN_TITAN_URL` auf.
- Dienst-Reihenfolge für ein funktionierendes System:
  Docker Desktop → Qdrant-Container → `titan-service` → `brain-mcp` / `brain-watcher`.
