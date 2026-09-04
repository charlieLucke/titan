"""Unit tests for the two report endpoints.

They exist so the vault checks stop requiring a remembered command line. Both are
thin: the reports themselves are `titan.tools`, tested there. What is worth
guarding here is that the route hands those functions the right input — the first
one used to fetch `/notes` over HTTP from the service it runs inside.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from titan.service import routes
from titan.service.repository import NoteAggregate


class _FakeRepo:
    def __init__(self, notes: list[NoteAggregate]) -> None:
        self._notes = notes

    def aggregate_notes(self, domain: str | None = None) -> list[NoteAggregate]:
        return self._notes


def _note(path: str, links: list[str]) -> NoteAggregate:
    return NoteAggregate(
        source_path=path, domain="betrieb", chunk_count=1, content_hash="h", links=links
    )


def test_graph_check_reads_the_repository_not_its_own_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`graph_check.sammle` calls GET /notes; inside the service that would be the
    process asking itself over the network."""
    notes = [_note("a.md", ["b"]), _note("b.md", [])]
    monkeypatch.setattr(routes, "_repo", lambda: _FakeRepo(notes))

    report = routes.graph_check_report()

    assert report["notizen"] == 2
    assert report["links_gesamt"] == 1
    assert report["tote_links"] == []
    # "a" is linked by nobody, "b" has no outgoing links — both are real findings.
    assert report["verwaist"] == ["a"]
    assert report["ohne_ausgehende_links"] == ["b"]


def test_graph_check_finds_a_dead_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_repo", lambda: _FakeRepo([_note("a.md", ["nirgendwo"])]))

    # Tuples in Python, arrays once serialised — the route does not reshape them.
    assert routes.graph_check_report()["tote_links"] == [("a", "nirgendwo")]


def test_offene_punkte_reads_the_vault_from_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """From disk and not from the index: an open point can sit in a note that is
    not indexed right now, and that is exactly the one that would fall out."""
    note = tmp_path / "x.md"
    note.write_text(
        "---\ndomain: betrieb\n---\n\n# X\n\n## Offene Punkte\n- [ ] etwas tun (seit 2026-01-01)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "VAULT_ROOT", tmp_path)

    report = routes.offene_punkte_report()

    assert report["ueberschrift"] == "Offene Punkte"
    assert len(report["punkte"]) == 1
    assert "etwas tun" in report["punkte"][0]["text"]


def test_offene_punkte_can_collect_ideas_instead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    note = tmp_path / "x.md"
    note.write_text(
        "---\ndomain: betrieb\n---\n\n# X\n\n## Ideen\n- [ ] eine idee (seit 2026-01-01)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "VAULT_ROOT", tmp_path)

    assert routes.offene_punkte_report(ideen=True)["ueberschrift"] == "Ideen"
    assert routes.offene_punkte_report(ideen=False)["punkte"] == []


def test_offene_punkte_passes_hints_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`sammle` reports headings that look like a misnamed list; dropping those
    would hide the reason a note contributes nothing."""
    seen: dict[str, Any] = {}

    def fake_sammle(wurzel: Path, ueberschrift: str) -> tuple[list[Any], list[str]]:
        seen["wurzel"] = wurzel
        return [], ["notes/y.md: 'Offene Fragen' — gemeint 'Offene Punkte'?"]

    monkeypatch.setattr(routes.offene_punkte, "sammle", fake_sammle)
    monkeypatch.setattr(routes, "VAULT_ROOT", tmp_path)

    report = routes.offene_punkte_report()

    assert seen["wurzel"] == tmp_path
    assert report["hinweise"] == ["notes/y.md: 'Offene Fragen' — gemeint 'Offene Punkte'?"]
