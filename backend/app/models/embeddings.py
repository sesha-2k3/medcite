"""MedCPT embeddings.

Per the model cards, the article encoder takes ``[title, text]`` pairs (max 512 tokens), the
query encoder takes short texts (max 64 tokens), and both use the [CLS] token's last hidden
state. Vectors are L2-normalized so pgvector's cosine distance ranks them consistently.

Encoding is CPU-bound and blocking: async callers should run it with ``asyncio.to_thread``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt

from app.config import Settings
from app.db.models import EMBEDDING_DIM

Vectors = npt.NDArray[np.float32]

ARTICLE_MAX_TOKENS = 512
QUERY_MAX_TOKENS = 64


class Embedder(Protocol):
    @property
    def article_model(self) -> str: ...

    def embed_articles(self, pairs: Sequence[tuple[str, str]]) -> Vectors:
        """One unit vector per (title, text) pair, shape (n, 768)."""
        ...

    def embed_queries(self, texts: Sequence[str]) -> Vectors:
        """One unit vector per query, shape (n, 768)."""
        ...


class MedCPTEmbedder:
    def __init__(
        self,
        query_model: str,
        article_model: str,
        cache_dir: Path,
        batch_size: int = 16,
        local_files_only: bool = False,
    ) -> None:
        self._article_model_name = article_model
        self._batch_size = batch_size
        self._query = _Encoder(query_model, cache_dir, local_files_only)
        self._article = _Encoder(article_model, cache_dir, local_files_only)

    @property
    def article_model(self) -> str:
        return self._article_model_name

    def embed_articles(self, pairs: Sequence[tuple[str, str]]) -> Vectors:
        titles = [title for title, _ in pairs]
        texts = [text for _, text in pairs]
        return self._batched(
            len(pairs),
            lambda s: self._article.encode(titles[s], texts[s], max_length=ARTICLE_MAX_TOKENS),
        )

    def embed_queries(self, texts: Sequence[str]) -> Vectors:
        items = list(texts)
        return self._batched(
            len(items), lambda s: self._query.encode(items[s], None, max_length=QUERY_MAX_TOKENS)
        )

    def _batched(self, total: int, encode: Callable[[slice], Vectors]) -> Vectors:
        if total == 0:
            return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        parts = [encode(slice(i, i + self._batch_size)) for i in range(0, total, self._batch_size)]
        return np.concatenate(parts).astype(np.float32)


class _Encoder:
    """One tokenizer + model pair, loaded once."""

    def __init__(self, name: str, cache_dir: Path, local_files_only: bool) -> None:
        from transformers import AutoModel, AutoTokenizer

        options = {"cache_dir": str(cache_dir), "local_files_only": local_files_only}
        self._tokenizer = AutoTokenizer.from_pretrained(name, **options)
        self._model = AutoModel.from_pretrained(name, **options)
        self._model.eval()

    def encode(self, first: list[str], second: list[str] | None, max_length: int) -> Vectors:
        import torch

        encoded = self._tokenizer(
            first,
            second,
            truncation=True,
            padding=True,
            max_length=max_length,
            return_tensors="pt",
        )
        with torch.inference_mode():
            cls = self._model(**encoded).last_hidden_state[:, 0, :]
            unit = torch.nn.functional.normalize(cls, p=2, dim=1)
        vectors: Vectors = unit.cpu().numpy().astype(np.float32)
        return vectors


_lock = threading.Lock()
_loaded: dict[tuple[str, str, str], MedCPTEmbedder] = {}


def load_embedder(settings: Settings, *, local_files_only: bool = False) -> MedCPTEmbedder:
    """Process-wide singleton: weights take seconds and ~1 GB of RAM to load."""
    key = (settings.embed_query_model, settings.embed_article_model, settings.hf_home)
    with _lock:
        if key not in _loaded:
            _loaded[key] = MedCPTEmbedder(
                query_model=settings.embed_query_model,
                article_model=settings.embed_article_model,
                cache_dir=Path(settings.hf_home) / "hub",
                batch_size=settings.embed_batch_size,
                local_files_only=local_files_only,
            )
        return _loaded[key]
