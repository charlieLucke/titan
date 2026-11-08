"""
rerank.py – Cross-Encoder ueber die besten Treffer (Qwen3-Reranker)
====================================================================
Optional und standardmaessig aus (``RERANK_MODEL`` leer). Eingeschaltet liest ein
Qwen3-Reranker jede der besten ``RERANK_POOL`` Passagen zusammen mit der Frage und
ordnet sie nach der Wahrscheinlichkeit von "yes" (beantwortet die Passage die Frage).
Die Reihenfolge danach bleibt unveraendert.

Gemessen in titan-platform am Vault-Goldset (DECISIONS dort, 2026-10-29 und 2026-11-04):
Qwen3-Reranker-4B ueber zehn Kandidaten, Passagen auf 512 Tokens gekuerzt, Stelle @1
um rund 14 Punkte besser als ColBERT allein, rund 0,8 s pro Frage auf der RTX 4070 Ti
SUPER. Kosten: etwa 8 GB VRAM in fp16, die Phi-4 dann fehlen.

Prompt und Bewertung folgen der Modellkarte. Die Modell-Commits sind gepinnt, damit
ein neuer Upload die Werte nicht still veraendert.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import torch

log = logging.getLogger(__name__)

MODELS = {
    "qwen3-reranker-0.6b": ("Qwen/Qwen3-Reranker-0.6B", "e61197ed45024b0ed8a2d74b80b4d909f1255473"),
    "qwen3-reranker-4b": ("Qwen/Qwen3-Reranker-4B", "22e683669bc0f0bd69640a1354a6d0aebcfeede5"),
}
INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query "
    'and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
    "<|im_start|>user\n"
)
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
BATCH = 8


class Scorer(Protocol):
    """Bewertet Passagen zu einer Frage; hoeher heisst passender."""

    def scores(self, query: str, passages: Sequence[str]) -> list[float]:
        """Ein Wert pro Passage, in derselben Reihenfolge."""
        ...


def pair_text(query: str, passage: str) -> str:
    """Der Nutzerteil eines Frage-Passage-Paars, wie auf der Modellkarte."""
    return f"<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: {passage}"


def passage_text(hit: dict[str, Any]) -> str:
    """Die Passage, die der Reranker liest: Notiz und Abschnitt vor dem Text.

    Wie ``ingest.mit_kopfzeile``; der Notiztitel ist im Payload nicht gespeichert, der
    Dateiname steht dafuer.
    """
    title = Path(str(hit.get("source", ""))).stem
    header = hit.get("header") or ""
    head = f"{title} › {header}" if header else title
    return f"{head}\n\n{hit.get('text', '')}"


def rerank(
    scorer: Scorer, query: str, hits: list[dict[str, Any]], pool: int
) -> list[dict[str, Any]]:
    """Ordnet die ersten ``pool`` Treffer nach dem Cross-Encoder neu.

    Gleichstand nach der bisherigen Reihenfolge. Jeder neu geordnete Treffer bekommt
    ``rerank_score``; ``score`` und der Rest bleiben, wie sie waren. ``rank`` wird
    neu durchnummeriert.
    """
    head, tail = hits[:pool], hits[pool:]
    if not head:
        return hits
    values = scorer.scores(query, [passage_text(h) for h in head])
    order = sorted(range(len(head)), key=lambda i: (-values[i], i))
    reordered = [{**head[i], "rerank_score": round(values[i], 6)} for i in order] + tail
    return [{**h, "rank": rank} for rank, h in enumerate(reordered, start=1)]


class QwenReranker:
    """Geladener Qwen3-Reranker auf der GPU.

    Args:
        name: Schluessel aus :data:`MODELS`.
        max_tokens: Grenze fuer das Frage-Passage-Paar (512 behielt den ganzen Gewinn).

    Raises:
        ValueError: bei einem unbekannten Modellnamen.
    """

    def __init__(self, name: str, max_tokens: int = 512) -> None:
        if name not in MODELS:
            raise ValueError(f"unbekannter Reranker {name!r}; bekannt: {sorted(MODELS)}")
        repo, revision = MODELS[name]
        # Zur Laufzeit importiert: transformers ist schwer und wird nur gebraucht, wenn
        # der Reranker eingeschaltet ist.
        transformers: Any = importlib.import_module("transformers")
        self.name = name
        self.max_tokens = max_tokens
        self.tok = transformers.AutoTokenizer.from_pretrained(
            repo, revision=revision, padding_side="left"
        )
        model = transformers.AutoModelForCausalLM.from_pretrained(
            repo, revision=revision, dtype=torch.float16
        )
        self.model = model.cuda().eval()
        self.yes = self.tok.convert_tokens_to_ids("yes")
        self.no = self.tok.convert_tokens_to_ids("no")
        self.pre = self.tok.encode(PREFIX, add_special_tokens=False)
        self.suf = self.tok.encode(SUFFIX, add_special_tokens=False)
        log.info("Reranker %s geladen (%s@%s)", name, repo, revision[:8])

    def scores(self, query: str, passages: Sequence[str]) -> list[float]:
        out: list[float] = []
        with torch.no_grad():
            for start in range(0, len(passages), BATCH):
                pairs = [pair_text(query, p) for p in passages[start : start + BATCH]]
                enc = self.tok(
                    pairs,
                    padding=False,
                    truncation="longest_first",
                    return_attention_mask=False,
                    max_length=self.max_tokens,
                )
                enc["input_ids"] = [self.pre + ids + self.suf for ids in enc["input_ids"]]
                batch = self.tok.pad(enc, padding=True, return_tensors="pt").to("cuda")
                # Nur die letzte Position entscheidet ja/nein; Logits ueber alle Positionen
                # und das ganze Vokabular kosten rund 1,3 GB pro Batch.
                logits = self.model(**batch, logits_to_keep=1).logits[:, -1, :]
                pair = torch.stack([logits[:, self.no], logits[:, self.yes]], dim=1).float()
                out += torch.log_softmax(pair, dim=1)[:, 1].exp().tolist()
        return out
