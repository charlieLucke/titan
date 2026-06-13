# Audit-Bericht: Task A13 (Phase 1 Service-Layer)

**Datum:** 2026-05-13
**Status:** ✅ Bestanden

Dieser Bericht dokumentiert die Ergebnisse der Audit-Runde (Task A13) gemäß dem Plan `plan_titan_brain_v2.md` (Abschnitt 3.15). Alle Punkte wurden anhand der Codebase überprüft.

## Checkliste & Ergebnisse

- [x] **Pfad-Traversal-Schutz auf `/ingest/file`, `/find_related`, `DELETE /chunks` aktiv?**
  **Ja.** Die Funktion `_path_check` in `routes.py` löst die Pfade mittels `Path.resolve()` auf und prüft über `is_relative_to(VAULT_ROOT)`, ob sie sich im erlaubten Verzeichnis befinden.

- [x] **Pydantic-Limits sinnvoll (`max_length`, `ge/le`, `min_length`)?**
  **Ja.** In `schemas.py` sind sinnvolle Begrenzungen gesetzt: `SearchRequest.query` hat `min_length=1` und `max_length=10_000`. Für `top_k` in Suchen und Related-Finds gibt es `ge=1` und `le=50` bzw. `le=20`.

- [x] **`sanitize()` auf alle Frontmatter-Werte vor Payload-Insert?**
  **Ja.** In `ingest.py` (`read_markdown`) wird das einzige ins Payload geschriebene externe Metadaten-Feld (`domain`) durch `sanitize(str(raw_domain).strip())` bereinigt. Es gelangen keine weiteren ungereinigten Frontmatter-Daten in die Qdrant-Datenbank.

- [x] **BGE-M3 wird im `lifespan` geladen UND freigegeben?**
  **Ja.** In `app.py` wird `state.bge_model = _load_bge_m3()` beim Startup aufgerufen. Beim Herunterfahren wird `del state.bge_model` gefolgt von `torch.cuda.empty_cache()` ausgeführt, um VRAM wieder vollständig freizugeben.

- [x] **CUDA-OOM-Handling mit `try/finally` und `empty_cache()`?**
  **Ja.** Sowohl in der Late-Chunking-Phase in `ingest.py` (`_embed_window`) als auch beim Shutdown der App wird strikt mit `try/finally` Blöcken gearbeitet und im `finally`-Block `torch.cuda.empty_cache()` aufgerufen, um Fragmentierung und Out-Of-Memory-Fehler abzufangen.

- [x] **Service akzeptiert nur 127.0.0.1, nicht extern?**
  **Ja.** `uvicorn.run` in `app.py` bindet explizit an `host="127.0.0.1"`, wodurch der Dienst nicht aus dem lokalen Netzwerk oder Internet erreichbar ist.

- [x] **Logs maskieren API-Keys und Datei-Inhalte?**
  **Ja.** API-Keys werden in `ingest.py` maskiert (`QDRANT_API_KEY[:4] + "***"`). Lange Suchanfragen werden in `search.py` auf maximal 80 Zeichen gekürzt geloggt. Datei-Inhalte tauchen im Logging nicht auf.

- [x] **Update-Semantik ist Upsert-before-Delete (mit `run_id`)?**
  **Ja.** Implementiert im Endpoint `/ingest/file`. Es wird eine `run_id` (UUID) generiert, die neuen Chunks werden in Qdrant eingefügt (`upsert`), und anschließend werden über einen Qdrant Filter alte Chunks mit demselben Pfad gelöscht (`must_not: run_id`). Dies verhindert Lücken im Index falls der Ingest mittendrin fehlschlägt.

- [x] **Embedding-Dimensions-Check im Lifespan aktiv (ColBERT-Mismatch wird erkannt)?**
  **Ja.** Die Funktion `_validate_embedding_dimension` in `app.py` generiert einen Test-Vektor und vergleicht dessen Dimension mit der Qdrant Collection Configuration. Bei Mismatch bricht der Startup sofort und sauber ab (`RuntimeError`).

- [x] **`indexed:false`-Übergang räumt alte Chunks weg?**
  **Ja.** In `ingest.py` wird `indexed: false` aus dem YAML Frontmatter als `_skip: True` weitergereicht. In `routes.py` wird daraufhin das Löschen der Chunks in Qdrant initiiert und der Domain-Counter entsprechend reduziert. Es werden keine neuen Punkte hochgeladen.

- [x] **Cache-Invalidierung bei Re-Ingest implementiert?**
  **Ja.** Der `/ingest/file`-Endpoint ruft nach erfolgreichem Upsert die Funktion `_invalidate_cache_for_domain(domain)` auf, die alle gecachten Suchanfragen dieser speziellen Domain verwirft.

- [x] **Domain-Counter wird inkrementell aktualisiert UND beim Restart neu initialisiert?**
  **Ja.** Der Counter in `state.domain_counts` wird beim Start über einen Qdrant `scroll()` über alle Metadaten komplett gefüllt (`app.py`). Bei jedem Ingest (und Löschen) wird der Zähler für die betroffene Domain in `routes.py` iterativ um das Delta (gelöschte vs. neue Chunks) angepasst.

- [x] **PDFs werden im Service abgelehnt, klare Fehlermeldung?**
  **Ja.** Im `/ingest/file`-Endpoint wird eine `.pdf`-Endung sofort abgefangen und eine 400er `HTTPException` ("PDFs via CLI ...") zurückgegeben.

- [x] **`python -m titan.search "..."` als CLI-Pfad funktioniert weiterhin (Smoke-Test)?**
  **Ja.** Die CLI-Schnittstelle im Modul und in `main.py` ist intakt, die Ausführung des CLIs funktioniert problemlos weiter. Der GPU-Lock wird bei manuellem Aufruf sauber vom Skript erworben und freigegeben.

## Fazit
Die Phase 1 (Service-Layer) ist nach der Code-Überprüfung aus Security- und Architekturperspektive **vollständig und sauber implementiert**. Es gibt keine Beanstandungen, die einen Fix erfordern. Der Code ist bereit für die End-to-End Tests und die endgültige Bereitstellung.
