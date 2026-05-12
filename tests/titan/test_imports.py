"""Import smoke tests – alle Module müssen ohne Fehler importierbar sein."""

from __future__ import annotations


def test_modules_importable() -> None:
    """Alle Titan-Module sind zur Laufzeit importierbar."""
    import titan.eval.ab_eval
    import titan.evaluate
    import titan.generate
    import titan.ingest
    import titan.search
    import titan.tools.init_col
    import titan.utils

    assert titan.utils is not None
    assert titan.ingest is not None
    assert titan.search is not None
    assert titan.generate is not None
    assert titan.evaluate is not None
    assert titan.eval.ab_eval is not None
    assert titan.tools.init_col is not None
