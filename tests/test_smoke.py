"""Smoke test for the package entry point."""

from __future__ import annotations

import pytest

from titan.main import main


def test_main_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """main() prints help text and exits with code 0."""
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "titan.ingest" in out
    assert "titan.search" in out
    assert "titan.generate" in out
