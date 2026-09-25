import hashlib
import hmac
import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.api import routes
from app.core.config import settings
from app.main import app
from app.services.rate_limit_service import clear_rate_limit_counters

client = TestClient(app)

NORMALIZED_INGRESS_SECRET = "test-internal-ingress-secret"
NORMALIZED_INGRESS_HEADERS = {"X-Internal-Ingress-Secret": NORMALIZED_INGRESS_SECRET}


def enable_normalized_ingress(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", NORMALIZED_INGRESS_SECRET)


def test_healthcheck() -> None:
    response = client.get("/health", headers={"X-Request-ID": "req-health-123"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "req-health-123"


def test_platform_shell() -> None:
    response = client.get("/platform")

    assert response.status_code == 200
    assert "WAPP Platform" in response.text
    assert "/platform/assets/app.js" in response.text
    # Cache-bust dos assets: a versão muda junto com o painel para o navegador
    # não servir um app.js antigo contra um backend novo.
    assert "/platform/assets/app.js?v=20260925-p08c" in response.text
    assert "/platform/assets/styles.css?v=20260925-p08c" in response.text


def test_platform_asset() -> None:
    response = client.get("/platform/assets/app.js")

    assert response.status_code == 200
    assert "bootstrap()" in response.text
    # Conexões por empresa (P08): o painel fala com o popup da Meta, oferece
    # os botões de conexão e trata a volta do OAuth do Google.
    # Embedded Signup da Meta: evento do postMessage, botões e SDK em <head>.
    assert "WA_EMBEDDED_SIGNUP" in response.text
    assert "data-connect-whatsapp" in response.text
    assert "data-connect-whatsapp-coexistence" in response.text
    assert "data-disconnect-whatsapp" in response.text
    assert "https://connect.facebook.net/pt_BR/sdk.js" in response.text
    assert "/admin/whatsapp/embedded-signup/complete" in response.text
    # Google Agenda por OAuth: botão, seletor de calendário e volta do callback.
    assert "data-connect-google" in response.text
    assert "data-google-calendar-select" in response.text
    assert "/admin/calendar/google/authorize" in response.text
    assert "google=connected" in response.text
    assert "no_refresh_token" in response.text
    # Navegação em dois grupos e editor de serviços fora do wizard (P08).
    assert "data-nav-more" in response.text
    assert "data-service-field" in response.text
    assert "/admin/company-context/record" in response.text
    # Revisão P08: a lista de calendários é carregada à parte (com "Tentar de
    # novo"), a conta do ambiente é avisada em vez de parecer conectada, o
    # popup da Meta tem saída e a volta do Google pelo bfcache destrava o botão.
    assert "data-google-calendars-loading" in response.text
    assert "data-google-calendars-refresh" in response.text
    assert "data-google-environment-note" in response.text
    assert "data-whatsapp-signup-cancel" in response.text
    assert '"pageshow"' in response.text


def _extract_js(source: str, pattern: str) -> str:
    match = re.search(pattern, source, re.S | re.M)
    assert match, f"trecho não encontrado em app.js: {pattern}"
    return match.group(0)


# Regras puras do painel, extraídas do asset pelo nome e executadas em Node:
# são as decisões que os testes de substring não conseguem cobrir.
_JS_HELPER_PATTERNS = (
    r"^function labelFor\(.*?^\}\n",
    r"^const GOOGLE_OAUTH_ERROR_MESSAGES = \{.*?^\}\n",
    r"^function isGoogleCalendarConnected\(.*?^\}\n",
    r"^function isGoogleCalendarFromEnvironment\(.*?^\}\n",
    r"^function googleCalendarLabel\(.*?^\}\n",
    r"^const WHATSAPP_SIGNUP_POPUP_STEPS = .*?\n",
    r"^function whatsappSignupCompanyForCode\(.*?^\}\n",
    r"^const FACEBOOK_SDK_SCRIPT_ID = .*?\n",
    r"^let _facebookSdkRequested = .*?\n",
    r"^function ensureFacebookSdk\(.*?^\}\n",
)

_JS_HELPER_ASSERTIONS = """
// Chave do protótipo vinda da URL (?google=error&reason=constructor) nunca
// vira texto de toast.
const fallback = "fallback"
for (const reason of ["constructor", "__proto__", "toString", "valueOf", "hasOwnProperty", "", null, undefined, "unknown"]) {
  assert.equal(labelFor(GOOGLE_OAUTH_ERROR_MESSAGES, reason, fallback), fallback, `reason=${reason}`)
}
assert.equal(labelFor(GOOGLE_OAUTH_ERROR_MESSAGES, "state_invalid", fallback), GOOGLE_OAUTH_ERROR_MESSAGES.state_invalid)
assert.equal(labelFor(GOOGLE_OAUTH_ERROR_MESSAGES, "session_required", fallback), GOOGLE_OAUTH_ERROR_MESSAGES.session_required)

// Integração do ambiente (GOOGLE_CALENDAR_REFRESH_TOKEN global) não é conexão
// da empresa: sem cartão "conectado" nem "Desconectar".
const environmentIntegration = { integration_id: null, provider: "google_calendar", active: true, source: "environment" }
assert.equal(isGoogleCalendarConnected(environmentIntegration), false)
assert.equal(isGoogleCalendarFromEnvironment(environmentIntegration), true)
const companyIntegration = { integration_id: 7, provider: "google_calendar", active: true, source: "database", connection_source: "oauth" }
assert.equal(isGoogleCalendarConnected(companyIntegration), true)
assert.equal(isGoogleCalendarFromEnvironment(companyIntegration), false)
assert.equal(isGoogleCalendarConnected({ ...companyIntegration, active: 0 }), false)
assert.equal(isGoogleCalendarConnected({ ...companyIntegration, active: false }), false)
assert.equal(isGoogleCalendarConnected({ ...companyIntegration, provider: "mock" }), false)
assert.equal(isGoogleCalendarConnected({ ...companyIntegration, integration_id: undefined }), false)
assert.equal(isGoogleCalendarConnected(null), false)
assert.equal(isGoogleCalendarFromEnvironment({ ...environmentIntegration, provider: "mock" }), false)
assert.equal(isGoogleCalendarFromEnvironment(null), false)

// Rótulo do calendário: o id mascarado pelo servidor nunca aparece.
assert.equal(googleCalendarLabel({ external_calendar_id: "primary" }, null), "Calendário principal da conta")
assert.equal(googleCalendarLabel({ external_calendar_id: "", config: { calendar_id: "primary" } }, null), "Calendário principal da conta")
assert.equal(googleCalendarLabel({}, null), "Calendário principal da conta")
assert.equal(googleCalendarLabel({ external_calendar_id: "******************.com" }, null), "Calendário escolhido pelo administrador")
assert.equal(googleCalendarLabel({ external_calendar_id: "***mary" }, null), "Calendário escolhido pelo administrador")
assert.equal(googleCalendarLabel({ external_calendar_id: "******************.com" }, { id: "x", summary: "Consultório" }), "Consultório")
assert.equal(googleCalendarLabel({ external_calendar_id: "******************.com" }, { id: "agenda@exemplo" }), "agenda@exemplo")

// O code da Meta só vale para a empresa registrada quando o popup abriu;
// estado zerado (cancelado, empresa trocada) descarta o code.
assert.equal(whatsappSignupCompanyForCode({ step: "popup", companyId: "7" }), "7")
assert.equal(whatsappSignupCompanyForCode({ step: "finished", companyId: "7" }), "7")
assert.equal(whatsappSignupCompanyForCode({ step: "popup", companyId: "" }), "")
assert.equal(whatsappSignupCompanyForCode({ step: "finished", companyId: "" }), "")
assert.equal(whatsappSignupCompanyForCode({ step: "idle", companyId: "7" }), "")
assert.equal(whatsappSignupCompanyForCode({ step: "done", companyId: "7" }), "")
assert.equal(whatsappSignupCompanyForCode({ step: "cancelled", companyId: "7" }), "")
assert.equal(whatsappSignupCompanyForCode(null), "")

// SDK da Meta: injetado uma vez em <head>; a falha de carga libera uma nova
// tentativa no próximo clique, sem F5.
const appended = []
globalThis.window = {}
globalThis.document = {
  head: { appendChild: (node) => appended.push(node) },
  getElementById: (id) => appended.find((node) => node.id === id && !node.removed) || null,
  createElement: () => {
    const node = { removed: false }
    node.remove = () => { node.removed = true }
    return node
  },
}
ensureFacebookSdk("123", "v23.0")
ensureFacebookSdk("123", "v23.0")
assert.equal(appended.length, 1, "SDK injetado uma única vez")
assert.equal(appended[0].src, "https://connect.facebook.net/pt_BR/sdk.js")
assert.equal(typeof window.fbAsyncInit, "function")
assert.equal(typeof appended[0].onerror, "function")
appended[0].onerror()
assert.equal(appended[0].removed, true, "script com falha sai do <head>")
ensureFacebookSdk("123", "v23.0")
assert.equal(appended.length, 2, "nova tentativa depois da falha de carga")
ensureFacebookSdk("123", "v23.0")
assert.equal(appended.length, 2, "carga em andamento não duplica")
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node não disponível para executar o asset")
def test_platform_asset_connection_helpers_behave(tmp_path) -> None:
    source = client.get("/platform/assets/app.js").text
    snippets = [_extract_js(source, pattern) for pattern in _JS_HELPER_PATTERNS]
    program = "\n".join(['const assert = require("node:assert/strict")', *snippets, _JS_HELPER_ASSERTIONS])
    # `.cjs`: o asset é módulo ES no navegador, mas os trechos extraídos são
    # funções soltas e `require` é o jeito mais simples de ter `assert`.
    script = tmp_path / "connection_helpers.cjs"
    script.write_text(program, encoding="utf-8")
    node = shutil.which("node")
    assert node

    # Entrada é o nosso próprio asset, não dado externo.
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30, check=False)  # noqa: S603

    assert result.returncode == 0, result.stderr


def test_platform_stylesheet_has_connection_styles() -> None:
    response = client.get("/platform/assets/styles.css")

    assert response.status_code == 200
    assert ".connection-facts" in response.text
    assert ".connection-notice" in response.text


def test_platform_session_login_when_admin_auth_disabled(monkeypatch) -> None:
    with TestClient(app) as local_client:
        monkeypatch.setattr(settings, "enable_admin_auth", False)

        response = local_client.post(
            "/platform/auth/session",
            json={"admin_actor": "platform-admin"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["authenticated"] is True
        assert payload["role"] == "superadmin"
        assert "project_wapp_platform_session=" in response.headers["set-cookie"]


def test_platform_session_cookie_can_access_admin_routes(monkeypatch) -> None:
    class FakeRepository:
        def get_observability_summary(self) -> dict:
            return {
                "totals": {
                    "messages": 1,
                    "contacts": 1,
                    "pending_questions": 0,
                    "knowledge_entries": 0,
                    "message_examples": 0,
                    "outbound_messages": 0,
                    "finetuning_jobs": 0,
                    "companies": 1,
                },
                "pending_questions_by_status": {},
                "outbound_by_status": {},
                "knowledge_by_status": {},
                "messages_by_classification": {},
                "messages_by_action": {},
                "messages_by_reply_source": {},
                "companies_by_status": {"active": 1, "inactive": 0},
            }

        def create_audit_event(self, **kwargs) -> int:
            return 1

    from app.api import admin_routes

    with TestClient(app) as local_client:
        monkeypatch.setattr(settings, "enable_admin_auth", True)
        monkeypatch.setattr(settings, "admin_api_token", None)
        monkeypatch.setattr(settings, "admin_viewer_token", "viewer-cookie-token")
        monkeypatch.setattr(settings, "admin_operator_token", None)
        monkeypatch.setattr(settings, "admin_superadmin_token", None)
        monkeypatch.setattr(admin_routes, "repository", FakeRepository())

        login_response = local_client.post(
            "/platform/auth/session",
            json={"admin_token": "viewer-cookie-token", "admin_actor": "cookie-user"},
        )
        assert login_response.status_code == 200

        summary_response = local_client.get("/admin/observability/summary")

        assert summary_response.status_code == 200
        assert summary_response.json()["totals"]["messages"] == 1


def test_readiness_check() -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database_ok"] is True
    assert response.json()["blocker_count"] == 0


def test_readiness_check_returns_503_when_runtime_is_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "check_runtime_database_connection",
        lambda: {"ok": True, "detail": "probe ok"},
    )
    monkeypatch.setattr(
        main_module,
        "get_system_readiness",
        lambda: {
            "overall_status": "blocked",
            "pilot_ready": False,
            "scale_ready": False,
        },
    )
    monkeypatch.setattr(
        main_module,
        "get_runtime_blockers",
        lambda: [{"code": "postgres_target", "detail": "runtime still on sqlite"}],
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["blockers"] == ["postgres_target"]


def test_incoming_message(monkeypatch) -> None:
    monkeypatch.setattr(routes, "process_incoming_message", lambda **kwargs: {
        "contact_id": kwargs["contact_id"],
        "classification": "lead_novo",
        "classification_reason": "Interesse comercial claro.",
        "classification_confidence": 0.91,
        "reply": "Olá, posso te ajudar com isso.",
        "source": "openai",
        "auto_reply": True,
        "action": "auto_reply",
        "priority": "high",
        "pipeline_stage": "qualificando",
        "next_action": "Entender escopo.",
        "lead_temperature": "warm",
        "handoff_required": False,
        "pending_question_id": None,
        "question_type": None,
    })

    payload = {
        "contact_id": "5511999999999",
        "contact_name": "Cliente Teste",
        "message": "oi",
    }
    response = client.post("/messages/incoming", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["reply"] == "Olá, posso te ajudar com isso."
    assert data["source"] == "openai"
    assert response.headers["X-Request-ID"]


def test_incoming_message_empty_after_trim() -> None:
    payload = {
        "contact_id": "5511999999999",
        "contact_name": "Cliente Teste",
        "message": "   ",
    }
    response = client.post("/messages/incoming", json=payload)

    assert response.status_code == 400


def test_incoming_message_rate_limited(monkeypatch) -> None:
    clear_rate_limit_counters()
    monkeypatch.setattr(settings, "enable_rate_limiting", True)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings, "rate_limit_incoming_requests", 1)
    monkeypatch.setattr(settings, "rate_limit_exempt_localhost", False)
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    monkeypatch.setattr(routes, "process_incoming_message", lambda **kwargs: {
        "contact_id": kwargs["contact_id"],
        "classification": "lead_novo",
        "classification_reason": "Interesse comercial claro.",
        "classification_confidence": 0.91,
        "reply": "Olá, posso te ajudar com isso.",
        "source": "openai",
        "auto_reply": True,
        "action": "auto_reply",
        "priority": "high",
        "pipeline_stage": "qualificando",
        "next_action": "Entender escopo.",
        "lead_temperature": "warm",
        "handoff_required": False,
        "pending_question_id": None,
        "question_type": None,
    })

    payload = {
        "contact_id": "5511999999999",
        "contact_name": "Cliente Teste",
        "message": "oi",
    }
    headers = {"X-Forwarded-For": "198.51.100.10"}
    first_response = client.post("/messages/incoming", json=payload, headers=headers)
    second_response = client.post("/messages/incoming", json=payload, headers=headers)

    assert first_response.status_code == 200
    assert first_response.headers["X-RateLimit-Scope"] == "incoming_message"
    assert second_response.status_code == 429
    assert second_response.json()["detail"] == "Too many requests."
    assert second_response.headers["X-RateLimit-Scope"] == "incoming_message"
    assert second_response.headers["Retry-After"]
    clear_rate_limit_counters()


def test_whatsapp_webhook(monkeypatch, company_factory) -> None:
    from uuid import uuid4

    from app.services import inbound_service
    from app.storage.inbound_repository import list_jobs
    company_id = company_factory()
    enable_normalized_ingress(monkeypatch)
    monkeypatch.setattr(inbound_service, "process_whatsapp_webhook", lambda **_: pytest.fail("HTTP must only enqueue"))
    message_id = uuid4().hex
    response = client.post("/webhooks/whatsapp", params={"company_id": company_id},
        json={"event": "message.received", "from_number": "5511999999999", "text": "Olá", "provider_message_id": message_id},
        headers=NORMALIZED_INGRESS_HEADERS)
    assert response.status_code == 200
    assert response.json()["queued_count"] == 1
    assert response.json()["processed"] is False
    assert list_jobs(company_id)[0]["status"] == "pending"
    assert response.headers["X-Request-ID"]


def test_whatsapp_webhook_accepts_meta_payload_and_queues_auto_reply(monkeypatch, company_factory) -> None:
    from uuid import uuid4

    from app.storage.inbound_repository import list_jobs
    cid = company_factory()
    monkeypatch.setattr(settings, "whatsapp_app_secret", "test-secret")
    monkeypatch.setattr(routes, "_resolve_meta_company", lambda _: {"company_id": cid, "company_slug": None})
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "bound"}, "messages": [{"id": uuid4().hex, "from": "5511999999999", "type": "text", "text": {"body": "Olá"}}]
    }}]}]}
    raw = json.dumps(payload).encode()
    signature = hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
    response = client.post("/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": "sha256="+signature})
    assert response.status_code == 200
    assert response.json()["payload_mode"] == "meta"
    assert list_jobs(cid)[0]["outbound_message_id"] is None


def test_whatsapp_webhook_ignores_duplicate_provider_message_id(monkeypatch, company_factory) -> None:
    from uuid import uuid4

    from app.storage.inbound_repository import list_jobs
    cid = company_factory()
    enable_normalized_ingress(monkeypatch)
    payload = {"event": "message.received", "from_number": "5511999999999", "text": "Olá", "provider_message_id": uuid4().hex}
    first = client.post("/webhooks/whatsapp", json=payload, params={"company_id": cid}, headers=NORMALIZED_INGRESS_HEADERS)
    second = client.post("/webhooks/whatsapp", json=payload, params={"company_id": cid}, headers=NORMALIZED_INGRESS_HEADERS)
    assert first.status_code == second.status_code == 200
    assert first.json()["queued_count"] == 1
    assert second.json()["queued_count"] == 0
    assert second.json()["jobs"][0]["duplicate"] is True
    assert len(list_jobs(cid)) == 1


def test_whatsapp_webhook_ignores_non_message_meta_events(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "meta-app-secret")
    raw_payload = (
        b'{"object":"whatsapp_business_account","entry":[{"changes":[{"value":{"statuses":'
        b'[{"id":"wamid.status.1","status":"delivered"}]}}]}]}'
    )
    signature = hmac.new(b"meta-app-secret", msg=raw_payload, digestmod=hashlib.sha256).hexdigest()

    response = client.post(
        "/webhooks/whatsapp",
        content=raw_payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )

    assert response.status_code == 200
    assert response.json()["processed"] is False


def test_whatsapp_webhook_rejects_unsigned_meta_status_event(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "meta-app-secret")
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.status.1", "status": "delivered"}]}}]}],
    }

    response = client.post("/webhooks/whatsapp", json=payload)

    assert response.status_code == 403


def test_whatsapp_webhook_rejects_invalid_meta_signature(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "meta-app-secret")
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [
                                {
                                    "wa_id": "5511999999999",
                                    "profile": {"name": "Cliente Meta"},
                                }
                            ],
                            "messages": [
                                {
                                    "id": "wamid.inbound.123",
                                    "from": "5511999999999",
                                    "type": "text",
                                    "text": {"body": "Oi, gostaria de agendar"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    response = client.post(
        "/webhooks/whatsapp",
        json=payload,
        headers={"X-Hub-Signature-256": "sha256=invalid"},
    )

    assert response.status_code == 403


def test_verify_whatsapp_webhook(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_webhook_verify_token", "verify-me")

    response = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "123456",
        },
    )

    assert response.status_code == 200
    assert response.text == "123456"


def test_verify_whatsapp_webhook_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_webhook_verify_token", "verify-me")

    response = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "123456",
        },
    )

    assert response.status_code == 403
