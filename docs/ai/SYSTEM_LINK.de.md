# SYSTEM_LINK — dieses Repo ist Teil eines Systems

> Wird von `./workspace.sh new` als `docs/ai/SYSTEM_LINK.md` in jedes Child-Repo abgelegt und
> von `./workspace.sh sync-shared` synchron gehalten. Es teilt einem Agenten, der *innerhalb*
> dieses Repos arbeitet, mit, dass es zu einem größeren System gehört und wo das System-Hirn liegt.

## Dieses Repo
- **Service-Name:** titan
- **Rolle:** RAG-Engine — Ingest (Docling → Late Chunking → BGE-M3) + hybride Suche über Qdrant
- **Konsumiert:** —
- **Stellt bereit:** contracts/titan.openapi.yaml
- **Port (lokal):** 8765

## Wo das System-Hirn liegt
Das koordinierende Workspace-Repo hält das Repo-übergreifende Gesamtbild:
- Systemkarte & Abhängigkeitsgraph → Workspace `docs/ai/SYSTEM.md`
- Welches Repo was besitzt → Workspace `docs/ai/ROUTING.md`
- Die Contracts, die dieses Repo einhalten muss → Workspace `docs/ai/CONTRACTS.md` + `contracts/`
- Repo-übergreifende Entscheidungen → Workspace `docs/ai/DECISIONS.md`

## Regeln, die nichts überschreiben, aber eine Sache ergänzen
Folge für alle lokale Arbeit der eigenen `CLAUDE.md` dieses Repos. Die einzige Ergänzung daraus,
Teil eines Systems zu sein: **eine Änderung an der Grenze dieses Repos (seinem exponierten Contract)
ist eine Entscheidung auf Workspace-Ebene** — anhalten und ansprechen, statt das Interface hier zu ändern.

<!-- SHARED-AGENT-RULES:START (synced from workspace shared/agent-rules.md — do not edit here) -->

# Gemeinsame Agenten-Regeln

> Kanonisches Konventionsfragment. Die `CLAUDE.md` des Single-Repo-Templates trägt bereits die
> Per-Repo-Regeln; diese Datei ist das **systemweite** Delta, das jedes Child-Repo zusätzlich
> einhalten muss. `./workspace.sh sync-shared` hängt diesen Block in der `docs/ai/SYSTEM_LINK.md`
> jedes Childs an bzw. aktualisiert ihn. Bearbeite ihn **nur hier** — niemals pro Repo.

## Du bist Teil eines größeren Systems

Dieses Repo steht nicht für sich allein. Es ist ein Service in `rag-system`. Bevor du etwas
änderst, das ein anderes Repo beobachten kann:

- Prüfe, ob die Änderung einen **Contract** kreuzt. Falls ja — anhalten: das ist eine Entscheidung
  auf Workspace-Ebene (Opus-Ebene), keine lokale. Sprich sie an.
- Deine Inputs und Outputs an der Grenze sind in den Workspace-`contracts/` definiert.
  Behandle sie als fix, sofern ein Workspace-Plan nichts anderes sagt.
- Halte dieses Repo **eigenständig lauffähig**: importiere keinen Code eines anderen Service;
  sprich mit ihm nur über seinen Contract.

## Was lokal bleibt vs. nach oben geht

- Eine Entscheidung über die Interna *dieses* Repos → lokale `docs/ai/DECISIONS.md`.
- Eine Entscheidung darüber, wie dieses Repo mit anderen spricht → Workspace `docs/ai/DECISIONS.md`.
- Eine Out-of-Scope-Idee, die nur dieses Repo betrifft → lokale `IDEAS.md`; betrifft sie andere →
  Workspace `IDEAS.md`.

## Grenzdisziplin

- Erweitere die öffentliche Oberfläche dieses Repos nicht leichtfertig — jeder neue Endpunkt/jedes Feld ist ein Contract.
- Lies nicht direkt die Datenbank, Dateien oder Interna eines anderen Service.
- Wenn du Verhalten an der Grenze änderst, landet die Contract-Änderung *zusammen mit* dem Code,
  und jeder Konsument wird im selben Workspace-Feature aktualisiert.

<!-- SHARED-AGENT-RULES:END -->
