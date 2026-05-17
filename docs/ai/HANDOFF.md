# Handoff – 2026-05-17
Model: Claude Opus 4.7

## Done in this session

`GET /notes` ergänzt — Endpoint für die Liste aller indexierten Notes (gruppiert nach
Datei, mit Domain + Chunk-Count). Dient dem neuen `list_notes`-Tool in brain-mcp.

**Code-Änderungen:**
- `src/titan/service/schemas.py` — neue Schemas `NoteInfo`, `NotesResponse`
- `src/titan/service/routes.py` — `GET /notes` (Voll-Scroll + Aggregation)
- `tests/integration/test_service.py` — `/notes`-Integrationstests + Reparatur der
  bis dahin komplett roten Suite (Details: DECISIONS.md 2026-05-17)

**Qualitätsstand:** `ruff` grün, `mypy` grün, Unit-Tests 12 passed, Integrationssuite
19/20 grün (1 flaky Teardown).

## Kontext

Teil von „Etappe 2 — vault-admin": brain-mcp bekommt die Tools `list_notes` und
`delete_note`. titan liefert dafür nur den neuen `/notes`-Endpoint; `delete_note` nutzt
den bestehenden `DELETE /chunks`. Siehe brain-mcp `docs/ai/` (2026-05-17).

## Offen / Next steps

- Flaky grpc-Fehler beim Collection-Teardown des letzten Integrationstests.
- Integrationstests laufen nur bei gestopptem titan-service (BGE-M3-GPU-Lock).

---

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
- Stand: **umgesetzt.** brain-mcp läuft als HTTP-Daemon (`127.0.0.1:9100`) hinter
  `tailscale funnel` (öffentlich), abgesichert per GitHub-OAuth-Proxy mit Login-
  Allowlist. In Claude als Custom Connector `https://charliespc.taild04050.ts.net/mcp`
  eingebunden; End-to-End verifiziert (`query_knowledge` liefert Vault-Treffer).
  Details im brain-mcp-Repo: `docs/ai/DECISIONS.md` und `docs/ai/HANDOFF.md`
  (jeweils 2026-05-16).
- An titan selbst wurde dafür **nichts geändert**. titan-service bleibt auf
  `127.0.0.1:8765`; brain-mcp ruft es über `BRAIN_TITAN_URL` auf.
- Dienst-Reihenfolge für ein funktionierendes System:
  Docker Desktop → Qdrant-Container → `titan-service` → `brain-mcp` / `brain-watcher`.
