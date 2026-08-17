"""Authentication and tenant isolation of the platform SSE stream."""

import asyncio
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.platform_routes import sse_stream
from app.core.config import settings
from app.main import app
from app.services.platform_session_service import (
    COOKIE_NAME,
    issue_platform_session,
    resolve_role_company_scope,
)
from app.services.realtime_service import _SSEHub, broadcast_event, hub

client = TestClient(app)


def _session_cookie(role: str = "superadmin", actor: str = "tester") -> str:
    return str(issue_platform_session(role=role, actor=actor)["token"])


def _expired_session_cookie(role: str = "superadmin") -> str:
    from app.services.platform_session_service import _encode

    now = int(time.time())
    payload = {"role": role, "actor": "tester", "iat": now - 7200, "exp": now - 60}
    return _encode(payload)


def _drain(subscription, *, timeout: float = 0.0) -> list[dict]:
    """Eventos entregues à assinatura, esperando até `timeout` pelo primeiro.

    A espera existe porque a entrega não é síncrona em todo transporte. Com
    `REDIS_URL` configurado, `broadcast_event` publica no Redis e o próprio
    instance recebe de volta pelo listener — é isso que dá fan-out entre
    réplicas sem entregar duas vezes. Drenar imediatamente só funciona no
    caminho em memória, e um teste que assume isso passa na máquina do
    desenvolvedor e falha no ambiente que se parece com produção.
    """
    deadline = time.monotonic() + timeout
    while subscription.queue.empty() and time.monotonic() < deadline:
        time.sleep(0.02)

    events: list[dict] = []
    while not subscription.queue.empty():
        events.append(json.loads(subscription.queue.get_nowait()))
    return events


# ── Endpoint authentication ───────────────────────────────────────────


def test_sse_rejects_anonymous_connections() -> None:
    response = client.get("/platform/events")

    assert response.status_code == 401
    assert response.json()["detail"] == "Platform session required."


def test_sse_rejects_invalid_session_cookie() -> None:
    response = client.get("/platform/events", cookies={COOKIE_NAME: "tampered.value"})

    assert response.status_code == 401


def test_sse_rejects_expired_session_cookie() -> None:
    response = client.get("/platform/events", cookies={COOKIE_NAME: _expired_session_cookie()})

    assert response.status_code == 401


class _FakeRequest:
    """Minimal Request stand-in: the SSE generator never terminates on its own."""

    def __init__(self, cookies: dict[str, str], query_params: dict[str, str] | None = None) -> None:
        self.cookies = cookies
        self.query_params = query_params or {}
        self.disconnect_checks = 0

    async def is_disconnected(self) -> bool:
        self.disconnect_checks += 1
        return True


async def _consume_stream(request: _FakeRequest) -> list[str]:
    response = await sse_stream(request)  # type: ignore[arg-type]
    return [chunk async for chunk in response.body_iterator]


def test_sse_accepts_authenticated_session_and_cleans_up_on_disconnect() -> None:
    baseline = hub.client_count
    request = _FakeRequest({COOKIE_NAME: _session_cookie()})

    chunks = asyncio.run(_consume_stream(request))

    assert chunks[0] == "event: connected\ndata: {}\n\n"
    assert request.disconnect_checks >= 1
    # The generator's finally-block unsubscribes when the client goes away.
    assert hub.client_count == baseline


def test_sse_scope_comes_from_the_session_not_the_query_string(monkeypatch) -> None:
    """A query parameter must never widen the scope resolved from the session."""
    monkeypatch.setattr(settings, "admin_viewer_company_ids", "1")
    assert resolve_role_company_scope("viewer") == [1]

    captured_scopes: list[list[int] | None] = []
    original_subscribe = hub.subscribe

    def _record_subscribe(company_ids=None):
        captured_scopes.append(company_ids)
        return original_subscribe(company_ids)

    monkeypatch.setattr(hub, "subscribe", _record_subscribe)

    request = _FakeRequest(
        {COOKIE_NAME: _session_cookie(role="viewer")},
        query_params={"company_id": "2"},
    )
    asyncio.run(_consume_stream(request))

    assert captured_scopes == [[1]]


def test_sse_rejects_anonymous_connection_before_subscribing() -> None:
    baseline = hub.client_count
    request = _FakeRequest({})

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_consume_stream(request))

    assert exc_info.value.status_code == 401
    assert hub.client_count == baseline


# ── Company scope resolution ──────────────────────────────────────────


@pytest.mark.parametrize("role", ["viewer", "operator", "superadmin"])
def test_role_scope_defaults_to_every_company(monkeypatch, role) -> None:
    monkeypatch.setattr(settings, f"admin_{role}_company_ids", None)

    assert resolve_role_company_scope(role) is None


def test_role_scope_parses_configured_company_ids(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_operator_company_ids", " 7, 9 ,7 ")

    assert resolve_role_company_scope("operator") == [7, 9]


def test_role_scope_fails_closed_on_unparsable_configuration(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_viewer_company_ids", "abc")

    assert resolve_role_company_scope("viewer") == []


# ── Tenant isolation at delivery time ─────────────────────────────────


def test_tenant_a_does_not_receive_tenant_b_events() -> None:
    isolated_hub = _SSEHub()
    tenant_a = isolated_hub.subscribe([1])
    tenant_b = isolated_hub.subscribe([2])

    isolated_hub.broadcast("new_message", {"classification": "lead_novo"}, company_id=2)

    assert _drain(tenant_a) == []
    delivered = _drain(tenant_b)
    assert len(delivered) == 1
    assert delivered[0]["company_id"] == 2


def test_tenant_receives_its_own_events() -> None:
    isolated_hub = _SSEHub()
    tenant_a = isolated_hub.subscribe([1])

    isolated_hub.broadcast("new_booking", {"booking_id": 5}, company_id=1)

    delivered = _drain(tenant_a)
    assert len(delivered) == 1
    assert delivered[0]["type"] == "new_booking"
    assert delivered[0]["company_id"] == 1


def test_global_administrator_receives_every_tenant_event() -> None:
    isolated_hub = _SSEHub()
    global_admin = isolated_hub.subscribe(None)

    isolated_hub.broadcast("new_message", {}, company_id=1)
    isolated_hub.broadcast("new_message", {}, company_id=2)

    delivered = _drain(global_admin)
    assert [event["company_id"] for event in delivered] == [1, 2]


def test_untagged_events_only_reach_global_scopes() -> None:
    isolated_hub = _SSEHub()
    global_admin = isolated_hub.subscribe(None)
    tenant_a = isolated_hub.subscribe([1])

    isolated_hub.broadcast("reminders_dispatched", {"count": 3}, company_id=None)

    assert len(_drain(global_admin)) == 1
    assert _drain(tenant_a) == []


def test_scoped_subscription_with_empty_scope_receives_nothing() -> None:
    isolated_hub = _SSEHub()
    restricted = isolated_hub.subscribe([])

    isolated_hub.broadcast("new_message", {}, company_id=1)
    isolated_hub.broadcast("new_message", {}, company_id=None)

    assert _drain(restricted) == []


def test_unsubscribe_removes_the_connection() -> None:
    isolated_hub = _SSEHub()
    subscription = isolated_hub.subscribe([1])
    assert isolated_hub.client_count == 1

    isolated_hub.unsubscribe(subscription)

    assert isolated_hub.client_count == 0
    isolated_hub.broadcast("new_message", {}, company_id=1)
    assert _drain(subscription) == []


def test_slow_consumers_are_dropped_instead_of_blocking_broadcasts() -> None:
    isolated_hub = _SSEHub()
    slow = isolated_hub.subscribe(None)
    for _ in range(64):
        slow.queue.put_nowait("filler")

    isolated_hub.broadcast("new_message", {}, company_id=1)

    assert isolated_hub.client_count == 0


# ── Event payload hygiene ─────────────────────────────────────────────


def test_message_event_masks_the_contact_identifier(monkeypatch) -> None:
    from app.services.realtime_service import publish_incoming_message_event

    published: list[tuple] = []
    monkeypatch.setattr(
        "app.services.realtime_service.broadcast_event",
        lambda event_type, data=None, company_id=None: published.append((event_type, data, company_id)),
    )

    publish_incoming_message_event(
        {"company_id": 3, "classification": "lead_novo", "handoff_required": False},
        contact_id="5511999999999",
    )

    event_type, data, company_id = published[0]
    assert event_type == "new_message"
    assert company_id == 3
    assert data["contact_id"] == "***9999"
    assert "5511999999999" not in json.dumps(data)


def test_broadcast_payload_only_carries_operational_fields() -> None:
    isolated_hub = _SSEHub()
    subscription = isolated_hub.subscribe(None)

    isolated_hub.broadcast("new_message", {"contact_id": "***9999"}, company_id=1)

    payload = _drain(subscription)[0]
    assert set(payload.keys()) == {"type", "company_id", "data", "ts"}
    assert set(payload["data"].keys()) == {"contact_id"}


def test_broadcast_event_helper_forwards_company_scope() -> None:
    subscription = hub.subscribe([11])
    try:
        broadcast_event("new_message", {"classification": "faq"}, company_id=11)
        delivered = _drain(subscription, timeout=3.0)
    finally:
        hub.unsubscribe(subscription)

    assert delivered[0]["company_id"] == 11


def test_subscription_queue_is_bounded() -> None:
    isolated_hub = _SSEHub()
    subscription = isolated_hub.subscribe(None)

    assert isinstance(subscription.queue, asyncio.Queue)
    assert subscription.queue.maxsize == 64
