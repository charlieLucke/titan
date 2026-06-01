"""Unit tests for titan.service.stats (pure /stats helpers)."""

from __future__ import annotations

from titan.service.stats import build_stats, compute_latency_stats


def test_latency_stats_empty() -> None:
    """No samples → count 0 and all percentiles None."""
    s = compute_latency_stats([])
    assert s.count == 0
    assert s.p50_ms is None
    assert s.p95_ms is None
    assert s.max_ms is None


def test_latency_stats_percentiles_and_sorting() -> None:
    """Unsorted input is sorted; nearest-rank p50/p95 over 10..100."""
    s = compute_latency_stats([100, 10, 50, 20, 90, 30, 80, 40, 70, 60])
    assert s.count == 10
    assert s.max_ms == 100
    assert s.p50_ms == 50  # ceil(0.50*10)=5 → index 4 → 50
    assert s.p95_ms == 100  # ceil(0.95*10)=10 → index 9 → 100


def test_latency_stats_single() -> None:
    """A single sample is its own p50/p95/max."""
    s = compute_latency_stats([42])
    assert s.count == 1
    assert s.p50_ms == 42
    assert s.p95_ms == 42
    assert s.max_ms == 42


def test_build_stats_totals_and_hit_rate() -> None:
    """Totals sum domains; hit rate = hits / searches."""
    resp = build_stats(
        uptime_seconds=120,
        collection_name="mein_wissen",
        domain_counts={"projekte": 30, "system": 10},
        search_count=4,
        cache_hit_count=1,
        latencies=[10, 20, 30],
        cache_enabled=True,
        cache_entries=7,
        last_ingest_age_seconds=None,
    )
    assert resp.total_chunks == 40
    assert resp.domain_count == 2
    assert resp.searches_total == 4
    assert resp.cache_hits == 1
    assert resp.cache_hit_rate == 0.25
    assert resp.search_latency.count == 3
    assert resp.cache_entries == 7
    assert resp.last_ingest_age_seconds is None


def test_build_stats_no_searches_zero_rate() -> None:
    """Zero searches must not divide by zero; rate is 0.0."""
    resp = build_stats(
        uptime_seconds=0,
        collection_name="c",
        domain_counts={},
        search_count=0,
        cache_hit_count=0,
        latencies=[],
        cache_enabled=False,
        cache_entries=None,
        last_ingest_age_seconds=None,
    )
    assert resp.cache_hit_rate == 0.0
    assert resp.total_chunks == 0
    assert resp.domain_count == 0
    assert resp.search_latency.p50_ms is None
