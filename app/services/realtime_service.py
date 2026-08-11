"""Server-Sent Events (SSE) hub for real-time platform notifications.

Clients connect via GET /platform/events with an authenticated platform session.
Every subscription carries the company scope resolved server-side from that
session, and every tenant event carries the internal company identifier, so an
event is only delivered to sessions authorized for that company.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.core.logging import mask_identifier


logger = get_logger(__name__)


class SSESubscription:
    """A connected SSE client and the tenant scope it is authorized for.

    ``company_ids is None`` means a global scope (every company plus
    non-tenant system events).
    """

    def __init__(self, company_ids: list[int] | None) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        try:
            self.loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None
        self.company_ids: frozenset[int] | None = (
            None if company_ids is None else frozenset(int(item) for item in company_ids)
        )

    @property
    def is_global(self) -> bool:
        return self.company_ids is None

    def is_authorized_for(self, company_id: int | None) -> bool:
        if self.is_global:
            return True
        if company_id is None:
            # System-wide events without a tenant are only for global sessions.
            return False
        return company_id in (self.company_ids or frozenset())


class _SSEHub:
    """In-process pub/sub for SSE connections."""

    def __init__(self) -> None:
        self._subscriptions: set[SSESubscription] = set()
        self._lock = threading.Lock()

    def subscribe(self, company_ids: list[int] | None = None) -> SSESubscription:
        subscription = SSESubscription(company_ids)
        with self._lock:
            self._subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription: SSESubscription) -> None:
        with self._lock:
            self._subscriptions.discard(subscription)

    @staticmethod
    def serialize_event(
        event_type: str,
        data: dict[str, Any] | None = None,
        company_id: int | None = None,
    ) -> tuple[str, int | None]:
        normalized_company_id = int(company_id) if company_id is not None else None
        return (
            json.dumps(
                {
                    "type": event_type,
                    "company_id": normalized_company_id,
                    "data": data or {},
                    "ts": time.time(),
                }
            ),
            normalized_company_id,
        )

    @staticmethod
    def _enqueue(subscription: SSESubscription, payload: str) -> bool:
        try:
            subscription.queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            return False

    def deliver_serialized(self, payload: str, company_id: int | None) -> None:
        with self._lock:
            subscriptions = list(self._subscriptions)
        dead: list[SSESubscription] = []
        for subscription in subscriptions:
            if not subscription.is_authorized_for(company_id):
                continue
            if subscription.loop is not None and subscription.loop.is_running():
                subscription.loop.call_soon_threadsafe(self._enqueue, subscription, payload)
                continue
            if not self._enqueue(subscription, payload):
                dead.append(subscription)
        if dead:
            with self._lock:
                for subscription in dead:
                    self._subscriptions.discard(subscription)

    def broadcast(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        company_id: int | None = None,
    ) -> None:
        payload, normalized_company_id = self.serialize_event(
            event_type,
            data,
            company_id,
        )
        self.deliver_serialized(payload, normalized_company_id)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)


hub = _SSEHub()


class _RedisRealtimeBroker:
    """Redis fan-out across API replicas with a safe in-process fallback."""

    def __init__(self) -> None:
        self._client: Any | None = None
        self._pubsub: Any | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> bool:
        redis_url = str(settings.redis_url or "").strip()
        if not redis_url:
            if settings.require_shared_state:
                raise RuntimeError("REQUIRE_SHARED_STATE=true but REDIS_URL is not configured.")
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            try:
                import redis

                self._client = redis.Redis.from_url(
                    redis_url,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                    decode_responses=True,
                )
                self._client.ping()
                self._pubsub = self._client.pubsub(ignore_subscribe_messages=True)
                self._pubsub.subscribe(settings.realtime_redis_channel)
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._listen,
                    name="project-wapp-realtime-redis",
                    daemon=True,
                )
                self._thread.start()
                return True
            except Exception as exc:
                self._close_resources()
                if settings.require_shared_state:
                    raise RuntimeError(
                        f"Redis realtime broker is unavailable: {type(exc).__name__}."
                    ) from exc
                log_event(
                    logger,
                    logging.WARNING,
                    "realtime.redis.unavailable",
                    error_type=type(exc).__name__,
                )
                return False

    def _listen(self) -> None:
        while not self._stop.is_set() and self._pubsub is not None:
            try:
                message = self._pubsub.get_message(timeout=1.0)
                if not message or message.get("type") != "message":
                    continue
                raw_payload = str(message.get("data") or "")
                decoded = json.loads(raw_payload)
                raw_company_id = decoded.get("company_id")
                company_id = int(raw_company_id) if raw_company_id is not None else None
                hub.deliver_serialized(raw_payload, company_id)
            except Exception as exc:
                if not self._stop.is_set():
                    log_event(
                        logger,
                        logging.WARNING,
                        "realtime.redis.listener_failed",
                        error_type=type(exc).__name__,
                    )
                    self._stop.wait(1.0)

    def publish(
        self,
        event_type: str,
        data: dict[str, Any] | None,
        company_id: int | None,
    ) -> None:
        payload, normalized_company_id = hub.serialize_event(
            event_type,
            data,
            company_id,
        )
        if self.start() and self._client is not None:
            try:
                self._client.publish(settings.realtime_redis_channel, payload)
                return
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "realtime.redis.publish_failed",
                    error_type=type(exc).__name__,
                    shared_state_required=settings.require_shared_state,
                )
        hub.deliver_serialized(payload, normalized_company_id)

    def _close_resources(self) -> None:
        if self._pubsub is not None:
            try:
                self._pubsub.close()
            except Exception:
                pass
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._pubsub = None
        self._client = None

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self._thread = None
        self._close_resources()


broker = _RedisRealtimeBroker()


def start_realtime_broker() -> bool:
    return broker.start()


def close_realtime_broker() -> None:
    broker.close()


def broadcast_event(
    event_type: str,
    data: dict[str, Any] | None = None,
    company_id: int | None = None,
) -> None:
    """Convenience function for broadcasting from sync code."""
    broker.publish(event_type, data, company_id)


def publish_incoming_message_event(result: dict[str, Any], *, contact_id: str) -> None:
    """Publish a tenant-scoped message event without leaking raw contact identifiers."""
    broadcast_event(
        "new_message",
        {
            "contact_id": mask_identifier(contact_id),
            "classification": result.get("classification"),
            "handoff_required": result.get("handoff_required"),
        },
        company_id=result.get("company_id"),
    )
