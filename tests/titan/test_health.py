"""GET /health erkennt eine GPU, die nach einem Treiber-Reset nicht mehr rechnet."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from titan.service import routes
from titan.service.state import state


@pytest.fixture
def loaded_state() -> Generator[None, None, None]:
    saved = (state.bge_model, state.qdrant_client)
    state.bge_model = MagicMock()
    state.qdrant_client = MagicMock()
    try:
        with patch.object(routes.QdrantRepository, "collection_ready", return_value=True):
            yield
    finally:
        state.bge_model, state.qdrant_client = saved


def _broken_cuda(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("CUDA error: unspecified launch failure")


@pytest.mark.usefixtures("loaded_state")
def test_health_degraded_when_cuda_fails() -> None:
    with patch.object(routes.torch, "ones", side_effect=_broken_cuda):
        health = routes.health()
    assert health.cuda_ok is False
    assert health.status == "degraded"
    assert health.vram_used_mb is None


@pytest.mark.usefixtures("loaded_state")
def test_health_ok_when_cuda_answers() -> None:
    with (
        patch.object(routes, "_cuda_ok", return_value=True),
        patch.object(routes.torch.cuda, "memory_allocated", return_value=3 * 1024 * 1024),
    ):
        health = routes.health()
    assert health.cuda_ok is True
    assert health.status == "ok"
    assert health.vram_used_mb == 3


def test_health_without_model_skips_the_probe() -> None:
    saved = state.bge_model
    state.bge_model = None
    try:
        with patch.object(routes, "_cuda_ok", side_effect=AssertionError("no probe")):
            health = routes.health()
    finally:
        state.bge_model = saved
    assert health.cuda_ok is None
    assert health.status == "degraded"
