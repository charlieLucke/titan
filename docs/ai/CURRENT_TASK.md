# Current Task

> Keep this short. One screen max. Update as you progress.

## Goal

Phase 1: Service-Layer. FastAPI-Service mit BGE-M3 als Singleton, alle Endpoints,
systemd-Unit, Integration-Tests.

Plan: `docs/ai/plans/plan_titan_brain_v2.md`
Branch: `feat/service-layer`

## Sub-steps

- [x] Branch `feat/service-layer` angelegt
- [x] A0: `_vram_probe.py` – VRAM-Probe-Skript
- [x] A1: `service/app.py` + `service/state.py` – FastAPI Lifespan, BGE-M3 Singleton,
       GPU-Lock, ColBERT-Dim-Check, Domain-Counter-Init; `main.py` Service-Dispatch
- [x] A2: `service/schemas.py` – alle Pydantic-Schemas; `routes.py` – `GET /health`
- [x] A3: `search.py` – `search()` Funktion mit injectablem model + qdrant_client
- [x] A4: `routes.py` – `POST /search`
- [x] A5: `ingest.py` – `read_markdown()` + `late_chunk_and_embed()` + `make_point()`
- [x] A6: `routes.py` – `POST /ingest/file` (Upsert-before-Delete mit run_id)
- [x] A7: `routes.py` – `GET /domains`
- [x] A8: `routes.py` – `POST /find_related`
- [x] A9: `routes.py` – `DELETE /chunks`
- [x] A10: `routes.py` – `_invalidate_cache_for_domain()` bei Re-Ingest
- [x] A11: `deploy/titan-service.service` + `deploy/README.md`
- [x] A12: `tests/integration/test_service.py` – 16 Test-Cases (pytest.mark.integration)
- [ ] A13: Audit-Runde (Opus)

## Status

**A0–A12 implementiert.** ruff + mypy grün über alle 10 Quelldateien (src + tests).
Nächster Schritt: A13 Audit-Runde (Opus), dann manueller E2E-Test wenn Qdrant+Ollama laufen.

## Notes

- `source_path` und `source` sind jetzt beide im Payload (Alias) — rückwärtskompatibel
- `run_id` im Payload ist neue Konvention (→ DECISIONS.md)
- python-frontmatter als neue Dependency für read_markdown
- pre-commit mypy-Hook bekommt pydantic/fastapi/torch als additional_dependencies
- `frontmatter`-Modul braucht mypy `ignore_missing_imports`
