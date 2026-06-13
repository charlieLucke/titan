# Übergabe – 2026-05-17
Modell: Claude Opus 4.7

## In dieser Sitzung erledigt

`GET /notes` ergänzt — ein Endpunkt für die Liste aller indexierten Notizen (gruppiert nach Datei,
mit Domain + Chunk-Anzahl). Bedient das neue `list_notes`-Tool in brain-mcp.

**Code-Änderungen:**
- `src/titan/service/schemas.py` — neue Schemas `NoteInfo`, `NotesResponse`
- `src/titan/service/routes.py` — `GET /notes` (vollständiger Scroll + Aggregation)
- `tests/integration/test_service.py` — `/notes`-Integrationstests + Reparatur der
  bis dahin komplett roten Suite (Details: DECISIONS.md 2026-05-17)

**Quality-Status:** `ruff` grün, `mypy` grün, Unit-Tests 12 passed, Integrations-Suite
19/20 grün (1 flaky Teardown).

## Kontext

Teil von „Stage 2 — vault-admin": brain-mcp erhält die Tools `list_notes` und
`delete_note`. titan stellt dafür nur den neuen `/notes`-Endpunkt bereit; `delete_note`
nutzt das bestehende `DELETE /chunks`. Siehe brain-mcp `docs/ai/` (2026-05-17).

## Offen / Nächste Schritte

- Flaky grpc-Fehler beim Collection-Teardown des letzten Integrationstests.
- Integrationstests laufen nur bei gestopptem titan-service (BGE-M3 GPU-Lock).

---

# Übergabe – 2026-05-16

## In dieser Sitzung erledigt

**Meilenstein erreicht!**
Phase 1 (titan-Service-Layer) und Phase 2 (brain-mcp) sind beide vollständig abgeschlossen und
auditiert. Das System ist nun **in Produktion**.
Der erste vollständige manuelle End-to-End-Test (E2E) war erfolgreich mit einem starken Score von **5,71**.

## In Arbeit

Nichts offen — alle Phasen der Erstentwicklung (1 & 2) sind abgeschlossen.

## Nächster konkreter Schritt

Das System im Alltag nutzen.
Cache-Verhalten, Latenzen und die Relevanz der abgerufenen Chunks beobachten.

## Offene Fragen / nötige Entscheidungen

- Keine.

## Notizen / entdeckte Stolperfallen

- **Wichtiges Setup-Detail (in DECISIONS.md dokumentiert):** das System benötigt WSL2
  Mirrored Networking, `QDRANT_HOST=localhost` und ein laufendes Docker Desktop. Bei Qdrant-
  Verbindungsproblemen ist das das Erste, was zu prüfen ist.
- **Häufigste Qdrant-Fehlerquelle:** läuft Docker Desktop nicht, ist der Qdrant-Container
  weg. `Test-NetConnection` unter Windows kann trotzdem „Port offen" melden
  (Phantom-Socket) — der gRPC-Connect läuft dann in „Deadline Exceeded". Zuerst Docker
  Desktop / den Container prüfen, dann das Netzwerk.

---

## Nachtrag (2026-05-16): Claude-Desktop-Integration

Die Claude-Desktop-Integration ist eingerichtet — sie läuft **nicht** direkt über titan, sondern
über den brain-mcp-Server.

- Der installierte Claude-Desktop-Build hat keinen Developer Mode; lokale stdio-MCP-Server via
  `claude_desktop_config.json` werden nicht unterstützt (die App entfernt den `mcpServers`-Block
  wieder).
- Status: **implementiert.** brain-mcp läuft als HTTP-Daemon (`127.0.0.1:9100`) hinter
  `tailscale funnel` (öffentlich), abgesichert durch einen GitHub-OAuth-Proxy mit Login-Allowlist.
  In Claude als Custom Connector `https://charliespc.taild04050.ts.net/mcp` verdrahtet;
  end-to-end verifiziert (`query_knowledge` liefert Vault-Treffer). Details im brain-mcp-
  Repo: `docs/ai/DECISIONS.md` und `docs/ai/HANDOFF.md` (beide 2026-05-16).
- **An titan selbst wurde nichts geändert.** titan-service bleibt auf
  `127.0.0.1:8765`; brain-mcp ruft ihn via `BRAIN_TITAN_URL` auf.
- Service-Reihenfolge für ein funktionierendes System:
  Docker Desktop → Qdrant-Container → `titan-service` → `brain-mcp` / `brain-watcher`.
