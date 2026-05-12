"""Entry point for titan – zeigt verfügbare Sub-CLIs oder startet den Service."""

from __future__ import annotations

import sys


def main() -> None:
    """Dispatcher: `python -m titan service [--port N]` oder Hilfe."""
    if len(sys.argv) >= 2 and sys.argv[1] == "service":
        import argparse

        parser = argparse.ArgumentParser(prog="titan service", description="Titan RAG Service")
        parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
        args = parser.parse_args(sys.argv[2:])

        from titan.service.app import serve

        serve(port=args.port)
        return

    print(
        "Titan – Local High-Performance RAG\n"
        "\n"
        "Verfügbare Sub-CLIs:\n"
        "\n"
        "  python -m titan service       FastAPI-Service starten (BGE-M3 + Qdrant)\n"
        "  python -m titan.ingest        PDFs einlesen, chunken, in Qdrant speichern\n"
        "  python -m titan.search        Hybridsuche (Dense + Sparse + ColBERT Rerank)\n"
        "  python -m titan.generate      Antwort generieren (liest Chunks von stdin)\n"
        "  python -m titan.evaluate      RAG-Triade Evaluierung (CR / GR / AR)\n"
        "  python -m titan.eval.ab_eval  A/B-Evaluierung über Eval-Cases-Set\n"
        "  python -m titan.tools.init_col  Qdrant-Collection initialisieren\n"
        "\n"
        "Vollständige Pipeline:\n"
        "  python -m titan.search 'Frage' --json | python -m titan.generate\n"
        "\n"
        "Hilfe zu einem Sub-CLI:\n"
        "  python -m titan.search --help\n"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
