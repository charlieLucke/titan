# Current Task

> Keep this short. One screen max. Update as you progress.

## Goal

Phase 0b: Big-Bang-Migration des Codes aus `~/projects/RAG_System/` in das neue
`~/projects/titan/`-Repo. Kein neues Feature, kein Service-Layer — nur sauberer Port.

Plan: `docs/ai/plans/2026-05-12_phase-0b-migration.md`

## Sub-steps

- [x] Plan nach `titan/docs/ai/plans/` kopiert
- [x] CONTEXT.md, CURRENT_TASK.md, DECISIONS.md befüllt
- [x] M0: Dependencies + `.env.example` + mypy-Overrides
- [x] M1: `utils.py` portieren + Smoke-Tests
- [x] M4: `generate.py` portieren
- [x] M3: `search.py` portieren
- [x] M5: `evaluate.py` portieren
- [x] M2: `ingest.py` portieren + Epic-5A-Removal
- [x] M6: `eval/ab_eval.py` + M7: `tools/init_col.py` + M8: Fixtures
- [x] M9: Audit + `make check` grün (ruff + mypy clean, 11/11 tests pass)

## Status

**Phase 0b abgeschlossen.** Alle Module portiert, Epic 5A vollständig entfernt,
`ruff check` + `mypy src/` grün über alle 12 Quelldateien, 11 Tests bestanden.

Nächster Schritt: E2E-Test manuell ausführen (Qdrant + Ollama müssen laufen).

## Notes

- Epic-5A-Code (contextual summaries) komplett entfernt — `~/projects/RAG_System/` bleibt Referenz
- `INGEST_BASE_DIR` Default: `/mnt/f/data/titan-input` (war `/mnt/ai_daten`)
- `MultivectorComparator` heißt in qdrant-client `MultiVectorComparator` (capital V)
- FlagEmbedding-Imports sind lazy (innerhalb von Funktionen) → `flagembedding.*` mypy-Override zeigt
  beim Check einzelner Dateien als "unused" — das ist erwartet, kein echtes Problem
