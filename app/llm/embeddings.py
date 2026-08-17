"""Contrato de embeddings para a camada de recuperação.

A recuperação lexical funciona sem embeddings em SQLite e Postgres. Embeddings
são uma extensão opcional: quando `EMBEDDINGS_PROVIDER=none` (padrão), o
provider nulo é usado e a busca permanece determinística, o que mantém os
testes reprodutíveis e sem chamada externa.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from app.core.config import settings


class EmbeddingError(Exception):
    pass


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    @property
    def is_semantic(self) -> bool:
        """False quando o provider não produz semântica real (fallback)."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class NullEmbeddingProvider:
    """Provider inativo: sinaliza que só a busca lexical está disponível."""

    @property
    def model(self) -> str:
        return "none"

    @property
    def dimensions(self) -> int:
        return 0

    @property
    def is_semantic(self) -> bool:
        return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


class HashingEmbeddingProvider:
    """Embedding determinístico por hashing de tokens.

    Não é semântico: serve para exercitar o caminho vetorial em testes sem
    depender de serviço externo. Não deve ser usado em produção.
    """

    def __init__(self, dimensions: int = 256) -> None:
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return f"hashing-{self._dimensions}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def is_semantic(self) -> bool:
        return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in re.findall(r"\w+", (text or "").lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class OpenAIEmbeddingProvider:
    """Embeddings reais da OpenAI. Nunca é acionado nos testes padrão."""

    def __init__(self, model: str | None = None) -> None:
        if not settings.openai_api_key:
            raise EmbeddingError("OPENAI_API_KEY is not configured.")
        from openai import OpenAI

        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = model or settings.embeddings_model
        self._dimensions = 1536

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def is_semantic(self) -> bool:
        return True

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(model=self._model, input=texts)
        vectors = [list(item.embedding) for item in response.data]
        if vectors:
            self._dimensions = len(vectors[0])
        return vectors


def get_embedding_provider() -> EmbeddingProvider:
    provider_name = (settings.embeddings_provider or "none").strip().lower()
    if provider_name in {"", "none", "off", "disabled"}:
        return NullEmbeddingProvider()
    if provider_name == "hashing":
        return HashingEmbeddingProvider()
    if provider_name == "openai":
        return OpenAIEmbeddingProvider()
    raise EmbeddingError(
        f"Unsupported EMBEDDINGS_PROVIDER: {provider_name}. Use 'none', 'hashing' or 'openai'."
    )


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
