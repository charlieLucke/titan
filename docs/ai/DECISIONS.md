# Decisions Log

> Architecture Decision Records. Append-only. One entry per significant decision.
> This prevents re-litigating the same questions in every new AI session.

## Format

```
## YYYY-MM-DD: Short title
**Decision:** What we decided
**Reasoning:** Why
**Alternatives considered:** What we rejected and why
**Consequences:** What this implies going forward
```

---

## Initial decisions (template defaults)

## 2026-XX-XX: Use uv as package manager
**Decision:** uv (over pip+venv, poetry, pdm).
**Reasoning:** 10-100x faster than pip; unified tool replacing pip, pip-tools, virtualenv, pyenv; lockfile by default; backed by Astral (same team as ruff).
**Alternatives considered:** Poetry (slower, more config overhead, separate from venv tooling). pip+venv (no lockfile by default, manual workflow).
**Consequences:** All dependency operations go through `uv add` / `uv remove` / `uv sync`. Never edit pyproject.toml dependencies manually.

## 2026-XX-XX: Use ruff for lint and format
**Decision:** ruff replaces black + flake8 + isort + pyupgrade.
**Reasoning:** Single tool, much faster, consistent config, actively maintained.
**Consequences:** Don't add black, flake8, or isort as separate tools.

## 2026-XX-XX: Mypy strict mode
**Decision:** Mypy in strict mode from day one.
**Reasoning:** Strictness is much easier to enforce from the start than retrofit. Catches whole categories of bugs at write-time.
**Consequences:** Every function needs full type hints. `# type: ignore` requires an inline comment explaining why.

---

## 2026-05-12: Big-Bang-Migration aus RAG_System, Epic 5A v1.0 nicht portiert
**Decision:** Code aus `~/projects/RAG_System/execution/` wird 1:1 in `src/titan/` portiert.
Epic 5A (Contextual Retrieval via Phi-4-Summaries) wird beim Port bewusst entfernt.
**Reasoning:** Epic 5A v1.0 hatte zu hohe Latenz und zu wenig Retrieval-Gewinn für den Aufwand.
Neuimplementierung (Epic 5A v2.0) soll sauber auf der neuen Code-Basis starten, nicht auf altem Code.
**Alternatives considered:** Selektiver Port mit Feature-Flag für 5A — verworfen, da totes Feature
im Code Verwirrung stiftet und mypy-Hygiene erschwert.
**Consequences:** `~/projects/RAG_System/` bleibt unverändert als Referenz. Wer 5A-Code braucht,
liest dort nach. Alle `--contextual`, `--clear-summary-cache`, `--summary-cache-stats` Flags entfallen.

## 2026-05-12: Package-Struktur flach unter src/titan/
**Decision:** Flache Struktur: `src/titan/utils.py`, `src/titan/ingest.py` etc. statt
`src/titan/rag_system/execution/utils.py` (altes Layout).
**Reasoning:** Das alte `rag_system/execution/`-Prefix war ein Workaround ohne Package-Namespace.
Mit `titan` als Package-Name ist die zusätzliche Verschachtelung reiner Overhead.
**Consequences:** Import-Pfade lauten `from titan.utils import ...` — kurz und eindeutig.

## 2026-05-12: mypy ignore_missing_imports für ML-Bibliotheken
**Decision:** `ignore_missing_imports = true` in `[[tool.mypy.overrides]]` für `flagembedding`,
`docling`, und ggf. `torch` (je nach Stub-Verfügbarkeit).
**Reasoning:** Diese Libraries haben keine Typestubs. mypy strict würde sonst auf jeden Import
knallen. Das ist kein echtes Typ-Problem, sondern fehlende Third-Party-Stubs.
**Consequences:** Diese Module sind von der Typ-Prüfung ausgenommen — ein akzeptabler Kompromiss
solange keine Community-Stubs verfügbar sind.

## 2026-05-12: ColBERT-Vektordimension 1024 (FlagEmbedding ≥ 1.3)
**Decision:** Qdrant-Collection wird mit `colbert.size=1024` angelegt.
**Reasoning:** FlagEmbedding hat ab Version 1.3 die ColBERT-Ausgabedimension von 128 auf 1024
geändert. Die Collection muss exakt zur installierten Library-Version passen — falscher Wert führt
zu Upsert-Fehlern oder unbrauchbaren Retrieval-Ergebnissen ohne offensichtliche Fehlermeldung.
**Alternatives considered:** 128 (Vorversion) — verworfen, da ab FE 1.3 faktisch falsch.
**Consequences:** `flagembedding>=1.3` ist implizite Mindestanforderung (pyproject.toml deklariert
`>=1.4.0`). Bei FlagEmbedding-Upgrade: Release Notes auf ColBERT-Dim-Änderungen prüfen. Bei
Downgrade unter 1.3: Collection mit `init_col.py --recreate` neu anlegen mit `colbert.size=128`.

## 2026-05-12: GPU_LOCK_PATH Default auf /tmp/bge_m3.lock geändert
**Decision:** GPU-Lock-Datei liegt unter `/tmp/bge_m3.lock` (war `/tmp/rag_gpu.lock` im alten
RAG_System-Repo).
**Reasoning:** Der neue Lock-Pfad reflektiert den neuen Package-Namen und vermeidet Kollisionen
mit noch laufenden Prozessen aus dem alten RAG_System-Setup.
**Consequences:** Wer vom alten RAG_System migriert: einmalig `rm -f /tmp/rag_gpu.lock` ausführen,
damit kein veralteter Lock-File einen Prozess-Start blockiert. Der neue Pfad kann via `.env`
überschrieben werden (`GPU_LOCK_PATH=/tmp/bge_m3.lock`).

---

## 2026-05-12: run_id-Payload-Konvention für Upsert-before-Delete
**Decision:** Jeder Ingest-Run setzt eine UUID (`run_id`) im Chunk-Payload.
Beim Re-Ingest: neue Chunks mit neuem `run_id` einfügen, dann alte Chunks mit
anderem `run_id` für dieselbe `source_path` löschen.
**Reasoning:** Verhindert Downtime-Fenster: Solange Schritt 2 (Upsert) läuft,
sind die alten Chunks noch durchsuchbar. Wenn Schritt 2 crasht, bleibt der alte
Stand erhalten. Doppelte Chunks (alter + neuer Run) sind harmlos — RRF gewichtet
sie gleichwertig und der nächste Lauf bereinigt.
**Alternatives considered:** Delete-before-Insert (führt zu Lücken während Re-Indexierung).
**Consequences:** `run_id` ist Pflicht-Payload-Feld für alle Ingest-Aufrufe via Service.
CLI-Ingest (`python -m titan.ingest`) nutzt diese Konvention noch nicht — das ist akzeptabel
da der CLI-Pfad typischerweise für Erst-Ingest genutzt wird.

## 2026-05-12: indexed:false Semantik — Service entscheidet, Client trusts
**Decision:** Frontmatter-Feld `indexed: false` wird ausschließlich vom Service
ausgewertet. Der Watcher (brain-mcp Phase 2) sendet den Pfad ohne Frontmatter-Auswertung.
**Reasoning:** Einzige Stelle die die Semantik kennt → kein Sync-Problem wenn sich
die Semantik ändert.
**Consequences:** `POST /ingest/file` bei `indexed:false`: löscht existierende Chunks,
gibt `skipped_reason: "indexed:false"` zurück, erstellt keine neuen Chunks.

## 2026-05-12: Cache-Invalidierung aggressiv (Domain-granular)
**Decision:** Bei Re-Ingest einer Note werden ALLE Cache-Einträge ihrer Domain geleert.
**Reasoning:** Einfachste korrekte Implementierung. Cache-Hit für "alten" Stand vermeiden
ohne Chunk→Cache-Dependency-Tracking.
**Alternatives considered:** Feingranulare Invalidierung nur für Einträge die die geänderte
Note enthielten — braucht Cache→Chunk-Tracking das Epic 5B nicht implementiert.
**Consequences:** Bei häufigen Edits in einer Domain kann die Cache-Hit-Rate sinken.
Wenn das messbar problematisch wird: feineres Tracking nachrüsten.

## 2026-05-12: source_path + source als Payload-Aliases
**Decision:** Neue Ingest-Uploads via Service setzen sowohl `source_path` als auch
`source` im Qdrant-Payload. Alte CLI-Ingests haben nur `source`.
**Reasoning:** Rückwärtskompatibilität: Bestehende Chunks im Index bleiben nutzbar.
Service-Schema (`Chunk.source_path`) ist der neue Standard.
**Consequences:** Search-Ergebnisse liefern beide Keys. Routes-Code nutzt `source_path`;
falls leer, ist die Note per altem CLI-Pfad ingested.
