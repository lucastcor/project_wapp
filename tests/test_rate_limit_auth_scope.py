"""Limite de autenticação protege credencial, não a leitura da própria sessão.

Achado do smoke do painel (P07, 14/09/2026): com o limite de 10 requisições de
`/platform/auth/*` por IP, cada carga do painel gastava o orçamento lendo sessão
e capacidades; alguns recarregamentos atrás do mesmo IP e o operador logado caía
na tela de login por 429.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.rate_limit_service import _resolve_scope_and_limit, clear_rate_limit_counters

client = TestClient(app)
SHARED_CLINIC_IP = {"X-Forwarded-For": "198.51.100.77"}


@pytest.fixture
def strict_auth_limit(monkeypatch):
    monkeypatch.setattr(settings, "enable_rate_limiting", True)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings, "rate_limit_auth_requests", 2)
    monkeypatch.setattr(settings, "rate_limit_admin_requests", 50)
    monkeypatch.setattr(settings, "rate_limit_exempt_localhost", False)
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    clear_rate_limit_counters()
    yield
    clear_rate_limit_counters()


def test_login_attempts_are_still_limited(strict_auth_limit) -> None:
    body = {"email": "ninguem@example.invalid", "password": "senha-errada-sintetica"}

    statuses = [
        client.post("/platform/auth/session", json=body, headers=SHARED_CLINIC_IP).status_code for _ in range(3)
    ]

    assert statuses[:2] != [429, 429]
    assert statuses[2] == 429
    blocked = client.post("/platform/auth/session", json=body, headers=SHARED_CLINIC_IP)
    assert blocked.headers["X-RateLimit-Scope"] == "platform_auth"
    assert blocked.headers["Retry-After"]


def test_reading_session_and_capabilities_does_not_spend_the_login_budget(strict_auth_limit) -> None:
    for _ in range(6):
        capabilities = client.get("/platform/auth/capabilities", headers=SHARED_CLINIC_IP)
        session = client.get("/platform/auth/session", headers=SHARED_CLINIC_IP)
        assert capabilities.status_code != 429
        assert session.status_code != 429
        assert capabilities.headers["X-RateLimit-Scope"] == "platform_auth_read"

    body = {"email": "ninguem@example.invalid", "password": "senha-errada-sintetica"}
    first_login = client.post("/platform/auth/session", json=body, headers=SHARED_CLINIC_IP)
    assert first_login.status_code != 429
    assert first_login.headers["X-RateLimit-Scope"] == "platform_auth"


@pytest.mark.parametrize(
    ("method", "scope"),
    [("GET", "platform_auth_read"), ("HEAD", "platform_auth_read"), ("POST", "platform_auth"),
     ("DELETE", "platform_auth"), (None, "platform_auth")],
)
def test_auth_scope_depends_on_the_method(method: str | None, scope: str) -> None:
    resolved = _resolve_scope_and_limit("/platform/auth/session", method)
    assert resolved is not None and resolved[0] == scope
