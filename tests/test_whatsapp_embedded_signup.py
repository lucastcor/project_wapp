"""Conexão do WhatsApp por empresa (Embedded Signup): fluxo, segredos e fallback.

O que estes testes protegem:

- o token de negócio da empresa entra no banco cifrado e nunca sai por
  resposta, listagem, status ou auditoria — nem em claro, nem cifrado;
- a ordem das chamadas à Graph valida tudo antes de alterar qualquer coisa
  na Meta (troca do code, descoberta, perfil, e só então inscrição e
  registro), com o token só onde cabe e os segredos fora da URL;
- o PIN de registro é do número: é reaproveitado na reconexão, sobrevive à
  desconexão e nunca é substituído por um que a Meta recusou;
- o provider de envio usa o token da empresa quando existe e cai no global
  quando não existe, sem misturar os dois, e o status do canal não chama de
  "configurado" um token que o servidor não consegue ler;
- a prontidão de produção aceita a instalação por Embedded Signup sem
  token global.
"""

from __future__ import annotations

import json
import re
import secrets
import traceback
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import admin_routes
from app.core.config import settings
from app.core.credential_vault import decrypt_secret, encrypt_secret, generate_key, is_encrypted
from app.main import app
from app.services import system_readiness_service, whatsapp_signup_service
from app.services.whatsapp_provider import (
    WhatsAppProvider,
    WhatsAppProviderError,
    check_whatsapp_connection,
    get_whatsapp_channel_status,
)
from app.services.whatsapp_signup_service import (
    MetaGraphClient,
    MetaGraphError,
    SignupError,
    embedded_signup_config,
)
from app.storage import whatsapp_credentials_repository as credentials_repository
from app.storage.db import db_session
from app.storage.repository import RuntimeRepository
from app.storage.whatsapp_credentials_repository import (
    get_credentials_for_company,
    mark_needs_reconnection,
    store_connection,
)

client = TestClient(app)
repository = RuntimeRepository()

APP_ID = "123456789012345"
CONFIG_ID = "987654321098765"
APP_SECRET = "synthetic-meta-app-secret-never-returned"
BUSINESS_TOKEN = "EAAB-synthetic-business-token-never-returned"
OLD_BUSINESS_TOKEN = "EAAB-synthetic-previous-token-never-returned"
GLOBAL_TOKEN = "synthetic-global-token-never-returned"
SIGNUP_CODE = "AQD-synthetic-authorization-code"
FORBIDDEN_IN_RESPONSES = (APP_SECRET, BUSINESS_TOKEN, OLD_BUSINESS_TOKEN, GLOBAL_TOKEN, SIGNUP_CODE)
# Nem o texto cifrado nem o nome das colunas de segredo podem aparecer numa
# resposta: um `**binding` num refactor devolveria `enc:v1:...` ao painel.
FORBIDDEN_MARKERS = ("enc:v1:", "access_secret_encrypted", "registration_pin", "_encrypted")
STATUS_KEYS = {
    "provider",
    "company_id",
    "ready",
    "delivery_configured",
    "inbound_configured",
    "active_binding_count",
    "binding",
    "credentials",
    "connection_source",
    "has_company_token",
    "credential_unreadable",
    "needs_reconnection",
    "can_connect",
    "can_send",
    "waba_id_masked",
    "verified_name",
    "display_phone_number",
    "quality_rating",
    "platform_type",
    "connected_at",
    "last_verified_at",
    "details",
}


def _meta_id(prefix: str) -> str:
    """Ids da Meta são numéricos; o prefixo distingue WABA (1) de número (2)."""
    return f"{prefix}{secrets.randbelow(10**12):012d}"


@pytest.fixture(autouse=True)
def _enable_admin_auth(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)


@pytest.fixture
def signup_env(monkeypatch) -> None:
    """Instalação com app da Meta e cofre explícito; sem token global."""
    monkeypatch.setattr(settings, "meta_app_id", APP_ID)
    monkeypatch.setattr(settings, "meta_embedded_signup_config_id", CONFIG_ID)
    monkeypatch.setattr(settings, "whatsapp_app_secret", APP_SECRET)
    monkeypatch.setattr(settings, "credentials_encryption_key", generate_key())
    monkeypatch.setattr(settings, "meta_graph_api_version", "v23.0")
    monkeypatch.setattr(settings, "whatsapp_api_base_url", None)
    monkeypatch.setattr(settings, "whatsapp_api_token", None)
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", None)
    monkeypatch.setattr(settings, "whatsapp_webhook_verify_token", "synthetic-verify-token")


def _headers(principal: dict) -> dict[str, str]:
    return {
        "Cookie": f"project_wapp_platform_session={principal['token']}; project_wapp_csrf={principal['csrf']}",
        "X-CSRF-Token": principal["csrf"],
    }


class _AuditRepository(RuntimeRepository):
    """Captura eventos de auditoria sem gravar, para inspecionar os metadados."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict] = []

    def create_audit_event(self, **kwargs) -> int:
        self.events.append(kwargs)
        return len(self.events)


class FakeGraph:
    """Graph API falsa: registra as chamadas na ordem e responde o mínimo."""

    def __init__(
        self,
        *,
        waba_id: str,
        phone_number_id: str,
        exchange_error: MetaGraphError | None = None,
        register_error: MetaGraphError | None = None,
        profile_id: str | None = None,
    ) -> None:
        self.waba_id = waba_id
        self.phone_number_id = phone_number_id
        self.exchange_error = exchange_error
        self.register_error = register_error
        self.profile_id = profile_id or phone_number_id
        self.calls: list[dict] = []
        self.register_pins: list[str] = []

    @property
    def register_pin(self) -> str | None:
        return self.register_pins[-1] if self.register_pins else None

    def get(self, path: str, params: dict | None = None, token: str | None = None) -> dict:
        self.calls.append({"method": "GET", "path": path, "params": dict(params or {}), "token": token})
        if path == "debug_token":
            return {
                "data": {
                    "app_id": APP_ID,
                    "granular_scopes": [
                        {"scope": "whatsapp_business_messaging", "target_ids": [self.waba_id]},
                        {"scope": "whatsapp_business_management", "target_ids": [self.waba_id]},
                    ],
                }
            }
        if path == f"{self.waba_id}/phone_numbers":
            return {"data": [{"id": self.phone_number_id, "display_phone_number": "+55 11 99999-0000"}]}
        if path == self.phone_number_id:
            return {
                "id": self.profile_id,
                "display_phone_number": "+55 11 99999-0000",
                "verified_name": "Studio Synthetic",
                "quality_rating": "GREEN",
                "code_verification_status": "VERIFIED",
                "platform_type": "CLOUD_API",
            }
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path: str, json: dict | None = None, token: str | None = None, *, data: dict | None = None) -> dict:
        self.calls.append(
            {"method": "POST", "path": path, "json": dict(json or {}), "data": dict(data or {}), "token": token}
        )
        if path == "oauth/access_token":
            if self.exchange_error is not None:
                raise self.exchange_error
            return {"access_token": BUSINESS_TOKEN, "token_type": "bearer"}
        if path == f"{self.waba_id}/subscribed_apps":
            return {"success": True}
        if path == f"{self.phone_number_id}/register":
            self.register_pins.append(str((json or {}).get("pin")))
            if self.register_error is not None:
                raise self.register_error
            return {"success": True}
        raise AssertionError(f"unexpected POST {path}")

    def delete(self, path: str, token: str | None = None) -> dict:
        self.calls.append({"method": "DELETE", "path": path, "token": token})
        return {"success": True}


def _install_fake(monkeypatch, **kwargs) -> FakeGraph:
    fake = FakeGraph(waba_id=_meta_id("1"), phone_number_id=_meta_id("2"), **kwargs)
    monkeypatch.setattr(whatsapp_signup_service, "MetaGraphClient", lambda *args, **kwargs: fake)
    return fake


@pytest.fixture
def fake_graph(monkeypatch) -> FakeGraph:
    return _install_fake(monkeypatch)


def _connect(company_id: int, principal: dict, body: dict | None = None):
    return client.post(
        "/admin/whatsapp/embedded-signup/complete",
        params={"company_id": company_id},
        headers=_headers(principal),
        json=body if body is not None else {"code": SIGNUP_CODE},
    )


def _disconnect(company_id: int, principal: dict):
    return client.post(
        "/admin/whatsapp/embedded-signup/disconnect", params={"company_id": company_id}, headers=_headers(principal)
    )


def _paths(fake: FakeGraph) -> list[tuple[str, str]]:
    return [(call["method"], call["path"]) for call in fake.calls]


def _assert_no_secret(text: str) -> None:
    for secret in FORBIDDEN_IN_RESPONSES:
        assert secret not in text
    for marker in FORBIDDEN_MARKERS:
        assert marker not in text


def _secret_columns(binding_id: int) -> dict:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT active, access_secret_encrypted, registration_pin_encrypted, credential_source, needs_reconnection "
            "FROM whatsapp_channel_bindings WHERE id = ?",
            (int(binding_id),),
        )
        return dict(cursor.fetchone())


# ── Configuração ──────────────────────────────────────────────────────


def test_config_reports_what_is_missing(monkeypatch, admin_principal_factory) -> None:
    monkeypatch.setattr(settings, "meta_app_id", None)
    monkeypatch.setattr(settings, "meta_embedded_signup_config_id", "change-me-config")
    monkeypatch.setattr(settings, "whatsapp_app_secret", None)

    config = embedded_signup_config()

    assert config["enabled"] is False
    assert config["app_id"] is None
    assert config["config_id"] is None
    for variable in ("META_APP_ID", "META_EMBEDDED_SIGNUP_CONFIG_ID", "WHATSAPP_APP_SECRET"):
        assert variable in config["reason"]

    viewer = admin_principal_factory(global_role="viewer")
    response = client.get("/admin/whatsapp/embedded-signup/config", headers=_headers(viewer))
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is False


def test_config_is_enabled_with_real_values_and_never_returns_the_secret(
    signup_env, admin_principal_factory
) -> None:
    viewer = admin_principal_factory(global_role="viewer")

    response = client.get("/admin/whatsapp/embedded-signup/config", headers=_headers(viewer))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "enabled": True,
        "app_id": APP_ID,
        "config_id": CONFIG_ID,
        "graph_api_version": "v23.0",
        "reason": None,
    }
    _assert_no_secret(response.text)


def test_config_requires_an_explicit_vault_key_outside_development(signup_env, monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "credentials_encryption_key", None)

    config = embedded_signup_config()

    assert config["enabled"] is False
    assert "CREDENTIALS_ENCRYPTION_KEY" in config["reason"]


# ── Conclusão da conexão ──────────────────────────────────────────────


def test_complete_runs_the_meta_steps_in_order_and_stores_encrypted_credentials(
    signup_env, fake_graph, company_factory, admin_principal_factory, monkeypatch
) -> None:
    audit = _AuditRepository()
    monkeypatch.setattr(admin_routes, "repository", audit)
    company_id = company_factory("signup")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(
        company_id,
        owner,
        {
            "code": SIGNUP_CODE,
            "phone_number_id": fake_graph.phone_number_id,
            "waba_id": fake_graph.waba_id,
            "business_id": "3000000000001",
            "event": "FINISH",
        },
    )

    assert response.status_code == 200, response.text
    status = response.json()
    assert set(status.keys()) == STATUS_KEYS
    assert status["has_company_token"] is True
    assert status["credential_unreadable"] is False
    assert status["can_send"] is True
    assert status["can_connect"] is True
    assert status["connection_source"] == "embedded_signup"
    assert status["verified_name"] == "Studio Synthetic"
    assert status["quality_rating"] == "GREEN"
    assert status["needs_reconnection"] is False
    assert status["connected_at"]
    assert status["waba_id_masked"].endswith(fake_graph.waba_id[-4:])
    assert fake_graph.waba_id not in response.text
    assert fake_graph.phone_number_id not in response.text
    _assert_no_secret(response.text)

    # Leituras primeiro, efeitos na Meta por último: a troca do code e o
    # perfil (que prova que o token alcança o número) antes de inscrever e
    # registrar. O code e o app secret só vão na troca, no corpo do POST.
    assert _paths(fake_graph) == [
        ("POST", "oauth/access_token"),
        ("GET", fake_graph.phone_number_id),
        ("POST", f"{fake_graph.waba_id}/subscribed_apps"),
        ("POST", f"{fake_graph.phone_number_id}/register"),
    ]
    exchange = fake_graph.calls[0]
    assert exchange["token"] is None
    assert exchange["data"] == {"client_id": APP_ID, "client_secret": APP_SECRET, "code": SIGNUP_CODE}
    assert exchange["json"] == {}
    assert all(call["token"] == BUSINESS_TOKEN for call in fake_graph.calls[1:])
    assert fake_graph.calls[1]["params"]["fields"].startswith("id,display_phone_number,verified_name")
    register = fake_graph.calls[3]
    assert register["json"]["messaging_product"] == "whatsapp"
    assert re.fullmatch(r"\d{6}", register["json"]["pin"])

    stored = get_credentials_for_company(company_id)
    assert stored is not None
    assert stored["phone_number_id"] == fake_graph.phone_number_id
    assert stored["waba_id"] == fake_graph.waba_id
    assert stored["business_id"] == "3000000000001"
    assert stored["credential_source"] == "embedded_signup"
    # O ator da sessão de identidade é o nome de exibição da pessoa.
    assert str(stored["connected_by"]).startswith("superadmin ")
    assert stored["needs_reconnection"] == 0
    assert is_encrypted(stored["access_secret_encrypted"])
    assert is_encrypted(stored["registration_pin_encrypted"])
    assert decrypt_secret(stored["access_secret_encrypted"]) == BUSINESS_TOKEN
    assert decrypt_secret(stored["registration_pin_encrypted"]) == fake_graph.register_pin

    connected = [event for event in audit.events if event.get("action") == "whatsapp_connected"]
    assert connected and connected[-1]["status"] == "success"
    assert connected[-1]["resource_type"] == "whatsapp_channel_binding"
    assert connected[-1]["resource_id"] == str(stored["id"])
    assert connected[-1]["metadata"]["credential_source"] == "embedded_signup"
    serialized = json.dumps(connected[-1]["metadata"])
    _assert_no_secret(serialized)
    assert fake_graph.register_pin not in serialized
    assert fake_graph.phone_number_id not in serialized


def test_secrets_never_leave_through_status_or_binding_listing(
    signup_env, fake_graph, company_factory, admin_principal_factory
) -> None:
    company_id = company_factory("signup-read")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    connected = _connect(company_id, owner)
    assert connected.status_code == 200
    _assert_no_secret(connected.text)

    channel_status = client.get(
        "/admin/whatsapp/channel-status", params={"company_id": company_id}, headers=_headers(owner)
    )
    assert channel_status.status_code == 200, channel_status.text
    assert set(channel_status.json().keys()) == STATUS_KEYS
    assert channel_status.json()["has_company_token"] is True
    assert channel_status.json()["credentials"]["api_token_present"] is True
    assert channel_status.json()["credentials"]["api_base_url_present"] is True
    _assert_no_secret(channel_status.text)
    assert fake_graph.phone_number_id not in channel_status.text

    bindings = client.get("/admin/whatsapp/bindings", params={"company_id": company_id}, headers=_headers(owner))
    assert bindings.status_code == 200, bindings.text
    lowered = bindings.text.lower()
    for forbidden in ["token", "secret", "app_secret", "authorization"]:
        assert forbidden not in lowered
    _assert_no_secret(bindings.text)


def test_complete_discovers_waba_and_number_when_the_popup_omits_them(
    signup_env, fake_graph, company_factory, admin_principal_factory
) -> None:
    company_id = company_factory("signup-discover")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(company_id, owner, {"code": SIGNUP_CODE, "event": "FINISH_ONLY_WABA"})

    assert response.status_code == 200, response.text
    assert _paths(fake_graph)[:3] == [
        ("POST", "oauth/access_token"),
        ("GET", "debug_token"),
        ("GET", f"{fake_graph.waba_id}/phone_numbers"),
    ]
    # O app token autentica a inspeção pelo cabeçalho; só o token
    # inspecionado vai como parâmetro.
    debug = fake_graph.calls[1]
    assert debug["params"] == {"input_token": BUSINESS_TOKEN}
    assert debug["token"] == f"{APP_ID}|{APP_SECRET}"
    stored = get_credentials_for_company(company_id)
    assert stored is not None
    assert stored["waba_id"] == fake_graph.waba_id
    assert stored["phone_number_id"] == fake_graph.phone_number_id


def test_coexistence_marks_the_credential_source(
    signup_env, fake_graph, company_factory, admin_principal_factory
) -> None:
    company_id = company_factory("signup-coex")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(
        company_id,
        owner,
        {"code": SIGNUP_CODE, "coexistence": True, "event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["connection_source"] == "embedded_signup_coexistence"


@pytest.mark.parametrize(
    "register_error",
    [
        MetaGraphError("rejected", http_status=400, provider_code=133005, title="Two step verification PIN mismatch"),
        MetaGraphError("rejected", http_status=400, provider_code=133006, title="Phone number re-verification needed"),
        MetaGraphError("rejected", http_status=400, provider_code=100, title="Phone number is already registered"),
    ],
    ids=["code_133005", "code_133006", "already_registered_text"],
)
def test_already_registered_number_is_a_success_without_storing_the_rejected_pin(
    signup_env, company_factory, admin_principal_factory, monkeypatch, register_error
) -> None:
    fake = _install_fake(monkeypatch, register_error=register_error)
    company_id = company_factory("signup-registered")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(company_id, owner)

    assert response.status_code == 200, response.text
    assert _paths(fake)[-1] == ("POST", f"{fake.phone_number_id}/register")
    stored = get_credentials_for_company(company_id)
    assert stored is not None
    assert is_encrypted(stored["access_secret_encrypted"])
    # A Meta não aplicou o PIN que enviamos; guardar um PIN aleatório
    # recusado seria fingir que sabemos a verificação em duas etapas.
    assert stored["registration_pin_encrypted"] is None


def test_reconnecting_reuses_the_registration_pin_meta_already_has(
    signup_env, fake_graph, company_factory, admin_principal_factory
) -> None:
    company_id = company_factory("signup-pin-reuse")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    assert _connect(company_id, owner).status_code == 200
    first_pin = fake_graph.register_pin
    assert first_pin is not None
    fake_graph.calls.clear()

    response = _connect(company_id, owner)

    assert response.status_code == 200, response.text
    assert fake_graph.register_pin == first_pin
    stored = get_credentials_for_company(company_id)
    assert stored is not None
    assert decrypt_secret(stored["registration_pin_encrypted"]) == first_pin


def test_pin_mismatch_on_reconnect_keeps_the_pin_we_know(
    signup_env, company_factory, admin_principal_factory, monkeypatch
) -> None:
    fake = _install_fake(monkeypatch)
    company_id = company_factory("signup-pin-keep")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    assert _connect(company_id, owner).status_code == 200
    first_pin = fake.register_pin
    fake.register_error = MetaGraphError(
        "rejected", http_status=400, provider_code=133005, title="Two step verification PIN mismatch"
    )

    response = _connect(company_id, owner)

    assert response.status_code == 200, response.text
    assert fake.register_pins == [first_pin, first_pin]
    stored = get_credentials_for_company(company_id)
    assert stored is not None
    assert decrypt_secret(stored["registration_pin_encrypted"]) == first_pin


def test_pin_survives_disconnect_and_follows_the_number_to_another_company(
    signup_env, fake_graph, company_factory, admin_principal_factory
) -> None:
    first_company = company_factory("signup-pin-a")
    first_owner = admin_principal_factory(global_role="superadmin", company_ids=[first_company])
    assert _connect(first_company, first_owner).status_code == 200
    first_pin = fake_graph.register_pin
    first_binding = get_credentials_for_company(first_company)
    assert first_binding is not None
    assert _disconnect(first_company, first_owner).status_code == 200
    row = _secret_columns(int(first_binding["id"]))
    assert row["access_secret_encrypted"] is None
    assert decrypt_secret(row["registration_pin_encrypted"]) == first_pin

    second_company = company_factory("signup-pin-b")
    second_owner = admin_principal_factory(global_role="superadmin", company_ids=[second_company])
    fake_graph.calls.clear()

    response = _connect(second_company, second_owner)

    assert response.status_code == 200, response.text
    assert fake_graph.register_pin == first_pin
    stored = get_credentials_for_company(second_company)
    assert stored is not None
    assert decrypt_secret(stored["registration_pin_encrypted"]) == first_pin


def test_register_failure_that_is_not_already_registered_is_a_502(
    signup_env, company_factory, admin_principal_factory, monkeypatch
) -> None:
    fake = _install_fake(
        monkeypatch,
        register_error=MetaGraphError("rejected", http_status=400, provider_code=131000, title="Something went wrong"),
    )
    company_id = company_factory("signup-register-fail")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(company_id, owner)

    assert response.status_code == 502, response.text
    assert "registro do número" in response.json()["detail"]
    assert get_credentials_for_company(company_id) is None
    assert fake.register_pin is not None


def test_failed_code_exchange_returns_502_without_leaking(
    signup_env, company_factory, admin_principal_factory, monkeypatch
) -> None:
    audit = _AuditRepository()
    monkeypatch.setattr(admin_routes, "repository", audit)
    fake = _install_fake(
        monkeypatch,
        exchange_error=MetaGraphError("rejected", http_status=400, provider_code=100, title="Invalid code"),
    )
    company_id = company_factory("signup-exchange-fail")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(company_id, owner)

    assert response.status_code == 502, response.text
    assert response.json()["detail"] == "A Meta não confirmou a autorização. Tente conectar de novo."
    _assert_no_secret(response.text)
    assert len(fake.calls) == 1
    assert get_credentials_for_company(company_id) is None
    assert repository.get_whatsapp_bindings(company_id=company_id) == []
    failures = [event for event in audit.events if event.get("action") == "whatsapp_connected"]
    assert failures and failures[-1]["status"] == "failure"
    assert failures[-1]["metadata"]["kind"] == "exchange_failed"
    assert failures[-1]["metadata"]["provider_code"] == 100
    _assert_no_secret(json.dumps(failures[-1]["metadata"]))


def test_exchange_without_access_token_is_a_502(signup_env, company_factory, admin_principal_factory, monkeypatch) -> None:
    class _EmptyExchange(FakeGraph):
        def post(self, path, json=None, token=None, *, data=None):
            if path == "oauth/access_token":
                self.calls.append({"method": "POST", "path": path, "data": dict(data or {}), "token": token})
                return {"token_type": "bearer"}
            return super().post(path, json, token, data=data)

    fake = _EmptyExchange(waba_id=_meta_id("1"), phone_number_id=_meta_id("2"))
    monkeypatch.setattr(whatsapp_signup_service, "MetaGraphClient", lambda *args, **kwargs: fake)
    company_id = company_factory("signup-empty-exchange")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(company_id, owner)

    assert response.status_code == 502, response.text
    assert "token de acesso" in response.json()["detail"]


def test_complete_is_unavailable_when_the_installation_lacks_the_meta_app(
    company_factory, admin_principal_factory, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "meta_app_id", None)
    monkeypatch.setattr(settings, "meta_embedded_signup_config_id", None)
    monkeypatch.setattr(settings, "whatsapp_app_secret", None)
    company_id = company_factory("signup-disabled")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(company_id, owner)

    assert response.status_code == 503, response.text
    assert "META_APP_ID" in response.json()["detail"]


def test_vault_failure_is_a_503_before_any_side_effect_at_meta(
    signup_env, fake_graph, company_factory, admin_principal_factory, monkeypatch
) -> None:
    def _broken(_plaintext: str) -> str:
        raise whatsapp_signup_service.CredentialVaultError("no key")

    monkeypatch.setattr(whatsapp_signup_service, "encrypt_secret", _broken)
    company_id = company_factory("signup-vault")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(company_id, owner, {"code": SIGNUP_CODE, "phone_number_id": fake_graph.phone_number_id, "waba_id": fake_graph.waba_id})

    assert response.status_code == 503, response.text
    assert "CREDENTIALS_ENCRYPTION_KEY" in response.json()["detail"]
    assert get_credentials_for_company(company_id) is None
    # Sem cofre não há inscrição nem registro: nada mudou na Meta.
    assert _paths(fake_graph) == [("POST", "oauth/access_token")]


@pytest.mark.parametrize(
    ("body", "expected_calls"),
    [
        ({"phone_number_id": True, "waba_id": True}, []),
        ({}, [("POST", "oauth/access_token"), ("GET", "debug_token"), ("GET", "{waba}/phone_numbers")]),
    ],
    ids=["ids_from_popup", "ids_discovered"],
)
def test_number_active_in_another_company_returns_409_before_touching_meta(
    signup_env, fake_graph, company_factory, admin_principal_factory, monkeypatch, body, expected_calls
) -> None:
    audit = _AuditRepository()
    monkeypatch.setattr(admin_routes, "repository", audit)
    other_company_id = company_factory("signup-other")
    repository.upsert_whatsapp_binding(
        company_id=other_company_id, phone_number_id=fake_graph.phone_number_id, updated_by="fixture"
    )
    company_id = company_factory("signup-conflict")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    payload = {"code": SIGNUP_CODE}
    if body.get("phone_number_id"):
        payload["phone_number_id"] = fake_graph.phone_number_id
    if body.get("waba_id"):
        payload["waba_id"] = fake_graph.waba_id

    response = _connect(company_id, owner, payload)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Este número já está conectado a outra empresa."
    # Só leituras (ou nada) antes do 409: o número da outra empresa não foi
    # inscrito nem registrado com um PIN novo.
    assert _paths(fake_graph) == [
        (method, path.replace("{waba}", fake_graph.waba_id)) for method, path in expected_calls
    ]
    assert get_credentials_for_company(company_id) is None
    still_owner = repository.get_whatsapp_binding_by_phone_number_id(fake_graph.phone_number_id)
    assert still_owner is not None and still_owner["company_id"] == other_company_id
    conflicts = [event for event in audit.events if event.get("action") == "whatsapp_connected"]
    assert conflicts and conflicts[-1]["status"] == "conflict"


def test_number_conflict_detected_at_store_time_is_still_a_409(
    signup_env, fake_graph, company_factory, admin_principal_factory, monkeypatch
) -> None:
    """A corrida: o número fica ativo em outra empresa entre a checagem e a gravação."""
    other_company_id = company_factory("signup-race-other")
    company_id = company_factory("signup-race")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    original = whatsapp_signup_service.store_connection

    def _racing_store(*args, **kwargs):
        repository.upsert_whatsapp_binding(
            company_id=other_company_id, phone_number_id=fake_graph.phone_number_id, updated_by="race"
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(whatsapp_signup_service, "store_connection", _racing_store)

    response = _connect(company_id, owner)

    assert response.status_code == 409, response.text
    assert get_credentials_for_company(company_id) is None


@pytest.mark.parametrize(
    "field",
    ["phone_number_id", "waba_id", "business_id"],
)
@pytest.mark.parametrize(
    "value",
    ["1234567890#x", "9876543210?fields=x", "123/../../me", "abc", "1" * 33],
    ids=["fragment", "query", "traversal", "letters", "too_long"],
)
def test_non_numeric_meta_ids_from_the_popup_are_rejected_before_any_graph_call(
    signup_env, fake_graph, company_factory, admin_principal_factory, field, value
) -> None:
    company_id = company_factory("signup-bad-id")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(company_id, owner, {"code": SIGNUP_CODE, field: value})

    assert response.status_code == 400, response.text
    assert field in response.json()["detail"]
    assert fake_graph.calls == []
    assert repository.get_whatsapp_bindings(company_id=company_id) == []


def test_non_numeric_ids_returned_by_discovery_are_a_502_without_side_effects(
    signup_env, company_factory, admin_principal_factory, monkeypatch
) -> None:
    class _GarbageDiscovery(FakeGraph):
        def get(self, path, params=None, token=None):
            if path == "debug_token":
                self.calls.append({"method": "GET", "path": path, "params": dict(params or {}), "token": token})
                return {"data": {"granular_scopes": [{"scope": "whatsapp_business_management", "target_ids": ["W9?x=1"]}]}}
            return super().get(path, params, token)

    fake = _GarbageDiscovery(waba_id=_meta_id("1"), phone_number_id=_meta_id("2"))
    monkeypatch.setattr(whatsapp_signup_service, "MetaGraphClient", lambda *args, **kwargs: fake)
    company_id = company_factory("signup-bad-discovery")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(company_id, owner)

    assert response.status_code == 502, response.text
    assert _paths(fake) == [("POST", "oauth/access_token"), ("GET", "debug_token")]
    assert repository.get_whatsapp_bindings(company_id=company_id) == []


def test_profile_of_a_different_number_aborts_before_subscribe_and_register(
    signup_env, company_factory, admin_principal_factory, monkeypatch
) -> None:
    fake = _install_fake(monkeypatch, profile_id=_meta_id("2"))
    company_id = company_factory("signup-profile-mismatch")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(
        company_id, owner, {"code": SIGNUP_CODE, "phone_number_id": fake.phone_number_id, "waba_id": fake.waba_id}
    )

    assert response.status_code == 502, response.text
    assert "outro número" in response.json()["detail"]
    assert _paths(fake) == [("POST", "oauth/access_token"), ("GET", fake.phone_number_id)]
    assert repository.get_whatsapp_bindings(company_id=company_id) == []


def test_reconnecting_replaces_the_previous_number_and_keeps_one_active_binding(
    signup_env, fake_graph, company_factory, admin_principal_factory
) -> None:
    company_id = company_factory("signup-reconnect")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    old_phone = _meta_id("2")
    repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=old_phone, updated_by="fixture")

    response = _connect(company_id, owner)

    assert response.status_code == 200, response.text
    assert response.json()["active_binding_count"] == 1
    active = repository.get_whatsapp_bindings(company_id=company_id, active_only=True)
    assert [binding["phone_number_id"] for binding in active] == [fake_graph.phone_number_id]
    historical = [b for b in repository.get_whatsapp_bindings(company_id=company_id) if b["phone_number_id"] == old_phone]
    assert historical and historical[0]["active"] == 0
    # Vínculo manual antigo não tinha WABA nem token: nada a cancelar na Meta.
    assert all(call["method"] != "DELETE" for call in fake_graph.calls)


def test_replacing_the_number_wipes_the_old_token_and_releases_the_old_waba(
    signup_env, fake_graph, company_factory, admin_principal_factory
) -> None:
    company_id = company_factory("signup-replace")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    old_waba = _meta_id("1")
    old_binding = store_connection(
        company_id,
        phone_number_id=_meta_id("2"),
        display_phone_number="5511999990000",
        waba_id=old_waba,
        business_id=None,
        verified_name="Old Co",
        quality_rating="GREEN",
        platform_type="CLOUD_API",
        access_secret_encrypted=encrypt_secret(OLD_BUSINESS_TOKEN),
        registration_pin_encrypted=encrypt_secret("111111"),
        credential_source="embedded_signup",
        connected_by="test",
    )
    assert old_binding is not None

    response = _connect(company_id, owner)

    assert response.status_code == 200, response.text
    _assert_no_secret(response.text)
    row = _secret_columns(int(old_binding["id"]))
    assert row["active"] == 0
    assert row["access_secret_encrypted"] is None
    assert decrypt_secret(row["registration_pin_encrypted"]) == "111111"
    # A WABA antiga é liberada depois da gravação, com o token antigo — o
    # novo não tem acesso a ela.
    assert fake_graph.calls[-1] == {
        "method": "DELETE",
        "path": f"{old_waba}/subscribed_apps",
        "token": OLD_BUSINESS_TOKEN,
    }
    assert _paths(fake_graph)[:-1] == [
        ("POST", "oauth/access_token"),
        ("GET", "debug_token"),
        ("GET", f"{fake_graph.waba_id}/phone_numbers"),
        ("GET", fake_graph.phone_number_id),
        ("POST", f"{fake_graph.waba_id}/subscribed_apps"),
        ("POST", f"{fake_graph.phone_number_id}/register"),
    ]


def test_old_waba_is_kept_subscribed_while_another_company_still_uses_it(
    signup_env, fake_graph, company_factory, admin_principal_factory
) -> None:
    shared_waba = _meta_id("1")
    neighbour = company_factory("signup-neighbour")
    assert _store_company_token(neighbour, phone_number_id=_meta_id("2"), waba_id=shared_waba) is not None
    company_id = company_factory("signup-shared-waba")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    assert _store_company_token(company_id, phone_number_id=_meta_id("2"), waba_id=shared_waba) is not None

    response = _connect(company_id, owner)

    assert response.status_code == 200, response.text
    assert all(call["method"] != "DELETE" for call in fake_graph.calls)
    assert get_credentials_for_company(neighbour) is not None


def test_identity_session_of_another_company_is_forbidden(signup_env, fake_graph, monkeypatch) -> None:
    from tests.test_saas_identity import _register

    suffix = uuid4().hex[:10]
    foreign_company_id = repository.create_company(slug=f"foreign-{suffix}", name="Foreign Co")
    with TestClient(app) as identity_client:
        payload, csrf_token = _register(identity_client, monkeypatch, suffix=suffix)
        own_company_id = int(payload["memberships"][0]["company_id"])

        forbidden = identity_client.post(
            "/admin/whatsapp/embedded-signup/complete",
            params={"company_id": foreign_company_id},
            headers={"X-CSRF-Token": csrf_token},
            json={"code": SIGNUP_CODE},
        )
        assert forbidden.status_code == 403, forbidden.text
        assert fake_graph.calls == []
        assert get_credentials_for_company(foreign_company_id) is None

        allowed = identity_client.post(
            "/admin/whatsapp/embedded-signup/complete",
            params={"company_id": own_company_id},
            headers={"X-CSRF-Token": csrf_token},
            json={"code": SIGNUP_CODE},
        )
        assert allowed.status_code == 200, allowed.text
        stored = get_credentials_for_company(own_company_id)
        assert stored is not None
        assert stored["connected_by"] == "Tenant Owner"


def test_operator_cannot_connect_or_disconnect(signup_env, fake_graph, company_factory, admin_principal_factory) -> None:
    company_id = company_factory("signup-operator")
    operator = admin_principal_factory(global_role="operator", company_ids=[company_id])

    assert _connect(company_id, operator).status_code == 403
    assert _disconnect(company_id, operator).status_code == 403
    assert fake_graph.calls == []


def test_break_glass_token_still_needs_an_existing_company(signup_env, fake_graph, monkeypatch) -> None:
    """O token de plataforma é global, mas a empresa vem da query e tem de existir."""
    monkeypatch.setattr(settings, "admin_superadmin_token", "signup-break-glass-token")
    headers = {"Authorization": "Bearer signup-break-glass-token"}

    missing_company = client.post(
        "/admin/whatsapp/embedded-signup/complete", headers=headers, json={"code": SIGNUP_CODE}
    )
    assert missing_company.status_code == 400, missing_company.text

    unknown_company = client.post(
        "/admin/whatsapp/embedded-signup/complete",
        params={"company_id": 999_999_999},
        headers=headers,
        json={"code": SIGNUP_CODE},
    )
    assert unknown_company.status_code == 404, unknown_company.text
    assert unknown_company.json()["detail"] == "Empresa não encontrada."
    assert fake_graph.calls == []


def test_break_glass_token_connects_and_records_the_declared_actor(
    signup_env, fake_graph, company_factory, monkeypatch
) -> None:
    audit = _AuditRepository()
    monkeypatch.setattr(admin_routes, "repository", audit)
    monkeypatch.setattr(settings, "admin_superadmin_token", "signup-break-glass-token")
    company_id = company_factory("signup-break-glass")
    headers = {"Authorization": "Bearer signup-break-glass-token", "X-Admin-Actor": "Operador da plataforma"}

    response = client.post(
        "/admin/whatsapp/embedded-signup/complete",
        params={"company_id": company_id},
        headers=headers,
        json={"code": SIGNUP_CODE},
    )

    assert response.status_code == 200, response.text
    _assert_no_secret(response.text)
    stored = get_credentials_for_company(company_id)
    assert stored is not None
    assert stored["connected_by"] == "Operador da plataforma"
    connected = [event for event in audit.events if event.get("action") == "whatsapp_connected"]
    assert connected and connected[-1]["status"] == "success"
    assert connected[-1]["actor_id"] == "Operador da plataforma"
    assert connected[-1]["auth_mode"] == "token"
    assert connected[-1]["resource_id"] == str(stored["id"])

    disconnected = client.post(
        "/admin/whatsapp/embedded-signup/disconnect", params={"company_id": company_id}, headers=headers
    )
    assert disconnected.status_code == 200, disconnected.text
    assert get_credentials_for_company(company_id) is None


# ── Desconexão ────────────────────────────────────────────────────────


def test_disconnect_unsubscribes_and_clears_the_token(
    signup_env, fake_graph, company_factory, admin_principal_factory, monkeypatch
) -> None:
    audit = _AuditRepository()
    monkeypatch.setattr(admin_routes, "repository", audit)
    company_id = company_factory("signup-disconnect")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    assert _connect(company_id, owner).status_code == 200
    stored = get_credentials_for_company(company_id)
    assert stored is not None
    binding_id = int(stored["id"])
    pin = fake_graph.register_pin
    fake_graph.calls.clear()

    response = _disconnect(company_id, owner)

    assert response.status_code == 200, response.text
    status = response.json()
    assert status["has_company_token"] is False
    assert status["can_send"] is False
    assert status["active_binding_count"] == 0
    assert status["connection_source"] is None
    _assert_no_secret(response.text)
    assert fake_graph.calls == [
        {"method": "DELETE", "path": f"{fake_graph.waba_id}/subscribed_apps", "token": BUSINESS_TOKEN}
    ]

    rows = [b for b in repository.get_whatsapp_bindings(company_id=company_id) if int(b["id"]) == binding_id]
    assert rows and rows[0]["active"] == 0
    assert get_credentials_for_company(company_id) is None
    row = _secret_columns(binding_id)
    assert row["access_secret_encrypted"] is None
    # O PIN fica: é a verificação em duas etapas que a Meta mantém no número
    # e a próxima conexão precisa dele (ver clear_connection).
    assert decrypt_secret(row["registration_pin_encrypted"]) == pin
    assert row["credential_source"] == "embedded_signup"
    assert row["needs_reconnection"] == 0

    disconnected = [event for event in audit.events if event.get("action") == "whatsapp_disconnected"]
    assert disconnected and disconnected[-1]["resource_id"] == str(binding_id)
    assert disconnected[-1]["metadata"]["credential_source"] == "embedded_signup"
    assert "binding_ids" not in disconnected[-1]["metadata"]
    assert fake_graph.phone_number_id not in json.dumps(disconnected[-1]["metadata"])
    _assert_no_secret(json.dumps(disconnected[-1]["metadata"]))


def test_disconnect_survives_a_meta_failure(signup_env, company_factory, admin_principal_factory, monkeypatch) -> None:
    class _FailingDelete(FakeGraph):
        def delete(self, path, token=None):
            raise MetaGraphError("auth", http_status=401, provider_code=190, title="token expired")

    fake = _FailingDelete(waba_id=_meta_id("1"), phone_number_id=_meta_id("2"))
    monkeypatch.setattr(whatsapp_signup_service, "MetaGraphClient", lambda *args, **kwargs: fake)
    company_id = company_factory("signup-disconnect-fail")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    assert _connect(company_id, owner).status_code == 200

    response = _disconnect(company_id, owner)

    assert response.status_code == 200, response.text
    assert get_credentials_for_company(company_id) is None


def test_disconnect_without_a_connection_is_404(signup_env, company_factory, admin_principal_factory) -> None:
    company_id = company_factory("signup-nothing")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _disconnect(company_id, owner)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Nenhum número conectado nesta empresa."


def test_disconnect_reaches_the_token_row_even_with_a_second_manual_binding(
    signup_env, fake_graph, company_factory, admin_principal_factory, monkeypatch
) -> None:
    audit = _AuditRepository()
    monkeypatch.setattr(admin_routes, "repository", audit)
    monkeypatch.setattr(settings, "whatsapp_api_token", GLOBAL_TOKEN)
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.example.test/v22.0")
    company_id = company_factory("signup-two-active")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    assert _connect(company_id, owner).status_code == 200
    connected = repository.get_whatsapp_bindings(company_id=company_id, active_only=True)[0]
    # Um vínculo manual por cima da conexão (PUT /admin/whatsapp/bindings)
    # deixa duas linhas ativas e o status "mais de um número".
    manual = repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=_meta_id("2"), updated_by="manual")
    assert manual is not None
    assert get_credentials_for_company(company_id) is None
    fake_graph.calls.clear()

    response = _disconnect(company_id, owner)

    assert response.status_code == 200, response.text
    _assert_no_secret(response.text)
    assert _paths(fake_graph) == [("DELETE", f"{fake_graph.waba_id}/subscribed_apps")]
    row = _secret_columns(int(connected["id"]))
    assert row["active"] == 0
    assert row["access_secret_encrypted"] is None
    # O vínculo manual não foi conectado pelo painel: continua como estava.
    status = response.json()
    assert status["active_binding_count"] == 1
    assert status["connection_source"] == "manual"
    assert status["has_company_token"] is False
    disconnected = [event for event in audit.events if event.get("action") == "whatsapp_disconnected"]
    assert disconnected and disconnected[-1]["resource_id"] == str(connected["id"])


def test_disconnect_clears_a_token_left_in_a_row_deactivated_by_the_legacy_state_endpoint(
    signup_env, fake_graph, company_factory, admin_principal_factory
) -> None:
    company_id = company_factory("signup-legacy-state")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    assert _connect(company_id, owner).status_code == 200
    stored = get_credentials_for_company(company_id)
    assert stored is not None
    assert repository.set_whatsapp_binding_active(int(stored["id"]), active=False, updated_by="legacy") == "updated"
    assert _secret_columns(int(stored["id"]))["access_secret_encrypted"] is not None
    fake_graph.calls.clear()

    response = _disconnect(company_id, owner)

    assert response.status_code == 200, response.text
    assert _paths(fake_graph) == [("DELETE", f"{fake_graph.waba_id}/subscribed_apps")]
    assert _secret_columns(int(stored["id"]))["access_secret_encrypted"] is None
    assert credentials_repository.list_bindings_with_token(company_id) == []
    assert _disconnect(company_id, owner).status_code == 404


def test_disconnect_with_two_manual_bindings_explains_the_ambiguity(
    signup_env, fake_graph, company_factory, admin_principal_factory
) -> None:
    company_id = company_factory("signup-two-manual")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    for _ in range(2):
        assert repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=_meta_id("2")) is not None

    response = _disconnect(company_id, owner)

    assert response.status_code == 409, response.text
    assert "mais de um número ativo" in response.json()["detail"]
    assert fake_graph.calls == []
    assert len(repository.get_whatsapp_bindings(company_id=company_id, active_only=True)) == 2


def test_disconnect_keeps_a_shared_waba_subscribed(signup_env, fake_graph, company_factory, admin_principal_factory) -> None:
    shared_waba = _meta_id("1")
    neighbour = company_factory("disconnect-neighbour")
    assert _store_company_token(neighbour, phone_number_id=_meta_id("2"), waba_id=shared_waba) is not None
    company_id = company_factory("disconnect-shared")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    assert _store_company_token(company_id, phone_number_id=_meta_id("2"), waba_id=shared_waba) is not None

    response = _disconnect(company_id, owner)

    assert response.status_code == 200, response.text
    assert fake_graph.calls == []
    assert get_credentials_for_company(company_id) is None
    assert get_credentials_for_company(neighbour) is not None


# ── Provider: token da empresa ou global ──────────────────────────────


def _store_company_token(company_id: int, *, phone_number_id: str, waba_id: str = "waba-provider") -> dict:
    stored = store_connection(
        company_id,
        phone_number_id=phone_number_id,
        display_phone_number="5511999990000",
        waba_id=waba_id,
        business_id=None,
        verified_name="Provider Co",
        quality_rating="GREEN",
        platform_type="CLOUD_API",
        access_secret_encrypted=encrypt_secret(BUSINESS_TOKEN),
        registration_pin_encrypted=encrypt_secret("123456"),
        credential_source="embedded_signup",
        connected_by="test",
    )
    assert stored is not None
    return stored


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


def test_store_connection_wipes_the_token_of_the_binding_it_replaces(signup_env, company_factory) -> None:
    company_id = company_factory("store-replace")
    first = _store_company_token(company_id, phone_number_id=f"phone-{uuid4().hex[:8]}")
    second = _store_company_token(company_id, phone_number_id=f"phone-{uuid4().hex[:8]}")

    assert second["id"] != first["id"]
    row = _secret_columns(int(first["id"]))
    assert row["active"] == 0
    assert row["access_secret_encrypted"] is None
    assert decrypt_secret(row["registration_pin_encrypted"]) == "123456"
    assert credentials_repository.list_bindings_with_token(company_id) == [get_credentials_for_company(company_id)]


def test_provider_sends_with_the_company_token_and_the_default_graph_url(
    signup_env, company_factory, monkeypatch
) -> None:
    import requests

    monkeypatch.setattr(settings, "whatsapp_api_token", GLOBAL_TOKEN)
    monkeypatch.setattr(settings, "whatsapp_api_base_url", None)
    company_id = company_factory("provider-company-token")
    phone_number_id = f"phone-{uuid4().hex[:8]}"
    _store_company_token(company_id, phone_number_id=phone_number_id)
    observed: list[dict] = []

    def fake_post(url, *, headers, json, timeout):
        observed.append({"url": url, "headers": headers, "json": json})
        return _Response(200, {"messages": [{"id": "wamid.company"}]})

    monkeypatch.setattr(requests, "post", fake_post)

    provider = WhatsAppProvider(company_id=company_id)
    result = provider.send_message("5511977776666", "olá")

    assert provider.has_company_token is True
    assert provider.base_url == "https://graph.facebook.com/v23.0"
    assert result["provider_message_id"] == "wamid.company"
    assert observed[0]["url"] == f"https://graph.facebook.com/v23.0/{phone_number_id}/messages"
    assert observed[0]["headers"]["Authorization"] == f"Bearer {BUSINESS_TOKEN}"
    assert GLOBAL_TOKEN not in json.dumps(observed)


def test_provider_prefers_a_pinned_base_url_with_the_company_token(signup_env, company_factory, monkeypatch) -> None:
    import requests

    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.example.test/v22.0/")
    company_id = company_factory("provider-pinned-url")
    phone_number_id = f"phone-{uuid4().hex[:8]}"
    _store_company_token(company_id, phone_number_id=phone_number_id)
    observed: dict = {}

    def fake_get(url, *, headers, params, timeout):
        observed.update(url=url, headers=headers)
        return _Response(200, {"id": phone_number_id})

    monkeypatch.setattr(requests, "get", fake_get)

    WhatsAppProvider(company_id=company_id).check_connection()

    assert observed["url"] == f"https://graph.example.test/v22.0/{phone_number_id}"
    assert observed["headers"]["Authorization"] == f"Bearer {BUSINESS_TOKEN}"


def test_provider_falls_back_to_the_global_token_without_company_credentials(company_factory, monkeypatch) -> None:
    import requests

    monkeypatch.setattr(settings, "whatsapp_api_token", GLOBAL_TOKEN)
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.example.test/v22.0")
    company_id = company_factory("provider-global-token")
    phone_number_id = f"phone-{uuid4().hex[:8]}"
    repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=phone_number_id, updated_by="fixture")
    observed: dict = {}

    def fake_post(url, *, headers, json, timeout):
        observed.update(url=url, headers=headers)
        return _Response(200, {"messages": [{"id": "wamid.global"}]})

    monkeypatch.setattr(requests, "post", fake_post)

    provider = WhatsAppProvider(company_id=company_id)
    provider.send_message("5511977776666", "olá")

    assert provider.has_company_token is False
    assert observed["headers"]["Authorization"] == f"Bearer {GLOBAL_TOKEN}"
    assert observed["url"] == f"https://graph.example.test/v22.0/{phone_number_id}/messages"


def test_provider_without_any_credential_explains_how_to_connect(company_factory, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_api_token", None)
    monkeypatch.setattr(settings, "whatsapp_api_base_url", None)
    company_id = company_factory("provider-no-credential")
    repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=f"phone-{uuid4().hex[:8]}")

    with pytest.raises(WhatsAppProviderError) as exc_info:
        WhatsAppProvider(company_id=company_id).send_message("5511977776666", "olá")

    assert exc_info.value.kind == "configuration"
    assert "reconecte o número pelo painel" in str(exc_info.value)


def test_undecryptable_company_token_is_a_configuration_error_not_a_fallback(
    signup_env, company_factory, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "whatsapp_api_token", GLOBAL_TOKEN)
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.example.test/v22.0")
    company_id = company_factory("provider-bad-key")
    _store_company_token(company_id, phone_number_id=f"phone-{uuid4().hex[:8]}")
    monkeypatch.setattr(settings, "credentials_encryption_key", generate_key())

    provider = WhatsAppProvider(company_id=company_id)
    with pytest.raises(WhatsAppProviderError) as exc_info:
        provider.send_message("5511977776666", "olá")

    assert exc_info.value.kind == "configuration"
    assert provider.has_company_token is False
    assert "CREDENTIALS_ENCRYPTION_KEY" in str(exc_info.value)
    assert BUSINESS_TOKEN not in str(exc_info.value)
    assert GLOBAL_TOKEN not in str(exc_info.value)


def test_channel_status_and_connection_test_report_an_unreadable_company_token(
    signup_env, company_factory, admin_principal_factory, monkeypatch
) -> None:
    import requests

    monkeypatch.setattr(settings, "whatsapp_api_token", GLOBAL_TOKEN)
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.example.test/v22.0")
    company_id = company_factory("status-bad-key")
    _store_company_token(company_id, phone_number_id=f"phone-{uuid4().hex[:8]}")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    monkeypatch.setattr(settings, "credentials_encryption_key", generate_key())

    def _never(*args, **kwargs):
        raise AssertionError("Meta must not be contacted with an unreadable credential")

    monkeypatch.setattr(requests, "get", _never)

    status = client.get("/admin/whatsapp/channel-status", params={"company_id": company_id}, headers=_headers(owner))
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["has_company_token"] is True
    assert body["credential_unreadable"] is True
    assert body["can_send"] is False
    assert body["delivery_configured"] is False
    assert body["needs_reconnection"] is True
    assert body["connection_source"] == "embedded_signup"
    assert "CREDENTIALS_ENCRYPTION_KEY" in body["details"]
    _assert_no_secret(status.text)

    check = client.post("/admin/whatsapp/test-connection", params={"company_id": company_id}, headers=_headers(owner))
    assert check.status_code == 200, check.text
    assert check.json()["configured"] is False
    assert check.json()["connected"] is False
    assert check.json()["error_kind"] == "configuration"
    assert "CREDENTIALS_ENCRYPTION_KEY" in check.json()["details"]
    _assert_no_secret(check.text)


def test_connection_test_does_not_blame_meta_for_a_server_side_configuration_error(
    signup_env, company_factory, monkeypatch
) -> None:
    company_id = company_factory("check-configuration")
    _store_company_token(company_id, phone_number_id=f"phone-{uuid4().hex[:8]}")

    def _configuration_failure(self) -> None:
        raise WhatsAppProviderError("credential cannot be decrypted; check CREDENTIALS_ENCRYPTION_KEY", kind="configuration")

    monkeypatch.setattr(WhatsAppProvider, "check_connection", _configuration_failure)

    result = check_whatsapp_connection(company_id)

    assert result["configured"] is False
    assert result["connected"] is False
    assert result["error_kind"] == "configuration"
    assert result["provider_code"] is None
    assert "a Meta não foi consultada" in result["details"]
    assert "CREDENTIALS_ENCRYPTION_KEY" in result["details"]


def test_meta_token_rejection_flags_the_binding_for_reconnection(
    signup_env, company_factory, admin_principal_factory, monkeypatch
) -> None:
    import requests

    company_id = company_factory("provider-190")
    phone_number_id = f"phone-{uuid4().hex[:8]}"
    stored = _store_company_token(company_id, phone_number_id=phone_number_id)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: _Response(401, {"error": {"code": 190, "message": "Error validating access token"}}),
    )

    with pytest.raises(WhatsAppProviderError) as exc_info:
        WhatsAppProvider(company_id=company_id).send_message("5511977776666", "olá")

    assert exc_info.value.code == 190
    assert exc_info.value.kind == "permanent"
    refreshed = get_credentials_for_company(company_id)
    assert refreshed is not None and refreshed["needs_reconnection"] == 1
    assert refreshed["id"] == stored["id"]

    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    status = client.get("/admin/whatsapp/channel-status", params={"company_id": company_id}, headers=_headers(owner))
    assert status.status_code == 200, status.text
    assert status.json()["needs_reconnection"] is True
    assert status.json()["credential_unreadable"] is False
    assert "reconecte" in status.json()["details"]


def test_rejected_global_token_does_not_flag_a_manual_binding(company_factory, monkeypatch) -> None:
    import requests

    monkeypatch.setattr(settings, "whatsapp_api_token", GLOBAL_TOKEN)
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.example.test/v22.0")
    company_id = company_factory("provider-global-190")
    repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=f"phone-{uuid4().hex[:8]}")
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: _Response(401, {"error": {"code": 190, "message": "Error validating access token"}}),
    )

    with pytest.raises(WhatsAppProviderError) as exc_info:
        WhatsAppProvider(company_id=company_id).send_message("5511977776666", "olá")

    assert exc_info.value.code == 190
    # O token recusado é o da instalação: reconectar pelo painel não
    # resolveria, então o vínculo não é marcado.
    refreshed = get_credentials_for_company(company_id)
    assert refreshed is not None and refreshed["needs_reconnection"] == 0
    status = get_whatsapp_channel_status(company_id)
    assert status["needs_reconnection"] is False
    assert "reconecte" not in status["details"]


def test_successful_connection_check_records_verification_and_clears_the_flag(
    signup_env, company_factory, monkeypatch
) -> None:
    import requests

    company_id = company_factory("provider-verify")
    phone_number_id = f"phone-{uuid4().hex[:8]}"
    stored = _store_company_token(company_id, phone_number_id=phone_number_id)
    mark_needs_reconnection(int(stored["id"]), True)
    assert get_credentials_for_company(company_id)["needs_reconnection"] == 1
    observed: dict = {}

    def fake_get(url, *, headers, params, timeout):
        observed.update(headers=headers)
        return _Response(200, {"id": phone_number_id, "verified_name": "Provider Co"})

    monkeypatch.setattr(requests, "get", fake_get)

    result = check_whatsapp_connection(company_id)

    assert result["connected"] is True
    assert observed["headers"]["Authorization"] == f"Bearer {BUSINESS_TOKEN}"
    refreshed = get_credentials_for_company(company_id)
    assert refreshed is not None
    assert refreshed["needs_reconnection"] == 0
    assert refreshed["last_verified_at"] is not None
    assert BUSINESS_TOKEN not in json.dumps(result)


def test_channel_status_without_any_number_points_to_the_panel(signup_env, company_factory) -> None:
    company_id = company_factory("status-empty")

    status = get_whatsapp_channel_status(company_id)

    assert status["binding"] is None
    assert status["can_connect"] is True
    assert status["can_send"] is False
    assert status["has_company_token"] is False
    assert status["connection_source"] is None
    assert "Conecte o WhatsApp da empresa pelo painel" in status["details"]


def test_channel_status_reports_a_manual_binding_source(company_factory, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_api_token", GLOBAL_TOKEN)
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.example.test/v22.0")
    company_id = company_factory("status-manual")
    repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=f"phone-{uuid4().hex[:8]}")

    status = get_whatsapp_channel_status(company_id)

    assert status["connection_source"] == "manual"
    assert status["has_company_token"] is False
    assert status["can_send"] is True


def test_reactivated_history_row_without_a_token_reports_the_global_credential(
    signup_env, fake_graph, company_factory, admin_principal_factory, monkeypatch
) -> None:
    company_id = company_factory("status-reactivated")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])
    assert _connect(company_id, owner).status_code == 200
    stored = get_credentials_for_company(company_id)
    assert stored is not None
    assert _disconnect(company_id, owner).status_code == 200
    assert repository.set_whatsapp_binding_active(int(stored["id"]), active=True, updated_by="admin") == "updated"
    monkeypatch.setattr(settings, "whatsapp_api_token", GLOBAL_TOKEN)
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.example.test/v22.0")

    status = get_whatsapp_channel_status(company_id)

    # A linha lembra que veio do popup, mas quem envia agora é o token global.
    assert status["has_company_token"] is False
    assert status["connection_source"] == "manual"
    assert status["can_send"] is True
    assert _secret_columns(int(stored["id"]))["credential_source"] == "embedded_signup"


# ── Cliente da Graph ──────────────────────────────────────────────────


def _graph_call(monkeypatch, *, status_code: int, body, capture: dict, exc: Exception | None = None):
    import requests

    def fake_request(method, url, *, headers, params, json, data, timeout):
        capture.update(method=method, url=url, headers=headers, params=params, json=json, data=data, timeout=timeout)
        if exc is not None:
            raise exc
        return _Response(status_code, body)

    monkeypatch.setattr(requests, "request", fake_request)


def test_graph_client_sends_the_bearer_only_when_given(monkeypatch) -> None:
    capture: dict = {}
    _graph_call(monkeypatch, status_code=200, body={"success": True}, capture=capture)
    graph = MetaGraphClient(base_url="https://graph.example.test/v23.0/")

    assert graph.post("waba-1/subscribed_apps", token=BUSINESS_TOKEN) == {"success": True}
    assert capture["method"] == "POST"
    assert capture["url"] == "https://graph.example.test/v23.0/waba-1/subscribed_apps"
    assert capture["headers"] == {"Authorization": f"Bearer {BUSINESS_TOKEN}"}
    assert capture["timeout"] == 20

    # Os segredos da troca vão no corpo do formulário, nunca na query string.
    graph.post("oauth/access_token", data={"client_id": APP_ID, "client_secret": APP_SECRET, "code": SIGNUP_CODE})
    assert capture["method"] == "POST"
    assert capture["headers"] == {}
    assert capture["params"] is None
    assert capture["json"] is None
    assert capture["data"] == {"client_id": APP_ID, "client_secret": APP_SECRET, "code": SIGNUP_CODE}
    assert APP_SECRET not in capture["url"]


def test_graph_client_defaults_to_the_official_graph_url(monkeypatch) -> None:
    monkeypatch.setattr(settings, "meta_graph_api_version", "v24.0")

    assert MetaGraphClient().base_url == "https://graph.facebook.com/v24.0"


ERROR_DETAILS = "+5511999990000 / EAAB-fragment-of-a-token"


@pytest.mark.parametrize(
    ("status_code", "body", "expected_kind", "expected_code"),
    [
        (
            401,
            {
                "error": {
                    "code": 190,
                    "message": "Error validating access token",
                    "type": "OAuthException",
                    "error_data": {"messaging_product": "whatsapp", "details": ERROR_DETAILS},
                }
            },
            "auth",
            190,
        ),
        (
            400,
            {"error": {"code": 100, "message": "Invalid parameter", "error_data": {"details": ERROR_DETAILS}}},
            "rejected",
            100,
        ),
        (429, {"error": {"code": 4, "message": "Rate limited"}}, "transient", 4),
        (500, "not json at all", "transient", None),
    ],
)
def test_graph_client_classifies_errors_without_the_raw_body(
    monkeypatch, status_code, body, expected_kind, expected_code
) -> None:
    capture: dict = {}
    if isinstance(body, str):
        import requests

        class _NotJson:
            status_code = 500

            @staticmethod
            def json():
                raise ValueError("not json")

        monkeypatch.setattr(requests, "request", lambda *args, **kwargs: _NotJson())
    else:
        _graph_call(monkeypatch, status_code=status_code, body=body, capture=capture)

    with pytest.raises(MetaGraphError) as exc_info:
        MetaGraphClient(base_url="https://graph.example.test/v23.0").get("thing", token=BUSINESS_TOKEN)

    assert exc_info.value.kind == expected_kind
    assert exc_info.value.provider_code == expected_code
    assert exc_info.value.http_status == status_code
    assert BUSINESS_TOKEN not in str(exc_info.value)
    # `error_data.details` traz telefone e pedaços de token: nem no título
    # nem na mensagem.
    assert ERROR_DETAILS not in str(exc_info.value)
    assert ERROR_DETAILS not in exc_info.value.title
    assert "5511999990000" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("via", "echoed"),
    [("params", (BUSINESS_TOKEN, APP_SECRET)), ("data", (APP_SECRET, SIGNUP_CODE))],
)
def test_graph_client_redacts_secrets_echoed_in_error_messages(monkeypatch, via, echoed) -> None:
    """Todo segredo enviado — no cabeçalho, na query ou no corpo — some do título."""
    capture: dict = {}
    _graph_call(
        monkeypatch,
        status_code=400,
        body={"error": {"code": 100, "message": f"Bad request with {echoed[0]} and {echoed[1]}"}},
        capture=capture,
    )
    graph = MetaGraphClient(base_url="https://graph.example.test/v23.0")

    with pytest.raises(MetaGraphError) as exc_info:
        if via == "params":
            graph.get("debug_token", params={"input_token": BUSINESS_TOKEN}, token=f"{APP_ID}|{APP_SECRET}")
        else:
            graph.post("oauth/access_token", data={"client_id": APP_ID, "client_secret": APP_SECRET, "code": SIGNUP_CODE})

    for secret in echoed:
        assert secret not in exc_info.value.title
        assert secret not in str(exc_info.value)
    assert exc_info.value.title.count("[redacted]") == 2


def test_graph_client_network_failures_are_typed_and_drop_the_request_url(monkeypatch) -> None:
    import requests

    capture: dict = {}
    # O `requests` formata a falha com a URL completa; para a troca do code
    # isso incluiria o app secret se ele fosse na query string.
    network_error = requests.ConnectionError(
        "HTTPSConnectionPool(host='graph.example.test', port=443): Max retries exceeded with url: "
        f"/v23.0/oauth/access_token?client_secret={APP_SECRET}&code={SIGNUP_CODE}"
    )
    _graph_call(monkeypatch, status_code=200, body={}, capture=capture, exc=network_error)

    with pytest.raises(MetaGraphError) as exc_info:
        MetaGraphClient(base_url="https://graph.example.test/v23.0").post(
            "oauth/access_token", data={"client_id": APP_ID, "client_secret": APP_SECRET, "code": SIGNUP_CODE}
        )

    exc = exc_info.value
    assert exc.kind == "network"
    assert exc.title == "ConnectionError"
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(exc))
    assert APP_SECRET not in rendered
    assert SIGNUP_CODE not in rendered


def test_graph_client_rejects_non_object_json(monkeypatch) -> None:
    capture: dict = {}
    _graph_call(monkeypatch, status_code=200, body=[1, 2], capture=capture)

    with pytest.raises(MetaGraphError) as exc_info:
        MetaGraphClient(base_url="https://graph.example.test/v23.0").get("thing")

    assert exc_info.value.kind == "invalid_response"


def test_network_failure_during_exchange_tells_the_owner_to_retry(
    signup_env, company_factory, admin_principal_factory, monkeypatch
) -> None:
    _install_fake(monkeypatch, exchange_error=MetaGraphError("network", title="ConnectTimeout"))
    company_id = company_factory("signup-network")
    owner = admin_principal_factory(global_role="superadmin", company_ids=[company_id])

    response = _connect(company_id, owner)

    assert response.status_code == 502, response.text
    assert "tente de novo em instantes" in response.json()["detail"]


def test_signup_error_status_code_override() -> None:
    assert SignupError("x", "detalhe").status_code == 502
    assert SignupError("x", "detalhe", status_code=400).status_code == 400


# ── Prontidão ─────────────────────────────────────────────────────────


def _readiness_checks() -> dict[str, dict]:
    return {check["code"]: check for check in system_readiness_service.get_system_readiness()["checks"]}


def test_production_readiness_accepts_embedded_signup_without_a_global_token(
    signup_env, company_factory, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "strict_runtime_validation", False)
    monkeypatch.setattr(settings, "outbound_provider", "whatsapp")
    monkeypatch.setattr(system_readiness_service, "_legacy_context_drift_status", lambda: {"ok": True, "detail": "ok"})
    monkeypatch.setattr(
        system_readiness_service,
        "get_company_context_status",
        lambda company_slug=None: {"is_valid": True, "error": None, "source": "database", "resolved_path": None},
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_calendar_profile",
        lambda: {"provider_readiness": {"ready": True, "details": "calendar ok"}},
    )
    # Nenhuma empresa conectou ainda: o vínculo é aviso, não bloqueio.
    monkeypatch.setattr(credentials_repository, "count_connected_companies", lambda: 0)

    checks = _readiness_checks()

    assert system_readiness_service._whatsapp_provider_credentials_are_real() is True
    assert checks["whatsapp_provider"]["status"] == "pass"
    assert checks["whatsapp_signature_validation"]["status"] == "pass"
    assert checks["credential_vault"]["status"] == "pass"
    assert checks["whatsapp_channel_binding"]["status"] == "warn"
    assert checks["whatsapp_channel_binding"]["severity"] == "warning"
    assert "tela Canal" in checks["whatsapp_channel_binding"]["recommendation"]
    whatsapp_blockers = [
        code for code, check in checks.items()
        if code.startswith("whatsapp") and check["status"] == "fail" and check["severity"] == "blocker"
    ]
    assert whatsapp_blockers == []

    # Com uma empresa conectada o vínculo passa.
    monkeypatch.setattr(credentials_repository, "count_connected_companies", lambda: 2)
    assert _readiness_checks()["whatsapp_channel_binding"]["status"] == "pass"


def test_connected_companies_count_reads_only_bindings_with_a_company_token(signup_env, company_factory) -> None:
    before = credentials_repository.count_connected_companies()
    company_id = company_factory("readiness-count")
    repository.upsert_whatsapp_binding(company_id=company_id, phone_number_id=f"phone-{uuid4().hex[:8]}")
    assert credentials_repository.count_connected_companies() == before

    _store_company_token(company_id, phone_number_id=f"phone-{uuid4().hex[:8]}")
    assert credentials_repository.count_connected_companies() == before + 1


def test_global_phone_number_keeps_the_legacy_binding_check(signup_env, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", f"legacy-{uuid4().hex[:8]}")

    status = system_readiness_service._whatsapp_channel_binding_status()

    assert status["ok"] is False
    assert status.get("warn_only") is None
    assert "Nenhum vínculo ativo" in status["detail"]


def test_credential_vault_check_blocks_production_with_meta_app_but_no_key(signup_env, monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "credentials_encryption_key", None)
    monkeypatch.setattr(system_readiness_service, "_legacy_context_drift_status", lambda: {"ok": True, "detail": "ok"})
    monkeypatch.setattr(
        system_readiness_service,
        "get_calendar_profile",
        lambda: {"provider_readiness": {"ready": True, "details": "calendar ok"}},
    )

    checks = _readiness_checks()

    assert checks["credential_vault"]["status"] == "fail"
    assert checks["credential_vault"]["severity"] == "blocker"
    assert "CREDENTIALS_ENCRYPTION_KEY" in checks["credential_vault"]["recommendation"]
    # Sem cofre o Embedded Signup não conta como credencial real.
    assert system_readiness_service._embedded_signup_is_configured() is False


def test_credential_vault_check_is_only_a_warning_when_nothing_needs_it(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "meta_app_id", None)
    monkeypatch.setattr(settings, "google_calendar_client_id", None)
    monkeypatch.setattr(settings, "credentials_encryption_key", None)

    assert system_readiness_service._credential_vault_is_required() is False

    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(system_readiness_service, "_legacy_context_drift_status", lambda: {"ok": True, "detail": "ok"})
    checks = _readiness_checks()
    assert checks["credential_vault"]["status"] == "warn"
    assert checks["credential_vault"]["severity"] == "warning"


def test_system_profile_exposes_connection_configuration(signup_env) -> None:
    profile = system_readiness_service.get_system_profile()

    assert profile["channels"]["whatsapp_configured"] is True
    assert profile["channels"]["whatsapp_embedded_signup_configured"] is True
    assert profile["channels"]["credential_vault_configured"] is True
    assert APP_SECRET not in json.dumps(profile)
