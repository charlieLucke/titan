"""Wann der Waechter titan neu startet (titan.tools.watchdog)."""

from __future__ import annotations

import pytest

from titan.tools.watchdog import PAUSE, Lage, entscheide


@pytest.mark.parametrize(
    ("lage", "neu"),
    [
        (Lage(True, 200.0, None), False),  # gesund
        (Lage(None, 200.0, None), False),  # Dienst startet oder ist weg: systemd
        (Lage(False, 200.0, None), True),  # GPU-Reset, Limit gesetzt
        (Lage(False, 200.0, PAUSE + 1), True),
        (Lage(False, 285.0, None), False),  # Reset hat das Limit zurueckgesetzt
        (Lage(False, None, None), False),  # Limit unbekannt
        (Lage(False, 200.0, 60.0), False),  # gerade erst neu gestartet
    ],
)
def test_entscheide(lage: Lage, neu: bool) -> None:
    assert entscheide(lage)[0] is neu


def test_grund_nennt_das_limit() -> None:
    assert "285 W" in entscheide(Lage(False, 285.0, None))[1]
