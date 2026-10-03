# Plan: Retrieval-Qualität am echten Vault messen

Stand 2026-10-03. Status: **geplant, nicht umgesetzt.** Ein Kurztest mit 12
Fragen ist gelaufen (Abschnitt 1). Dieser Plan beschreibt die ausführliche
Messung, die Charlie haben will: viele Aufrufe, viele Daten, ein belastbares
Ergebnis zu Treffsicherheit, Kontextmenge und der Frage, ob Graph und Vektor
besser kombiniert werden sollten.

## 0. Die Fragen, die das Ergebnis beantworten soll

1. **Findet die Suche die richtige Stelle?** Nicht nur die richtige Notiz,
   sondern den Abschnitt, in dem die Antwort steht.
2. **Wie viel Kontext kostet ein Aufruf**, und wie viel davon ist nutzlos
   (Dubletten, Abschnitte ohne Bezug)?
3. **Wo versagt sie**, nach Fragetyp getrennt?
4. **Was bringt welcher Hebel?** Weniger Treffer, Dubletten zusammenfassen,
   Link-Graph, Cross-Encoder. Jeder Hebel wird gegen dieselbe Messung
   verglichen, nicht nach Gefühl behalten.

## 1. Kurztest vom 2026-10-03 (Ausgangslage)

12 Fragen mit bekannter Antwortstelle, abgeschickt mit genau dem Code des
Connectors (`brain_mcp.mcp_server._client.search` + `_format_chunks`,
`top_k=10`). Der Kern steht in Abschnitt 4.

| Messgröße | Ergebnis |
|---|---|
| Richtige Notiz unter den 10 Treffern | 11 / 11 (dazu ein Negativtest) |
| Richtige Notiz auf Platz 1 | 10 / 11 |
| Antwortstelle wörtlich in einem Treffer | 10 / 11 |
| Notiz mit `indexed: false` im Ergebnis | nein, wie gewollt |
| Antwortlänge an Claude | 8 450 bis 20 800 Zeichen, Median rund 12 600 (grob 3 700 Tokens) |
| Verschiedene Notizen je Antwort | 2 bis 6 |
| Latenz | 55 bis 111 ms |

**Auffällig, und Ausgangspunkt für diesen Plan:**

- **Viele Treffer aus derselben Notiz.** Oft sind die Plätze 1 bis 3 drei
  Abschnitte derselben Notiz. Für die Antwort reicht meist einer.
- **Die Scores liegen eng beieinander** (z. B. 5,54 / 5,12 / 5,11). Es gibt
  keinen Abstand, an dem man „ab hier irrelevant" abschneiden könnte, und
  die Suche liefert immer volle 10 Treffer, auch bei einer Frage, die nur
  eine Stelle braucht.
- **Richtige Notiz, falsche Stelle:** „Ab wie vielen Zeichen schneidet
  titan?" fand `projekt-titan`, aber nicht den Abschnitt mit der Zahl.
  Dieser Fehlertyp ist unsichtbar, solange man nur Notizen zählt.
- **Ein Fall auf Platz 4:** „Welcher Servertyp läuft bei Hetzner?" brachte
  zuerst Notizen, die über den Server reden, statt `coolify-prod`, die ihn
  beschreibt.

**Was der Kurztest nicht beweist:** Die Fragen sind von einer Sitzung
geschrieben, die die Notizen kannte; sie benutzen deren Wörter. Echte
Fragen sind unschärfer. 12 Fälle reichen nicht für Aussagen je Fragetyp.

## 2. Der Fragenkatalog (Goldset)

Ziel: **150 bis 200 Fragen**, jede mit Sollstelle. Speicherort:
`src/titan/eval/fixtures/vault_goldset.jsonl`, ein Fall pro Zeile:

```json
{"id": "f017", "typ": "fakt", "frage": "...", "soll": [{"notiz": "coolify-prod", "abschnitt": "Eckdaten", "beleg": "CX33"}], "darf_nicht": [], "quelle": "charlie"}
```

`soll` darf mehrere Stellen haben, für Fragen mit verteilter Antwort.
`beleg` ist ein kurzer Text, der in der richtigen Stelle wörtlich steht; so
lässt sich ohne LLM prüfen, ob die Stelle getroffen ist.

**Fragetypen**, jeweils mindestens 15 Fälle:

| Typ | Beispiel | Was er prüft |
|---|---|---|
| fakt | „Was hat das Pixel gekostet?" | eine Zahl oder ein Name an einer Stelle |
| anleitung | „Wie starte ich die Vorschau neu?" | Befehle, oft in Codeblöcken |
| warum | „Warum hoste ich selbst?" | Begründungen, längere Abschnitte |
| verteilt | „Was ist zu DSGVO alles offen?" | Antwort über mehrere Notizen |
| abhängigkeit | „Was hängt an coolify-prod?" | Multi-Hop, Link-Graph |
| zeitlich | „Was hat sich am Gewerbe seit September geändert?" | überholte gegen aktuelle Aussagen |
| umgangssprachlich | „mc server geht nicht" | Tippfehler, Kürzel, ohne Notizwörter |
| negativ | Frage, deren Antwort nicht im Vault steht | soll wenig oder nichts liefern |
| geschützt | Frage nach Inhalten einer `indexed: false`-Notiz | darf sie nie zeigen |

**Woher die Fragen kommen**, gegen die Schwäche aus Abschnitt 1:

1. **Charlie schreibt 40 bis 60 echte Fragen** aus dem Gedächtnis, ohne in
   die Notizen zu schauen. Das ist der wertvollste Teil.
2. **Aus echten Connector-Aufrufen**, sofern brain-mcp sie protokolliert
   (prüfen; sonst ab jetzt mitschreiben, nur Frage, Zeit und Treffer-IDs).
3. **Generiert:** ein Modell liest je Abschnitt den Text und schreibt eine
   Frage **in anderen Worten** als der Text. Von Hand sichten; was die
   Notiz wörtlich zitiert, fliegt raus.
4. Sollstellen für 1 und 2 trägt eine Sitzung ein, Charlie bestätigt
   stichprobenartig.

Das Goldset veraltet mit dem Vault. Der Runner prüft deshalb vor jedem
Lauf, dass jeder `beleg` noch in seiner Notiz steht, und meldet verwaiste
Fälle getrennt, statt mit ihnen falsch zu messen.

## 3. Messgrößen

Alle ohne LLM berechenbar, damit ein Lauf Sekunden dauert und
reproduzierbar ist:

| Größe | Definition |
|---|---|
| Notiz-Recall@k | Anteil der Fälle mit Sollnotiz unter den ersten k (k = 1, 3, 5, 10) |
| Stellen-Recall@k | dasselbe, aber der `beleg` muss im Treffertext stehen. **Hauptgröße** |
| MRR | mittlerer Kehrwert des Rangs der ersten richtigen Stelle |
| Kontextkosten | Zeichen und Tokens der formatierten Antwort, Median und 90. Perzentil |
| Nutzanteil | Zeichen in Treffern mit Sollstelle geteilt durch alle Zeichen |
| Dublettenquote | Treffer aus einer Notiz, die schon mit höherem Rang vertreten ist |
| Negativ-Score | bei Fragen ohne Antwort: bester Score, gegen die Verteilung bei echten Treffern |
| Leckage | Treffer aus `indexed: false`-Notizen. **Muss 0 sein**, sonst Abbruch |
| Latenz | p50 und p95, getrennt mit und ohne Cache |

Den Semantic Cache für Messläufe umgehen oder vorher leeren, sonst misst
der zweite Lauf den Cache.

**Zweite Stufe, optional: Antwortqualität mit LLM als Richter.** Das alte
`evaluate.py` (RAG-Triade mit phi4) misst, ob eine generierte Antwort durch
die Treffer gedeckt ist. Für den Connector ist das zweitrangig, weil Claude
die Antwort schreibt, nicht titan. Sinnvoll erst, wenn Stufe 1 zeigt, dass
die Treffer stimmen, und dann an einer Stichprobe von 30 Fällen, die
Charlie gegenliest.

## 4. Umsetzung

**Kein neues Werkzeug, sondern `ab_eval` umbauen.** `src/titan/eval/ab_eval.py`
kann schon `--label`, `--cases-file` und `--rank-only`. Seine Fixtures
(`cases.json`, 20 Fälle) stammen aber aus der Zeit vor dem Vault
(`trading_log.pdf`, `system_architektur.pdf`) und passen zu keiner
heutigen Notiz.

1. Neues Format `vault_goldset.jsonl` einlesen; die alten Fixtures als
   `legacy` kennzeichnen, nicht löschen.
2. Über den brain-mcp-Client **und** direkt über `titan.search` suchen,
   damit Unterschiede zwischen Connector-Formatierung und Rohsuche
   sichtbar werden. Kern des Kurztests:

   ```python
   from brain_mcp.mcp_server import _client, _format_chunks
   r = _client.search(frage, top_k=10)
   text = _format_chunks(r.chunks, r.cache_hit, r.latency_ms)
   quellen = [Path(c.source_path).stem for c in r.chunks]
   ```

3. Ausgabe je Lauf: `logs/eval/<datum>_<label>.jsonl` mit einer Zeile je
   Fall (Ränge, Zeichen, Treffer-IDs) und eine Zusammenfassung in Markdown.
   Treffertexte nicht ins Repo committen, sie enthalten Vault-Inhalt.
4. `make eval-vault LABEL=baseline` als Einstieg.
5. `--compare baseline v2` zeigt je Messgröße die Differenz und listet die
   Fälle, die sich verbessert oder verschlechtert haben.

## 5. Die Hebel, in dieser Reihenfolge

Jeder Hebel ist ein Flag, läuft gegen dieselbe Baseline und wird nur
behalten, wenn Stellen-Recall@5 nicht sinkt und Kontextkosten oder Recall
messbar besser werden.

| # | Hebel | Erwartung aus dem Kurztest | Aufwand |
|---|---|---|---|
| 1 | **`top_k` senken** (10 → 5) | halbiert die Kontextkosten; messen, was Recall@5 gegen Recall@10 verliert | keiner |
| 2 | **Höchstens 2 Treffer je Notiz** | entfernt Dubletten, Plätze rücken für andere Notizen frei. Einfacher als die Near-Duplicate-Idee in `IDEAS.md` | niedrig |
| 3 | **Abschneiden nach Score-Abstand** | weniger Ballast bei einfachen Fragen; braucht die Negativ-Verteilung aus Abschnitt 3 | niedrig |
| 4 | **Link-Graph als Nachbar-Bonus** (Abschnitt 6) | besser bei „verteilt" und „abhängigkeit" | mittel |
| 5 | **Cross-Encoder** (z. B. bge-reranker-v2-m3) nach dem ColBERT-Schritt | laut `IDEAS.md` der größte Einzelhebel; erst messen, ob ColBERT ihn schon abdeckt | mittel |
| 6 | **Kürzere Treffertexte**: Abschnitt plus Überschriftenpfad statt ganzem Chunk, Rest gezielt nachladen | Kontextkosten | mittel |

Hebel 1 bis 3 sind fast kostenlos und beantworten die Frage „sprengt es den
Kontext?" direkt.

## 6. Graph und Vektor kombinieren

**Was heute schon da ist:** Beim Ingest werden die ausgehenden Wikilinks
jeder Notiz in den Qdrant-Payload geschrieben (`ingest.py`,
`extract_wikilinks`, Feld `links`). Das ist Schritt 1 der Link-Graph-Idee
in `IDEAS.md`. `graph_check` kennt den ganzen Graphen (51 Notizen, 470
Links am 2026-10-03). **Die Suche selbst benutzt die Links nicht**, und
`find_related` rechnet rein semantisch.

Drei Stufen, jede gegen das Goldset gemessen:

1. **Link-Bonus beim Ranking.** Nach der Vektorsuche bekommt ein Treffer
   einen kleinen Zuschlag, wenn seine Notiz mit einer höher gerankten
   Trefferquelle verlinkt ist. Billig, keine neue Abfrage. Gefahr:
   Hub-Notizen wie `00-home` (verlinkt alles) und `mein-pc` (27
   Rückverweise) bekämen ihn immer. Deshalb den Bonus durch die Linkzahl
   der Notiz teilen.
2. **Nachbar-Expansion für Mehrteiliges.** Bei Fragen nach Abhängigkeiten
   oder wenn die Treffer aus wenigen Notizen stammen: die direkt verlinkten
   Notizen nachladen, aber nur ihren besten Abschnitt zur Frage und nur bis
   zu einem festen Zeichenbudget. Als eigenes Tool `find_linked`, damit
   Claude es bewusst aufruft statt bei jeder Suche dafür zu zahlen.
3. **GraphRAG im engeren Sinn** (Entitäten und Beziehungen aus dem Text
   extrahieren, Community-Zusammenfassungen) erst bei mehreren hundert
   Notizen. Bei 51 handgepflegten Notizen mit 470 Links ist der
   handgemachte Graph besser als jeder extrahierte.

**Hypothese, damit sie widerlegt werden kann:** Stufe 1 und 2 helfen bei
„verteilt" und „abhängigkeit" und sind bei „fakt" neutral. Zeigt das
Goldset das nicht, bleiben sie draußen.

## 7. Abnahme

- Goldset mit mindestens 150 Fällen, davon mindestens 40 von Charlie
- Baseline-Lauf mit allen Messgrößen aus Abschnitt 3, nach Fragetyp
  aufgeschlüsselt
- Hebel 1 bis 3 gemessen, Entscheidung je Hebel mit Zahlen in
  `DECISIONS.md`
- Leckage 0 in jedem Lauf
- Ergebnis als Notiz im Vault (Domain `rag-system`); Rohdaten bleiben in
  `logs/`

## 8. Offene Fragen

- Protokolliert brain-mcp die Anfragen? Wenn nein: mitschreiben (nur Frage,
  Zeit, Treffer-IDs), damit echte Fragen ins Goldset kommen.
- Soll der Cache für Messläufe per Flag umgehbar sein, oder reicht Leeren?
- Wie oft wiederholen? Vorschlag: nach jeder Änderung an Chunking,
  Embedding oder Ranking, und monatlich gegen den gewachsenen Vault.
