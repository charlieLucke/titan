# Handoff – 2026-05-13
Model: Claude Sonnet 4.6

## Done in this session

Phase 2 (brain-mcp, B0–B10) vollständig implementiert und committed.
Repo: `~/projects/brain-mcp/` (GitHub: charlievincentlucke-afk/brain-mcp, private)

**Was implementiert wurde:**
- `TitanClient` (httpx + tenacity 3x retry mit exp. Backoff)
- FastMCP-Server mit 4 Tools: `query_knowledge`, `ingest_note`, `list_domains`, `find_related`
- `VaultWatcher` (watchdog, injectable debounce, Reconnect-Logik B7)
- systemd-Unit `brain-watcher.service` (`Wants=`, nicht `Requires=`)
- Claude Desktop JSON-Config in `deploy/README.md`
- 33 Unit- + Integration-Tests (33 passed, 3 E2E-Tests skippen ohne live Titan)

## In progress

Nichts offen — alles committed.

## Next concrete step

1. **B11: Audit-Runde (Opus)** — Checkliste Plan Abschnitt 4.13
2. **Manuelle Aktivierung** (in WSL):
   ```bash
   cd ~/projects/brain-mcp
   mkdir -p ~/.config/systemd/user/
   ln -sf ~/projects/brain-mcp/deploy/brain-watcher.service ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now brain-watcher
   ```
3. **Claude Desktop Config** (Windows-Seite):
   → `%APPDATA%\Claude\claude_desktop_config.json` — Inhalt in `deploy/README.md`
4. **E2E-Test** (braucht laufenden titan-service):
   ```bash
   cd ~/projects/brain-mcp
   uv run pytest tests/integration/ -m integration -v
   ```
5. **VRAM-Probe** (noch offen aus Phase 1):
   ```bash
   cd ~/projects/titan
   uv run python -m titan.service._vram_probe
   # Ergebnis → docs/ai/vram_probe_results.md
   ```

## Open questions / decisions needed

- VRAM_MODE: Probe-Ergebnis fehlt noch — nach Probe in titan DECISIONS.md eintragen
- VAULT_ROOT: Ist `/mnt/f/vault` korrekt gemountet? Falls nicht, `BRAIN_VAULT_ROOT` anpassen
- B11 Audit: Opus sollte brain-mcp Checkliste (Plan 4.13) prüfen

## Files the next session must read first

**Wenn Audit-Session:**
1. `~/projects/titan/docs/ai/plans/plan_titan_brain_v2.md` Abschnitt 4.13
2. `~/projects/brain-mcp/src/brain_mcp/mcp_server.py`
3. `~/projects/brain-mcp/src/brain_mcp/watcher.py`
4. `~/projects/brain-mcp/docs/ai/DECISIONS.md`

**Wenn Fortsetzungs-Session:**
1. `~/projects/brain-mcp/docs/ai/HANDOFF.md`
2. `~/projects/brain-mcp/docs/ai/CURRENT_TASK.md`

## Notes / gotchas discovered

- pre-commit mypy Hook in brain-mcp braucht `additional_dependencies` — war der Haupt-Blocker
- `replace_all` edit auf `# type: ignore` entfernte Whitespace vor `def` → Syntax-Fehler.
  In Zukunft: nie `# type: ignore` Kommentare mit replace_all entfernen.
- brain-mcp hat kein systemd-Daemon — nur brain-watcher. brain-mcp startet Claude Desktop.
