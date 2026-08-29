"""Tests fuer den Domain-Zaehler beim Ingest.

Der Zaehler ist In-Memory und wird beim Start aus einem Voll-Scroll aufgebaut;
danach fortgeschrieben. Ein Fortschreibungsfehler faellt deshalb erst auf, wenn
jemand /domains gegen /domains/{d}/notes haelt - beim Domain-Umzug des Vaults am
29.08.2026 meldete /domains 17 Chunks fuer eine Domain, in der keine einzige
Note mehr lag.
"""

from __future__ import annotations

import pytest

from titan.service.routes import _adjust_domain_count
from titan.service.state import state


@pytest.fixture(autouse=True)
def _sauberer_zaehler():
    vorher = dict(state.domain_counts)
    state.domain_counts.clear()
    yield
    state.domain_counts.clear()
    state.domain_counts.update(vorher)


def test_erhoeht() -> None:
    _adjust_domain_count("betrieb", 5)
    assert state.domain_counts["betrieb"] == 5


def test_verringert() -> None:
    _adjust_domain_count("betrieb", 10)
    _adjust_domain_count("betrieb", -4)
    assert state.domain_counts["betrieb"] == 6


def test_domain_verschwindet_bei_null() -> None:
    """Eine leere Domain gehoert nicht in /domains.

    Genau das war beim Umzug sichtbar: 'system' stand mit 17 Chunks in der Liste,
    obwohl keine Note mehr darin lag.
    """
    _adjust_domain_count("system", 3)
    _adjust_domain_count("system", -3)
    assert "system" not in state.domain_counts


def test_negativ_wird_nicht_gespeichert() -> None:
    _adjust_domain_count("betrieb", 2)
    _adjust_domain_count("betrieb", -10)
    assert "betrieb" not in state.domain_counts


def test_none_ist_wirkungslos() -> None:
    """Legacy-Chunks ohne Domain duerfen den Zaehler nicht anfassen."""
    _adjust_domain_count(None, 5)
    assert state.domain_counts == {}


def test_domainwechsel_verschiebt_statt_zu_verdoppeln() -> None:
    """Der eigentliche Fehler: alte Domain behielt ihre Chunks.

    Note hatte 8 Chunks in 'system', wird mit 10 Chunks nach 'betrieb'
    re-ingestiert. Vorher wurden die 8 von 'betrieb' abgezogen, wo sie nie
    lagen — 'system' blieb bei 8 stehen.
    """
    state.domain_counts.update({"system": 8, "betrieb": 20})

    n_before, neue_chunks = 8, 10
    prev_domain, domain = "system", "betrieb"

    _adjust_domain_count(prev_domain, -n_before)
    _adjust_domain_count(domain, neue_chunks)

    assert "system" not in state.domain_counts
    assert state.domain_counts["betrieb"] == 30


def test_ohne_wechsel_wird_die_differenz_gebucht() -> None:
    state.domain_counts["betrieb"] = 20
    n_before, neue_chunks = 8, 10
    _adjust_domain_count("betrieb", neue_chunks - n_before)
    assert state.domain_counts["betrieb"] == 22
