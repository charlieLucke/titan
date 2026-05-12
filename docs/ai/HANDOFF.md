# Handoff – 2026-05-12
Model: Claude Sonnet 4.6

## Done in this session

Phase 0b: Big-Bang-Migration aus `~/projects/RAG_System/execution/` abgeschlossen.

**Portierte Module (alle in `src/titan/`):**
- `utils.py` — GPU-Lock (fcntl), stable_uuid, cache_uuid, sanitize
- `generate.py` — LongContextReorder + Ollama-Streaming
- `search.py` — BGE-M3 Hybrid-Search, RRF-Fusion, Epic-5B Semantic Cache
- `evaluate.py` — RAG-Triade Judge (CR/GR/AR) via Phi-4
- `ingest.py` — Docling PDF-Parser, Late Chunking, Qdrant-Upsert
- `eval/ab_eval.py` — Evaluation Suite (V1 Determinismus / V3 Rank-Only / V4 Full)
- `tools/init_col.py` — Qdrant Collection Setup mit `--recreate`-Flag
- `eval/fixtures/cases.json` — 20 stratifizierte Eval-Cases (4 Kategorien)

**Epic 5A vollständig entfernt:** Contextual Retrieval (Phi-4-Summaries in ingest),
summary_cache, `--contextual`-Flags, `CONTEXTUAL_MODEL`-Env-Var. Einzige Spuren sind
Docstring-Notizen in `ingest.py` und `utils.py`.

**Qualitätsstand beim letzten `make check`:**
- `uv run ruff check src/` → 0 Fehler
- `uv run mypy src/ tests/` → 0 Fehler (16 Dateien)
- `uv run pytest` → 12/12 Tests bestanden
- CUDA verfügbar: True, Version 13.0

**Cleanup nachgepflegt (zweiter Commit):**
- `DECISIONS.md`: 2 ADRs ergänzt (ColBERT-Dim, GPU_LOCK_PATH)
- `main.py`: Help-Dispatcher statt Hello-World
- `tests/titan/test_imports.py`: Import-Smoke für alle 6 Module
- CI: `mypy src` → `mypy src tests`
- `cases.json`: description ohne Epic-5A-Referenz

## In progress

Nichts offen — Phase 0b ist abgeschlossen.

## Next concrete step

**E2E-Smoke-Test** — manuell wenn Qdrant + Ollama laufen:
```bash
cp .env.example .env
uv run python -m titan.tools.init_col
uv run python -m titan.ingest --domain test --input /mnt/f/data/titan-input/test/
uv run python -m titan.search "Testfrage" --domain test --json | uv run python -m titan.generate
```

Danach: Brain-Plan für Phase 1 + 2 schreiben lassen (Opus).

## Open questions / decisions needed

- **Phase 1 Scope:** Service-Layer (FastAPI)? Monitoring? Besseres Chunking?
  → Entscheidung durch Opus-Plan, nicht durch Implementierungs-Session.
- **Epic 5A v2.0:** Noch kein Plan. Erst wenn Baseline-System läuft und Eval-Zahlen vorliegen.

## Files the next session must read first

1. `docs/ai/CONTEXT.md`
2. `docs/ai/DECISIONS.md` — besonders die vier 2026-05-12 ADRs
3. `docs/ai/plans/2026-05-12_phase-0b-migration.md`
4. Den neuen Brain-Plan in `brain-mcp/docs/ai/plans/` (noch zu schreiben)

## Notes / gotchas discovered

- `MultivectorComparator` → `MultiVectorComparator` (capital V) in qdrant-client; mypy
  findet das; alte dict-Form im RAG_System wurde nicht geprüft.
- FlagEmbedding-Imports sind lazy (in Funktionen) → `flagembedding.*` mypy-Override erscheint
  beim Check einzelner Dateien als "unused" — das ist erwartet, kein Problem.
- Pre-commit ruff-format formatiert automatisch um; bei Fehler immer `git add -u` und neu
  commiten, niemals `--no-verify`.
- Vor erstem Lauf alten Lock aus RAG_System-Zeiten löschen:
  `rm -f /tmp/rag_gpu.lock /tmp/bge_m3.lock`
