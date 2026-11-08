"""
watchdog.py – titan nach einem GPU-Reset neu starten
=====================================================
Nach einem Treiber-Reset (TDR unter WSL) bleibt der Dienst oben, aber CUDA rechnet nicht
mehr; /health meldet dann ``cuda_ok: false``. Nur ein Neustart hilft. Diesen Neustart
uebernimmt der Waechter, den ein systemd-Timer alle zwei Minuten aufruft
(``deploy/titan-watchdog.timer``).

Er startet nur neu, wenn

- /health antwortet und ausdruecklich ``cuda_ok: false`` meldet (ein Dienst, der gerade
  startet oder abgestuerzt ist, ist Sache von systemd ``Restart=on-failure``),
- das Power-Limit der GPU hoechstens ``MAX_WATT`` betraegt: ein Reset setzt es auf den
  Werkswert zurueck (285 W), und unter dem Werkswert stuerzte die Karte zuletzt ab;
  setzen kann es nur Windows mit Adminrechten,
- der letzte Neustart laenger als ``PAUSE`` zurueckliegt, damit eine kaputte GPU nicht
  im Kreis neu gestartet wird.

Aufruf:
    python -m titan.tools.watchdog
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)

HEALTH_URL = "http://localhost:8765/health"
UNIT = "titan-service"
MAX_WATT = 200.0
PAUSE = 15 * 60
STAMP = Path(__file__).resolve().parents[3] / "logs" / "watchdog_last_restart"


@dataclass(frozen=True, slots=True)
class Lage:
    """Was der Waechter sieht."""

    cuda_ok: bool | None
    """None: /health nicht erreichbar oder ohne Angabe."""
    power_limit_w: float | None
    seit_letztem_neustart_s: float | None


def entscheide(lage: Lage) -> tuple[bool, str]:
    """Ob neu gestartet wird, und warum (fuer das Log)."""
    if lage.cuda_ok is not False:
        return False, "gesund" if lage.cuda_ok else "health ohne Antwort, systemd zustaendig"
    if lage.power_limit_w is None:
        return False, "CUDA kaputt, Power-Limit unbekannt: kein Neustart"
    if lage.power_limit_w > MAX_WATT:
        return False, (
            f"CUDA kaputt, Power-Limit {lage.power_limit_w:.0f} W ueber {MAX_WATT:.0f} W: "
            "erst in Windows als Admin setzen (nvidia-smi -pl 200), dann neu starten"
        )
    if lage.seit_letztem_neustart_s is not None and lage.seit_letztem_neustart_s < PAUSE:
        return False, "CUDA kaputt, letzter Neustart zu kurz her: GPU pruefen"
    return True, "CUDA kaputt nach GPU-Reset: Neustart"


def _cuda_ok() -> bool | None:
    try:
        resp = requests.get(HEALTH_URL, timeout=10)
        value = resp.json().get("cuda_ok")
    except (requests.RequestException, json.JSONDecodeError):
        return None
    return value if isinstance(value, bool) else None


def _power_limit() -> float | None:
    try:
        done = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.limit", "--format=csv,noheader,nounits"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        return float(done.stdout.split()[0])
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        return None


def _seit_letztem_neustart() -> float | None:
    try:
        return time.time() - STAMP.stat().st_mtime
    except FileNotFoundError:
        return None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    lage = Lage(_cuda_ok(), _power_limit(), _seit_letztem_neustart())
    neu, grund = entscheide(lage)
    if not neu:
        if lage.cuda_ok is False:
            log.error(grund)
        else:
            log.info(grund)
        return 0
    log.warning(grund)
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.touch()
    # Feste Argumente, keine Eingabe von aussen.
    subprocess.run(  # noqa: S603
        ["systemctl", "--user", "restart", UNIT],  # noqa: S607
        check=True,
        timeout=120,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
