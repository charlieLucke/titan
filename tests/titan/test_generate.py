"""Unit-Tests für titan.generate (Reorder, Prompt-Bau, Ollama-Call, generate_answer).

Reine Logik-Tests ohne Ollama/GPU — der Netzwerk-Call wird gemockt.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from titan.generate import build_prompt, call_ollama, generate_answer, long_context_reorder


def _chunks(n: int) -> list[dict[str, Any]]:
    return [{"text": f"chunk-{i}"} for i in range(n)]


def test_long_context_reorder_best_first_second_last() -> None:
    out = long_context_reorder(_chunks(5))
    assert [c["text"] for c in out] == ["chunk-0", "chunk-2", "chunk-3", "chunk-4", "chunk-1"]


def test_long_context_reorder_noop_for_two_or_fewer() -> None:
    two = _chunks(2)
    assert long_context_reorder(two) == two


def test_build_prompt_contains_query_context_and_source() -> None:
    prompt = build_prompt(
        "Was ist X?",
        [{"text": "X ist Y.", "header": "Kapitel 1", "source": "note.md"}],
    )
    assert "Was ist X?" in prompt
    assert "X ist Y." in prompt
    assert "Quelle: note.md" in prompt
    assert "=== FRAGE ===" in prompt
    assert "=== ANTWORT ===" in prompt


def test_call_ollama_returns_stripped_response_and_is_non_streaming() -> None:
    fake = MagicMock()
    fake.raise_for_status.return_value = None
    fake.json.return_value = {"response": "  eine Antwort  "}
    with patch("titan.generate.requests.post", return_value=fake) as post:
        out = call_ollama("der prompt", model="phi4:latest")
    assert out == "eine Antwort"
    sent = post.call_args.kwargs["json"]
    assert sent["stream"] is False
    assert sent["model"] == "phi4:latest"
    assert sent["keep_alive"] == 0


def test_generate_answer_composes_prompt_and_calls_ollama() -> None:
    with patch("titan.generate.call_ollama", return_value="DIE ANTWORT") as co:
        out = generate_answer("meine frage", [{"text": "der kontext"}])
    assert out == "DIE ANTWORT"
    prompt_arg = co.call_args.args[0]
    assert "meine frage" in prompt_arg
    assert "der kontext" in prompt_arg
