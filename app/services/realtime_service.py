"""Barramento de eventos em tempo real (SSE) escopado por empresa.

Contratos importantes:
- todo evento carrega `company_id`; assinantes só recebem eventos da própria
  empresa (superadmin pode assinar o escopo global explicitamente);
- identificadores de contato nunca trafegam em claro no stream;
- o backend padrão é local ao processo e só serve a desenvolvimento/teste.
  Em múltiplos workers use `SHARED_STATE_BACKEND=redis`.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import mask_identifier
from app.services.shared_state import SharedStateError

REDIS_EVENT_CHANNEL = "project_wapp:events"

# Campos que nunca podem sair em claro em um stream compartilhado.
_MASKED_EVENT_FIELDS = {"contact_id", "from_number", "phone", "contact_name"}


def _sanitize_event_data(data: dict[str, Any] | None) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in (data or {}).items():
        if key in _MASKED_EVENT_FIELDS and value is not None:
            sanitized[key] = mask_identifier(str(value))
            continue
        sanitized[key] = value
    return sanitized


class EventBusBackend(Protocol):
    def publish(self, payload: str) -> None: ...

    def attach(self, hub: _SSEHub) -> None: ...

    @property
    def is_shared(self) -> bool: ...


class InProcessEventBackend:
    """Entrega direta na mesma instância do processo."""

    def __init__(self) -> None:
        self._hub: _SSEHub | None = None

    def attach(self, hub: _SSEHub) -> None:
        self._hub = hub

    def publish(self, payload: str) -> None:
        if self._hub is not None:
            self._hub.deliver_local(payload)

    @property
    def is_shared(self) -> bool:
        return False


class RedisEventBackend:
    """Publica no Redis e reinjeta o que chega de outras réplicas."""

    def __init__(self, redis_url: str) -> None:
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

        self._client = redis.Redis.from_url(normalized_url, decode_responses=True)
        self._hub: _SSEHub | None = None
        self._listener: threading.Thread | None = None

    def attach(self, hub: _SSEHub) -> None:
        self._hub = hub
        if self._listener is not None:
            return
        self._listener = threading.Thread(
            target=self._listen,
            name="project-wapp-sse-redis",
            daemon=True,
        )
        self._listener.start()

    def _listen(self) -> None:  # pragma: no cover - exige Redis real
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(REDIS_EVENT_CHANNEL)
        for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            if self._hub is not None:
                self._hub.deliver_local(str(message.get("data") or ""))

    def publish(self, payload: str) -> None:
        self._client.publish(REDIS_EVENT_CHANNEL, payload)

    @property
    def is_shared(self) -> bool:
        return True


class Subscription:
    """Fila de um cliente SSE, com o escopo de empresa que ele pode ver."""

    def __init__(self, *, company_id: int | None, role: str) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self.company_id = company_id
        self.role = role

    def accepts(self, event_company_id: int | None) -> bool:
        # Assinante global (superadmin sem empresa escolhida) vê tudo.
        if self.company_id is None:
            return True
        if event_company_id is None:
            return False
        return int(event_company_id) == int(self.company_id)


class _SSEHub:
    def __init__(self) -> None:
        self._subscriptions: set[Subscription] = set()
        self._backend: EventBusBackend | None = None

    def _get_backend(self) -> EventBusBackend:
        if self._backend is None:
            backend_name = (settings.shared_state_backend or "memory").strip().lower()
            if backend_name == "redis":
                self._backend = RedisEventBackend(settings.redis_url or "")
            elif backend_name == "memory":
                self._backend = InProcessEventBackend()
            else:
                raise SharedStateError(
                    f"Unsupported SHARED_STATE_BACKEND: {backend_name}. Use 'memory' or 'redis'."
                )
            self._backend.attach(self)
        return self._backend

    def reset_backend(self) -> None:
        self._backend = None

    def subscribe(self, *, company_id: int | None = None, role: str = "viewer") -> Subscription:
        self._get_backend()
        subscription = Subscription(company_id=company_id, role=role)
        self._subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscriptions.discard(subscription)

    def deliver_local(self, payload: str) -> None:
        """Entrega um payload já serializado às filas locais elegíveis."""
        try:
            event = json.loads(payload)
        except (TypeError, ValueError):
            return

        event_company_id = event.get("company_id")
        dead: list[Subscription] = []
        for subscription in self._subscriptions:
            if not subscription.accepts(event_company_id):
                continue
            try:
                subscription.queue.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(subscription)
        for subscription in dead:
            self._subscriptions.discard(subscription)

    def broadcast(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        company_id: int | None = None,
    ) -> None:
        payload = json.dumps(
            {
                "type": event_type,
                "company_id": int(company_id) if company_id is not None else None,
                "data": _sanitize_event_data(data),
                "ts": time.time(),
            }
        )
        self._get_backend().publish(payload)

    @property
    def client_count(self) -> int:
        return len(self._subscriptions)

    @property
    def backend_is_shared(self) -> bool:
        return self._get_backend().is_shared


hub = _SSEHub()


def broadcast_event(
    event_type: str,
    data: dict[str, Any] | None = None,
    *,
    company_id: int | None = None,
) -> None:
    """Publica um evento escopado por empresa a partir de código síncrono."""
    hub.broadcast(event_type, data, company_id=company_id)
