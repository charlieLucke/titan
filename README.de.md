# titan

Ein lokales, hochperformantes RAG-System, das vollständig auf einer einzigen
Workstation läuft — keine Cloud, keine API-Keys, ein einzelner Nutzer. Es
indexiert PDFs und Markdown-Notizen und beantwortet Anfragen in natürlicher
Sprache darüber. Es ist das Retrieval-Backend hinter `brain-mcp`,
`brain-dashboard` und `obsidian-inbox-watcher`.

## Funktionsweise

```
PDF / Markdown ─▶ Parsen (Docling / Frontmatter) ─▶ Late Chunking
              ─▶ BGE-M3 Embedding (dense + sparse + colbert) ─▶ Qdrant
Query ─▶ Zerlegung (Phi-4) ─▶ BGE-M3 ─▶ Qdrant-Suche ─▶ RRF-Fusion
      ─▶ (Semantic Cache) ─▶ Phi-4 Antwortgenerierung
```

- **Late Chunking** bettet den vollständigen Dokumentkontext ein, bevor gesplittet
  wird, und erhält so die Bedeutung über Chunk-Grenzen hinweg.
- **BGE-M3** erzeugt drei Vektoren pro Chunk (dense, sparse, colbert);
  **Reciprocal Rank Fusion** führt die drei Rankings zusammen.
- **Phi-4 via Ollama** zerlegt Anfragen und generiert Antworten — vollständig lokal.
- Die Neuindexierung erfolgt nach dem Prinzip **upsert-before-delete**, verankert an
  einer pro Lauf vergebenen `run_id`, sodass die alten Chunks einer Notiz atomar
  ersetzt statt dupliziert werden.

## Stack

- **Sprache:** Python 3.12+ · **Paketmanager:** uv
- **Embeddings:** BGE-M3 via FlagEmbedding (dense + sparse + colbert)
- **LLM:** Phi-4 via Ollama (lokal, kein API-Key)
- **Vektor-DB:** Qdrant (lokal, gRPC-Port 6334)
- **PDF-Parsing:** Docling
- **API:** FastAPI (Service auf `127.0.0.1:8765`)
- **GPU:** NVIDIA (~16 GB VRAM), serialisiert über ein fcntl-Lock (`/tmp/bge_m3.lock`)

## Voraussetzungen

- Eine NVIDIA-GPU mit einem CUDA-fähigen `torch`-Build (das standardmäßige
  `uv`-Wheel ist CPU-only — prüfen mit
  `uv run python -c "import torch; print(torch.cuda.is_available())"`).
- Eine laufende **Qdrant**-Instanz (lokal, gRPC `6334`) und **Ollama**, das Phi-4
  bereitstellt.
- WSL2-Nutzer: systemd in `/etc/wsl.conf` aktivieren (`[boot]\nsystemd=true`).

## Einrichtung

Erfordert [uv](https://docs.astral.sh/uv/) und Python 3.12+.

```bash
make install                       # Abhängigkeiten + pre-commit-Hooks
uv run python -m titan.tools.init_col   # Qdrant-Collection anlegen
```

`.env.example` nach `.env` kopieren und `QDRANT_*`, `COLLECTION_NAME`,
`OLLAMA_URL`/`OLLAMA_MODEL`, `INGEST_BASE_DIR`, `VAULT_ROOT` sowie die
Cache-Einstellungen nach Bedarf anpassen. `VAULT_ROOT` / `INGEST_BASE_DIR` können
beliebige Verzeichnisse sein — die Defaults in `.env.example` (`/mnt/f/...`) sind
**Beispiele aus dem WSL2-Setup des Autors** (wo `/mnt/f` das Windows-Laufwerk `F:`
ist); auf einem nativen Linux-System Pfade unterhalb deines Home-Verzeichnisses
verwenden.

## Service starten

```bash
uv run python -m titan service            # API auf Port 8765 starten
uv run python -m titan service --port 9000  # alternativer Port
curl localhost:8765/health                # Health-Check
```

Im Deployment läuft er als systemd-User-Service (`titan-service`); siehe `deploy/`.

### HTTP-API

| Methode & Pfad | Zweck |
|---|---|
| `GET /health` | Service-Status: BGE-M3 geladen, Qdrant erreichbar, VRAM-Auslastung. |
| `POST /search` | Hybride Suche: Query-Zerlegung → BGE-M3 → RRF → Semantic Cache. |
| `POST /ingest/file` | Eine Markdown-Datei indexieren (upsert-before-delete via `run_id`). |
| `GET /domains` | Alle Domains im Index mit Chunk-Anzahl. |
| `GET /notes` | Alle indexierten Notizen, gruppiert nach Quellpfad, mit Domain + Chunk-Anzahl. |
| `POST /find_related` | Notizen, die einer gegebenen Notiz semantisch ähnlich sind. |
| `DELETE /chunks` | Alle Chunks einer Datei entfernen (nach `source_path`). |

## Entwicklung

```bash
make test       # Tests mit Coverage ausführen
make test-fast  # langsame + Integrationstests überspringen
make check      # vollständiges Quality-Gate: Lint + Typen + Tests
make format     # Style-Probleme automatisch beheben
make help       # alle verfügbaren Befehle auflisten

uv run pytest tests/integration/ -m integration -v   # Integrationstests (benötigen einen laufenden Service)
```

## Projektstruktur

```
src/titan/
├── main.py            # Dispatcher: python -m titan service | help
├── ingest.py          # Parsen → Late Chunking → BGE-M3 Embedding → Qdrant-Upsert
├── search.py          # Query-Zerlegung, BGE-M3, RRF, Semantic Cache
├── generate.py        # Phi-4 Antwortgenerierung aus Chunk-Kontext
├── evaluate.py        # LLM-as-Judge-Evaluation
├── service/           # FastAPI-App, State-Singleton, Schemas, Routes
├── eval/              # A/B-Evaluations-Tool + Fixtures
└── tools/init_col.py  # Qdrant-Collection-Setup
deploy/                # systemd-Service-Unit + Installationsanleitung
tests/{titan,integration}/   # Unit- + Service-Integrationstests
docs/ai/               # Kontext und Pläne für KI-Agenten
.github/workflows/     # CI-Konfiguration
```

## Tooling

| Tool         | Zweck                                |
|--------------|--------------------------------------|
| **uv**       | Paketmanager + Python-Installer      |
| **ruff**     | Linter + Formatter                   |
| **mypy**     | Statischer Typprüfer (Strict Mode)   |
| **pytest**   | Test-Runner mit Coverage             |
| **pre-commit** | Git-Hook-Runner                    |

Alle Tools laufen bei jedem Push in der CI.

## Arbeiten mit KI-Tools

Dieses Projekt nutzt einen strukturierten Workflow für KI-gestütztes Coding. Jeder
KI-Agent (Claude, Gemini, Cursor, Aider usw.) sollte zuerst `CLAUDE.md` lesen — sie
ist als `AGENTS.md` und `GEMINI.md` für Tool-Kompatibilität gespiegelt.

Wichtige Dateien für den KI-Kontext:

- `docs/ai/CONTEXT.md` — Stack, Konventionen, Glossar
- `docs/ai/CURRENT_TASK.md` — woran aktiv gearbeitet wird
- `docs/ai/HANDOFF.md` — Zustand für die Fortsetzung von Sitzungen über Modellwechsel hinweg
- `docs/ai/DECISIONS.md` — Protokoll der Architekturentscheidungen
- `docs/ai/plans/` — gespeicherte Pläne, erstellt von einem Planungsmodell (z. B. Opus)

Der vorgesehene Workflow:

1. Architektur- und Feature-Pläne werden von einem starken Reasoning-Modell erstellt und unter `docs/ai/plans/` gespeichert
2. Ein schnelleres/günstigeres Modell implementiert die Pläne
3. Beide referenzieren den gemeinsamen Kontext in `docs/ai/`
4. Der Zustand wird über `HANDOFF.md` über Sitzungen hinweg bewahrt

## Lizenz

Noch offen (TBD)
