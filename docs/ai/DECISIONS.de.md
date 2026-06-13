# Entscheidungs-Log

> Architecture Decision Records. Nur anhängen. Ein Eintrag pro signifikanter Entscheidung.
> Das verhindert, dieselben Fragen in jeder neuen KI-Sitzung neu auszufechten.

## Format

```
## JJJJ-MM-TT: Kurztitel
**Entscheidung:** Was wir entschieden haben
**Begründung:** Warum
**Erwogene Alternativen:** Was wir verworfen haben und warum
**Konsequenzen:** Was das für die Zukunft bedeutet
```

---

## Anfangsentscheidungen (Template-Defaults)

## 2026-XX-XX: uv als Paketmanager verwenden
**Entscheidung:** uv (statt pip+venv, poetry, pdm).
**Begründung:** 10–100× schneller als pip; vereintes Werkzeug, das pip, pip-tools, virtualenv, pyenv ersetzt; Lockfile standardmäßig; getragen von Astral (dasselbe Team wie ruff).
**Erwogene Alternativen:** Poetry (langsamer, mehr Konfigurationsaufwand, getrennt vom venv-Tooling). pip+venv (kein Lockfile by default, manueller Workflow).
**Konsequenzen:** Alle Dependency-Operationen laufen über `uv add` / `uv remove` / `uv sync`. Niemals pyproject.toml-Abhängigkeiten manuell bearbeiten.

## 2026-XX-XX: ruff für Lint und Format verwenden
**Entscheidung:** ruff ersetzt black + flake8 + isort + pyupgrade.
**Begründung:** Einzelwerkzeug, deutlich schneller, konsistente Konfiguration, aktiv gepflegt.
**Konsequenzen:** black, flake8 oder isort nicht als separate Werkzeuge ergänzen.

## 2026-XX-XX: Mypy Strict Mode
**Entscheidung:** Mypy im Strict Mode ab Tag eins.
**Begründung:** Striktheit ist von Anfang an viel leichter durchzusetzen als nachzurüsten. Fängt ganze Bug-Kategorien zur Schreibzeit ab.
**Konsequenzen:** Jede Funktion braucht vollständige Type-Hints. `# type: ignore` erfordert einen Inline-Kommentar mit Begründung.

---

## 2026-05-12: Big-Bang-Migration von RAG_System, Epic 5A v1.0 nicht portiert
**Entscheidung:** Code aus `~/projects/RAG_System/execution/` wird 1:1 nach `src/titan/` portiert.
Epic 5A (Contextual Retrieval via Phi-4-Zusammenfassungen) wird beim Port bewusst entfernt.
**Begründung:** Epic 5A v1.0 hatte zu hohe Latenz und zu geringen Retrieval-Gewinn für den Aufwand.
Die Reimplementierung (Epic 5A v2.0) soll sauber auf der neuen Codebasis starten, nicht auf altem Code.
**Erwogene Alternativen:** Ein selektiver Port mit Feature-Flag für 5A — verworfen, da ein totes
Feature im Code Verwirrung stiftet und die mypy-Hygiene erschwert.
**Konsequenzen:** `~/projects/RAG_System/` bleibt unverändert als Referenz. Wer 5A-Code braucht,
liest ihn dort. Alle Flags `--contextual`, `--clear-summary-cache`, `--summary-cache-stats` entfallen.

## 2026-05-12: Flache Paketstruktur unter src/titan/
**Entscheidung:** Flache Struktur: `src/titan/utils.py`, `src/titan/ingest.py` usw. statt
`src/titan/rag_system/execution/utils.py` (das alte Layout).
**Begründung:** Der alte Präfix `rag_system/execution/` war ein Workaround ohne Paket-Namespace.
Mit `titan` als Paketname ist die zusätzliche Verschachtelung reiner Overhead.
**Konsequenzen:** Import-Pfade lauten `from titan.utils import ...` — kurz und eindeutig.

## 2026-05-12: mypy ignore_missing_imports für ML-Bibliotheken
**Entscheidung:** `ignore_missing_imports = true` in `[[tool.mypy.overrides]]` für `flagembedding`,
`docling` und ggf. `torch` (je nach Stub-Verfügbarkeit).
**Begründung:** Diese Bibliotheken haben keine Type-Stubs. mypy strict würde sonst bei jedem Import scheitern.
Das ist kein echtes Typproblem, nur fehlende Drittanbieter-Stubs.
**Konsequenzen:** Diese Module sind von der Typprüfung ausgenommen — ein akzeptabler Kompromiss, solange
keine Community-Stubs verfügbar sind.

## 2026-05-12: ColBERT-Vektordimension 1024 (FlagEmbedding ≥ 1.3)
**Entscheidung:** Die Qdrant-Collection wird mit `colbert.size=1024` angelegt.
**Begründung:** FlagEmbedding änderte die ColBERT-Ausgabedimension ab Version 1.3 von 128 auf 1024.
Die Collection muss exakt zur installierten Bibliotheksversion passen — ein falscher Wert führt zu Upsert-
Fehlern oder unbrauchbaren Retrieval-Ergebnissen ohne offensichtliche Fehlermeldung.
**Erwogene Alternativen:** 128 (Vorgängerversion) — verworfen, da ab FE 1.3 effektiv falsch.
**Konsequenzen:** `flagembedding>=1.3` ist eine implizite Mindestanforderung (pyproject.toml deklariert
`>=1.4.0`). Bei einem FlagEmbedding-Upgrade: die Release Notes auf ColBERT-Dim-Änderungen prüfen. Bei einem
Downgrade unter 1.3: die Collection mit `init_col.py --recreate` und `colbert.size=128` neu anlegen.

## 2026-05-12: GPU_LOCK_PATH-Default geändert auf /tmp/bge_m3.lock
**Entscheidung:** Die GPU-Lock-Datei liegt unter `/tmp/bge_m3.lock` (war `/tmp/rag_gpu.lock` im alten
RAG_System-Repo).
**Begründung:** Der neue Lock-Pfad spiegelt den neuen Paketnamen wider und vermeidet Kollisionen mit noch laufenden
Prozessen aus dem alten RAG_System-Setup.
**Konsequenzen:** Wer vom alten RAG_System migriert: einmal `rm -f /tmp/rag_gpu.lock` ausführen, damit eine
veraltete Lock-Datei keinen Prozessstart blockiert. Der neue Pfad ist via `.env` überschreibbar
(`GPU_LOCK_PATH=/tmp/bge_m3.lock`).

---

## 2026-05-12: run_id-Payload-Konvention für upsert-before-delete
**Entscheidung:** Jeder Ingest-Lauf setzt eine UUID (`run_id`) im Chunk-Payload.
Beim Re-Ingest: neue Chunks mit neuer `run_id` einfügen, dann alte Chunks mit
abweichender `run_id` für denselben `source_path` löschen.
**Begründung:** Verhindert ein Downtime-Fenster: solange Schritt 2 (Upsert) läuft, sind die alten Chunks
weiterhin durchsuchbar. Stürzt Schritt 2 ab, bleibt der alte Zustand erhalten. Duplikat-Chunks (alter + neuer Lauf) sind
harmlos — RRF gewichtet sie gleich, und der nächste Lauf räumt auf.
**Erwogene Alternativen:** Delete-before-insert (führt zu Lücken während der Neuindexierung).
**Konsequenzen:** `run_id` ist ein Pflicht-Payload-Feld für alle Ingest-Aufrufe über den Service.
CLI-Ingest (`python -m titan.ingest`) nutzt diese Konvention noch nicht — das ist akzeptabel, da der
CLI-Pfad typischerweise für den ersten Ingest genutzt wird.

## 2026-05-12: indexed:false-Semantik — der Service entscheidet, der Client vertraut
**Entscheidung:** Das Frontmatter-Feld `indexed: false` wird ausschließlich vom Service ausgewertet.
Der Watcher (brain-mcp Phase 2) sendet den Pfad, ohne Frontmatter auszuwerten.
**Begründung:** Eine einzige Stelle, die die Semantik kennt → kein Sync-Problem, wenn sich die Semantik ändert.
**Konsequenzen:** `POST /ingest/file` mit `indexed:false`: löscht bestehende Chunks, gibt
`skipped_reason: "indexed:false"` zurück, legt keine neuen Chunks an.

## 2026-05-12: Aggressive Cache-Invalidierung (domain-granular)
**Entscheidung:** Beim Re-Ingest einer Notiz werden ALLE Cache-Einträge ihrer Domain gelöscht.
**Begründung:** Die einfachste korrekte Implementierung. Vermeidet einen Cache-Hit auf den „alten" Zustand ohne
Chunk→Cache-Dependency-Tracking.
**Erwogene Alternativen:** Feingranulare Invalidierung nur für Einträge, die die geänderte
Notiz enthielten — braucht Cache→Chunk-Tracking, das Epic 5B nicht implementiert.
**Konsequenzen:** Bei häufigen Edits in einer Domain kann die Cache-Hit-Rate sinken. Wird das messbar
problematisch: feineres Tracking ergänzen.

## 2026-05-13: Cache-Invalidierung in titan.search, nicht in routes

**Entscheidung:** `invalidate_domain_cache(qdrant_client, domain)` lebt in `titan.search`, nicht in
`titan.service.routes`.
**Begründung:** Audit T-MED-2: die Funktion braucht nur `qdrant_client` und die globalen Cache-Konstanten
(`CACHE_COLLECTION_NAME`, `CACHE_ENABLED`) — beide aus `search.py` erreichbar. In `routes` war sie
wegen `state.qdrant_client` gelandet, aber das ist kein gültiges Argument: `search()` nimmt
den Client ebenfalls als Parameter. So wird die Funktion ohne FastAPI testbar.
**Konsequenzen:** Routes importieren `invalidate_domain_cache` aus `titan.search`.

## 2026-05-13: Der Service bindet nur an 127.0.0.1 (kein Remote-Zugriff)

**Entscheidung:** uvicorn läuft auf `host="127.0.0.1"`, ohne externe Konfigurierbarkeit.
**Begründung:** Der Service ist für Single-User-Betrieb auf der lokalen Maschine ausgelegt. Eine Exposition auf
öffentlichen IPs würde Auth, Rate-Limiting und TLS erfordern.
Audit-Bewertung (Security-Tabelle): ✅ akzeptables Härtungsniveau für ein Single-User-Setup.
**Konsequenzen:** Wer Mehrbenutzerbetrieb oder Remote braucht: einen Reverse-Proxy mit Auth davorsetzen, dann
`VAULT_ROOT` und alle Pfadprüfungen überprüfen.

## 2026-05-12: source_path + source als Payload-Aliase
**Entscheidung:** Neue Ingest-Uploads über den Service setzen sowohl `source_path` als auch `source` im Qdrant-
Payload. Alte CLI-Ingests haben nur `source`.
**Begründung:** Abwärtskompatibilität: bestehende Chunks im Index bleiben nutzbar. Das Service-Schema
(`Chunk.source_path`) ist der neue Standard.
**Konsequenzen:** Suchergebnisse liefern beide Schlüssel zurück. Der Routes-Code nutzt `source_path`; ist er leer, wurde
die Notiz über den alten CLI-Pfad ingestet.

## 2026-05-16: Setup-Annahme — WSL2 Mirrored Networking & Docker Desktop
**Entscheidung:** Das Setup setzt voraus, dass Docker Desktop (für Container) läuft, WSL2 Mirrored
Networking aktiv ist und `QDRANT_HOST=localhost` konfiguriert ist.
**Begründung:** Undokumentierte Netzwerk-Setups führen über die Zeit zwangsläufig zu langwierigem Debugging der Qdrant-
Verbindung. Da Titan den Qdrant-Container über `localhost` anspricht, muss Mirrored Networking
in WSL2 aktiviert sein, damit das Port-Mapping funktioniert.
**Konsequenzen:** Bei einem `ConnectError` zu Qdrant immer zuerst prüfen: läuft Docker Desktop? Ist
Mirrored Networking in `.wslconfig` aktiv?

## 2026-05-17: GET /notes — alle indexierten Notizen auflisten

**Entscheidung:** Ein neuer Endpunkt `GET /notes` listet alle indexierten Notizen auf, gruppiert nach `source_path`, mit
Domain und Chunk-Anzahl. Er scrollt die gesamte Collection (Pagination zu 256) und aggregiert in-memory.
**Begründung:** brain-mcp braucht einen Überblick über alle indexierten Dateien für sein neues `list_notes`-Tool.
`GET /domains` liefert nur Domain-Zähler, nicht einzelne Notizen. Ein vollständiger Scroll ist für einen
persönlichen Vault unkritisch (ein paar hundert/tausend Chunks).
**Konsequenzen:** Neue Schemas `NoteInfo` / `NotesResponse`. Für sehr große Collections wäre ein gecachter Zähler
(wie `domain_counts`) effizienter — bei Bedarf ergänzen.

## 2026-05-17: Integrationstests repariert (Versions-Drift + Test-Isolation)

**Entscheidung:** `tests/integration/test_service.py` auf den aktuellen Stand gebracht.
**Begründung:** Die Integrations-Suite war komplett rot und nicht mehr lauffähig — vier überlappende Defekte:
1. `VectorsConfig(root=...)` — in qdrant-client 1.18 ist `VectorsConfig` ein `typing.Union`-Alias, nicht
   instanziierbar. Fix: `vectors_config` direkt als Dict übergeben (wie der Produktivcode in
   `init_col.py`).
2. Qdrant verlangt jetzt einen API-Key — die `qdrant_client`-Fixture gab keinen mit. Fix: `load_dotenv()`
   + `api_key=os.getenv("QDRANT_API_KEY")`.
3. `TestClient(app)` als Context-Manager führte den `lifespan` aus, der BGE-M3 und Qdrant neu lädt und
   den vorinjizierten Test-State überschrieb. Fix: ohne `with`.
4. `test_health_degraded_without_qdrant` mutierte das `state`-Singleton, ohne es wiederherzustellen — alle
   folgenden Tests sahen `None` (503). Fix: Save/Restore.
**Konsequenzen:** Die Suite läuft wieder (19/20 grün). Ein gelegentlicher grpc-Fehler beim Collection-
Teardown des letzten Tests bleibt — separate Teardown-Robustheit, offen. Integrationstests müssen bei
gestopptem `titan-service` laufen (BGE-M3 GPU-Lock).
