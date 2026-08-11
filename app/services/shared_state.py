"""Backends de estado compartilhado entre workers.

O container roda múltiplos workers, então rate limit e barramento de eventos
não podem depender de estado local ao processo em produção.

- `memory`: local ao processo. Só é adequado para desenvolvimento e testes.
- `redis`: compartilhado entre réplicas.

O backend é escolhido por `SHARED_STATE_BACKEND`. A validação real do adapter
Redis está documentada em `docs/production_runbook.md`.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol

from app.core.config import settings


class SharedStateError(Exception):
    pass


class CounterStore(Protocol):
    """Contador com janela fixa, usado pelo rate limit."""

    def increment(self, key: str, window_seconds: int) -> int:
        """Incrementa e devolve o total corrente da janela."""

    def reset(self) -> None:
        """Limpa o estado. Usado apenas por testes."""

    @property
    def is_shared(self) -> bool:
        """True quando o estado é visível para todas as réplicas."""


class InMemoryCounterStore:
    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def increment(self, key: str, window_seconds: int) -> int:
        with self._lock:
            current = self._counters.get(key, 0) + 1
            self._counters[key] = current
            return current

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()

    @property
    def is_shared(self) -> bool:
        return False


class RedisCounterStore:
    def __init__(self, redis_url: str) -> None:
        self._client = _build_redis_client(redis_url)

    def increment(self, key: str, window_seconds: int) -> int:
        pipeline = self._client.pipeline()
        pipeline.incr(key)
        pipeline.expire(key, max(1, window_seconds) + 1)
        results = pipeline.execute()
        return int(results[0])

    def reset(self) -> None:
        for key in self._client.scan_iter(match="project_wapp:ratelimit:*"):
            self._client.delete(key)

    @property
    def is_shared(self) -> bool:
        return True


def _build_redis_client(redis_url: str) -> Any:
    try:
        import redis  # type: ignore
    except ImportError as exc:  # pragma: no cover - depende de extra opcional
        raise SharedStateError(
            "SHARED_STATE_BACKEND=redis requires the 'redis' package. "
            "Install it with: pip install redis"
        ) from exc

    normalized_url = (redis_url or "").strip()
    if not normalized_url:
        raise SharedStateError("SHARED_STATE_BACKEND=redis requires REDIS_URL.")

    return redis.Redis.from_url(normalized_url, decode_responses=True)


_COUNTER_STORE: CounterStore | None = None
_COUNTER_STORE_LOCK = threading.Lock()


def get_counter_store() -> CounterStore:
    global _COUNTER_STORE
    if _COUNTER_STORE is not None:
        return _COUNTER_STORE

    with _COUNTER_STORE_LOCK:
        if _COUNTER_STORE is None:
            backend = (settings.shared_state_backend or "memory").strip().lower()
            if backend == "redis":
                _COUNTER_STORE = RedisCounterStore(settings.redis_url or "")
            elif backend == "memory":
                _COUNTER_STORE = InMemoryCounterStore()
            else:
                raise SharedStateError(
                    f"Unsupported SHARED_STATE_BACKEND: {backend}. Use 'memory' or 'redis'."
                )
    return _COUNTER_STORE


def reset_counter_store() -> None:
    """Recria o store. Usado por testes e por troca de configuração."""
    global _COUNTER_STORE
    with _COUNTER_STORE_LOCK:
        if _COUNTER_STORE is not None:
            _COUNTER_STORE.reset()
        _COUNTER_STORE = None


def shared_state_is_process_local() -> bool:
    backend = (settings.shared_state_backend or "memory").strip().lower()
    return backend == "memory"
