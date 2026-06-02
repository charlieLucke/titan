"""
tests/integration/test_service.py – Integration-Tests für den Titan Service

Voraussetzungen:
    - Qdrant läuft lokal (gRPC :6334)
    - BGE-M3 ist installiert und CUDA verfügbar
    - Phi-4 via Ollama (wird für Decompose gemockt)

Aufruf:
    uv run pytest tests/integration/ -m integration -v

Alle Tests nutzen eine separate Test-Collection (Prefix: titan_test_),
die nach dem Test-Lauf automatisch gelöscht wird.
"""

from __future__ import annotations

import contextlib
import os
import textwrap
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ─── Fixtures ────────────────────────────────────────────────────────────────

TEST_COLLECTION = f"titan_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def qdrant_client() -> Generator[Any, None, None]:
    """Echter Qdrant-Client auf Test-Collection."""
    from dotenv import load_dotenv
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        MultiVectorComparator,
        MultiVectorConfig,
        SparseIndexParams,
        SparseVectorParams,
        VectorParams,
    )

    # .env laden, damit QDRANT_API_KEY verfügbar ist (Qdrant verlangt Auth).
    load_dotenv()

    host = os.getenv("QDRANT_HOST", "localhost")
    port = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
    client = QdrantClient(
        host=host,
        grpc_port=port,
        prefer_grpc=True,
        api_key=os.getenv("QDRANT_API_KEY") or None,
        https=False,
        check_compatibility=False,
    )

    client.create_collection(
        collection_name=TEST_COLLECTION,
        vectors_config={
            "dense": VectorParams(size=1024, distance=Distance.COSINE),
            "colbert": VectorParams(
                size=1024,
                distance=Distance.COSINE,
                multivector_config=MultiVectorConfig(comparator=MultiVectorComparator.MAX_SIM),
            ),
        },
        sparse_vectors_config={"sparse": SparseVectorParams(index=SparseIndexParams())},
    )
    client.create_payload_index(TEST_COLLECTION, "source_path", "keyword")
    client.create_payload_index(TEST_COLLECTION, "domain", "keyword")
    client.create_payload_index(TEST_COLLECTION, "run_id", "keyword")

    yield client

    # Teardown defensiv: ein transienter gRPC-Fehler beim Cleanup darf den
    # Test-Lauf nicht rot färben.
    with contextlib.suppress(Exception):
        client.delete_collection(TEST_COLLECTION)
    with contextlib.suppress(Exception):
        client.close()


@pytest.fixture(scope="module")
def bge_model() -> Any:
    """Echter BGE-M3 (lädt Modell in CUDA — einmalig pro Test-Session)."""
    from FlagEmbedding import BGEM3FlagModel  # type: ignore[import]

    return BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device="cuda")


@pytest.fixture(scope="module")
def app_client(qdrant_client: Any, bge_model: Any) -> Generator[TestClient, None, None]:
    """FastAPI TestClient mit vorgeladenem Modell (kein echter Lifespan)."""
    import torch

    from titan.service.app import create_app
    from titan.service.state import state

    state.bge_model = bge_model
    state.qdrant_client = qdrant_client

    # Domain-Counter initial leer (Test-Collection ist frisch)
    from collections import Counter

    state.domain_counts = Counter()

    # COLLECTION_NAME auf Test-Collection umbiegen
    with (
        patch.dict(os.environ, {"COLLECTION_NAME": TEST_COLLECTION}),
        patch("titan.service.routes.COLLECTION_NAME", TEST_COLLECTION),
        patch("titan.service.app.COLLECTION_NAME", TEST_COLLECTION),
    ):
        app = create_app()
        # TestClient ohne Kontextmanager: der lifespan würde sonst BGE-M3 und
        # Qdrant neu laden und den hier vorinjizierten State überschreiben.
        client = TestClient(app, raise_server_exceptions=True)
        yield client

    # Cleanup State
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    state.bge_model = None
    state.qdrant_client = None


@pytest.fixture()
def tmp_vault(tmp_path: Path) -> Path:
    """Temporäres Vault-Verzeichnis."""
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


# ─── Health Tests ─────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_health_ok(app_client: TestClient) -> None:
    """GET /health gibt status ok zurück wenn BGE geladen und Qdrant erreichbar."""
    resp = app_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["bge_loaded"] is True
    assert data["qdrant_reachable"] is True
    assert data["status"] == "ok"


@pytest.mark.integration
def test_health_degraded_without_qdrant() -> None:
    """GET /health gibt status degraded zurück wenn Qdrant nicht erreichbar."""
    from collections import Counter

    from titan.service.app import create_app
    from titan.service.state import state

    # Modell nochmal laden ist teuer — wir nutzen einen Mock
    mock_model = MagicMock()
    mock_model.encode.return_value = {
        "dense_vecs": [[0.1] * 1024],
        "lexical_weights": [{}],
        "colbert_vecs": [[[0.1] * 1024]],
    }

    # state ist ein Singleton — sichern und nach dem Test wiederherstellen,
    # damit nachfolgende Tests den von app_client injizierten State behalten.
    saved = (state.bge_model, state.qdrant_client, state.domain_counts)
    try:
        state.bge_model = mock_model
        state.qdrant_client = None  # kein Client → degraded
        state.domain_counts = Counter()

        with (
            patch.dict(os.environ, {"COLLECTION_NAME": TEST_COLLECTION}),
            patch("titan.service.routes.COLLECTION_NAME", TEST_COLLECTION),
        ):
            app = create_app()
            # Ohne Kontextmanager: der lifespan würde sonst echte Ressourcen laden
            # und den degraded-Zustand (qdrant_client=None) überschreiben.
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/health")

        assert resp.status_code == 200
        assert resp.json()["status"] == "degraded"
        assert resp.json()["qdrant_reachable"] is False
    finally:
        state.bge_model, state.qdrant_client, state.domain_counts = saved


# ─── Domains Tests ────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_domains_empty(app_client: TestClient) -> None:
    """GET /domains auf leerer Collection gibt leere Liste zurück."""
    resp = app_client.get("/domains")
    assert resp.status_code == 200
    data = resp.json()
    assert data["domains"] == []
    assert data["counts"] == {}


# ─── Ingest Tests ─────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_ingest_new_note(app_client: TestClient, tmp_vault: Path) -> None:
    """POST /ingest/file neue Note → chunks_created > 0, chunks_deleted == 0."""
    note = tmp_vault / "test_new.md"
    note.write_text(
        textwrap.dedent("""\
            ---
            domain: test
            indexed: true
            ---
            # Testnotiz
            Dies ist ein Integration-Test für den Titan Service.
            Unique-Content-Marker-ALPHA1234.
        """)
    )

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        resp = app_client.post("/ingest/file", json={"file_path": str(note)})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["chunks_created"] > 0
    assert data["chunks_deleted"] == 0
    assert data["domain"] == "test"
    assert data["skipped_reason"] is None


@pytest.mark.integration
def test_ingest_same_note_twice(app_client: TestClient, tmp_vault: Path) -> None:
    """POST /ingest/file selbe Note nochmal → alte Chunks ersetzt."""
    note = tmp_vault / "test_reingest.md"
    note.write_text(
        textwrap.dedent("""\
            ---
            domain: test
            ---
            # Re-Ingest Test
            Erster Inhalt. Unique-Content-BETA5678.
        """)
    )

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        resp1 = app_client.post("/ingest/file", json={"file_path": str(note)})
    assert resp1.status_code == 200
    created_first = resp1.json()["chunks_created"]
    assert created_first > 0

    # Inhalt ändern, nochmal ingesten
    note.write_text(
        textwrap.dedent("""\
            ---
            domain: test
            ---
            # Re-Ingest Test (v2)
            Geänderter Inhalt. Unique-Content-BETA5678-V2.
        """)
    )

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        resp2 = app_client.post("/ingest/file", json={"file_path": str(note)})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["chunks_created"] > 0


@pytest.mark.integration
def test_ingest_indexed_false(app_client: TestClient, tmp_vault: Path) -> None:
    """POST /ingest/file mit indexed:false → skipped, chunks_created == 0."""
    note = tmp_vault / "test_skip.md"
    note.write_text(
        textwrap.dedent("""\
            ---
            domain: test
            indexed: false
            ---
            # Private Notiz
            Dieser Inhalt soll nicht indexiert werden.
        """)
    )

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        resp = app_client.post("/ingest/file", json={"file_path": str(note)})

    assert resp.status_code == 200
    data = resp.json()
    assert data["chunks_created"] == 0
    assert data["skipped_reason"] == "indexed:false"
    assert data["domain"] is None


@pytest.mark.integration
def test_ingest_outside_vault(app_client: TestClient, tmp_path: Path) -> None:
    """POST /ingest/file mit Pfad außerhalb VAULT_ROOT → 400."""
    outside = tmp_path / "outside.md"
    outside.write_text("---\ndomain: test\n---\nContent")

    with patch("titan.service.routes.VAULT_ROOT", tmp_path / "vault"):
        resp = app_client.post("/ingest/file", json={"file_path": str(outside)})

    assert resp.status_code == 400


@pytest.mark.integration
def test_ingest_pdf_rejected(app_client: TestClient, tmp_vault: Path) -> None:
    """POST /ingest/file mit .pdf → 400 (PDFs via CLI)."""
    pdf = tmp_vault / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        resp = app_client.post("/ingest/file", json={"file_path": str(pdf)})

    assert resp.status_code == 400
    assert "CLI" in resp.json()["detail"]


@pytest.mark.integration
def test_ingest_transition_indexed_false(app_client: TestClient, tmp_vault: Path) -> None:
    """indexed:true → indexed:false → alte Chunks werden entfernt."""
    note = tmp_vault / "test_transition.md"
    note.write_text("---\ndomain: test\nindexed: true\n---\nTransition test content GAMMA9999.")

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        resp1 = app_client.post("/ingest/file", json={"file_path": str(note)})
    assert resp1.status_code == 200
    assert resp1.json()["chunks_created"] > 0

    # Auf indexed:false setzen
    note.write_text("---\ndomain: test\nindexed: false\n---\nTransition test content GAMMA9999.")

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        resp2 = app_client.post("/ingest/file", json={"file_path": str(note)})
    assert resp2.status_code == 200
    assert resp2.json()["chunks_created"] == 0
    assert resp2.json()["skipped_reason"] == "indexed:false"


# ─── Search Tests ─────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_search_invalid_top_k(app_client: TestClient) -> None:
    """POST /search mit top_k=51 → 422 (Pydantic validation)."""
    resp = app_client.post(
        "/search",
        json={"query": "test", "top_k": 51},
    )
    assert resp.status_code == 422


@pytest.mark.integration
def test_search_returns_chunks(app_client: TestClient, tmp_vault: Path) -> None:
    """POST /search nach Ingest einer Note → Chunks zurück."""
    # Zuerst eine Note ingesten
    note = tmp_vault / "search_test.md"
    note.write_text(
        "---\ndomain: searchtest\n---\n"
        "# Suchtest\nUnique-Search-Phrase-DELTA0042. BGE-M3 und RRF sind gut."
    )

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        ingest_resp = app_client.post("/ingest/file", json={"file_path": str(note)})
    assert ingest_resp.status_code == 200

    # Phi-4 Decompose mocken (kein Ollama nötig)
    with patch("titan.search.decompose_query", return_value=["DELTA0042"]):
        resp = app_client.post(
            "/search",
            json={"query": "DELTA0042", "domain": "searchtest", "top_k": 5, "use_decompose": False},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "chunks" in data
    assert data["latency_ms"] >= 0


@pytest.mark.integration
def test_search_domain_isolation(app_client: TestClient, tmp_vault: Path) -> None:
    """Suche in Domain A gibt keine Ergebnisse aus Domain B zurück."""
    note_a = tmp_vault / "domain_a.md"
    note_a.write_text("---\ndomain: domain_a\n---\nContent für Domain A. EPSILON1111.")
    note_b = tmp_vault / "domain_b.md"
    note_b.write_text("---\ndomain: domain_b\n---\nContent für Domain B. ZETA2222.")

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        app_client.post("/ingest/file", json={"file_path": str(note_a)})
        app_client.post("/ingest/file", json={"file_path": str(note_b)})

    with patch("titan.search.decompose_query", return_value=["EPSILON1111"]):
        resp = app_client.post(
            "/search",
            json={"query": "EPSILON1111", "domain": "domain_b", "top_k": 5, "use_decompose": False},
        )

    assert resp.status_code == 200
    chunks = resp.json()["chunks"]
    # Alle zurückgegebenen Chunks müssen aus domain_b stammen
    for chunk in chunks:
        assert chunk["domain"] == "domain_b", f"Domain-Leck: {chunk}"


# ─── Domains nach Ingest ─────────────────────────────────────────────────────


@pytest.mark.integration
def test_domains_after_ingest(app_client: TestClient, tmp_vault: Path) -> None:
    """GET /domains zeigt korrekte Domains nach mehreren Ingests."""
    note = tmp_vault / "domains_check.md"
    note.write_text("---\ndomain: domains_test\n---\nContent für Domains-Test.")

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        app_client.post("/ingest/file", json={"file_path": str(note)})

    resp = app_client.get("/domains")
    assert resp.status_code == 200
    data = resp.json()
    assert "domains_test" in data["domains"]
    assert data["counts"].get("domains_test", 0) > 0


# ─── Find Related ─────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_find_related_not_found(app_client: TestClient, tmp_vault: Path) -> None:
    """POST /find_related für nicht existierende Note → 404."""
    ghost = tmp_vault / "ghost.md"

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        resp = app_client.post("/find_related", json={"file_path": str(ghost)})

    assert resp.status_code == 404


@pytest.mark.integration
def test_find_related_excludes_self(app_client: TestClient, tmp_vault: Path) -> None:
    """POST /find_related gibt keine Chunks der Quell-Note selbst zurück."""
    note = tmp_vault / "related_source.md"
    note.write_text("---\ndomain: related_test\n---\nQuelle für find_related. ETA3333.")

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        app_client.post("/ingest/file", json={"file_path": str(note)})
        resp = app_client.post("/find_related", json={"file_path": str(note), "top_k": 5})

    assert resp.status_code == 200
    data = resp.json()
    assert data["source_path"] == str(note)
    # Kein Ergebnis darf die eigene source_path haben
    for chunk in data["related"]:
        assert chunk["source_path"] != str(note), "find_related gibt self zurück!"


# ─── Delete Chunks ────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_delete_chunks(app_client: TestClient, tmp_vault: Path) -> None:
    """DELETE /chunks löscht alle Chunks einer Datei."""
    note = tmp_vault / "to_delete.md"
    note.write_text("---\ndomain: delete_test\n---\nChunks die gelöscht werden. THETA4444.")

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        app_client.post("/ingest/file", json={"file_path": str(note)})
        resp = app_client.request(
            "DELETE",
            "/chunks",
            params={"source_path": str(note)},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source_path"] == str(note)


@pytest.mark.integration
def test_delete_outside_vault(app_client: TestClient, tmp_path: Path) -> None:
    """DELETE /chunks mit Pfad außerhalb VAULT_ROOT → 400."""
    with patch("titan.service.routes.VAULT_ROOT", tmp_path / "vault"):
        resp = app_client.request(
            "DELETE",
            "/chunks",
            params={"source_path": str(tmp_path / "outside.md")},
        )
    assert resp.status_code == 400


# ─── Notes ────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_notes_lists_ingested_note(app_client: TestClient, tmp_vault: Path) -> None:
    """GET /notes listet eine ingestete Note mit Domain und Chunk-Count."""
    note = tmp_vault / "listed_note.md"
    note.write_text("---\ndomain: notes_test\n---\n# Listed\nInhalt zum Auflisten. KAPPA7777.")

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        ingest = app_client.post("/ingest/file", json={"file_path": str(note)})
        resp = app_client.get("/notes")

    assert ingest.status_code == 200
    assert resp.status_code == 200
    data = resp.json()
    entry = next((n for n in data["notes"] if n["source_path"] == str(note)), None)
    assert entry is not None, "ingestete Note fehlt in /notes"
    assert entry["domain"] == "notes_test"
    assert entry["chunk_count"] == ingest.json()["chunks_created"]
    assert data["total"] == len(data["notes"])


@pytest.mark.integration
def test_notes_excludes_deleted_note(app_client: TestClient, tmp_vault: Path) -> None:
    """Nach DELETE /chunks taucht die Note nicht mehr in /notes auf."""
    note = tmp_vault / "soon_gone.md"
    note.write_text("---\ndomain: notes_test\n---\nWird gleich entfernt. LAMBDA8888.")

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        app_client.post("/ingest/file", json={"file_path": str(note)})
        app_client.request("DELETE", "/chunks", params={"source_path": str(note)})
        resp = app_client.get("/notes")

    assert resp.status_code == 200
    paths = [n["source_path"] for n in resp.json()["notes"]]
    assert str(note) not in paths


# ─── Domain Notes ─────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_domain_notes_isolation(app_client: TestClient, tmp_vault: Path) -> None:
    """GET /domains/<A>/notes gibt nur Notes aus Domain A zurück, nicht aus Domain B."""
    note_a = tmp_vault / "domain_notes_a.md"
    note_a.write_text("---\ndomain: dn_alpha\n---\nContent Domain A. MU1001.")
    note_b = tmp_vault / "domain_notes_b.md"
    note_b.write_text("---\ndomain: dn_beta\n---\nContent Domain B. NU2002.")

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        ingest_a = app_client.post("/ingest/file", json={"file_path": str(note_a)})
        ingest_b = app_client.post("/ingest/file", json={"file_path": str(note_b)})

    assert ingest_a.status_code == 200
    assert ingest_b.status_code == 200

    resp = app_client.get("/domains/dn_alpha/notes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == len(data["notes"])
    for note in data["notes"]:
        assert note["domain"] == "dn_alpha", f"Domain-Leck: {note}"
    # The note from domain A must be present
    paths = [n["source_path"] for n in data["notes"]]
    assert str(note_a) in paths
    # The note from domain B must NOT be present
    assert str(note_b) not in paths


@pytest.mark.integration
def test_domain_notes_unknown(app_client: TestClient) -> None:
    """GET /domains/zzz/notes für unbekannte Domain → 200, notes=[], total=0."""
    resp = app_client.get("/domains/zzz_nonexistent/notes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["notes"] == []
    assert data["total"] == 0


# ─── Content Hash ─────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_notes_include_content_hash(app_client: TestClient, tmp_vault: Path) -> None:
    """GET /notes nach Ingest → content_hash ist ein 64-Zeichen-Hex-String, der dem
    sha256 der rohen Datei-Bytes entspricht."""
    import hashlib

    note = tmp_vault / "hash_check.md"
    note.write_text(
        textwrap.dedent("""\
            ---
            domain: hash_test
            ---
            # Hash-Test
            Inhalt für den content_hash-Test. Unique-HASH-XI5050.
        """)
    )
    expected_hash = hashlib.sha256(note.read_bytes()).hexdigest()

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        ingest_resp = app_client.post("/ingest/file", json={"file_path": str(note)})
        assert ingest_resp.status_code == 200, ingest_resp.text

        resp = app_client.get("/notes")

    assert resp.status_code == 200
    data = resp.json()
    entry = next((n for n in data["notes"] if n["source_path"] == str(note)), None)
    assert entry is not None, "ingestete Note fehlt in /notes"
    assert entry["content_hash"] is not None, "content_hash sollte nicht null sein"
    assert len(entry["content_hash"]) == 64, "content_hash muss ein 64-Zeichen-Hex-Digest sein"
    assert entry["content_hash"] == expected_hash, "content_hash stimmt nicht mit sha256 überein"


@pytest.mark.integration
def test_content_hash_changes_on_edit(app_client: TestClient, tmp_vault: Path) -> None:
    """content_hash ändert sich nach einer Bearbeitung der Note."""
    import hashlib

    note = tmp_vault / "hash_edit.md"
    note.write_text(
        textwrap.dedent("""\
            ---
            domain: hash_test
            ---
            # Hash-Edit-Test v1
            Erster Inhalt. Unique-HASH-OMICRON6060.
        """)
    )

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        resp1 = app_client.post("/ingest/file", json={"file_path": str(note)})
    assert resp1.status_code == 200, resp1.text

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        notes_resp1 = app_client.get("/notes")
    entry1 = next((n for n in notes_resp1.json()["notes"] if n["source_path"] == str(note)), None)
    assert entry1 is not None
    hash_before = entry1["content_hash"]
    assert hash_before == hashlib.sha256(note.read_bytes()).hexdigest()

    # Inhalt ändern → neuer Hash erwartet
    note.write_text(
        textwrap.dedent("""\
            ---
            domain: hash_test
            ---
            # Hash-Edit-Test v2
            Geänderter Inhalt. Unique-HASH-OMICRON6060-V2.
        """)
    )

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        resp2 = app_client.post("/ingest/file", json={"file_path": str(note)})
    assert resp2.status_code == 200, resp2.text

    with patch("titan.service.routes.VAULT_ROOT", tmp_vault):
        notes_resp2 = app_client.get("/notes")
    entry2 = next((n for n in notes_resp2.json()["notes"] if n["source_path"] == str(note)), None)
    assert entry2 is not None
    hash_after = entry2["content_hash"]
    assert hash_after == hashlib.sha256(note.read_bytes()).hexdigest()
    assert hash_before != hash_after, "content_hash muss sich nach einer Inhaltsänderung ändern"
