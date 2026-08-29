"""
repository.py – Qdrant-Zugriff des Service als Repository
===========================================================
Kapselt alle Qdrant-Filter-/Scroll-Konstruktionen der Routes an einer Stelle.
Vorher wiederholten die Endpoints sechsfach denselben Lazy-Import von
FieldCondition/Filter/MatchValue, und die Scroll-Aggregation für /notes
existierte zweimal (list_notes / list_domain_notes, ~90 % identisch).

Bewusst leichtgewichtig: das Repository wird pro Request konstruiert (nur
Client-Referenz + Collection-Name, kein eigener Zustand), damit Tests weiter
einfach state.qdrant_client injizieren und COLLECTION_NAME patchen können.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NoteAggregate:
    """Aggregierte Sicht auf eine indexierte Note (für /notes)."""

    source_path: str
    domain: str
    chunk_count: int
    content_hash: str | None
    # Kuratierungsfelder aus dem Frontmatter (titan.ingest.CURATION_FIELDS).
    # None heisst "nicht gesetzt" — bei 'geprueft' also "nie geprüft", und das
    # ist die eigentlich interessante Auskunft.
    updated: str | None = None
    geprueft: str | None = None
    quelle: str | None = None
    # Ausgehende Wikilinks der Note (titan.ingest.extract_wikilinks).
    links: list[str] = field(default_factory=list)


class QdrantRepository:
    """Dünner, service-spezifischer Wrapper um den QdrantClient."""

    def __init__(self, client: Any, collection: str) -> None:
        self._client = client
        self._collection = collection

    # ── intern ────────────────────────────────────────────────────────────

    @staticmethod
    def _source_filter(source_path: str, exclude_run_id: str | None = None) -> Any:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        # list[Any]: mypy-Listeninvarianz vs. Qdrants breite Condition-Union
        must_not: list[Any] | None = None
        if exclude_run_id:
            must_not = [FieldCondition(key="run_id", match=MatchValue(value=exclude_run_id))]
        return Filter(
            must=[FieldCondition(key="source_path", match=MatchValue(value=source_path))],
            must_not=must_not,
        )

    # ── Health / Counts ───────────────────────────────────────────────────

    def collection_ready(self) -> bool:
        """True wenn die Collection erreichbar ist (für /health)."""
        try:
            self._client.get_collection(self._collection)
            return True
        except Exception:
            return False

    def count_chunks(self, source_path: str) -> int:
        """Exakte Chunk-Anzahl einer Datei."""
        result = self._client.count(
            collection_name=self._collection,
            count_filter=self._source_filter(source_path),
            exact=True,
        )
        return int(result.count)

    # ── Einzel-Lookups ────────────────────────────────────────────────────

    def _first_payload(self, source_path: str, fields: list[str]) -> dict[str, Any] | None:
        records, _ = self._client.scroll(
            collection_name=self._collection,
            scroll_filter=self._source_filter(source_path),
            limit=1,
            with_payload=fields,
            with_vectors=False,
        )
        if not records:
            return None
        return dict(records[0].payload or {})

    def stored_content_hash(self, source_path: str) -> str | None:
        """content_hash der indexierten Version einer Datei (oder None)."""
        payload = self._first_payload(source_path, ["content_hash"])
        if payload is None:
            return None
        value = payload.get("content_hash")
        return str(value) if value else None

    def domain_of(self, source_path: str) -> str | None:
        """Domain der indexierten Datei (oder None wenn nicht indexiert)."""
        payload = self._first_payload(source_path, ["domain"])
        if payload is None:
            return None
        value = payload.get("domain")
        return str(value) if value else None

    def first_dense_vector(self, source_path: str) -> Any | None:
        """Dense-Vektor des ersten Chunks einer Datei (Anchor für /find_related)."""
        records, _ = self._client.scroll(
            collection_name=self._collection,
            scroll_filter=self._source_filter(source_path),
            limit=1,
            with_vectors=True,
            with_payload=False,
        )
        if not records:
            return None
        raw = records[0].vector
        return raw["dense"] if isinstance(raw, dict) else raw

    # ── Mutationen ────────────────────────────────────────────────────────

    def upsert(self, points: list[Any]) -> None:
        self._client.upsert(collection_name=self._collection, points=points)

    def delete_by_source(self, source_path: str) -> None:
        """Löscht alle Chunks einer Datei."""
        self._client.delete(
            collection_name=self._collection,
            points_selector=self._source_filter(source_path),
        )

    def delete_stale_runs(self, source_path: str, keep_run_id: str) -> None:
        """Löscht alle Chunks einer Datei, die NICHT zur run_id gehören."""
        self._client.delete(
            collection_name=self._collection,
            points_selector=self._source_filter(source_path, exclude_run_id=keep_run_id),
        )

    # ── Suche / Aggregation ───────────────────────────────────────────────

    def find_similar(self, dense_vector: Any, top_k: int, exclude_source: str) -> list[Any]:
        """Dense-Suche, exklusive der Quelldatei selbst (für /find_related)."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        results = self._client.query_points(
            collection_name=self._collection,
            query=dense_vector,
            using="dense",
            limit=top_k,
            query_filter=Filter(
                must_not=[FieldCondition(key="source_path", match=MatchValue(value=exclude_source))]
            ),
            with_payload=True,
        )
        return list(results.points)

    def link_neighbours(self, source_path: str) -> list[tuple[str, str, str]]:
        """Notizen, die per Wikilink mit dieser verbunden sind.

        Returns:
            Tripel (source_path, domain, richtung) mit richtung in
            outgoing / incoming / both.
        """
        from pathlib import Path

        eigener_name = Path(source_path).stem
        aggregates = self.aggregate_notes()

        eigene_links: set[str] = set()
        for a in aggregates:
            if a.source_path == source_path:
                eigene_links = set(a.links)
                break

        nachbarn: list[tuple[str, str, str]] = []
        for a in aggregates:
            if a.source_path == source_path:
                continue
            name = Path(a.source_path).stem
            raus = name in eigene_links
            rein = eigener_name in a.links
            if raus and rein:
                nachbarn.append((a.source_path, a.domain, "both"))
            elif raus:
                nachbarn.append((a.source_path, a.domain, "outgoing"))
            elif rein:
                nachbarn.append((a.source_path, a.domain, "incoming"))
        return sorted(nachbarn)

    def aggregate_notes(self, domain: str | None = None) -> list[NoteAggregate]:
        """Scrollt die Collection und aggregiert pro source_path.

        Für einen persönlichen Vault (einige hundert/tausend Chunks)
        unkritisch; bei deutlichem Wachstum auf Qdrant-Facets umstellen (P2.4).
        """
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        scroll_filter = None
        if domain is not None:
            scroll_filter = Filter(
                must=[FieldCondition(key="domain", match=MatchValue(value=domain))]
            )

        from titan.ingest import CURATION_FIELDS

        counts: dict[str, int] = {}
        domains: dict[str, str] = {}
        hashes: dict[str, str | None] = {}
        # Pro Note ein Dict mit den Kuratierungsfeldern. Alle Chunks einer Datei
        # stammen aus demselben Ingest-Lauf und tragen dieselben Werte — der
        # erste gesehene Chunk entscheidet, wie schon bei domain und hash.
        curation: dict[str, dict[str, str | None]] = {}
        links: dict[str, list[str]] = {}
        offset = None
        while True:
            records, offset = self._client.scroll(
                collection_name=self._collection,
                scroll_filter=scroll_filter,
                limit=256,
                offset=offset,
                with_payload=["source_path", "domain", "content_hash", "links", *CURATION_FIELDS],
                with_vectors=False,
            )
            for record in records:
                payload = record.payload or {}
                source_path = str(payload.get("source_path", ""))
                if not source_path:
                    continue
                counts[source_path] = counts.get(source_path, 0) + 1
                domains.setdefault(source_path, str(payload.get("domain", "")))
                hashes.setdefault(source_path, payload.get("content_hash"))
                curation.setdefault(source_path, {f: payload.get(f) for f in CURATION_FIELDS})
                links.setdefault(source_path, list(payload.get("links") or []))
            if offset is None:
                break

        return [
            NoteAggregate(
                source_path=sp,
                domain=domains[sp],
                chunk_count=counts[sp],
                content_hash=hashes[sp],
                updated=curation[sp].get("updated"),
                geprueft=curation[sp].get("geprueft"),
                quelle=curation[sp].get("quelle"),
                links=links.get(sp, []),
            )
            for sp in sorted(counts)
        ]
