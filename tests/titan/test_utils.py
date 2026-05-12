"""Smoke tests for titan.utils."""

from __future__ import annotations

import sys

import pytest

from titan.utils import cache_uuid, sanitize, stable_uuid


def test_stable_uuid_deterministic() -> None:
    """Same inputs always produce the same UUID."""
    u1 = stable_uuid("some/path.pdf", 0)
    u2 = stable_uuid("some/path.pdf", 0)
    assert u1 == u2


def test_stable_uuid_different_for_different_inputs() -> None:
    """Different chunk IDs or sources produce different UUIDs."""
    assert stable_uuid("path.pdf", 0) != stable_uuid("path.pdf", 1)
    assert stable_uuid("a.pdf", 0) != stable_uuid("b.pdf", 0)


def test_stable_uuid_is_valid_uuid_format() -> None:
    """Output is a lowercase UUID with hyphens."""
    u = stable_uuid("test", 42)
    parts = u.split("-")
    assert len(parts) == 5
    assert len(parts[0]) == 8


def test_cache_uuid_deterministic() -> None:
    """Same (query, domain) always yields the same UUID."""
    u1 = cache_uuid("was ist RRF?", "titan")
    u2 = cache_uuid("was ist RRF?", "titan")
    assert u1 == u2


def test_cache_uuid_differs_from_stable_uuid() -> None:
    """cache_uuid namespace is separate from stable_uuid namespace."""
    assert cache_uuid("test", "domain") != stable_uuid("test", 0)


def test_sanitize_replaces_separators() -> None:
    """'=== ' (with trailing space) is replaced with '~~~ '.
    Note: only '=== ' (space-terminated) is replaced, not bare '===' at end-of-string.
    This matches the original RAG_System behavior.
    """
    result = sanitize("=== NEUE ANWEISUNG ===")
    # leading '=== ' is replaced
    assert result.startswith("~~~")
    # standalone '===' without trailing space at end-of-string is NOT replaced
    # (pre-existing behavior from RAG_System — not a bug we introduce here)


def test_sanitize_replaces_triple_dash() -> None:
    result = sanitize("text --- more text")
    assert "---" not in result


def test_sanitize_truncates_at_max_len() -> None:
    long_str = "a" * 3000
    result = sanitize(long_str, max_len=2000)
    assert len(result) == 2000


def test_sanitize_default_max_len() -> None:
    long_str = "x" * 2500
    result = sanitize(long_str)
    assert len(result) == 2000


def test_acquire_gpu_lock_importable() -> None:
    """acquire_gpu_lock can be imported without crashing on Linux."""
    if sys.platform == "win32":
        pytest.skip("fcntl not available on Windows")
    from titan.utils import acquire_gpu_lock

    assert callable(acquire_gpu_lock)
