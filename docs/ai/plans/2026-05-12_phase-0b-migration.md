# Plan: Titan-Migration (Phase 0b)

> **Autor:** Claude Opus 4.7
> **Datum:** Mai 2026
> **Vorgänger:** Phase 0a (Inventur) abgeschlossen
> **Nachfolger:** Phase 1 (Service-Layer)
> **Workflow:** Opus-Plan → Sonnet pro Modul → Audit-Runde am Ende

---

## 0. Ziel und Scope

**Was hier passiert:** Big-Bang-Port des Codes aus `~/projects/RAG_System/` in das neue `~/projects/titan/`-Repo, das bereits auf Template-Standard läuft (`make check` grün).

**Was hier explizit NICHT passiert:**
- Keine neuen Features
- Keine Service-Layer-Erweiterung (das ist Phase 1)
- Keine Verbesserung der Algorithmen
- Keine Mitnahme von Epic-5A v1.0-Code (verworfen laut Titan-Doku)

**Erfolgskriterium:** `make check` bleibt grün, der alte CLI-Workflow funktioniert über das neue Package (`python -m titan.search "..."` statt `python execution/search.py "..."`), `init_col.py` legt eine Test-Collection an, ein einzelnes PDF kann ingested und gesucht werden.

---

## 1. Strategieentscheidungen (vorab festgehalten)

### 1.1 Package-Struktur: Flach unter `src/titan/`

```
src/titan/
├── __init__.py
├── __main__.py            # ggf. CLI-Dispatcher
├── utils.py
├── ingest.py
├── search.py
├── generate.py
├── evaluate.py
├── eval/                  # Validierungs-Tooling
│   ├── __init__.py
│   ├── ab_eval.py
│   └── fixtures/          # JSON-Cases + Baseline-Outputs
└── tools/
    └── init_col.py
```

**Begründung:** `rag_system/execution/` als Top-Level war Workaround, weil das alte Repo kein Package-Namespace hatte. Jetzt heißt das Repo `titan`, also brauchen wir keine zweite Verschachtelung. `eval/` und `tools/` sind logische Sub-Sphären (Validation-Tooling vs. produktiver RAG-Code).

### 1.2 Epic-5A-Entfernung

Folgendes wird beim Port entfernt:
- `--contextual`-Flag in `ingest.py`
- `--clear-summary-cache` und `--summary-cache-stats` (gehören zur 5A-Implementation)
- Env-Variablen `CONTEXTUAL_MODEL`, `CONTEXTUAL_TIMEOUT`, `CONTEXTUAL_MAX_SECTION_TOKENS`
- Alle Funktionen, die Contextual-Summaries generieren oder cachen
- Cross-References in Docstrings

**Backup-Garantie:** `~/projects/RAG_System/` bleibt unverändert. Wenn der Re-Design beginnt, ist der alte Code dort als Referenz vollständig vorhanden.

### 1.3 CLI-Migration: `python -m`-Pattern

Alle Module sind heute eigenständige Scripts mit `if __name__ == "__main__":`-Block. Beim Port:
- Shebang-Zeile (`#!/usr/bin/env python3`) entfällt — irrelevant für Package
- `if __name__ == "__main__":`-Block bleibt, sodass `python -m titan.search "Query"` funktioniert
- Später (Phase 1) kommen saubere Entry Points über `pyproject.toml` dazu — jetzt nicht

### 1.4 Eval-Daten

Die JSON-Files im alten Repo (`eval_baseline.json`, `eval_cases_v2.json`, `validation_v3_ranks_*.json` etc.) sind Test-Fixtures bzw. historische Baseline-Daten. Strategie:
- `eval_cases_v2.json` → `src/titan/eval/fixtures/cases.json` (wird vom Code geladen)
- Validierungs-Outputs (`validation_v*_*.json`) → bleiben im alten Repo als historische Aufzeichnung
- `test_results.md` → bleibt im alten Repo

Wenn du später einen Eval-Lauf neu machst, schreibst du in das neue Repo (`./eval_runs/` o.ä., nicht in `src/`).

---

## 2. Vorbereitung (Task M0)

Diese eine Task läuft VOR der Modul-Migration und ist eigenständig committable.

### 2.1 Dependencies installieren

```bash
cd ~/projects/titan
uv add python-dotenv qdrant-client torch requests
uv add flagembedding docling
```

**Hinweise zu den schweren Dependencies:**
- `torch`: Standardinstallation via `uv add torch`. Falls CUDA-Version manuell gepflegt werden muss (z.B. spezifische CUDA-Version), das in `DECISIONS.md` festhalten. uv akzeptiert standardmäßig die CPU-Version, was für die Service-Maschine nicht reicht.
- `flagembedding`: Aktuell version-pinning auf `>=1.2.0` empfohlen (laut deinem alten Code-Check)
- `docling`: Kann langsam installieren wegen ML-Dependencies

**Empfehlung bei torch+CUDA:** Nach `uv add torch` einmalig prüfen:
```bash
uv run python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```
Wenn `False`: in `pyproject.toml` den Source-Index für CUDA-Wheels eintragen (uv unterstützt `[[tool.uv.index]]`). Ein typisches Pattern, das wir bei Bedarf in `DECISIONS.md` festhalten.

### 2.2 `.env.example` anlegen

Diese Datei kommt ins Repo (nicht ins `.gitignore`!), als Vorlage für andere und für dich selbst:

```env
# Qdrant
QDRANT_HOST=localhost
QDRANT_GRPC_PORT=6334
QDRANT_API_KEY=
COLLECTION_NAME=mein_wissen

# Hardware
MAX_WORKERS=12
EMBED_BATCH_SIZE=32
LATE_CHUNK_WINDOW_TOKENS=7800

# Ollama
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=phi4:latest
OLLAMA_TIMEOUT=120

# Epic 4 Evaluation
JUDGE_MODEL=phi4:latest
JUDGE_TIMEOUT=60

# Epic 5B Semantic Caching
CACHE_ENABLED=true
CACHE_COLLECTION_NAME=query_cache
CACHE_THRESHOLD=0.95
CACHE_TTL_SECONDS=604800
CACHE_TOP_K=1

# System
GPU_LOCK_PATH=/tmp/bge_m3.lock
INGEST_BASE_DIR=/mnt/f/data/titan-input
```

**Wichtig:** `INGEST_BASE_DIR` zeigt jetzt auf einen sinnvollen WSL-Pfad. Den Ordner musst du einmal anlegen, bevor das erste Ingest läuft.

### 2.3 docs/ai/-Struktur befüllen

Im Template-Standard liegen die Doku-Ordner schon. Drei Dateien initial befüllen:
- `docs/ai/CONTEXT.md`: Stack-Beschreibung aus deiner Titan-Zusammenfassung als Basis (gekürzt auf Implementierungs-relevantes)
- `docs/ai/ARCHITECTURE.md`: Modul-Übersicht aus Abschnitt 3 deiner Titan-Doku
- `docs/ai/DECISIONS.md`: Erste ADR — „Big-Bang-Migration, Epic 5A v1.0 wird nicht portiert"

---

## 3. Modul-Migration: Reihenfolge nach Abhängigkeiten

Sechs Modul-Tasks plus init_col plus Fixtures. Bei jeder Task der Workflow ist identisch:
1. Datei kopieren nach `src/titan/<modul>.py`
2. Shebang entfernen, Imports umbiegen (`from utils import ...` → `from titan.utils import ...`)
3. `make check` laufen lassen
4. Mypy/Ruff-Errors fixen
5. Smoke-Test pro Modul
6. Commit mit `refactor: port <modul>.py to titan package`

### Task M1: `utils.py`

**Quelle:** `~/projects/RAG_System/execution/utils.py` (109 Zeilen)
**Ziel:** `~/projects/titan/src/titan/utils.py`

**Anpassungen:**
- Shebang weg
- `__main__`-Block (falls vorhanden) weg
- Logger-Setup intakt lassen
- Lazy-Import von `fcntl` (Windows-Kompatibilität) bleibt — laut Titan-Doku ein Designprinzip

**Verifikation:**
```bash
uv run python -c "from titan.utils import acquire_gpu_lock, stable_uuid, cache_uuid, sanitize; print('ok')"
make check
```

**Smoke-Test** (in `tests/test_utils.py`):
- `stable_uuid("foo", 0)` deterministisch (zwei Aufrufe = gleiches Ergebnis)
- `sanitize()` ersetzt `===` und `---`
- `acquire_gpu_lock()` ist auf Linux importierbar (kein Crash beim Import)

**Aufwand:** 30 min

### Task M2: `ingest.py` (mit Epic-5A-Removal)

**Quelle:** `~/projects/RAG_System/execution/ingest.py` (1009 Zeilen, davon geschätzt 200–300 Epic-5A)
**Ziel:** `~/projects/titan/src/titan/ingest.py`

**Anpassungen:**
- Standard-Migration (Shebang weg, Imports umbiegen)
- **Epic-5A-Removal:** Sonnet bekommt explizit folgenden Auftrag:
  > „Entferne alle Code-Pfade, die mit dem `--contextual`-Flag, dem `--clear-summary-cache`-Flag oder dem `--summary-cache-stats`-Flag zusammenhängen. Entferne die Env-Variablen `CONTEXTUAL_MODEL`, `CONTEXTUAL_TIMEOUT`, `CONTEXTUAL_MAX_SECTION_TOKENS` und ihre Konsumenten. Entferne Funktionen, die Contextual-Summaries via Phi-4 generieren oder cachen. Behalte alles, was zu Epic 1 (Domain-Tagging), Epic 3 (Late Chunking) und der Basis-Pipeline gehört. Dokumentiere am Anfang der Datei im Docstring, dass Epic 5A v1.0 entfernt wurde — Verweis auf altes Repo zur Referenz."
- Docstring oben aktualisieren: „PDF-Parsing, Late Chunking & Multi-Vector Embedding" (kein „Contextual Retrieval" mehr)
- `INGEST_BASE_DIR`-Default auf `/mnt/f/data/titan-input` (oder analog zu deinem Setup) statt `/mnt/ai_daten`

**Verifikation:**
```bash
make check
uv run python -m titan.ingest --help   # zeigt aktualisierte CLI ohne --contextual
```

**Smoke-Test:** Nicht voll automatisierbar (braucht echte PDFs). Stattdessen:
- Argparse-Test: `--contextual`-Flag existiert nicht mehr (klarer Argparse-Error)
- Import-Test: `from titan.ingest import ingest_pdf` (oder wie die Hauptfunktion heißt — beim Port klären)

**Aufwand:** 2–3 h (Epic-5A-Removal ist der zeitintensive Teil)

**Audit-Punkt:** Nach Abschluss prüfen, dass keine toten Code-Pfade übrig sind (`ruff check --select F` für unused imports/functions).

### Task M3: `search.py`

**Quelle:** `~/projects/RAG_System/execution/search.py` (746 Zeilen)
**Ziel:** `~/projects/titan/src/titan/search.py`

**Anpassungen:**
- Standard-Migration
- Imports von `utils` umbiegen
- Falls Modul Epic-5A-spezifische Lookups macht (z.B. Lesen von Contextual-Summary-Feldern aus Qdrant-Payload): entfernen oder mit Default-Fallback versehen, falls Felder gar nicht mehr existieren

**Verifikation:**
```bash
make check
uv run python -m titan.search "test query" --json   # gegen leere Test-Collection ok, erwartet 0 Ergebnisse
```

**Smoke-Test:** Argparse-Vollständigkeit, Imports lädbar.

**Aufwand:** 1–1.5 h

### Task M4: `generate.py`

**Quelle:** `~/projects/RAG_System/execution/generate.py` (358 Zeilen)
**Ziel:** `~/projects/titan/src/titan/generate.py`

**Anpassungen:** Standard-Migration. Modul ist klein und in sich geschlossen.

**Verifikation:**
```bash
make check
echo '[]' | uv run python -m titan.generate   # leere Chunks → klarer Fehler oder Hinweis
```

**Aufwand:** 30 min

### Task M5: `evaluate.py`

**Quelle:** `~/projects/RAG_System/execution/evaluate.py` (408 Zeilen)
**Ziel:** `~/projects/titan/src/titan/evaluate.py`

**Anpassungen:** Standard-Migration. Keine bekannten Epic-5A-Berührungen.

**Verifikation:**
```bash
make check
uv run python -m titan.evaluate --help
```

**Aufwand:** 1 h

### Task M6: `ab_eval.py` (in `eval/`-Subpackage)

**Quelle:** `~/projects/RAG_System/execution/ab_eval.py` (489 Zeilen)
**Ziel:** `~/projects/titan/src/titan/eval/ab_eval.py`
**Plus:** `src/titan/eval/__init__.py` anlegen (kann leer sein)

**Anpassungen:**
- Import-Pfade: `from evaluate import ...` → `from titan.evaluate import ...`
- Default-Pfad für `--cases-file` neu setzen: zeigt jetzt auf `src/titan/eval/fixtures/cases.json` (oder via `importlib.resources` lookup — robuster)

**Hinweis:** ab_eval ist ein Validierungs-Tool, kein Library-Code. Aufruf via `python -m titan.eval.ab_eval --label baseline --determinism-test --runs 10`.

**Aufwand:** 1 h

### Task M7: `init_col.py` (Tools-Subpackage)

**Quelle:** `~/projects/RAG_System/tools/init_col.py`
**Ziel:** `~/projects/titan/src/titan/tools/init_col.py`
**Plus:** `src/titan/tools/__init__.py` (leer)

**Anpassungen:**
- Path-Resolution: `Path(__file__).parent.parent / ".env"` muss neu berechnet werden (durch eine zusätzliche Ordner-Ebene) → besser umstellen auf `from dotenv import load_dotenv; load_dotenv()` (sucht im CWD, was beim CLI-Aufruf vom Repo-Root korrekt ist)
- API-Key-Härtung: laut deiner Titan-Doku-Roadmap soll der API-Key aus `.env` geladen werden statt hardcoded. Wenn der Code aktuell hardcoded ist (`init_col.py` wurde dort als „🔴 Hoch: API-Key in init_col.py aus .env laden, 5 Min" geführt), das gleich mitmigrieren: `os.getenv("QDRANT_API_KEY")` statt Literal.

**Verifikation:**
```bash
uv run python -m titan.tools.init_col   # gegen Test-Qdrant-Instanz
```

**Aufwand:** 30 min (inkl. API-Key-Härtung)

### Task M8: Eval-Fixtures

**Quelle:** `~/projects/RAG_System/eval_cases_v2.json`
**Ziel:** `~/projects/titan/src/titan/eval/fixtures/cases.json`

Andere JSONs (Validation-Outputs) bleiben im alten Repo. Ihre Daten sind historisch und für die Neu-Validierung nicht nötig.

**Aufwand:** 5 min

---

## 4. Audit-Runde (Task M9)

Nach allen acht Migrations-Tasks: konsolidierte Audit-Runde mit Opus.

### 4.1 Checkliste

- [ ] Alle Module importierbar: `uv run python -c "import titan.utils, titan.ingest, titan.search, titan.generate, titan.evaluate, titan.eval.ab_eval, titan.tools.init_col"`
- [ ] `make check` grün
- [ ] Keine `from utils import` oder `from evaluate import` (alte Imports) mehr im Code: `grep -rn "^from \(utils\|evaluate\|ingest\|search\|generate\) import" src/`
- [ ] Keine Epic-5A-Reste: `grep -rni "contextual\|summary[_-]cache" src/` sollte nur Doku-Erwähnungen im DECISIONS finden
- [ ] Keine Hardcoded API-Keys: `grep -rn "api_key\s*=\s*\"" src/` zeigt nur Variablen-Referenzen
- [ ] `.env.example` enthält alle tatsächlich gelesenen Env-Variablen (aus deinem Inventur-Output)
- [ ] `INGEST_BASE_DIR` zeigt nicht mehr auf `/mnt/ai_daten`
- [ ] `GPU_LOCK_PATH`-Default ist `/tmp/bge_m3.lock` (kein Wechsel)
- [ ] Smoke-Tests grün
- [ ] Mypy strict ohne Findings auf allen Modulen (BGE-M3 und Docling haben oft fehlende Typestubs — bei Bedarf `[[tool.mypy.overrides]]` mit `ignore_missing_imports = true` für diese Packages, in `DECISIONS.md` festhalten warum)
- [ ] Coverage nicht eingebrochen (war 75%, sollte vergleichbar bleiben — viel der portierten Logik ist nicht von Unit-Tests abgedeckt, das ist erwartbar)

### 4.2 Manueller End-to-End-Test

Ein PDF, eine Domain, eine Query — wenn das durchläuft, ist die Migration validiert:

```bash
mkdir -p /mnt/f/data/titan-input
cp /pfad/zu/einem/test.pdf /mnt/f/data/titan-input/
uv run python -m titan.tools.init_col
uv run python -m titan.ingest /mnt/f/data/titan-input/test.pdf --domain test
uv run python -m titan.search "irgendeine relevante Frage" --domain test --json | uv run python -m titan.generate
```

Wenn die Antwort am Ende halbwegs sinnvoll erscheint und keine Stack-Traces auftauchen: Migration erfolgreich.

---

## 5. Reihenfolge & Pacing

**Empfohlene Abfolge:**

| Tag | Tasks | Aufwand |
|---|---|---|
| 1 | M0 (Vorbereitung) | 1 h |
| 2 | M1 (utils) + M4 (generate) | 1 h |
| 3 | M3 (search) + M5 (evaluate) | 2–3 h |
| 4 | M2 (ingest, schwerstes Modul) | 2–3 h |
| 5 | M6 (ab_eval) + M7 (init_col) + M8 (fixtures) | 2 h |
| 6 | M9 (Audit + E2E-Test) | 2 h |

Realistisch über 1–2 Wochen verteilt, je nach Abend-Verfügbarkeit.

**Modell-Empfehlung pro Task:**
- M0, M7, M8: lokal/Sonnet, klein und mechanisch
- M1, M3, M4, M5: Sonnet, Standard-Migration
- M2: Sonnet mit präzisem Auftrag (Epic-5A-Removal), bei Schwierigkeiten Opus-Konsultation
- M6: Sonnet
- M9: Opus für Audit, Sonnet/lokal für Fix-Implementation

---

## 6. Stolpersteine

| Risiko | Wahrscheinlichkeit | Mitigation |
|---|---|---|
| torch installiert nur CPU-Wheel | Hoch | Nach M0 sofort CUDA-Verfügbarkeit prüfen, ggf. `[[tool.uv.index]]` für PyTorch-CUDA-Index |
| FlagEmbedding-Typestubs fehlen → mypy meckert | Hoch | `ignore_missing_imports = true` für `flagembedding` in `pyproject.toml` |
| Docling-Typestubs fehlen | Hoch | Analog für `docling` |
| Epic-5A-Removal bricht versehentlich Epic-1-Code (z.B. Domain-Tagging in derselben Funktion) | Mittel | Nach M2: gezielte manuelle Code-Review, nicht nur `make check` |
| `init_col.py` Path-Resolution bricht durch zusätzliche Ebene | Mittel | Auf `load_dotenv()` ohne Pfad umstellen, CWD-relativ |
| `ab_eval.py` lädt Cases via relativer Pfad | Mittel | `importlib.resources` nutzen für Package-interne Daten |
| Hardcoded Pfade (z.B. log-Files, lock-Files) sind nicht mehr passend | Niedrig | Während Module-Migration aufgreifen |

---

## 7. Definition of Done

- [ ] Alle acht Migrations-Tasks abgeschlossen
- [ ] Audit-Runde durchgeführt, alle Findings ≥ Warnung behoben
- [ ] `make check` grün
- [ ] Manueller End-to-End-Test (Abschnitt 4.2) erfolgreich
- [ ] `docs/ai/HANDOFF.md` aktualisiert: „Phase 0b abgeschlossen, bereit für Phase 1 (Service-Layer)"
- [ ] `docs/ai/DECISIONS.md` enthält: Big-Bang-Migration, Epic-5A-Removal, Package-Struktur, torch/CUDA-Setup-Entscheidung
- [ ] Conventional Commits, sauber gemerged in `main` (oder gepushed, falls direkter Workflow)
- [ ] `~/projects/RAG_System/` unverändert als Referenz erhalten
- [ ] CI grün auf GitHub Actions

---

## 8. Was danach kommt

Nach Phase 0b ist die saubere Code-Basis da. Phase 1 (Service-Layer) baut darauf den FastAPI-Daemon. Der ursprüngliche Brain-Plan wird vor Phase-1-Start einmal überarbeitet, um Sonnets sieben Architektur-Befunde zu integrieren:

- `GET /domains` und `POST /find_related` als eigene Tasks
- API-Contract für `indexed: false`
- Update-Semantik als „Upsert-before-Delete" eindeutig
- VRAM-Validierungs-Task (A0) vor den Endpoint-Tasks
- Test-Hook für Debouncing statt `time.sleep`
- `Wants=` statt `Requires=` im systemd, plus Reconnect-Logik

---

*Ende des Plans. Nächster Schritt: Diesen Plan in `~/projects/titan/docs/ai/plans/2026-05-12_phase-0b-migration.md` ablegen, dann M0 mit Sonnet starten.*
