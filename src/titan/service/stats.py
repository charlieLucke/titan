"""
stats.py – Pure helpers for the /stats endpoint
================================================
No service/torch/Qdrant dependencies, so the metric assembly is fully unit-testable.
The route gathers the raw numbers from ``ServiceState`` and calls ``build_stats``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from titan.service.schemas import LatencyStats, StatsResponse


def _percentile(ordered: Sequence[int], pct: float) -> int:
    """Nearest-rank percentile of a pre-sorted, non-empty sequence."""
    idx = math.ceil(pct / 100 * len(ordered)) - 1
    return ordered[min(max(idx, 0), len(ordered) - 1)]


def compute_latency_stats(latencies: Sequence[int]) -> LatencyStats:
    """Summarize a window of latency samples (p50/p95/max). Empty → all None."""
    if not latencies:
        return LatencyStats(count=0, p50_ms=None, p95_ms=None, max_ms=None)
    ordered = sorted(latencies)
    return LatencyStats(
        count=len(ordered),
        p50_ms=_percentile(ordered, 50),
        p95_ms=_percentile(ordered, 95),
        max_ms=ordered[-1],
    )


def build_stats(
    *,
    uptime_seconds: int,
    collection_name: str,
    domain_counts: Mapping[str, int],
    search_count: int,
    cache_hit_count: int,
    latencies: Sequence[int],
    cache_enabled: bool,
    cache_entries: int | None,
    last_ingest_age_seconds: int | None,
) -> StatsResponse:
    """Assemble a ``StatsResponse`` from raw runtime counters (pure function)."""
    hit_rate = (cache_hit_count / search_count) if search_count else 0.0
    return StatsResponse(
        uptime_seconds=uptime_seconds,
        collection_name=collection_name,
        total_chunks=sum(domain_counts.values()),
        domain_count=len(domain_counts),
        searches_total=search_count,
        cache_hits=cache_hit_count,
        cache_hit_rate=round(hit_rate, 4),
        search_latency=compute_latency_stats(latencies),
        cache_enabled=cache_enabled,
        cache_entries=cache_entries,
        last_ingest_age_seconds=last_ingest_age_seconds,
    )
