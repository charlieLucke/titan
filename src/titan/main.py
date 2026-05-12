"""Entry point for titan – zeigt verfügbare Sub-CLIs."""

from __future__ import annotations

import sys


def main() -> None:
    """Listet die verfügbaren Sub-CLIs von Titan."""
    print(
        "Titan – Local High-Performance RAG\n"
        "\n"
        "Verfügbare Sub-CLIs:\n"
        "\n"
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
