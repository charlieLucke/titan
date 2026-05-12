# Handoff – 2026-05-12
Model: Claude Sonnet 4.6

## Done in this session

Phase 1 — Service-Layer vollständig implementiert (A0–A12) auf Branch `feat/service-layer`.

**Neue Dateien:**
- `src/titan/service/__init__.py`
- `src/titan/service/_vram_probe.py` — VRAM-Probe (BGE-M3 + Phi-4 parallel)
- `src/titan/service/state.py` — ServiceState Singleton
- `src/titan/service/app.py` — FastAPI Lifespan, BGE-M3 Singleton, GPU-Lock, ColBERT-Dim-Check
- `src/titan/service/schemas.py` — Pydantic Schemas
- `src/titan/service/routes.py` — Alle 6 Endpoints (health, search, ingest/file, domains, find_related, chunks)
- `deploy/titan-service.service` — systemd User-Service
- `deploy/README.md` — Installations-Anleitung
- `tests/integration/__init__.py`
- `tests/integration/test_service.py` — 16 pytest.mark.integration Tests

**Modifizierte Dateien:**
- `src/titan/main.py` — Service-Dispatch
- `src/titan/search.py` — `search()` mit injectable model/client; hybrid_search_with_rerank gibt source_path/domain/chunk_offset zurück
- `src/titan/ingest.py` — `read_markdown()`, `late_chunk_and_embed()`, `make_point()`
- `pyproject.toml` — neue Deps + mypy-Overrides
- `.pre-commit-config.yaml` — mypy additional_dependencies

**Qualitätsstand (letzter Check):**
- `ruff check` → 0 Fehler (10 Dateien)
- `mypy src/ tests/` → 0 Fehler (10 Dateien)
- Unit-Tests: `make test-fast` noch nicht ausgeführt (kein Qdrant/BGE im CI)
- Commits: zwei saubere Commits auf `feat/service-layer`

## In progress

Nichts offen — A0–A12 vollständig committed.

## Next concrete step

1. **A13: Audit-Runde (Opus)** — Checkliste aus Plan Abschnitt 3.15 durchgehen
2. **VRAM-Probe manuell ausführen** (Qdrant + Ollama müssen laufen):
   ```bash
   uv run python -m titan.service._vram_probe
   # Ergebnis in docs/ai/vram_probe_results.md
   ```
3. **systemd einrichten:**
   ```bash
   mkdir -p ~/projects/titan/logs ~/.config/systemd/user/
   ln -sf ~/projects/titan/deploy/titan-service.service ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now titan-service
   ```
4. **E2E-Test:**
   ```bash
   curl localhost:8765/health
   curl -X POST localhost:8765/search -H "Content-Type: application/json" \
        -d '{"query":"Testfrage","domain":"titan","use_decompose":false}'
   ```
5. **Integration-Tests** (brauchen echtes Qdrant + BGE-M3):
   ```bash
   uv run pytest tests/integration/ -m integration -v
   ```

## Open questions / decisions needed

- **VRAM_MODE:** Probe-Ergebnis fehlt noch → nach Probe in DECISIONS.md eintragen
- **VAULT_ROOT in .env.example:** noch nicht dokumentiert — vor Produktion hinzufügen
- **A13 Audit:** Opus sollte alle Punkte aus Plan-Checkliste (Abschnitt 3.15) prüfen,
  besonders: Pfad-Traversal, sanitize() auf Frontmatter, CUDA-OOM-Handling

## Files the next session must read first

1. `docs/ai/CONTEXT.md`
2. `docs/ai/CURRENT_TASK.md`
3. `docs/ai/DECISIONS.md` — neue ADRs: run_id, indexed:false, cache-invalidation, source_path alias
4. `src/titan/service/routes.py` — vollständige Endpoint-Implementierung
5. `docs/ai/plans/plan_titan_brain_v2.md` Abschnitt 3.15 — Audit-Checkliste

## Notes / gotchas discovered

- `qdrant_client.count()` gibt `CountResult.count` als `Any` → `int()` cast nötig
- FastAPI-Decorators sind in mypy strict `untyped-decorator` → pyproject.toml Override
- pre-commit mypy-Hook braucht additional_dependencies (pydantic/fastapi/torch/httpx)
- `frontmatter`-Paket hat keine Typstubs → mypy `ignore_missing_imports = true`
- `tests.*` bekommt `ignore_errors = true` (Fixtures + Dynamic Mocking nicht sinnvoll strict zu prüfen)
- Domain-Counter nach Service-Restart: Voll-Scan über alle Chunks — bei großen Collections langsam
- `VectorsConfig(root={...})` für named vectors in qdrant-client ≥1.18 (in Integration-Test-Fixture)
