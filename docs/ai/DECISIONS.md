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

## 2026-09-05: Die zwei Vault-Berichte über HTTP ausliefern
**Entscheidung:** `GET /reports/graph_check` und `GET /reports/offene_punkte` neben den
bestehenden Endpunkten. Sie liefern das, was `titan.tools.graph_check` und
`titan.tools.offene_punkte` ohnehin schon berechnen. Aus `_sortiert` wurde `sortiert`:
die Funktion wird inzwischen von außerhalb ihres Moduls aufgerufen, der Unterstrich war
also falsch.
**Begründung:** Phase 4b von `plan-zentrales-dashboard` will die beiden als Bericht im
Browser statt als Befehl, den sich jemand merken muss. Sie wurden in brain-mcp vermutet;
sie liegen hier, und titan liefert bereits HTTP aus — also zwei dünne Routen statt eines
neuen Transports, eines neuen Tokens und eines neuen Clients an anderer Stelle.
**Konsequenzen:** `graph_check_report` liest das Repository direkt, statt
`graph_check.sammle` aufzurufen, das `GET /notes` über HTTP holt — innerhalb des Dienstes
wäre das der Prozess, der sich selbst über das Netz befragt. `offene_punkte_report` liest
den Vault bewusst weiterhin von der Platte und nicht aus dem Index: ein offener Punkt kann
in einer Notiz stehen, die gerade nicht indexiert ist, und genau die fiele sonst heraus.
Beide sind unauthentifiziert wie der Rest von titans API, die auf Loopback bindet — ändert
sich das je, geben sie Vault-Inhalt preis und brauchen dieselbe Behandlung wie alles andere.

## 2026-10-24: Vault-Eval mit 196 Fällen, und extraktive Kompression als Option von `/search`
**Entscheidung:**
1. Neues Werkzeug `titan.eval.vault_eval` statt Umbau von `ab_eval` (Abweichung vom Plan
   `2026-10-03_eval-vault-retrieval.md`, Abschnitt 4). `ab_eval` lädt BGE-M3 selbst und
   braucht dafür den GPU-Lock, misst also nie gegen den laufenden Dienst. Die neue Messung
   geht über `POST /search` mit genau den Parametern des Connectors (ohne Zerlegung, ohne
   Cache).
2. Das Goldset liegt **im Vault** unter `.eval/goldset.jsonl`, nicht unter
   `src/titan/eval/fixtures/` wie im Plan. Dieses Repo ist öffentlich, und jeder Fall trägt
   ein wörtliches Zitat aus dem Vault. Versteckte Ordner indexiert der Watcher nicht.
3. `SearchRequest` bekommt `kompakt` (Default aus) und `kompakt_zeichen` (Default 600).
   `titan.kompakt` kürzt jeden Treffer auf die Zeilen, deren BGE-M3-Vektor der Frage am
   nächsten ist. Nichts wird umformuliert, Lücken stehen als `[…]` da.
**Begründung:** Gemessen am 24.10.2026 mit 196 Fällen (165 mit Sollstelle, 16 negativ,
15 geschützt), Stellen-Recall = der Beleg steht wörtlich im Treffertext:

| Variante | Stellen-Recall | Notiz-Recall | Tokens (Median) |
|---|---|---|---|
| top_k 10, voll | 0,50 | 0,79 | 4 336 |
| top_k 5, voll (Connector-Default seit 24.10.) | 0,38 | 0,74 | 2 110 |
| top_k 10, kompakt 800 | 0,47 | 0,79 | 2 335 |
| top_k 5, kompakt 800 | 0,36 | 0,74 | 1 170 |
| top_k 10, kompakt 400 | 0,38 | 0,79 | 1 411 |

Zehn kompakte Treffer kosten so viel wie fünf volle und finden 9 Punkte mehr. Eine
Zusammenfassung durch ein lokales Modell kam nicht in Frage: neuer Text kann falsch sein,
ohne dass der Leser es merkt.
**Konsequenzen:** *(Überholt am selben Tag: die Ursache war Late Chunking, nicht das
Ranking, siehe nächster Eintrag.)* Das größere Problem ist das Ranking, nicht die Länge. Die richtige Notiz
steht in 52 % der Fälle auf Platz 1, die richtige *Stelle* nur in 9 %. Unter 20 Kandidaten
ist die Notiz in 87 % der Fälle. Nächster Hebel laut Plan: ein Cross-Encoder hinter
ColBERT. Kompression kostet rund 300 ms je Suche (p50 67 → 362 ms). Leckage in allen
Läufen 0. „Höchstens zwei Treffer je Notiz“ (Hebel 2) verschlechtert: 0,38 → 0,32 bei
top_k 5, weil die richtige Stelle oft der zweite oder dritte Abschnitt derselben Notiz
ist; verworfen. Der Wechsel des Connectors auf top_k 5 hat 12 Punkte Stellen-Recall
gekostet; mit `kompakt` ließe er sich bei gleichen Kosten zurückholen. Die neuen Felder ändern den Contract `titan.openapi.yaml`, additiv; ob brain-mcp
sie nutzt, ist eine Entscheidung auf Workspace-Ebene und noch nicht getroffen.

## 2026-10-24: Late Chunking aus, jeder Chunk einzeln mit Kopfzeile eingebettet
**Entscheidung:** `settings.late_chunking = False`. `ingest.embed_chunks` bettet jeden
Chunk für sich ein, mit `Notiztitel › Abschnitt` davor (`mit_kopfzeile`); gespeichert und
angezeigt wird weiter nur der Chunk-Text. Late Chunking bleibt per Einstellung schaltbar.
Der Live-Index ist am selben Tag mit `force` neu eingelesen (55 Notizen, 770 Chunks).
**Begründung:** Experiment mit sechs Test-Collections, gleiche 165 Fragen mit Sollstelle.
Die ersten vier haben exakt dieselben 770 Chunk-Grenzen wie der Live-Index, nur die
Einbettung unterscheidet sich:

| Variante | Stelle @1 | @5 | @10 | @50 | mit Reranker @5 |
|---|---|---|---|---|---|
| Late Chunking (wie bisher) | 0,10 | 0,42 | 0,55 | 0,81 | 0,71 |
| nur Dense einzeln (CLS), Sparse/ColBERT late | 0,09 | 0,42 | 0,53 | 0,82 | 0,72 |
| einzeln | 0,41 | 0,71 | 0,84 | 0,95 | 0,78 |
| **einzeln + Kopfzeile** | **0,50** | **0,82** | **0,87** | 0,96 | 0,78 |
| kleinere Chunks (bis `###`, 1 200 Zeichen) + Kopfzeile | 0,50 | 0,80 | 0,85 | 0,95 | 0,77 |
| kleinere Chunks, late | 0,12 | 0,30 | 0,41 | 0,67 | 0,58 |

- Das Fenster war 7 800 Tokens groß, also meist die ganze Notiz. Jeder Abschnitt trug
  deren Kontext, die Abschnitte einer Notiz glichen sich, und die Suche fand das Thema,
  nicht die Stelle (Notiz @1 52 %, Stelle @1 9 %).
- Der Verdacht, das Mean-Pooling des Dense-Vektors passe nicht zum CLS-Pooling der Frage,
  ist widerlegt (Zeile 2). Den Ausschlag geben ColBERT und Sparse, die das Endranking
  machen.
- **Kein Cross-Encoder.** `bge-reranker-v2-m3` über die Top 50 hebt nur den kaputten Index
  (0,42 → 0,71) und senkt den guten (0,82 → 0,78), bei rund 570 ms je Frage.
- **Keine kleineren Chunks.** Kein Gewinn, 50 % mehr Punkte.

Nachgemessen am Live-Dienst nach dem Reingest (`vault_eval --label einzeln_kopf`, 196
Fälle): top_k 5 Stelle 0,38 → 0,81, Stelle @1 0,09 → 0,50, Notiz @5 0,74 → 0,90,
Leckage 0, p50 115 ms.
**Konsequenzen:**
- Für die Zukunft: Late Chunking hilft, wenn ein Abschnitt ohne seine Nachbarn nicht zu
  verstehen ist (Pronomen, „wie oben"). Die Vault-Notizen sind so geschrieben, dass jeder
  Abschnitt für sich steht; den Rest trägt die Kopfzeile. Für lange PDFs ohne gute
  Überschriften kann es wieder anders aussehen — vor dem Umschalten mit `vault_eval`
  messen, nicht nach Gefühl.
- Schwächster Fragetyp ist jetzt „abhängigkeit" (Stelle @5 0,40): Fragen über mehrere
  verlinkte Notizen. Das ist der Link-Graph-Hebel aus dem Eval-Plan, Abschnitt 6.
- Mit 0,81 bei top_k 5 und 0,87 bei top_k 10 trägt der Connector-Default 5 wieder; die
  Kompression (`kompakt`) bleibt als Option, ist aber nicht mehr nötig, um Recall
  zurückzuholen.
- Das Experiment-Skript lag im Scratchpad und ist nicht im Repo; die Tabelle oben ist
  das Ergebnis.

## 2026-10-24: Abhängigkeitsfragen — Link-Graph und Zerlegung im Ranking verworfen
**Entscheidung:** Weder ein Link-Bonus noch Query-Zerlegung kommt in die Suche.
Abhängigkeitsfragen („was hängt an X") löst der Aufrufer in zwei Schritten: X finden,
dann die Rückverweise lesen. brain-mcp gibt sie dafür seit heute in `find_related` mit
aus (Namen, je Richtung), und der Claude-Code-Skill `vault-recherche` plant genau diesen
zweiten Schritt.
**Begründung:** Gemessen gegen den Index mit Einzel-Embedding (Stelle @5 gesamt 0,81,
„abhängigkeit" 0,40):
- **Link-Bonus** auf die 50 Kandidaten, wenn ihre Notiz mit den ersten ein bis drei
  Treffer-Notizen verlinkt ist, gedämpft nach Linkzahl; dazu Varianten mit festen
  Plätzen für Nachbarn. Bestes Ergebnis „abhängigkeit" 0,47 bei gesamt 0,80, alle
  anderen verloren. Der Vault ist zu dicht verlinkt: Median 12 Nachbarn bei 57 Notizen,
  „ist verlinkt" trennt kaum.
- **Zerlegung per Phi-4** (`use_decompose`, `vault_eval --zerlegen`): „abhängigkeit"
  0,40 → 0,47, gesamt 0,81 → 0,70, p50 115 ms → 5,9 s. Phi-4 zerlegt nicht in Schritte,
  sondern in Stichwörter („Studium", „aktuell", „was betrifft").
**Konsequenzen:** Der Connector bleibt bei `use_decompose=False`. Ein stärkeres lokales
Modell mit besserem Prompt wäre ein eigener Versuch; er lohnt sich am ehesten für
`/ask`, wo kein Claude die Schritte plant. Feintuning von BGE-M3 ist bei 770 Chunks nicht
sinnvoll: Die Trainingsfragen kämen aus derselben Quelle wie das Goldset, die Messung
wäre danach wertlos.
