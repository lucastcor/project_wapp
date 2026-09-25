"""P08/G2 — conexão da agenda Google por OAuth, por empresa.

Cobre as duas metades do fluxo (authorize → callback) com um cliente Google
falso, e o que importa para o piloto: o refresh token nunca sai da API, vai
cifrado para o banco e a empresa dona do token sai do `state`, nunca da URL.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.credential_vault import CredentialVaultError, decrypt_secret, generate_key, is_encrypted
from app.main import app
from app.services import calendar_service
from app.services import google_calendar_oauth_service as oauth_service
from app.services.google_calendar_oauth_service import GoogleOAuthClientError
from app.services.platform_session_service import issue_platform_session
from app.services.rate_limit_service import _resolve_scope_and_limit
from app.storage import google_oauth_repository as oauth_repository
from app.storage.repository import RuntimeRepository
from app.storage.sql_support import fetch_all, fetch_one

repository = RuntimeRepository()

CLIENT_ID = "client-id-sintetico.apps.googleusercontent.com"
CLIENT_SECRET = "client-secret-sintetico-XYZ"
PUBLIC_URL = "https://painel.example.invalid"
CALLBACK = "/platform/integrations/google/callback"
REFRESH_TOKEN = "refresh-sintetico-primeiro"
ACCOUNT_EMAIL = "dona@example.invalid"
SUPERADMIN_TOKEN = "oauth-superadmin-token"
VIEWER_TOKEN = "oauth-viewer-token"


@pytest.fixture
def google_env(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_calendar_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_calendar_client_secret", CLIENT_SECRET)
    monkeypatch.setattr(settings, "google_calendar_refresh_token", None)
    monkeypatch.setattr(settings, "google_oauth_redirect_url", None)
    monkeypatch.setattr(settings, "platform_public_url", PUBLIC_URL)
    monkeypatch.setattr(settings, "credentials_encryption_key", generate_key())
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "strict_runtime_validation", False)


class FakeGoogleClient:
    """Duplo de `GoogleOAuthClient`: registra chamadas, nunca toca a rede."""

    def __init__(
        self,
        *,
        refresh_token: str | None = REFRESH_TOKEN,
        calendars: list[dict] | None = None,
        exchange_error: str | None = None,
        revoke_error: str | None = None,
        calendars_error: str | None = None,
    ) -> None:
        self.refresh_token = refresh_token
        self.calendars = calendars if calendars is not None else [
            {"id": ACCOUNT_EMAIL, "summary": "Dona", "primary": True, "access_role": "owner"},
            {"id": "equipe@group.calendar.google.com", "summary": "Equipe", "primary": False, "access_role": "writer"},
        ]
        self.exchange_error = exchange_error
        self.revoke_error = revoke_error
        self.calendars_error = calendars_error
        self.calls: list[tuple] = []

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        self.calls.append(("exchange_code", code, redirect_uri))
        if self.exchange_error:
            raise GoogleOAuthClientError("exchange_failed", "falhou", provider_code=self.exchange_error)
        tokens = {"access_token": "access-sintetico", "expires_in": 3599, "token_type": "Bearer"}
        if self.refresh_token:
            tokens["refresh_token"] = self.refresh_token
        return tokens

    def revoke(self, token: str) -> bool:
        self.calls.append(("revoke", token))
        if self.revoke_error:
            raise GoogleOAuthClientError("revoke_failed", "falhou", provider_code=self.revoke_error)
        return True

    def list_calendars(self, refresh_token: str) -> list[dict]:
        self.calls.append(("list_calendars", refresh_token))
        if self.calendars_error:
            raise GoogleOAuthClientError("calendar_list_failed", "falhou", provider_code=self.calendars_error)
        return [dict(item) for item in self.calendars]


def _broken_vault(_value: str) -> str:
    raise CredentialVaultError("CREDENTIALS_ENCRYPTION_KEY is required")


def _revocations(fake: FakeGoogleClient) -> list[tuple]:
    return [call for call in fake.calls if call[0] == "revoke"]


def _assert_audit_metadata_hides(events: list[dict], *secrets: str) -> None:
    """Metadata de auditoria não carrega o token em claro, cifrado nem a chave."""
    for event in events:
        metadata_json = event["metadata_json"] or ""
        assert '"refresh_token"' not in metadata_json
        for secret in secrets:
            assert secret and secret not in metadata_json


@pytest.fixture
def fake_google(monkeypatch) -> FakeGoogleClient:
    fake = FakeGoogleClient()
    monkeypatch.setattr(oauth_service, "GoogleOAuthClient", lambda *args, **kwargs: fake)
    return fake


def _register(client: TestClient, monkeypatch, *, suffix: str) -> tuple[dict, str]:
    monkeypatch.setattr(settings, "enable_self_service_signup", True)
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", None)
    monkeypatch.setattr(settings, "admin_operator_token", None)
    monkeypatch.setattr(settings, "admin_superadmin_token", None)
    response = client.post(
        "/platform/auth/register",
        json={
            "email": f"owner-{suffix}@example.com",
            "password": "Agenda!Secure2026",
            "display_name": "Tenant Owner",
            "company_name": f"Agenda Company {suffix}",
            "company_slug": f"agenda-{suffix}",
        },
    )
    assert response.status_code == 200, response.text
    csrf_token = client.cookies.get("project_wapp_csrf")
    assert csrf_token
    return response.json(), str(csrf_token)


def _owner_company(payload: dict) -> int:
    return int(payload["memberships"][0]["company_id"])


def _authorize(client: TestClient, company_id: int, csrf_token: str) -> tuple[str, str]:
    response = client.post(
        "/admin/calendar/google/authorize",
        params={"company_id": company_id},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200, response.text
    url = response.json()["authorization_url"]
    return url, parse_qs(urlparse(url).query)["state"][0]


def _callback(client: TestClient, **params: str) -> tuple[int, dict[str, list[str]], str]:
    response = client.get(CALLBACK, params=params, follow_redirects=False)
    location = response.headers.get("location", "")
    return response.status_code, parse_qs(urlparse(location).query), location


def _integration_row(integration_id: int) -> dict:
    row = fetch_one(
        """
        SELECT id, company_id, provider, external_calendar_id, config_json, active,
               connection_source, connected_account_email, connected_at, connected_by
        FROM calendar_integrations WHERE id = ?
        """,
        (integration_id,),
    )
    assert row is not None
    row["config"] = json.loads(row["config_json"] or "{}")
    return row


def _active_google_id(company_id: int) -> int:
    integration = oauth_repository.get_active_google_integration(company_id)
    assert integration is not None
    return int(integration["id"])


def _enable_break_glass(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", VIEWER_TOKEN)
    monkeypatch.setattr(settings, "admin_operator_token", None)
    monkeypatch.setattr(settings, "admin_superadmin_token", SUPERADMIN_TOKEN)


# ── configuração ──────────────────────────────────────────────────────


def test_config_names_missing_variables_and_enables_when_complete(monkeypatch, google_env) -> None:
    assert oauth_service.google_oauth_config() == {
        "enabled": True,
        "reason": None,
        "redirect_url": PUBLIC_URL + CALLBACK,
    }

    monkeypatch.setattr(settings, "google_oauth_redirect_url", "https://outro.example.invalid/cb")
    assert oauth_service.google_oauth_config()["redirect_url"] == "https://outro.example.invalid/cb"

    monkeypatch.setattr(settings, "google_oauth_redirect_url", None)
    monkeypatch.setattr(settings, "platform_public_url", None)
    monkeypatch.setattr(settings, "google_calendar_client_id", "change-me")
    disabled = oauth_service.google_oauth_config()
    assert disabled["enabled"] is False
    assert disabled["redirect_url"] is None
    assert "GOOGLE_CALENDAR_CLIENT_ID" in disabled["reason"]
    assert "PLATFORM_PUBLIC_URL" in disabled["reason"]
    assert CLIENT_SECRET not in disabled["reason"]

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "credentials_encryption_key", None)
    assert "CREDENTIALS_ENCRYPTION_KEY" in oauth_service.google_oauth_config()["reason"]


def test_config_route_is_readable_without_company_by_any_role(monkeypatch, google_env) -> None:
    _enable_break_glass(monkeypatch)
    with TestClient(app) as client:
        response = client.get(
            "/admin/calendar/google/config",
            headers={"Authorization": f"Bearer {VIEWER_TOKEN}"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True
    assert CLIENT_SECRET not in response.text


# ── authorize ─────────────────────────────────────────────────────────


def test_owner_authorize_builds_the_exact_url_and_records_the_state(monkeypatch, google_env) -> None:
    suffix = uuid4().hex[:10]
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=suffix)
        company_id = _owner_company(payload)
        before = datetime.now(UTC)
        url, state = _authorize(client, company_id, csrf_token)

    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert parse_qs(parsed.query) == {
        "client_id": [CLIENT_ID],
        "redirect_uri": [PUBLIC_URL + CALLBACK],
        "response_type": ["code"],
        "scope": [calendar_service.CALENDAR_SCOPE],
        "access_type": ["offline"],
        "prompt": ["consent"],
        "include_granted_scopes": ["true"],
        "state": [state],
    }
    assert CLIENT_SECRET not in url

    row = fetch_one("SELECT * FROM oauth_states WHERE state = ?", (state,))
    assert row is not None
    assert row["provider"] == "google_calendar"
    assert int(row["company_id"]) == company_id
    assert int(row["user_id"]) == int(payload["user_id"])
    assert row["auth_mode"] == "identity"
    assert row["consumed_at"] is None
    expires_at = datetime.fromisoformat(str(row["expires_at"]))
    assert 14 * 60 <= (expires_at - before).total_seconds() <= 16 * 60


def test_authorize_is_refused_without_configuration_or_privileges(monkeypatch, google_env) -> None:
    suffix = uuid4().hex[:10]
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=suffix)
        company_id = _owner_company(payload)

        monkeypatch.setattr(settings, "google_calendar_client_secret", "")
        response = client.post(
            "/admin/calendar/google/authorize",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 503, response.text
        assert "GOOGLE_CALENDAR_CLIENT_SECRET" in response.json()["detail"]
    assert fetch_all("SELECT state FROM oauth_states WHERE company_id = ?", (company_id,)) == []


def test_authorize_rejects_identity_from_another_company_and_operators(
    monkeypatch, google_env, company_factory, admin_principal_factory
) -> None:
    other_company_id = company_factory("agenda-alheia")
    suffix = uuid4().hex[:10]
    with TestClient(app) as client:
        _payload, csrf_token = _register(client, monkeypatch, suffix=suffix)
        foreign = client.post(
            "/admin/calendar/google/authorize",
            params={"company_id": other_company_id},
            headers={"X-CSRF-Token": csrf_token},
        )
    assert foreign.status_code == 403, foreign.text

    operator = admin_principal_factory(global_role="operator", company_ids=[other_company_id])
    with TestClient(app) as client:
        denied = client.post(
            "/admin/calendar/google/authorize",
            params={"company_id": other_company_id},
            headers={
                "Cookie": f"project_wapp_platform_session={operator['token']}; project_wapp_csrf={operator['csrf']}",
                "X-CSRF-Token": operator["csrf"],
            },
        )
    assert denied.status_code == 403, denied.text
    assert fetch_all("SELECT state FROM oauth_states WHERE company_id = ?", (other_company_id,)) == []


# ── callback: state e sessão ──────────────────────────────────────────


def test_callback_rejects_invalid_expired_and_consumed_states(monkeypatch, google_env, fake_google) -> None:
    suffix = uuid4().hex[:10]
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=suffix)
        company_id = _owner_company(payload)

        status, query, _ = _callback(client, code="c", state="nao-existe")
        assert status == 303
        assert query == {"view": ["agenda"], "google": ["error"], "reason": ["state_invalid"]}

        expired = oauth_repository.create_state(
            provider="google_calendar",
            company_id=company_id,
            user_id=int(payload["user_id"]),
            actor="Tenant Owner",
            auth_mode="identity",
            ttl_minutes=-1,
        )
        _status, query, _ = _callback(client, code="c", state=expired)
        assert query["reason"] == ["state_invalid"]

        _url, state = _authorize(client, company_id, csrf_token)
        _status, first, _ = _callback(client, code="codigo-1", state=state)
        assert first["google"] == ["connected"]
        _status, replay, _ = _callback(client, code="codigo-2", state=state)
        assert replay["reason"] == ["state_invalid"]

    assert [call[0] for call in fake_google.calls].count("exchange_code") == 1


def test_callback_rejects_a_session_that_did_not_start_the_flow(monkeypatch, google_env, fake_google) -> None:
    with TestClient(app) as owner_client:
        payload, csrf_token = _register(owner_client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        _url, state = _authorize(owner_client, company_id, csrf_token)

    with TestClient(app) as intruder_client:
        _register(intruder_client, monkeypatch, suffix=uuid4().hex[:10])
        status, query, _ = _callback(intruder_client, code="codigo", state=state)

    assert status == 303
    assert query["reason"] == ["forbidden"]
    assert query["company_id"] == [str(company_id)]
    assert fake_google.calls == []
    assert oauth_repository.get_active_google_integration(company_id) is None
    assert oauth_repository.consume_state(state, "google_calendar") is None


def test_callback_without_session_redirects_to_login(google_env) -> None:
    with TestClient(app) as anonymous:
        status, query, location = _callback(anonymous, code="codigo", state="qualquer")
    assert status == 303
    assert location.startswith("/platform?")
    assert query == {"view": ["agenda"], "google": ["error"], "reason": ["session_required"]}


# ── callback: conexão ─────────────────────────────────────────────────


def test_callback_connects_encrypts_the_token_and_deactivates_other_integrations(
    monkeypatch, google_env, fake_google
) -> None:
    suffix = uuid4().hex[:10]
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=suffix)
        company_id = _owner_company(payload)
        previous_id = repository.create_calendar_integration(
            company_id=company_id, provider="mock", external_calendar_id=None, config={}, active=True
        )
        _url, state = _authorize(client, company_id, csrf_token)

        status, query, location = _callback(client, code="codigo-google", state=state)

    assert status == 303
    assert query["google"] == ["connected"]
    assert location == f"{PUBLIC_URL}/platform?view=agenda&company_id={company_id}&google=connected"
    assert REFRESH_TOKEN not in location
    assert fake_google.calls[0] == ("exchange_code", "codigo-google", PUBLIC_URL + CALLBACK)
    assert fake_google.calls[1] == ("list_calendars", REFRESH_TOKEN)

    integration_id = _active_google_id(company_id)
    row = _integration_row(integration_id)
    assert row["provider"] == "google_calendar"
    assert row["external_calendar_id"] == "primary"
    assert row["config"]["calendar_id"] == "primary"
    assert is_encrypted(row["config"]["refresh_token"])
    assert decrypt_secret(row["config"]["refresh_token"]) == REFRESH_TOKEN
    assert REFRESH_TOKEN not in row["config_json"]
    assert "client_secret" not in row["config"]
    assert row["connection_source"] == "oauth"
    assert row["connected_account_email"] == ACCOUNT_EMAIL
    assert row["connected_by"] == "Tenant Owner"
    assert row["connected_at"]
    assert int(_integration_row(previous_id)["active"]) == 0

    events = fetch_all(
        "SELECT actor_id, resource_id, metadata_json FROM audit_events WHERE company_id = ? AND action = ?",
        (company_id, "google_calendar_connected"),
    )
    assert len(events) == 1
    assert events[0]["resource_id"] == str(integration_id)
    assert json.loads(events[0]["metadata_json"])["account_email"] == ACCOUNT_EMAIL
    _assert_audit_metadata_hides(events, REFRESH_TOKEN, row["config"]["refresh_token"])


def test_reconnection_updates_the_same_integration_row(monkeypatch, google_env, fake_google) -> None:
    suffix = uuid4().hex[:10]
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=suffix)
        company_id = _owner_company(payload)
        _url, state = _authorize(client, company_id, csrf_token)
        _callback(client, code="primeiro", state=state)
        first_id = _active_google_id(company_id)
        oauth_service.set_calendar(company_id, "equipe@group.calendar.google.com", actor="Tenant Owner")

        fake_google.refresh_token = "refresh-sintetico-segundo"
        _url, state = _authorize(client, company_id, csrf_token)
        _status, query, _ = _callback(client, code="segundo", state=state)

    assert query["google"] == ["connected"]
    assert _active_google_id(company_id) == first_id
    row = _integration_row(first_id)
    assert decrypt_secret(row["config"]["refresh_token"]) == "refresh-sintetico-segundo"
    assert row["external_calendar_id"] == "primary"
    assert len(fetch_all("SELECT id FROM calendar_integrations WHERE company_id = ?", (company_id,))) == 1


def test_callback_without_refresh_token_stores_nothing(monkeypatch, google_env, fake_google) -> None:
    fake_google.refresh_token = None
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        _url, state = _authorize(client, company_id, csrf_token)
        _status, query, _ = _callback(client, code="codigo", state=state)

    assert query["reason"] == ["no_refresh_token"]
    assert oauth_repository.get_active_google_integration(company_id) is None
    assert fetch_all("SELECT id FROM calendar_integrations WHERE company_id = ?", (company_id,)) == []


def test_callback_reports_access_denied_and_exchange_failures(monkeypatch, google_env, fake_google) -> None:
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)

        _url, state = _authorize(client, company_id, csrf_token)
        _status, denied, _ = _callback(client, state=state, error="access_denied")
        assert denied["reason"] == ["access_denied"]
        assert oauth_repository.consume_state(state, "google_calendar") is None

        fake_google.exchange_error = "invalid_grant"
        _url, state = _authorize(client, company_id, csrf_token)
        _status, failed, location = _callback(client, code="codigo", state=state)
        assert failed["reason"] == ["exchange_failed"]
        assert "invalid_grant" not in location

    assert oauth_repository.get_active_google_integration(company_id) is None


def test_callback_reports_vault_unavailable_without_storing(monkeypatch, google_env, fake_google) -> None:
    monkeypatch.setattr(oauth_service, "encrypt_secret", _broken_vault)
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        _url, state = _authorize(client, company_id, csrf_token)
        _status, query, _ = _callback(client, code="codigo", state=state)

    assert query["reason"] == ["vault_unavailable"]
    assert oauth_repository.get_active_google_integration(company_id) is None
    # O Google já tinha emitido o token: sem guardá-lo, a concessão é revogada
    # para não ficar órfã na conta do dono.
    assert _revocations(fake_google) == [("revoke", REFRESH_TOKEN)]


def test_callback_revokes_a_fresh_token_it_cannot_finish_with(monkeypatch, google_env, fake_google) -> None:
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)

        fake_google.calendars_error = "HttpError"
        _url, state = _authorize(client, company_id, csrf_token)
        _status, query, location = _callback(client, code="codigo-1", state=state)
        assert query["reason"] == ["exchange_failed"]
        assert "HttpError" not in location
        assert fake_google.calls[-2:] == [("list_calendars", REFRESH_TOKEN), ("revoke", REFRESH_TOKEN)]

        # A revogação é melhor esforço: falhar nela não muda o motivo devolvido.
        fake_google.calendars_error = None
        fake_google.revoke_error = "503"
        monkeypatch.setattr(oauth_service, "encrypt_secret", _broken_vault)
        _url, state = _authorize(client, company_id, csrf_token)
        _status, query, _ = _callback(client, code="codigo-2", state=state)
        assert query["reason"] == ["vault_unavailable"]
        assert fake_google.calls[-1] == ("revoke", REFRESH_TOKEN)

    assert oauth_repository.get_active_google_integration(company_id) is None
    assert fetch_all("SELECT id FROM calendar_integrations WHERE company_id = ?", (company_id,)) == []


def test_platform_token_session_connects_and_identity_cannot_finish_its_state(
    monkeypatch, google_env, fake_google, company_factory
) -> None:
    _enable_break_glass(monkeypatch)
    company_id = company_factory("agenda-plataforma")
    with TestClient(app) as client:
        started = client.post(
            "/admin/calendar/google/authorize",
            params={"company_id": company_id},
            headers={"Authorization": f"Bearer {SUPERADMIN_TOKEN}", "X-Admin-Actor": "ops-lead"},
        )
        assert started.status_code == 200, started.text
        state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
        row = fetch_one("SELECT user_id, auth_mode FROM oauth_states WHERE state = ?", (state,))
        assert row is not None
        assert row["user_id"] is None
        assert row["auth_mode"] == "token"

        platform_cookie = issue_platform_session(role="superadmin", actor="ops-lead")["token"]
        client.cookies.set("project_wapp_platform_session", platform_cookie)
        _status, query, _ = _callback(client, code="codigo", state=state)

    assert query["google"] == ["connected"]
    assert _integration_row(_active_google_id(company_id))["connected_by"] == "ops-lead"

    # Um state iniciado pelo token de plataforma não fecha com sessão de identidade.
    other_state = oauth_repository.create_state(
        provider="google_calendar", company_id=company_id, user_id=None, actor="ops-lead", auth_mode="token"
    )
    with TestClient(app) as identity_client:
        _register(identity_client, monkeypatch, suffix=uuid4().hex[:10])
        _status, query, _ = _callback(identity_client, code="codigo", state=other_state)
    assert query["reason"] == ["forbidden"]


def test_platform_session_without_superadmin_role_cannot_finish_a_token_state(
    monkeypatch, google_env, fake_google, company_factory
) -> None:
    _enable_break_glass(monkeypatch)
    company_id = company_factory("agenda-operador")
    state = oauth_repository.create_state(
        provider="google_calendar", company_id=company_id, user_id=None, actor="ops-lead", auth_mode="token"
    )
    with TestClient(app) as client:
        operator_cookie = issue_platform_session(role="operator", actor="ops-operator")["token"]
        client.cookies.set("project_wapp_platform_session", operator_cookie)
        status, query, _ = _callback(client, code="codigo", state=state)

    assert status == 303
    assert query["reason"] == ["forbidden"]
    assert query["company_id"] == [str(company_id)]
    assert fake_google.calls == []
    assert oauth_repository.get_active_google_integration(company_id) is None
    assert fetch_all("SELECT id FROM calendar_integrations WHERE company_id = ?", (company_id,)) == []


def test_authorize_with_an_unknown_company_returns_404_instead_of_crashing(monkeypatch, google_env) -> None:
    _enable_break_glass(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/admin/calendar/google/authorize",
            params={"company_id": 987654},
            headers={"Authorization": f"Bearer {SUPERADMIN_TOKEN}"},
        )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Empresa não encontrada."
    assert fetch_all("SELECT state FROM oauth_states WHERE company_id = ?", (987654,)) == []


def test_callback_logs_never_carry_the_code_or_state(monkeypatch, google_env, fake_google, caplog) -> None:
    caplog.set_level(logging.INFO)
    code = "4/0A-codigo-secreto-do-google"
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        _url, state = _authorize(client, company_id, csrf_token)
        _status, query, location = _callback(client, code=code, state=state)
        assert query["google"] == ["connected"]

        fake_google.exchange_error = "invalid_grant"
        _url, failed_state = _authorize(client, company_id, csrf_token)
        _status, query, failed_location = _callback(client, code=code, state=failed_state)
        assert query["reason"] == ["exchange_failed"]

    for text in (location, failed_location):
        assert code not in text
        assert state not in text and failed_state not in text
    for record in caplog.records:
        if record.name.startswith("httpx"):
            # Log do cliente HTTP da suíte (lado do navegador), não do servidor.
            continue
        if getattr(record, "event", None) == "http.request.started":
            # Linha do middleware (app/main.py, arquivo compartilhado): ainda
            # registra a query inteira do callback. Fica fora deste guarda até
            # o middleware redigir `code`/`state` — ver blocked_shared_changes.
            continue
        rendered = record.getMessage() + json.dumps(getattr(record, "event_data", None) or {}, default=str)
        assert code not in rendered, record.name
        assert state not in rendered and failed_state not in rendered, record.name
        assert REFRESH_TOKEN not in rendered, record.name


# ── leitura pública e uso interno ─────────────────────────────────────


def test_public_reads_hide_the_token_and_the_provider_receives_it_decrypted(
    monkeypatch, google_env, fake_google
) -> None:
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        _url, state = _authorize(client, company_id, csrf_token)
        _callback(client, code="codigo", state=state)
        stored = _integration_row(_active_google_id(company_id))["config"]["refresh_token"]

        listed = client.get("/admin/calendar/integrations", params={"company_id": company_id})
        profile = client.get("/admin/calendar/profile", params={"company_id": company_id})

    for response in (listed, profile):
        assert response.status_code == 200, response.text
        assert REFRESH_TOKEN not in response.text
        assert stored not in response.text
        assert '"refresh_token"' not in response.text
    integration = profile.json()["integration"]
    assert integration["config"]["refresh_token_present"] is True
    assert integration["connected_account_email"] == ACCOUNT_EMAIL
    assert integration["connection_source"] == "oauth"
    assert integration["connected_at"]
    assert listed.json()[0]["connected_account_email"] == ACCOUNT_EMAIL
    assert profile.json()["provider_readiness"]["ready"] is True

    internal = calendar_service._resolve_calendar_integration(company_id=company_id)
    assert internal["config"]["refresh_token"] == REFRESH_TOKEN
    assert internal["connected_account_email"] == ACCOUNT_EMAIL
    assert internal["provider"] == "google_calendar"


def test_vault_failure_on_read_guides_reconnection_instead_of_crashing(monkeypatch, google_env, fake_google) -> None:
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        _url, state = _authorize(client, company_id, csrf_token)
        _callback(client, code="codigo", state=state)

        monkeypatch.setattr(settings, "credentials_encryption_key", generate_key())
        with pytest.raises(calendar_service.CalendarProviderError, match="reconecte"):
            calendar_service._resolve_calendar_integration(company_id=company_id)
        profile = client.get("/admin/calendar/profile", params={"company_id": company_id})

    assert profile.status_code == 200, profile.text
    assert profile.json()["provider_readiness"]["ready"] is False
    assert "reconecte" in profile.json()["provider_readiness"]["details"]
    assert REFRESH_TOKEN not in profile.text


def test_public_integration_keeps_the_primary_alias_and_masks_real_calendar_ids(
    monkeypatch, google_env, fake_google
) -> None:
    team_calendar = "equipe@group.calendar.google.com"
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        _url, state = _authorize(client, company_id, csrf_token)
        _callback(client, code="codigo", state=state)

        primary = client.get("/admin/calendar/profile", params={"company_id": company_id}).json()["integration"]
        oauth_service.set_calendar(company_id, team_calendar, actor="Tenant Owner")
        profile = client.get("/admin/calendar/profile", params={"company_id": company_id})

    # "primary" é apelido fixo do Google, não identificador: o painel compara
    # com o literal para escrever "Calendário principal da conta".
    assert primary["external_calendar_id"] == "primary"
    assert primary["config"]["calendar_id"] == "primary"
    # Um id real pode ser e-mail e continua mascarado em toda leitura pública.
    masked = profile.json()["integration"]
    assert masked["external_calendar_id"] != team_calendar
    assert masked["external_calendar_id"].endswith(".com")
    assert "equipe@group" not in profile.text


def test_connection_test_reports_a_vault_failure_instead_of_crashing(monkeypatch, google_env, fake_google) -> None:
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        _url, state = _authorize(client, company_id, csrf_token)
        _callback(client, code="codigo", state=state)

        monkeypatch.setattr(settings, "credentials_encryption_key", generate_key())
        response = client.post(
            "/admin/calendar/test-connection",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
        )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["provider"] == "google_calendar"
    assert result["configured"] is False
    assert result["connected"] is False
    assert result["error_kind"] == "configuration"
    assert "reconecte" in result["details"]
    assert REFRESH_TOKEN not in response.text
    events = fetch_all(
        "SELECT status, metadata_json FROM audit_events WHERE company_id = ? AND action = ?",
        (company_id, "connection_test"),
    )
    assert [event["status"] for event in events] == ["failure"]
    assert json.loads(events[0]["metadata_json"])["error_kind"] == "configuration"


def test_manual_integration_secrets_are_encrypted_at_rest_and_legacy_rows_still_work(google_env) -> None:
    company_id = repository.create_company(slug=f"agenda-manual-{uuid4().hex[:8]}", name="Manual")
    created = calendar_service.create_calendar_integration(
        provider="google_calendar",
        company_id=company_id,
        external_calendar_id="primary",
        config={"calendar_id": "primary", "client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN},
    )
    row = _integration_row(int(created["id"]))
    assert is_encrypted(row["config"]["refresh_token"])
    assert is_encrypted(row["config"]["client_secret"])
    assert REFRESH_TOKEN not in row["config_json"]
    assert CLIENT_SECRET not in row["config_json"]
    assert created["config"]["refresh_token_present"] is True
    assert REFRESH_TOKEN not in json.dumps(created, default=str)
    internal = calendar_service._resolve_calendar_integration(company_id=company_id)
    assert internal["config"]["refresh_token"] == REFRESH_TOKEN
    assert internal["config"]["client_secret"] == CLIENT_SECRET

    legacy_company_id = repository.create_company(slug=f"agenda-legada-{uuid4().hex[:8]}", name="Legada")
    repository.create_calendar_integration(
        company_id=legacy_company_id,
        provider="google_calendar",
        external_calendar_id="primary",
        config={"calendar_id": "primary", "refresh_token": "refresh-legado-em-claro"},
        active=True,
    )
    legacy = calendar_service._resolve_calendar_integration(company_id=legacy_company_id)
    assert legacy["config"]["refresh_token"] == "refresh-legado-em-claro"
    fake = FakeGoogleClient()
    calendars = oauth_service.list_calendars(legacy_company_id, client=fake)
    assert fake.calls == [("list_calendars", "refresh-legado-em-claro")]
    assert [item["selected"] for item in calendars] == [True, False]


def test_manual_caldav_password_is_encrypted_and_a_vault_failure_writes_nothing(monkeypatch, google_env) -> None:
    password = "senha-de-app-sintetica-XYZ"
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        created = client.post(
            "/admin/calendar/integrations",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
            json={
                "provider": "caldav",
                "external_calendar_id": "https://caldav.example.invalid/agenda/",
                "config": {"url": "https://caldav.example.invalid/", "username": "dona", "password": password},
            },
        )
        assert created.status_code == 200, created.text
        assert password not in created.text
        assert created.json()["config"]["password_present"] is True
        row = _integration_row(int(created.json()["id"]))
        assert is_encrypted(row["config"]["password"])
        assert password not in row["config_json"]
        assert calendar_service._resolve_calendar_integration(company_id=company_id)["config"]["password"] == password

        rows_before = fetch_all("SELECT id FROM calendar_integrations WHERE company_id = ?", (company_id,))
        monkeypatch.setattr(calendar_service, "encrypt_secret", _broken_vault)
        failed = client.post(
            "/admin/calendar/integrations",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
            json={"provider": "google_calendar", "config": {"calendar_id": "primary", "refresh_token": REFRESH_TOKEN}},
        )
    assert failed.status_code == 400, failed.text
    assert "CREDENTIALS_ENCRYPTION_KEY" in failed.json()["detail"]
    assert REFRESH_TOKEN not in failed.text
    assert fetch_all("SELECT id FROM calendar_integrations WHERE company_id = ?", (company_id,)) == rows_before


# ── calendários, escolha e desconexão ─────────────────────────────────


def test_calendars_selection_and_disconnect(monkeypatch, google_env, fake_google) -> None:
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        _url, state = _authorize(client, company_id, csrf_token)
        _callback(client, code="codigo", state=state)
        integration_id = _active_google_id(company_id)
        stored_token = _integration_row(integration_id)["config"]["refresh_token"]

        calendars = client.get("/admin/calendar/google/calendars", params={"company_id": company_id})
        assert calendars.status_code == 200, calendars.text
        assert [(item["id"], item["selected"]) for item in calendars.json()] == [
            (ACCOUNT_EMAIL, True),
            ("equipe@group.calendar.google.com", False),
        ]
        assert REFRESH_TOKEN not in calendars.text

        selected = client.patch(
            "/admin/calendar/google/calendar",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
            json={"calendar_id": "equipe@group.calendar.google.com"},
        )
        assert selected.status_code == 200, selected.text
        assert REFRESH_TOKEN not in selected.text
        assert stored_token not in selected.text
        assert selected.json()["config"]["refresh_token_present"] is True
        row = _integration_row(integration_id)
        assert row["external_calendar_id"] == "equipe@group.calendar.google.com"
        assert row["config"]["calendar_id"] == "equipe@group.calendar.google.com"
        assert row["config"]["refresh_token"] == stored_token
        assert calendar_service._resolve_calendar_integration(company_id=company_id)["external_calendar_id"] == (
            "equipe@group.calendar.google.com"
        )
        after_selection = client.get("/admin/calendar/google/calendars", params={"company_id": company_id})
        assert [item["selected"] for item in after_selection.json()] == [False, True]

        invalid = client.patch(
            "/admin/calendar/google/calendar",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
            json={"calendar_id": ""},
        )
        assert invalid.status_code == 422
        # `min_length=1` deixa passar só espaços; o serviço é quem recusa.
        blank = client.patch(
            "/admin/calendar/google/calendar",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
            json={"calendar_id": "   "},
        )
        assert blank.status_code == 400, blank.text
        assert blank.json()["detail"] == "Informe um calendário válido."
        assert _integration_row(integration_id)["external_calendar_id"] == "equipe@group.calendar.google.com"

        disconnected = client.post(
            "/admin/calendar/google/disconnect",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert disconnected.status_code == 200, disconnected.text
        assert disconnected.json() == {"disconnected": True, "integration_id": integration_id, "revoked": True}
        assert ("revoke", REFRESH_TOKEN) in fake_google.calls
        row = _integration_row(integration_id)
        assert int(row["active"]) == 0
        assert row["config"] == {}
        assert oauth_repository.get_active_google_integration(company_id) is None

        not_connected = client.get("/admin/calendar/google/calendars", params={"company_id": company_id})
        assert not_connected.status_code == 400
        again = client.post(
            "/admin/calendar/google/disconnect",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert again.status_code == 400

    events = fetch_all(
        "SELECT action, metadata_json FROM audit_events WHERE company_id = ? AND resource_id = ? ORDER BY id",
        (company_id, str(integration_id)),
    )
    actions = [event["action"] for event in events]
    assert "google_calendar_selected" in actions
    assert "google_calendar_disconnected" in actions
    _assert_audit_metadata_hides(events, REFRESH_TOKEN, stored_token)


def test_disconnect_survives_a_failed_revocation(monkeypatch, google_env, fake_google) -> None:
    fake_google.revoke_error = "503"
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        _url, state = _authorize(client, company_id, csrf_token)
        _callback(client, code="codigo", state=state)
        integration_id = _active_google_id(company_id)
        response = client.post(
            "/admin/calendar/google/disconnect",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
        )
    assert response.status_code == 200, response.text
    assert response.json()["revoked"] is False
    assert int(_integration_row(integration_id)["active"]) == 0


def test_disconnect_never_revokes_the_installation_wide_token(monkeypatch, google_env, fake_google) -> None:
    shared_token = "refresh-global-da-instalacao"
    monkeypatch.setattr(settings, "google_calendar_refresh_token", shared_token)
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        # Linha legada (onboarding anterior a P08): só o calendário, sem
        # credencial própria; o provider usa o token global do ambiente.
        integration_id = repository.create_calendar_integration(
            company_id=company_id,
            provider="google_calendar",
            external_calendar_id="primary",
            config={"calendar_id": "primary"},
            active=True,
        )
        # Leitura ainda enxerga a conta compartilhada, como o provider.
        listed = client.get("/admin/calendar/google/calendars", params={"company_id": company_id})
        assert listed.status_code == 200, listed.text
        assert fake_google.calls == [("list_calendars", shared_token)]

        disconnected = client.post(
            "/admin/calendar/google/disconnect",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
        )
    assert disconnected.status_code == 200, disconnected.text
    assert disconnected.json()["revoked"] is False
    # O token do ambiente é de todas as empresas: desconectar uma não o revoga.
    assert _revocations(fake_google) == []
    assert int(_integration_row(integration_id)["active"]) == 0
    assert calendar_service._resolve_calendar_integration(company_id=company_id)["integration_id"] is None

    # Linha legada com token próprio em claro: esse sim é revogado.
    legacy_company_id = repository.create_company(slug=f"agenda-propria-{uuid4().hex[:8]}", name="Própria")
    repository.create_calendar_integration(
        company_id=legacy_company_id,
        provider="google_calendar",
        external_calendar_id="primary",
        config={"calendar_id": "primary", "refresh_token": "refresh-legado-em-claro"},
        active=True,
    )
    own = FakeGoogleClient()
    result = oauth_service.disconnect(legacy_company_id, actor="ops", client=own)
    assert result["revoked"] is True
    assert own.calls == [("revoke", "refresh-legado-em-claro")]
    assert shared_token not in json.dumps(own.calls)


def test_vault_failure_on_calendars_and_disconnect_guides_reconnection(monkeypatch, google_env, fake_google) -> None:
    with TestClient(app) as client:
        payload, csrf_token = _register(client, monkeypatch, suffix=uuid4().hex[:10])
        company_id = _owner_company(payload)
        _url, state = _authorize(client, company_id, csrf_token)
        _callback(client, code="codigo", state=state)
        integration_id = _active_google_id(company_id)

        # Chave trocada em produção: o token guardado deixa de ser legível.
        monkeypatch.setattr(settings, "credentials_encryption_key", generate_key())
        calendars = client.get("/admin/calendar/google/calendars", params={"company_id": company_id})
        assert calendars.status_code == 503, calendars.text
        assert "reconecte" in calendars.json()["detail"]
        assert REFRESH_TOKEN not in calendars.text

        # O remédio documentado é desconectar e conectar de novo: precisa funcionar.
        disconnected = client.post(
            "/admin/calendar/google/disconnect",
            params={"company_id": company_id},
            headers={"X-CSRF-Token": csrf_token},
        )
    assert disconnected.status_code == 200, disconnected.text
    assert disconnected.json() == {"disconnected": True, "integration_id": integration_id, "revoked": False}
    assert _revocations(fake_google) == []
    row = _integration_row(integration_id)
    assert int(row["active"]) == 0
    assert row["config"] == {}
    assert oauth_repository.get_active_google_integration(company_id) is None


# ── cliente real (rede simulada) e repositório ────────────────────────


class _HttpResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_real_client_exchanges_revokes_and_never_leaks_the_google_body(monkeypatch, google_env, caplog) -> None:
    import requests

    observed: list[dict] = []
    responses: list[_HttpResponse] = []

    def _post(url, *, data, headers, timeout):
        observed.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return responses.pop(0)

    monkeypatch.setattr(requests, "post", _post)
    client = oauth_service.GoogleOAuthClient(timeout=7)

    responses.append(_HttpResponse(200, {"access_token": "acesso", "refresh_token": REFRESH_TOKEN}))
    tokens = client.exchange_code("codigo-google", PUBLIC_URL + CALLBACK)
    assert tokens["refresh_token"] == REFRESH_TOKEN
    assert observed[0]["url"] == "https://oauth2.googleapis.com/token"
    assert observed[0]["timeout"] == 7
    assert observed[0]["data"] == {
        "code": "codigo-google",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": PUBLIC_URL + CALLBACK,
        "grant_type": "authorization_code",
    }

    responses.append(_HttpResponse(400, {"error": "invalid_grant", "error_description": "segredo-no-corpo-XYZ"}))
    with pytest.raises(GoogleOAuthClientError) as failed:
        client.exchange_code("codigo-velho", PUBLIC_URL + CALLBACK)
    assert failed.value.reason == "exchange_failed"
    assert failed.value.provider_code == "invalid_grant"
    assert "segredo-no-corpo-XYZ" not in str(failed.value)
    assert "segredo-no-corpo-XYZ" not in caplog.text

    responses.append(_HttpResponse(502, ValueError("not json")))
    with pytest.raises(GoogleOAuthClientError):
        client.exchange_code("codigo", PUBLIC_URL + CALLBACK)

    def _unreachable(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "post", _unreachable)
    with pytest.raises(GoogleOAuthClientError, match="falar com o Google"):
        client.exchange_code("codigo", PUBLIC_URL + CALLBACK)
    with pytest.raises(GoogleOAuthClientError, match="revogue"):
        client.revoke(REFRESH_TOKEN)

    monkeypatch.setattr(requests, "post", _post)
    responses.append(_HttpResponse(200, {}))
    assert client.revoke(REFRESH_TOKEN) is True
    assert observed[-1]["url"] == "https://oauth2.googleapis.com/revoke"
    assert observed[-1]["data"] == {"token": REFRESH_TOKEN}
    responses.append(_HttpResponse(400, {"error": "invalid_token"}))
    with pytest.raises(GoogleOAuthClientError) as revoke_failed:
        client.revoke(REFRESH_TOKEN)
    assert revoke_failed.value.reason == "revoke_failed"


def test_real_client_lists_calendars_through_the_google_sdk(monkeypatch, google_env) -> None:
    from googleapiclient import discovery

    observed: dict = {}

    class _Execute:
        @staticmethod
        def execute() -> dict:
            return {
                "items": [
                    {"id": ACCOUNT_EMAIL, "summary": "Dona", "primary": True, "accessRole": "owner"},
                    {"id": "", "summary": "sem id"},
                    {"id": "equipe@group.calendar.google.com", "accessRole": "reader"},
                ]
            }

    class _CalendarList:
        @staticmethod
        def list() -> _Execute:
            return _Execute()

    class _Service:
        @staticmethod
        def calendarList() -> _CalendarList:
            return _CalendarList()

    def _build(api, version, *, credentials, cache_discovery):
        observed.update(api=api, version=version, credentials=credentials, cache_discovery=cache_discovery)
        return _Service()

    monkeypatch.setattr(discovery, "build", _build)
    calendars = oauth_service.GoogleOAuthClient().list_calendars(REFRESH_TOKEN)

    assert calendars == [
        {"id": ACCOUNT_EMAIL, "summary": "Dona", "primary": True, "access_role": "owner"},
        {
            "id": "equipe@group.calendar.google.com",
            "summary": "equipe@group.calendar.google.com",
            "primary": False,
            "access_role": "reader",
        },
    ]
    assert (observed["api"], observed["version"], observed["cache_discovery"]) == ("calendar", "v3", False)
    credentials = observed["credentials"]
    assert credentials.refresh_token == REFRESH_TOKEN
    assert credentials.client_id == CLIENT_ID
    assert credentials.scopes == [calendar_service.CALENDAR_SCOPE]

    def _broken_build(*args, **kwargs):
        raise RuntimeError(f"HttpError with {REFRESH_TOKEN} inside")

    monkeypatch.setattr(discovery, "build", _broken_build)
    with pytest.raises(GoogleOAuthClientError) as failed:
        oauth_service.GoogleOAuthClient().list_calendars(REFRESH_TOKEN)
    assert failed.value.reason == "calendar_list_failed"
    assert REFRESH_TOKEN not in str(failed.value)


def test_repository_purges_expired_states_guards_the_tenant_and_deactivates_everything(
    google_env, company_factory
) -> None:
    company_id = company_factory("agenda-repo")
    other_company_id = company_factory("agenda-repo-outra")
    expired = oauth_repository.create_state(
        provider="google_calendar", company_id=company_id, user_id=None, actor="ops", auth_mode="token", ttl_minutes=-5
    )
    live = oauth_repository.create_state(
        provider="google_calendar", company_id=company_id, user_id=None, actor="ops", auth_mode="token"
    )
    assert oauth_repository.purge_expired_states() >= 1
    assert fetch_one("SELECT state FROM oauth_states WHERE state = ?", (expired,)) is None
    assert oauth_repository.consume_state(live, "outro_provider") is None
    consumed = oauth_repository.consume_state(live, "google_calendar")
    assert consumed is not None and consumed["company_id"] == company_id
    assert consumed["consumed_at"]

    first = repository.create_calendar_integration(company_id=company_id, provider="mock", config={}, active=True)
    second = repository.create_calendar_integration(company_id=company_id, provider="caldav", config={}, active=True)
    foreign = repository.create_calendar_integration(company_id=other_company_id, provider="mock", config={}, active=True)

    assert oauth_repository.update_calendar_integration(first, company_id=other_company_id, active=False) == 0
    assert int(_integration_row(first)["active"]) == 1
    assert oauth_repository.get_calendar_integration_row(first, company_id=other_company_id) is None

    assert oauth_repository.deactivate_calendar_integrations(company_id) == 2
    assert int(_integration_row(first)["active"]) == 0
    assert int(_integration_row(second)["active"]) == 0
    assert int(_integration_row(foreign)["active"]) == 1
    assert oauth_repository.get_active_google_integration(company_id) is None


# ── limite de requisições ─────────────────────────────────────────────


def test_integration_callbacks_have_their_own_rate_limit_scope() -> None:
    resolved = _resolve_scope_and_limit(CALLBACK, "GET")
    assert resolved == ("platform_integrations", max(1, settings.rate_limit_admin_requests))
    assert _resolve_scope_and_limit("/platform/integrations/qualquer", "POST")[0] == "platform_integrations"
    assert _resolve_scope_and_limit("/platform/auth/session", "GET")[0] == "platform_auth_read"
