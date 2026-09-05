import json
import os
import time
import uuid

import pytest
from fastapi import Request

from app.core.config import settings
from app.services.rate_limit_service import (
    clear_rate_limit_counters,
    evaluate_rate_limit,
    reset_rate_limit_state,
)
from app.services.realtime_service import (
    broadcast_event,
    close_realtime_broker,
)

TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not TEST_REDIS_URL,
    reason="TEST_REDIS_URL is required for Redis integration tests.",
)


def _incoming_request(client_ip: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/messages/incoming",
            "headers": [],
            "client": (client_ip, 4321),
            "scheme": "http",
            "server": ("test", 80),
            "query_string": b"",
        }
    )


def test_redis_rate_limit_is_shared_across_process_state_resets(monkeypatch) -> None:
    import redis

    client = redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    client_ip = f"198.51.100.{int(time.time()) % 200 + 1}"
    monkeypatch.setattr(settings, "redis_url", TEST_REDIS_URL)
    monkeypatch.setattr(settings, "require_shared_state", True)
    monkeypatch.setattr(settings, "enable_rate_limiting", True)
    monkeypatch.setattr(settings, "rate_limit_exempt_localhost", False)
    monkeypatch.setattr(settings, "rate_limit_incoming_requests", 1)

    first = evaluate_rate_limit(_incoming_request(client_ip))
    reset_rate_limit_state()  # Simulates another application replica.
    second = evaluate_rate_limit(_incoming_request(client_ip))

    assert first is not None and first.allowed is True
    assert second is not None and second.allowed is False
    for key in client.scan_iter("project_wapp:rate_limit:incoming_message:*"):
        client.delete(key)
    client.close()


def test_clearing_counters_releases_the_window_that_a_reset_keeps(monkeypatch) -> None:
    """A diferença entre as duas funções, provada nos dois sentidos.

    `reset_rate_limit_state` simula uma réplica nova e por isso **preserva** a
    janela compartilhada — é o que o teste acima garante. Isolamento de teste
    precisa do oposto, e sem esta função a suíte inteira herdava a janela do
    teste anterior sempre que `REDIS_URL` apontava para um Redis real: uma
    falha intermitente em quem apenas copiou o `.env.example`.
    """
    client_ip = f"203.0.113.{int(time.time()) % 200 + 1}"
    monkeypatch.setattr(settings, "redis_url", TEST_REDIS_URL)
    monkeypatch.setattr(settings, "require_shared_state", True)
    monkeypatch.setattr(settings, "enable_rate_limiting", True)
    monkeypatch.setattr(settings, "rate_limit_exempt_localhost", False)
    monkeypatch.setattr(settings, "rate_limit_incoming_requests", 1)

    first = evaluate_rate_limit(_incoming_request(client_ip))
    reset_rate_limit_state()
    blocked = evaluate_rate_limit(_incoming_request(client_ip))
    clear_rate_limit_counters()
    after_clear = evaluate_rate_limit(_incoming_request(client_ip))

    assert first is not None and first.allowed is True
    assert blocked is not None and blocked.allowed is False, (
        "reset nao pode apagar o contador compartilhado"
    )
    assert after_clear is not None and after_clear.allowed is True, (
        "clear precisa liberar a janela, senao o proximo teste herda o limite"
    )


def test_realtime_event_is_published_to_external_redis_subscriber(monkeypatch) -> None:
    import redis

    channel = f"project_wapp:test:realtime:{uuid.uuid4().hex}"
    client = redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    subscriber = client.pubsub(ignore_subscribe_messages=True)
    subscriber.subscribe(channel)
    monkeypatch.setattr(settings, "redis_url", TEST_REDIS_URL)
    monkeypatch.setattr(settings, "require_shared_state", True)
    monkeypatch.setattr(settings, "realtime_redis_channel", channel)
    close_realtime_broker()

    try:
        broadcast_event("redis_integration", {"ok": True}, company_id=42)
        deadline = time.monotonic() + 3
        message = None
        while time.monotonic() < deadline and message is None:
            message = subscriber.get_message(timeout=0.25)
        assert message is not None
        payload = json.loads(str(message["data"]))
        assert payload["type"] == "redis_integration"
        assert payload["company_id"] == 42
        assert payload["data"] == {"ok": True}
    finally:
        close_realtime_broker()
        subscriber.close()
        client.close()
