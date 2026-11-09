# Plan: Feinerer Zuschnitt, mehr Treffer bei gleichem Textbudget

Stand 2026-11-09. Status: **geplant**, nichts davon ist live. Autor: Opus (Planung).

## 1. Ausgangslage (gemessen)

Messreihe vom 08. und 09.11.2026, Zahlen im Vault unter `suchqualitaet-versuche`
(Versuche 2 bis 5), Labor unter `~/titan-platform-lab/20261110_*`.

- Einfache Fragen sind gut: Goldset Stelle @5 0,80 mit Reranker.
- Verkettete, aufzählende und abhängige Fragen sind schwach. Eine Suche mit der Rohfrage
  liefert 26 bis 46 % der nötigen Stellen in den Top 5.
- Andere Agenten-Strategien (Zerlegen, Lückenprüfung, Öffnen-Werkzeug, festes Vorgehen) bringen
  auf 40 frischen Fragen keinen gesicherten Gewinn.
- Diagnose: Bei 63 von 67 verpassten Stellen lag die richtige Notiz im Treffer, nur ein anderer
  Abschnitt. 21 Stellen findet titan nicht einmal mit ihrem eigenen Wortlaut unter den Top 10.
  Heute wird nur an H1/H2 geschnitten (max 2 800 Zeichen); H3-Unterabschnitte stecken im
  H2-Abschnitt und verwässern ihn.

Labor ohne Reranker (gleiche Dateien wie der Live-Index):

| Zuschnitt | Goldset @1 / @5 / MRR | verkettet @5 | Synthese @5 | Aufzählung @5 | Zeichen @5 |
| --- | --- | --- | --- | --- | --- |
| heute (H1/H2, max 2 800) | 0,50 / 0,81 / 0,61 | 0,42 | 0,33 | 0,25 | 7 000 |
| H1 bis H3, max 2 800 | 0,54 / 0,82 / 0,65 | 0,47 | 0,31 | 0,26 | 5 900 |
| H1 bis H3, max 1 200 | 0,51 / 0,82 / 0,63 | 0,46 | 0,36 | 0,25 | 4 600 |
| H1 bis H3, max 800 | 0,50 / 0,76 / 0,61 | 0,37 | 0,26 | 0,20 | 3 400 |

Bei gleichem Textbudget (rund 8 000 Zeichen) liefert H1 bis H3 mit max 1 200 und 10 Treffern
verkettet 0,59, Synthese 0,45, Aufzählung 0,32; heute mit 5 Treffern 0,42 / 0,33 / 0,25.

## 2. Entscheidung, die dieser Plan vorbereitet

Kandidat: **an H1 bis H3 schneiden, max 1 200 Zeichen, 200 Overlap; brain-mcp gibt 8 statt 5
Treffer zurück; Reranker-Pool 15 statt 10.** Endgültig erst nach Schritt 3 (Messung mit
Reranker), weil alle Laborzahlen oben ohne Reranker entstanden sind.

Nicht Teil dieses Plans: Mehrfachsuche auf dem Server für Aufzählfragen (neuer Werkzeugvertrag,
eigener Plan) und „klein finden, groß lesen“ (Nachbarabschnitt mitliefern).

## 3. Schritte

### Schritt 1: Chunker konfigurierbar machen (Code, ohne Wirkung auf den Live-Index)

- `src/titan/config.py`: `chunk_header_tiefe: int = 2`, `chunk_max_zeichen: int = 2_800`,
  `chunk_overlap_zeichen: int = 400`. Standardwerte = heutiges Verhalten.
- `src/titan/ingest.py`: `_HEADER_RE`, `MAX_SUPER_CHUNK_CHARS`, `OVERLAP_CHARS` aus den Settings
  ableiten. Ein Code-Block mit `#` am Zeilenanfang darf keine Überschrift sein (heute nur H1/H2,
  bei H3 häufiger; Test).
- Tests in `tests/titan/test_chunking.py`: H3 schneidet bei Tiefe 3, nicht bei Tiefe 2;
  max 1 200 wird eingehalten; Code-Block-Kommentar bleibt im Abschnitt. Die Invariante in
  `test_curation_fields.py` (Zeichen je Token unter `MAX_COLBERT_ROWS`) gilt weiter.
- `section_id` hängt am Header. Bei Tiefe 3 bekommen Unterabschnitte eigene IDs. Prüfen, wer
  `section_id` liest (heute nur `models.py`), damit nichts auf H2-IDs angewiesen ist.

### Schritt 2: Neue Sammlung offline bauen (schreibt nur `mein_wissen_v2`)

- Lab-Skript nach Vorbild `~/titan-platform-lab/20261110_chunks/lab.py`, aber gegen die neuen
  Settings statt Monkeypatch. Schreibt ausschließlich in `mein_wissen_v2`; ein Assert verweigert
  jeden anderen Namen.
- Dauer gemessen: rund 3 Minuten für den Vault (1 248 Abschnitte).
- `mein_wissen` und `query_cache` bleiben unberührt.

### Schritt 3: Mit Reranker messen (braucht Charlies Freigabe)

- Der 4B-Reranker passt nicht neben den laufenden titan-Dienst in den Grafikspeicher. Für die
  Messung den Dienst rund 20 Minuten stoppen, **nur nach Freigabe** (Regel: titan nicht ohne
  Rückfrage stoppen). 200-W-Limit vorher prüfen.
- Raster auf `mein_wissen` und `mein_wissen_v2`: top_k 5 / 8 / 10, Pool 10 / 15 / 20.
- Messgrößen: Goldset (Stelle @1, @5, MRR), die 86 Mehrschritt-Fragen (Stellen @k, alle @k),
  Zeichen je Antwort, Latenz p50/p95.
- Annahme, wenn: Goldset @5 nicht schlechter als 0,80, MRR nicht schlechter als 0,68,
  Mehrschritt-Stellen bei höchstens gleichen Zeichen besser, p50 unter 1,5 s.
- Danach den Dienst wieder starten und `/health` prüfen (`cuda_ok`).

### Schritt 4: Umschalten mit Rückweg

- `.env`: `COLLECTION_NAME=mein_wissen_v2`, `CACHE_COLLECTION_NAME=query_cache_v2` (frischer
  Cache, sonst liefert er Antworten aus alten Abschnitten), neue Chunk-Settings, `RERANK_POOL`.
- titan neu starten (Freigabe liegt vor, siehe Regeln). brain-watcher ingestiert ab dann in
  `mein_wissen_v2` mit dem neuen Zuschnitt; ein Reconcile nach dem Start holt Änderungen aus der
  Bauzeit nach.
- brain-mcp: Standard `top_k` von 5 auf den gemessenen Wert. Das ist ein eigener Commit in
  brain-mcp. **brain-mcp-Neustart leert den OAuth-Speicher**: Charlie muss den Connector danach
  neu verbinden; vorher ankündigen.
- Rückweg: `.env` zurück auf `mein_wissen` / `query_cache`, titan neu starten. Die alte Sammlung
  bleibt eine Woche stehen; erst danach mit Charlies Zustimmung löschen.

### Schritt 5: Festhalten

- `DECISIONS.md`: Entscheidung mit Messung.
- Vault `suchqualitaet-versuche` und `projekt-titan`: neuer Zuschnitt, Trefferzahl.
- titan-platform hat einen eigenen Chunker: Übertragbarkeit für PDFs der Freunde-Pilotphase dort
  als eigenen Punkt prüfen (Layout-Ebenen statt Markdown-Überschriften).

## 4. Risiken

- **Mehr Treffer, mehr Tokens je Suche,** wenn die Abschnitte doch größer ausfallen als gemessen.
  Gegenmittel: Annahmekriterium „höchstens gleiche Zeichen“.
- **Kleinere Abschnitte verlieren Zusammenhang.** 800 Zeichen haben gemessen geschadet; 1 200 lag
  gleichauf mit heute. Der Reranker sieht pro Abschnitt Kopfzeile „Notiz › Überschrift“.
- **Ziel-Fit am Goldset:** Die Mehrschritt-Sätze sind frisch erzeugt, das Goldset ist fester
  Regressionstest. Vor dem Umschalten einen weiteren frischen Satz (anderer Seed) gegenrechnen.
- **Reranker-Latenz steigt mit dem Pool** (10 Passagen rund 0,8 s). Pool 20 nur, wenn messbar
  besser.
