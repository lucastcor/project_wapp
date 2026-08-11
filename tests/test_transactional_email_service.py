"""Identity action links are delivered without leaking their tokens to logs."""

import json

import pytest

from app.core.config import settings
from app.services import transactional_email_service
from app.services.transactional_email_service import (
    TransactionalEmailError,
    send_invitation_email,
    send_password_reset_email,
)


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return b'{"id":"email_test_123"}'


def _configure(monkeypatch) -> None:
    monkeypatch.setattr(settings, "transactional_email_provider", "resend")
    monkeypatch.setattr(settings, "transactional_email_from", "WAPP <hello@acme.test>")
    monkeypatch.setattr(settings, "resend_api_key", "re_test_real")
    monkeypatch.setattr(settings, "platform_public_url", "https://app.acme.test")


def test_invitation_email_contains_encoded_action_link(monkeypatch) -> None:
    _configure(monkeypatch)
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    monkeypatch.setattr(transactional_email_service, "urlopen", fake_urlopen)

    result = send_invitation_email(
        recipient="member@acme.test",
        company_name="Acme & Co",
        role="operator",
        token="secret/token+value",
    )

    assert result["message_id"] == "email_test_123"
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["payload"]["to"] == ["member@acme.test"]
    assert "secret%2Ftoken%2Bvalue" in captured["payload"]["text"]
    assert "Acme &amp; Co" in captured["payload"]["html"]


def test_password_reset_requires_public_platform_url(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "platform_public_url", None)

    with pytest.raises(TransactionalEmailError, match="PLATFORM_PUBLIC_URL"):
        send_password_reset_email(
            recipient="owner@acme.test",
            display_name="Owner",
            token="secret",
        )


def test_email_delivery_requires_explicit_provider(monkeypatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "transactional_email_provider", "disabled")

    with pytest.raises(TransactionalEmailError, match="resend"):
        send_invitation_email(
            recipient="member@acme.test",
            company_name="Acme",
            role="viewer",
            token="secret",
        )
