# Handoff – 2026-05-12
Model: Claude Sonnet 4.6

## Done in this session

Phase 1 — Service-Layer, Tasks A0–A10 implementiert auf Branch `feat/service-layer`.

**Neue Dateien:**
- `src/titan/service/__init__.py`
- `src/titan/service/_vram_probe.py` — VRAM-Probe (BGE-M3 + Phi-4 parallel)
- `src/titan/service/state.py` — ServiceState Singleton (model, client, gpu_lock, domain_counts)
- `src/titan/service/app.py` — FastAPI App + Lifespan (BGE-M3 load, ColBERT-Dim-Check, Domain-Counter-Init)
- `src/titan/service/schemas.py` — Pydantic Schemas (Health, Search, Ingest, Domains, FindRelated, Delete)
- `src/titan/service/routes.py` — Alle Endpoints: GET /health, POST /search, POST /ingest/file,
  GET /domains, POST /find_related, DELETE /chunks + _invalidate_cache_for_domain()

**Modifizierte Dateien:**
- `src/titan/main.py` — Service-Dispatch (`python -m titan service --port 8765`)
- `src/titan/search.py` — `search()` Funktion (injectable model + qdrant_client);
  `hybrid_search_with_rerank` gibt jetzt auch `source_path`, `domain`, `chunk_offset` zurück
- `src/titan/ingest.py` — `read_markdown()`, `late_chunk_and_embed()`, `make_point()`
- `pyproject.toml` — Dependencies: fastapi, uvicorn, httpx, python-frontmatter;
  mypy-overrides für routes (untyped-decorator, no-any-return) + frontmatter
- `.pre-commit-config.yaml` — mypy additional_dependencies: pydantic, fastapi, torch, httpx

**Qualitätsstand:**
- `ruff check` → 0 Fehler (8 Dateien)
- `mypy src/` → 0 Fehler (8 Dateien)
- Tests: noch nicht gegen Service gelaufen (braucht Qdrant + BGE-M3)

## In progress

Nichts offen — A0–A10 abgeschlossen, aber noch nicht committed (warte auf pre-commit Abschluss).

## Next concrete step

1. **A11:** `systemd/user/titan-service.service` schreiben (~/config/systemd/user/)
2. **A12:** `tests/integration/test_service.py` — 16 Test-Cases aus Plan (pytest.mark.integration)
3. **A13:** Audit-Runde mit Opus
4. **Manueller E2E-Test** wenn Qdrant + Ollama laufen:
   ```bash
   python -m titan service --port 8765
   curl localhost:8765/health
   curl -X POST localhost:8765/search -H "Content-Type: application/json" \
        -d '{"query":"Testfrage","domain":"titan"}'
   ```

## Open questions / decisions needed

- **VRAM_MODE:** A0 (VRAM-Probe) noch nicht manuell ausgeführt — Ergebnis in
  `docs/ai/vram_probe_results.md` fehlt noch. Muss vor Produktionsbetrieb gemacht werden.
- **VAULT_ROOT:** Default `/mnt/f/vault` in `routes.py` — in `.env.example` dokumentieren.

## Files the next session must read first

1. `docs/ai/CONTEXT.md`
2. `docs/ai/CURRENT_TASK.md`
3. `docs/ai/DECISIONS.md` — besonders die neuen 2026-05-12 ADRs (run_id, indexed:false, cache)
4. `src/titan/service/routes.py` — aktueller Stand aller Endpoints

## Notes / gotchas discovered

- `qdrant_client.count()` gibt `CountResult.count` als `Any` zurück → `int()` cast nötig
- `hybrid_search_with_rerank` gibt Payload-Dict zurück, nicht ein Pydantic-Model — Mapping
  in routes.py per `.get()` mit Defaults
- `frontmatter`-Package (python-frontmatter) hat keine Typstubs → mypy override nötig
- FastAPI-Decorators (`@router.post`) sind in mypy strict `untyped-decorator` → override in
  `pyproject.toml` für `titan.service.routes`
- pre-commit mypy-Hook braucht `additional_dependencies` für pydantic/fastapi/torch,
  sonst "BaseModel has type Any" bei jedem Commit
- `qdrant_client.scroll()` gibt Tuple `(records, next_offset)` zurück — `next_offset is None`
  signalisiert Ende des Scrolls
- Domain-Counter nach Service-Restart: Voll-Scan via scroll in `_init_domain_counts()` —
  bei sehr großen Collections (>500k Chunks) kann das mehrere Sekunden dauern
