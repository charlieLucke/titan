# Ideen

> Out-of-Scope-Ideen, die während der Arbeit festgehalten werden, um sie später wieder aufzugreifen.
> Nichts hier ist verbindlich. Das ist ein Parkplatz.

## Format
- [ ] **JJJJ-MM-TT:** Ideenbeschreibung. Warum sie wichtig ist. Grobe Aufwandsschätzung.

---

## Ausstehend

- [ ] **2026-06-15: docs/ai NICHT in die Haupt-Collection `mein_wissen` ingesten (Entscheidung/Guard).**
      Verlockend, aber es verwässert den Wissens-Index: docs/ai ist dichtes
      Engineering-Gerüst (CURRENT_TASK, HANDOFF, IDEAS-TODOs, plans mit
      Implementierer-Prompts, CONTRACTS) — als Chunks tauchten Plan-/Meta-Fragmente bei
      Wissensfragen als „Treffer" auf (dasselbe Problem wie die obsidian-IDEA „nur die
      Summary indexieren"), es hat kein `domain:`-Frontmatter, ändert sich ständig
      (Cache-Invalidierung, Staleness) und liegt außerhalb von `VAULT_ROOT`. Für
      „Projektstand/was-als-nächstes" ist ohnehin ein **direkter Datei-Read** das
      richtige Muster (autoritativ, aktuell), nicht semantisches top-k — siehe
      workspace-mcp-IDEA `project_status` und die Chatbot-IDEA in homebase. Falls
      docs/ai je *semantisch* durchsuchbar sein soll: **eigene Collection/Namespace**
      (vgl. die Multi-Collection-IDEA weiter unten), niemals `mein_wissen`.
      *Aufwand: 0 (bewusst nichts tun) — bzw. mittel, falls separate Collection gewollt.*

- [ ] **2026-06-14: Ingest-500, wenn der Multivektor eines Chunks Qdrants 1-MiB-Pro-Punkt-gRPC-Limit sprengt.**
      BGE-M3-ColBERT erzeugt einen 1024-dim-Vektor pro Token, sodass schon ein ~256+-Token-Chunk
      1 048 576 Bytes überschreitet. `repository.upsert()` wirft dann
      `grpc._channel._InactiveRpcError: INVALID_ARGUMENT („Total size of all vectors (N) must be
      less than 1048576")` und der **ganze** Batch scheitert → die Notiz wird nie indexiert und
      brain-watcher loopt darauf (5 Retries, dann probiert es der Reconcile endlos erneut).
      Beobachtet an einer großen Vault-Notiz (Chunk #5 ≈ 1,94 MB). Fix-Optionen, billigste zuerst:
      (a) **graceful** — Punkte einzeln / in Sub-Batches upserten und einen zu großen Punkt
      überspringen + warnen, statt den ganzen Ingest mit 500 zu killen (kleinste, robusteste
      Lösung; der Rest der Notiz bleibt indexiert); (b) **kappen** — ColBERT-Tokenzahl / max-Chunk-
      Größe begrenzen, sodass ein Punkt < 1 MiB bleibt; (c) Qdrants gRPC-max-message-size anheben
      (Server `service.*` + Client `grpc_options`) — schiebt nur die Grenze. Repro: jede Notiz mit
      einem dichten ≥256-Token-Chunk. Ref: `service/routes.py:338` → `service/repository.py:118`.
      *Aufwand: Low (a) → Medium (b).*
      `QdrantRepository.aggregate_notes()` scrollt die gesamte Collection und aggregiert
      in Python — O(N) pro Aufruf. Bei heutiger Vault-Größe unkritisch; ab einigen
      zehntausend Chunks auf die Qdrant-Facet-API umstellen oder eine kleine
      Notes-Registry (SQLite) pflegen — Letzteres löst auch den domain_counts-Drift,
      wenn CLI-Ingests am Service vorbei laufen. Referenz:
      `~/projects/rag-workspace/docs/ai/plans/2026-06-12_architecture-review.md` (P2.4).
      *Aufwand: Mittel. Auslöser: Vault-Wachstum, nicht Kalender.*

- [ ] **2026-06-12: Property-based Tests für _split_text via hypothesis (Review P3.2).**
      `tests/titan/test_chunking.py` deckt die Overlap-Invarianten mit festen Beispielen
      ab; hypothesis würde sie generativ prüfen ("Sub-Chunks ohne Overlap ergeben den
      Originaltext", "kein Sub-Chunk > MAX_SUPER_CHUNK_CHARS") und Randfälle an
      Wortgrenzen/Separator-Fallbacks finden. Neue dev-Dependency `hypothesis`.
      Referenz: Plan P3.2. *Aufwand: Niedrig.*

- [ ] **2026-06-12: Request-ID-Korrelation titan ↔ brain-mcp (Review P3.3).**
      X-Request-ID-Middleware in titan + Durchreichen im TitanClient, damit sich ein
      MCP-Call durch beide Journals verfolgen lässt. Erst sinnvoll, wenn Debugging über
      Service-Grenzen hinweg real Zeit kostet. Referenz: Plan P3.3. *Aufwand: Mittel.*

- [x] **2026-06-06: Content-Hash-Skip — unveränderte Re-Ingests kurzschließen.**
      ✅ Umgesetzt 2026-06-12 (Commit f0602ce, Review-Session): `skipped_reason:
      "unchanged"`, `force=true` umgeht den Skip, `indexed: false` bleibt geehrt.
      `ingest_file_endpoint` bettet bei jedem Aufruf die gesamte Datei neu ein, auch wenn die rohen
      Bytes identisch zu dem bereits Indexierten sind. `content_hash` (sha256 der rohen
      Datei) wird bereits berechnet und im Payload gespeichert, aber nur für den Reconcile genutzt —
      nicht, um Arbeit zu sparen. Ein Early-Return am Anfang des Ingest-Pfads ergänzen: günstig
      den gespeicherten Hash für diesen `source_path` lesen (ein `scroll`, `with_payload=["content_hash"]`,
      `limit=1`), gegen den frisch berechneten Hash vergleichen und früh zurückkehren (Chunks
      unverändert), wenn sie übereinstimmen. Warum es wichtig ist: der Watcher feuert bei Touch-/Metadaten-
      Events erneut (Syncthing-Rename-Zustellung, Editor-Saves ohne Inhaltsänderung), und
      jeder überflüssige Trigger kostet derzeit einen vollen Docling→Chunk→GPU-Embed-Durchlauf unter
      dem GPU-Lock. Bonus: das gibt dem `force`-Flag endlich eine echte Bedeutung
      (`force=True` umgeht den Skip). Muss weiterhin `indexed: false` ehren. Aufwand: Niedrig.

- [ ] **2026-06-06: Near-Duplicate-Unterdrückung zur Retrieval-Zeit.** RRF
      dedupliziert bereits *exakt* gleiche Chunk-Treffer über Sub-Queries (verankert an der Point-ID), aber es gibt
      keine Unterdrückung von *Beinahe*-Duplikaten — zwei verschiedene IDs, deren Text fast
      identisch ist, können beide in die Top-k gelangen. Die eine reale Quelle dafür in der aktuellen
      Pipeline ist `_split_text`: Abschnitte über `MAX_SUPER_CHUNK_CHARS` (24k) werden
      in Sub-Chunks mit `OVERLAP_CHARS` (4k) Overlap gesplittet, sodass benachbarte Sub-Chunks Text
      teilen und beide ranken können, wenn eine Query die Overlap-Region trifft. Erst die günstigste Lösung (NICHT
      direkt zu MMR springen): ein Post-Filter auf der finalen Ergebnismenge, der benachbarte
      Chunks derselben `source` mit aufeinanderfolgender `chunk_id` zusammenfasst und den
      höher gerankten behält (oder ihren Text verschmilzt). Erst zu echtem Diversity-
      Reranking eskalieren — MMR (Maximal Marginal Relevance) oder einem Cosine-Similarity-Schwellwert auf
      der abgerufenen Menge — wenn der günstige Post-Filter sich als unzureichend erweist. Warum nicht jetzt: bei
      ~14 Notizen mit Header-basiertem Chunking sind Chunks meist eigenständige Abschnitte und
      Near-Dups selten; das ist Komplexität für ein Problem, das es kaum gibt.
      Erst wieder aufgreifen, wenn duplikatartige Treffer tatsächlich in Ergebnissen auftauchen (am ehesten aus
      großen, overlap-gesplitteten Dokumenten). Aufwand: Niedrig (Post-Filter) → Mittel (MMR).

- [ ] **2026-05-31: Wikilink-bewusstes Retrieval (Link-Graph).** `[[wikilinks]]`
      aus Notizen parsen und die manuelle Linkstruktur des Vaults zur Verbesserung des Retrievals nutzen.
      Manuelle Links sind hochwertiges menschliches Signal ("die gehören zusammen"), das reine
      Vektorsuche verpasst, und sie ermöglichen strukturelle/Multi-Hop-Fragen
      ("was hängt von X ab?") und Map-of-Content-Hub-Notizen. Gestaffelt, risikoärmstes zuerst:
      1. **Günstig/jetzt:** beim Ingest ausgehende Wikilinks im Qdrant-Payload speichern
         (`links: [...]`). Nahezu nullkostig, macht die Daten zukunftssicher, keine Retrieval-Änderung.
      2. **Später, hinter einem Flag:** ein `find_linked`-Tool (Nachbarn einer Notiz) und ein
         optionaler, gedeckelter Graph-Expansionsschritt in `search` (verlinkte Nachbarn
         nach dem Vektortreffer hereinziehen, sorgfältig gewichtet, damit `top_k` nicht verwässert).
      3. **Vollständige GraphRAG-artige Expansion** erst, wenn der Vault auf Hunderte von
         Notizen wächst und MOC-/Hub-Notiz-Gewohnheiten existieren.
      Jetzt nicht bauen: bei den aktuellen ~14 Notizen decken Semantiksuche + `find_related`
      das bereits ab, Link-Auflösung ist fummelig (Aliase, `#headings`, Renames),
      und es widerspricht titans schlankem Single-User-Ethos. Aufwand: 1 (Schritt 1) →
      Mittel/Hoch (Schritt 3).

- [ ] **2026-05-31: Cross-Encoder-Reranker als finale Retrieval-Stufe.** Nach RRF
      die Top-N-Kandidaten (z. B. 30) mit einem Cross-Encoder neu bewerten (z. B.
      BGE-reranker-v2) und die neu sortierten `top_k` zurückgeben. Typischerweise der größte einzelne
      Qualitätshebel in einer RAG-Pipeline — er beurteilt Query↔Chunk-Relevanz direkt
      statt über unabhängige Embeddings. Läuft auf der Workstation-GPU. Kosten: ein
      weiteres Modell im VRAM + zusätzliche Latenz pro Query (GPU-Lock / VRAM-Budget beachten).
      Aufwand: Mittel.

- [ ] **2026-05-31: Contextual Retrieval (Epic 5A v2.0).** Phi-4 einen 1–2-Satz-
      Kontext-Blurb pro Chunk generieren lassen und ihn vor dem Embedding voranstellen (Anthropics
      "Contextual Retrieval"). Stellt an Chunk-Grenzen verlorenen Kontext wieder her → besserer
      Recall, besonders für kurze/mehrdeutige Chunks. Phi-4 ist bereits im Stack.
      Der v1.0-Versuch wurde wegen schlechtem Kosten/Nutzen verworfen, daher muss die Neuauflage gegen
      das Eval-Harness (`ab_eval`) gemessen werden, bevor man sie behält. Kosten: langsamerer, teurerer
      Ingest. Aufwand: Mittel–Hoch.

- [ ] **2026-05-31: Antwort-Provenienz / Inline-Zitate.** `generate` so erweitern, dass es
      Aussagen den Chunks/`source_path` zuordnet, die sie stützen (z. B. Inline-`[n]`-Marker + eine
      Quellenliste). Macht Antworten zu verifizierbarem, nachvollziehbarem Output — hoher Wert für ein
      persönliches Wissens-RAG. Aufwand: Niedrig–Mittel.

- [ ] **2026-05-31: Qdrant-Snapshot-Backup/Restore-CLI.** Ein Ein-Befehl-Snapshot (und
      -Restore) der Collection. Der Index ist abgeleitet, aber teuer neu aufzubauen (vollständiges
      Re-Embedding); ein Snapshot ist günstige Versicherung gegen Korruption, eine fehlgeschlagene Migration oder
      ein verlorenes Container-Volume. Aufwand: Niedrig.

- [x] **2026-05-31: `/stats`-Endpunkt + leichtgewichtige Metriken.** ✅ Umgesetzt
      (`GET /stats`): Uptime, Chunks gesamt + Domain-Anzahl, Cache-Hit-Rate, jüngste
      Such-Latenz p50/p95/max, Cache-Eintragsanzahl, Alter des letzten Ingests. In-Memory-
      Counter in `ServiceState`, reine Zusammenstellung in `service/stats.py`.

- [ ] **2026-05-31: Multi-Collection / Namespaces (der schlanke Mehrbenutzer-Pfad).** Ein
      `collection`/`namespace`-Request-Parameter, sodass unterschiedliche Kontexte (z. B. privat vs.
      Arbeit oder pro Projekt) isolierte Suche bekommen und sich dabei das eine BGE-M3-Modell teilen.
      Liefert ~80 % des praktischen Nutzens von Mehrbenutzerbetrieb (Datentrennung) bei ~10 % der
      Kosten — keine Per-User-Auth, kein TLS, kein GPU-Concurrency-Umbau, und es lässt das
      Single-User-`127.0.0.1`-Design intakt. Echte Mandantenfähigkeit (mehrere Personen,
      isoliert + authentifiziert) bleibt out of scope: titan dann mit einem Gateway davor versehen,
      statt Mandantenfähigkeit hineinzubacken. Aufwand: Mittel.
