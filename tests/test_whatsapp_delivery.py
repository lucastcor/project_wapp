"""P05 — W04: erro permanente, transitório e incerto no envio WhatsApp.

Classificação segundo os códigos oficiais da Cloud API. Nenhuma chamada de
rede real: `requests.post` é substituído por respostas sintéticas.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError

from app.core.config import settings
from app.services import outbound_dispatch_service
from app.services.outbound_dispatch_service import OutboundDispatchError, dispatch_outbound_message
from app.services.whatsapp_provider import WhatsAppProvider, WhatsAppProviderError
from app.storage.repository import RuntimeRepository

repository = RuntimeRepository()
SYNTHETIC_TOKEN = "synthetic-token-never-logged"


class _Response:
    def __init__(self, status_code: int, body) -> None:
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise ValueError("not json")
        return self._body

    @property
    def text(self) -> str:
        return str(self._body)


@pytest.fixture
def configured_provider(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.invalid/v99.0")
    monkeypatch.setattr(settings, "whatsapp_api_token", SYNTHETIC_TOKEN)
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "synthetic-phone-number-id")


def _post(monkeypatch, outcome) -> list[dict]:
    calls: list[dict] = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(requests, "post", fake_post)
    return calls


@pytest.mark.parametrize(
    ("status", "body", "kind", "code"),
    [
        (400, {"error": {"code": 131047, "message": "Re-engagement message"}}, "permanent", 131047),
        (400, {"error": {"code": 132001, "message": "Template name does not exist"}}, "permanent", 132001),
        (401, {"error": {"code": 190, "message": "Access token has expired"}}, "permanent", 190),
        (400, {"error": {"code": 131026, "message": "Message undeliverable"}}, "permanent", 131026),
        (400, {"error": {"code": 130429, "message": "Rate limit hit"}}, "transient", 130429),
        (400, {"error": {"code": 131056, "message": "Pair rate limit hit"}}, "transient", 131056),
        (429, {"error": {"code": 4, "message": "Too many calls"}}, "transient", 4),
        (503, ValueError(), "transient", None),
    ],
)
def test_http_errors_are_classified_by_meta_code(configured_provider, monkeypatch, status, body, kind, code) -> None:
    _post(monkeypatch, _Response(status, body))

    with pytest.raises(WhatsAppProviderError) as exc_info:
        WhatsAppProvider().send_message("5511900000000", "Olá")

    assert exc_info.value.kind == kind
    assert exc_info.value.code == code
    assert SYNTHETIC_TOKEN not in str(exc_info.value)


def test_error_details_are_not_copied_into_the_diagnostic(configured_provider, monkeypatch) -> None:
    body = {"error": {"code": 131026, "message": "Undeliverable", "error_data": {"details": "5511900000000"}}}
    _post(monkeypatch, _Response(400, body))

    with pytest.raises(WhatsAppProviderError) as exc_info:
        WhatsAppProvider().send_message("5511900000000", "Olá")

    assert "5511900000000" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (requests.ConnectTimeout(), "transient"),
        (
            requests.ConnectionError(
                MaxRetryError(pool=None, url="/messages", reason=NewConnectionError(None, "refused"))
            ),
            "transient",
        ),
        (requests.ReadTimeout(), "uncertain"),
        (requests.ConnectionError("connection reset after send"), "uncertain"),
    ],
)
def test_network_failures_distinguish_not_sent_from_unknown(configured_provider, monkeypatch, failure, kind) -> None:
    _post(monkeypatch, failure)

    with pytest.raises(WhatsAppProviderError) as exc_info:
        WhatsAppProvider().send_message("5511900000000", "Olá")

    assert exc_info.value.kind == kind


def test_accepted_response_without_message_id_is_uncertain(configured_provider, monkeypatch) -> None:
    _post(monkeypatch, _Response(200, {"messaging_product": "whatsapp"}))

    with pytest.raises(WhatsAppProviderError) as exc_info:
        WhatsAppProvider().send_message("5511900000000", "Olá")

    assert exc_info.value.kind == "uncertain"


def test_accepted_response_returns_the_provider_message_id(configured_provider, monkeypatch) -> None:
    calls = _post(monkeypatch, _Response(200, {"messages": [{"id": "wamid.synthetic"}]}))

    result = WhatsAppProvider().send_message("5511900000000", "Olá")

    assert result["provider_message_id"] == "wamid.synthetic"
    assert calls[0]["type"] == "text"


def test_missing_configuration_is_classified(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "")
    monkeypatch.setattr(settings, "whatsapp_api_token", "")

    with pytest.raises(WhatsAppProviderError) as exc_info:
        WhatsAppProvider().send_message("5511900000000", "Olá")

    assert exc_info.value.kind == "configuration"


def _outbound() -> int:
    contact_id = f"delivery-{uuid4().hex[:12]}"
    repository.save_contact(contact_id, "Delivery")
    return repository.create_outbound_message(contact_id=contact_id, message="olá", source="delivery-test")


@pytest.mark.parametrize(
    ("kind", "expected_status"),
    [
        ("permanent", "dead_letter"),
        ("uncertain", "dead_letter"),
        ("transient", "retry"),
        ("configuration", "retry"),
    ],
)
def test_dispatch_retries_only_when_repeating_is_safe(monkeypatch, kind, expected_status) -> None:
    outbound_id = _outbound()
    monkeypatch.setattr(settings, "outbound_max_attempts", 5)
    calls: list[int] = []

    def failing_provider(channel, outbound_message):
        calls.append(int(outbound_message["id"]))
        raise OutboundDispatchError("synthetic failure", kind=kind, code=131047)

    monkeypatch.setattr(outbound_dispatch_service, "_send_via_provider", failing_provider)

    with pytest.raises(OutboundDispatchError):
        dispatch_outbound_message(outbound_id, "generic")

    stored = repository.get_outbound_message(outbound_id)
    assert calls == [outbound_id]
    assert stored["status"] == expected_status
    assert stored["attempt_count"] == 1
    assert stored["last_error"].startswith(f"{kind}:131047:")
    if expected_status == "dead_letter":
        assert stored["next_attempt_at"] is None
    else:
        assert stored["next_attempt_at"] is not None


def test_permanent_meta_rejection_through_the_real_provider_is_not_retried(
    configured_provider, monkeypatch
) -> None:
    outbound_id = _outbound()
    monkeypatch.setattr(settings, "outbound_provider", "whatsapp")
    monkeypatch.setattr(settings, "outbound_max_attempts", 5)
    _post(monkeypatch, _Response(400, {"error": {"code": 131047, "message": "Re-engagement message"}}))

    with pytest.raises(OutboundDispatchError) as exc_info:
        dispatch_outbound_message(outbound_id, "whatsapp")

    assert exc_info.value.kind == "permanent"
    stored = repository.get_outbound_message(outbound_id)
    assert stored["status"] == "dead_letter"
    assert SYNTHETIC_TOKEN not in (stored["last_error"] or "")
