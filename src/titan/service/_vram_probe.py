"""
_vram_probe.py – Empirische VRAM-Validierung vor Service-Start
==============================================================
Misst, ob BGE-M3 (FP16) und ein Phi-4-Inference-Call parallel in den
verfügbaren VRAM passen.

Kein pytest-Test – einmaliges manuell laufendes Validierungs-Skript.
Ergebnis wird nach docs/ai/vram_probe_results.md geschrieben.

Aufruf:
    uv run python -m titan.service._vram_probe
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def _vram_mb() -> int:
    """Gibt aktuell allokierten VRAM in MB zurück."""
    try:
        import torch

        if not torch.cuda.is_available():
            return 0
        return int(torch.cuda.memory_allocated() / 1024 / 1024)
    except ImportError:
        return 0


def _vram_reserved_mb() -> int:
    """Gibt reservierten (gecachten) VRAM in MB zurück."""
    try:
        import torch

        if not torch.cuda.is_available():
            return 0
        return int(torch.cuda.memory_reserved() / 1024 / 1024)
    except ImportError:
        return 0


def _ollama_ps(ollama_url: str) -> list[dict[str, Any]]:
    """Fragt Ollama /api/ps ab: aktuell geladene Modelle mit VRAM-Angabe."""
    try:
        import httpx

        resp = httpx.get(f"{ollama_url}/api/ps", timeout=5)
        resp.raise_for_status()
        result: list[dict[str, Any]] = resp.json().get("models", [])
        return result
    except Exception as e:
        log.warning("Ollama /api/ps nicht erreichbar: %s", e)
        return []


def _ollama_generate(ollama_url: str, model: str) -> str:
    """Schickt einen kurzen Prompt an Ollama (keep_alive=0 → kein VRAM danach)."""
    import httpx

    payload = {
        "model": model,
        "prompt": "Antwort in einem Wort: Was ist 2+2?",
        "stream": False,
        "keep_alive": 0,
        "options": {"num_predict": 5},
    }
    resp = httpx.post(f"{ollama_url}/api/generate", json=payload, timeout=120)
    resp.raise_for_status()
    result: str = resp.json().get("response", "")
    return result


def run_probe(
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "phi4:latest",
) -> dict[str, Any]:
    """Führt die VRAM-Messung durch und gibt ein Ergebnis-Dict zurück."""
    try:
        import torch
    except ImportError:
        log.error("torch nicht installiert")
        sys.exit(1)

    if not torch.cuda.is_available():
        log.error("CUDA nicht verfügbar – Probe abbrechen")
        sys.exit(1)

    device_name = torch.cuda.get_device_name(0)
    total_vram_mb = int(torch.cuda.get_device_properties(0).total_memory / 1024 / 1024)

    log.info("GPU: %s, gesamt VRAM: %d MB", device_name, total_vram_mb)

    # ── Schritt 1: Baseline ───────────────────────────────────────────────
    torch.cuda.empty_cache()
    baseline_mb = _vram_mb()
    log.info("[1/4] Baseline VRAM allokiert: %d MB", baseline_mb)

    # ── Schritt 2: BGE-M3 laden ───────────────────────────────────────────
    log.info("[2/4] Lade BGE-M3 (FP16) …")
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError:
        log.error("FlagEmbedding nicht installiert")
        sys.exit(1)

    t_load_start = time.perf_counter()
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device="cuda")
    t_load_end = time.perf_counter()

    bge_idle_mb = _vram_mb()
    log.info(
        "BGE-M3 geladen in %.1fs — VRAM allokiert: %d MB (Δ %d MB)",
        t_load_end - t_load_start,
        bge_idle_mb,
        bge_idle_mb - baseline_mb,
    )

    # Dummy-Embedding zur Aktivierung
    import torch as _torch

    with _torch.no_grad():
        model.encode(
            ["VRAM probe warmup"],
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )

    bge_after_warmup_mb = _vram_mb()
    log.info("BGE-M3 nach Warmup-Embedding — VRAM: %d MB", bge_after_warmup_mb)

    # ── Schritt 3: Phi-4 parallel ─────────────────────────────────────────
    log.info("[3/4] Starte Phi-4 via Ollama (keep_alive=0) …")

    t_phi4_start = time.perf_counter()
    try:
        phi4_response = _ollama_generate(ollama_url, ollama_model)
        phi4_ok = True
    except Exception as e:
        log.warning("Phi-4-Aufruf fehlgeschlagen: %s", e)
        phi4_response = ""
        phi4_ok = False
    t_phi4_end = time.perf_counter()

    bge_during_phi4_mb = _vram_mb()
    bge_reserved_during_phi4_mb = _vram_reserved_mb()
    ollama_models_during = _ollama_ps(ollama_url)

    log.info(
        "Phi-4 %s in %.1fs — BGE-M3 VRAM allokiert: %d MB, reserviert: %d MB",
        "OK" if phi4_ok else "FEHLER",
        t_phi4_end - t_phi4_start,
        bge_during_phi4_mb,
        bge_reserved_during_phi4_mb,
    )
    log.info("Ollama-Modelle während Phi-4: %s", json.dumps(ollama_models_during, indent=2))

    # ── Schritt 4: Nachher ────────────────────────────────────────────────
    log.info("[4/4] Nach Phi-4 (keep_alive=0 → sollte entladen sein) …")
    time.sleep(2)  # kurz warten damit Ollama entlädt
    bge_after_phi4_mb = _vram_mb()
    ollama_models_after = _ollama_ps(ollama_url)

    # ── Auswertung ────────────────────────────────────────────────────────
    peak_mb = max(bge_during_phi4_mb, bge_reserved_during_phi4_mb)
    headroom_mb = total_vram_mb - peak_mb
    feasible = headroom_mb >= 512  # mind. 512 MB Puffer

    verdict = "VRAM_MODE=relaxed feasible" if feasible else "VRAM_MODE=strict required"
    log.info("Ergebnis: %s (Peak %d MB, Headroom %d MB)", verdict, peak_mb, headroom_mb)

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "gpu": device_name,
        "total_vram_mb": total_vram_mb,
        "phi4_model": ollama_model,
        "phi4_ok": phi4_ok,
        "phi4_response": phi4_response.strip(),
        "measurements": {
            "baseline_mb": baseline_mb,
            "bge_idle_mb": bge_idle_mb,
            "bge_after_warmup_mb": bge_after_warmup_mb,
            "bge_during_phi4_allocated_mb": bge_during_phi4_mb,
            "bge_during_phi4_reserved_mb": bge_reserved_during_phi4_mb,
            "bge_after_phi4_mb": bge_after_phi4_mb,
            "phi4_load_time_s": round(t_phi4_end - t_phi4_start, 1),
            "bge_load_time_s": round(t_load_end - t_load_start, 1),
        },
        "verdict": verdict,
        "peak_mb": peak_mb,
        "headroom_mb": headroom_mb,
        "feasible": feasible,
        "ollama_models_during": ollama_models_during,
        "ollama_models_after": ollama_models_after,
    }


def write_results(results: dict[str, Any], out_path: Path) -> None:
    """Schreibt die Ergebnisse als Markdown nach docs/ai/vram_probe_results.md."""
    m = results["measurements"]
    lines = [
        "# VRAM Probe Results",
        "",
        f"**Datum:** {results['timestamp']}",
        f"**GPU:** {results['gpu']}",
        f"**Gesamt-VRAM:** {results['total_vram_mb']} MB",
        f"**Phi-4 Modell:** {results['phi4_model']} ({'OK' if results['phi4_ok'] else 'FEHLER'})",
        "",
        "## Messungen",
        "",
        "| Zustand | Allokiert (MB) | Reserviert (MB) |",
        "|---|---|---|",
        f"| Baseline (vor BGE-M3) | {m['baseline_mb']} | — |",
        f"| BGE-M3 idle (nach Load) | {m['bge_idle_mb']} | — |",
        f"| BGE-M3 nach Warmup-Embedding | {m['bge_after_warmup_mb']} | — |",
        (
            f"| BGE-M3 während Phi-4 | {m['bge_during_phi4_allocated_mb']} "
            f"| {m['bge_during_phi4_reserved_mb']} |"
        ),
        f"| BGE-M3 nach Phi-4 (keep_alive=0) | {m['bge_after_phi4_mb']} | — |",
        "",
        "## Ladezeiten",
        "",
        f"- BGE-M3 Load: **{m['bge_load_time_s']}s**",
        f"- Phi-4 Inference (inkl. Load): **{m['phi4_load_time_s']}s**",
        "",
        "## Ergebnis",
        "",
        "| Kennzahl | Wert |",
        "|---|---|",
        f"| Peak VRAM | **{results['peak_mb']} MB** |",
        f"| Headroom | **{results['headroom_mb']} MB** |",
        f"| Feasible | {'✓ Ja' if results['feasible'] else '✗ Nein'} |",
        f"| Verdict | `{results['verdict']}` |",
        "",
        "## Phi-4 Antwort",
        "",
        "```",
        results["phi4_response"] or "(kein Output)",
        "```",
        "",
        "## Ollama-Modelle während Phi-4",
        "",
        "```json",
        json.dumps(results["ollama_models_during"], indent=2),
        "```",
        "",
        "> Generiert von `titan.service._vram_probe`",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Ergebnisse geschrieben nach: %s", out_path)


if __name__ == "__main__":
    import argparse
    import os

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Titan VRAM-Probe")
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--ollama-model", default=os.getenv("OLLAMA_MODEL", "phi4:latest"))
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parents[4] / "docs" / "ai" / "vram_probe_results.md"),
    )
    args = parser.parse_args()

    results = run_probe(ollama_url=args.ollama_url, ollama_model=args.ollama_model)
    write_results(results, Path(args.out))
    print(f"\nVerdict: {results['verdict']}")
    sys.exit(0 if results["feasible"] else 1)
