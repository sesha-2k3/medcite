"""MedCPT cross-encoder: reads a query and a passage together and scores their relevance.

Per the model card, input is ``[query, passage]`` pairs truncated to 512 tokens, and the single
output logit is the score: higher is more relevant. Scores are comparable within one query, not
across queries. Scoring is CPU-bound and blocking; async callers use ``asyncio.to_thread``.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from app.config import Settings

PAIR_MAX_TOKENS = 512


class RelevanceScorer(Protocol):
    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


class MedCPTCrossEncoder:
    def __init__(
        self, name: str, cache_dir: Path, batch_size: int = 16, local_files_only: bool = True
    ) -> None:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        options = {"cache_dir": str(cache_dir), "local_files_only": local_files_only}
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(name, **options)
            self._model = AutoModelForSequenceClassification.from_pretrained(name, **options)
        except OSError as exc:
            raise OSError(f"{name} is not in {cache_dir}; run: make models") from exc
        self._model.eval()
        self._batch_size = batch_size

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        import torch

        scores: list[float] = []
        items = list(passages)
        for start in range(0, len(items), self._batch_size):
            batch = items[start : start + self._batch_size]
            encoded = self._tokenizer(
                [query] * len(batch),
                batch,
                truncation=True,
                padding=True,
                max_length=PAIR_MAX_TOKENS,
                return_tensors="pt",
            )
            with torch.inference_mode():
                logits = self._model(**encoded).logits.squeeze(dim=1)
            scores.extend(float(value) for value in logits.tolist())
        return scores


_lock = threading.Lock()
_loaded: dict[tuple[str, str], MedCPTCrossEncoder] = {}


def load_cross_encoder(settings: Settings, *, local_files_only: bool = True) -> MedCPTCrossEncoder:
    """Process-wide singleton, like the embedder, and likewise loaded from the local cache only.

    This also stops transformers from fetching a converted safetensors copy in the background,
    which it otherwise does for repositories that only publish pytorch_model.bin.
    """
    key = (settings.rerank_model, settings.hf_home)
    with _lock:
        if key not in _loaded:
            _loaded[key] = MedCPTCrossEncoder(
                settings.rerank_model,
                Path(settings.hf_home) / "hub",
                batch_size=settings.rerank_batch_size,
                local_files_only=local_files_only,
            )
        return _loaded[key]
