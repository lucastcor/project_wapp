from fastapi.testclient import TestClient

from app.main import app
from app.api import admin_routes
from app.core.config import settings


client = TestClient(app)


def test_read_observability_summary(monkeypatch) -> None:
    class FakeRepository:
        def get_observability_summary(self) -> dict:
            return {
                "totals": {
                    "messages": 12,
                    "contacts": 4,
                    "pending_questions": 2,
                    "knowledge_entries": 3,
                    "message_examples": 5,
                    "outbound_messages": 6,
                    "finetuning_jobs": 1,
                    "companies": 2,
                },
                "pending_questions_by_status": {"pending": 2},
                "outbound_by_status": {"pending": 1, "sent": 5},
                "knowledge_by_status": {"active": 3},
                "messages_by_classification": {"lead_novo": 8, "suporte": 4},
                "messages_by_action": {"auto_reply": 10, "handoff_human": 2},
                "messages_by_reply_source": {"openai": 7, "template": 3, "none": 2},
                "companies_by_status": {"active": 2, "inactive": 0},
            }

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())

    response = client.get("/admin/observability/summary", headers={"X-Request-ID": "req-admin-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["messages"] == 12
    assert payload["pending_questions_by_status"]["pending"] == 2
    assert payload["messages_by_classification"]["lead_novo"] == 8
    assert response.headers["X-Request-ID"] == "req-admin-1"


def test_read_observability_metrics(monkeypatch) -> None:
    class FakeRepository:
        def get_observability_summary(self) -> dict:
            return {
                "totals": {
                    "messages": 3,
                    "contacts": 2,
                    "pending_questions": 1,
                    "knowledge_entries": 0,
                    "message_examples": 0,
                    "outbound_messages": 1,
                    "finetuning_jobs": 0,
                    "companies": 1,
                },
                "pending_questions_by_status": {"pending": 1},
                "outbound_by_status": {"sent": 1},
                "knowledge_by_status": {},
                "messages_by_classification": {"lead_novo": 3},
                "messages_by_action": {"auto_reply": 3},
                "messages_by_reply_source": {"openai": 2, "template": 1},
                "companies_by_status": {"active": 1, "inactive": 0},
            }

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())

    response = client.get("/admin/observability/metrics")

    assert response.status_code == 200
    assert "project_wapp_total{entity=\"messages\"} 3" in response.text
    assert "project_wapp_messages_by_classification{classification=\"lead_novo\"} 3" in response.text


def test_read_audit_events(monkeypatch) -> None:
    class FakeRepository:
        def get_audit_events(self, limit: int, action: str | None, resource_type: str | None) -> list[dict]:
            assert limit == 50
            assert action == "create"
            assert resource_type == "company"
            return [
                {
                    "id": 1,
                    "actor_id": "streamlit-admin",
                    "action": "create",
                    "resource_type": "company",
                    "resource_id": "12",
                    "company_id": 12,
                    "status": "success",
                    "request_id": "req-audit-1",
                    "metadata": {"company_slug": "empresa-1"},
                    "created_at": "2026-04-06 12:00:00",
                }
            ]

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())

    response = client.get("/admin/audit-events", params={"limit": 50, "action": "create", "resource_type": "company"})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["actor_id"] == "streamlit-admin"
    assert payload[0]["metadata"]["company_slug"] == "empresa-1"


def test_read_schema_migrations(monkeypatch) -> None:
    class FakeRepository:
        def get_schema_migrations(self) -> list[dict]:
            return [
                {
                    "version": "001_runtime_tables",
                    "name": "Create runtime tables and columns",
                    "applied_at": "2026-04-06 10:00:00",
                },
                {
                    "version": "002_audit_events",
                    "name": "Create audit events table",
                    "applied_at": "2026-04-06 10:01:00",
                },
            ]

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())

    response = client.get("/admin/system/migrations")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["version"] == "001_runtime_tables"
    assert payload[1]["version"] == "002_audit_events"


def test_read_system_profile(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_routes,
        "get_system_profile",
        lambda: {
            "app": {"environment": "development", "version": "1.0.0"},
            "database": {
                "runtime": {"backend": "sqlite"},
                "postgres_target": {"configured": False},
            },
            "security": {"admin_auth_enabled": False},
        },
    )

    response = client.get("/admin/system/profile")

    assert response.status_code == 200
    payload = response.json()
    assert payload["app"]["environment"] == "development"
    assert payload["database"]["runtime"]["backend"] == "sqlite"


def test_read_system_readiness(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_routes,
        "get_system_readiness",
        lambda: {
            "overall_status": "warning",
            "pilot_ready": True,
            "scale_ready": False,
            "checks": [
                {
                    "code": "postgres_target",
                    "title": "Preparação para Postgres",
                    "status": "warn",
                    "severity": "warning",
                    "detail": "POSTGRES_DATABASE_URL ainda não foi configurada.",
                    "recommendation": "Configurar o alvo Postgres.",
                }
            ],
            "blocking_items": [],
            "warning_items": [{"code": "postgres_target"}],
        },
    )

    response = client.get("/admin/system/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["overall_status"] == "warning"
    assert payload["pilot_ready"] is True
    assert payload["scale_ready"] is False
    assert payload["checks"][0]["code"] == "postgres_target"


def test_read_system_onboarding(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_routes,
        "get_onboarding_status",
        lambda company_id=None, company_slug=None: {
            "scope": {
                "company_id": company_id,
                "company_slug": company_slug,
                "company_name": "Empresa Z",
                "mode": "company",
            },
            "overall_status": "ready",
            "checklist": [
                {
                    "code": "company_context",
                    "title": "Contexto operacional",
                    "status": "pass",
                    "severity": "blocker",
                    "detail": "Contexto válido carregado.",
                    "recommendation": None,
                }
            ],
            "operational_snapshot": {"counts": {"messages": 4}},
            "finetuning_readiness": {"eligible_examples_count": 12},
            "context_status": {"is_valid": True},
            "next_actions": [],
        },
    )

    response = client.get("/admin/system/onboarding", params={"company_slug": "empresa-z"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"]["company_slug"] == "empresa-z"
    assert payload["overall_status"] == "ready"
    assert payload["checklist"][0]["code"] == "company_context"


def test_read_calendar_providers(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_routes,
        "list_supported_calendar_providers",
        lambda: [
            {"provider": "mock", "label": "Mock"},
            {"provider": "google_calendar", "label": "Google Calendar"},
        ],
    )

    response = client.get("/admin/calendar/providers")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["provider"] == "mock"
    assert payload[1]["provider"] == "google_calendar"


def test_query_calendar_availability_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_routes,
        "query_calendar_availability",
        lambda **kwargs: {
            "provider": "mock",
            "slots": [
                {
                    "starts_at": "2026-04-07T09:00:00-03:00",
                    "ends_at": "2026-04-07T10:00:00-03:00",
                    "provider": "mock",
                    "timezone": "America/Sao_Paulo",
                    "duration_minutes": 60,
                }
            ],
        },
    )

    response = client.post(
        "/admin/calendar/availability/query",
        json={
            "start_date": "2026-04-07",
            "end_date": "2026-04-08",
            "duration_minutes": 60,
            "limit": 5,
        },
    )

    assert response.status_code == 200
    assert response.json()["slots"][0]["provider"] == "mock"


def test_create_calendar_booking_endpoint(monkeypatch) -> None:
    class FakeRepository:
        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    monkeypatch.setattr(
        admin_routes,
        "create_calendar_booking",
        lambda **kwargs: {
            "id": 22,
            "provider": "mock",
            "contact_id": "contato-1",
            "starts_at": "2026-04-08T13:00:00+00:00",
            "ends_at": "2026-04-08T14:00:00+00:00",
        },
    )

    response = client.post(
        "/admin/calendar/bookings",
        json={
            "contact_id": "contato-1",
            "title": "Atendimento comercial",
            "starts_at": "2026-04-08T10:00:00-03:00",
            "ends_at": "2026-04-08T11:00:00-03:00",
            "timezone": "America/Sao_Paulo",
            "description": "Detalhes",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == 22
    assert response.json()["provider"] == "mock"


def test_read_customer_reminders(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_routes,
        "list_reminders",
        lambda company_id=None, status=None, audience=None: [
            {
                "id": 1,
                "contact_id": "5511999999999",
                "reminder_text": "Enviar proposta",
                "status": "pending",
            }
        ],
    )

    response = client.get("/admin/reminders")

    assert response.status_code == 200
    assert response.json()[0]["reminder_text"] == "Enviar proposta"


def test_create_internal_reminder_endpoint(monkeypatch) -> None:
    class FakeRepository:
        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    monkeypatch.setattr(
        admin_routes,
        "create_internal_reminder",
        lambda **kwargs: {
            "id": 4,
            "audience": "internal",
            "contact_id": kwargs["owner_contact_id"],
        },
    )

    response = client.post(
        "/admin/reminders",
        json={
            "owner_contact_id": "funcionario-1",
            "message_text": "Me lembra amanhã às 9h de ligar para o cliente João",
            "timezone": "America/Sao_Paulo",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == 4
    assert response.json()["audience"] == "internal"


def test_dispatch_due_customer_reminders(monkeypatch) -> None:
    class FakeRepository:
        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    monkeypatch.setattr(
        admin_routes,
        "dispatch_due_reminders",
        lambda **kwargs: [
            {
                "reminder_id": 3,
                "outbound_message_id": 9,
                "status": "queued",
            }
        ],
    )

    response = client.post("/admin/reminders/dispatch-due")

    assert response.status_code == 200
    assert response.json()[0]["reminder_id"] == 3


def test_admin_auth_blocks_when_enabled_and_token_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", "super-secret")

    response = client.get("/admin/observability/summary")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid admin credentials."


def test_admin_auth_accepts_bearer_token(monkeypatch) -> None:
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

    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", "super-secret")
    monkeypatch.setattr(admin_routes, "repository", FakeRepository())

    response = client.get(
        "/admin/observability/summary",
        headers={
            "Authorization": "Bearer super-secret",
            "X-Admin-Actor": "qa-user",
        },
    )

    assert response.status_code == 200
    assert response.json()["totals"]["messages"] == 1


def test_viewer_role_cannot_create_company(monkeypatch) -> None:
    class FakeRepository:
        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", "viewer-token")
    monkeypatch.setattr(settings, "admin_operator_token", None)
    monkeypatch.setattr(settings, "admin_superadmin_token", None)
    monkeypatch.setattr(admin_routes, "repository", FakeRepository())

    response = client.post(
        "/admin/companies",
        headers={"Authorization": "Bearer viewer-token"},
        json={
            "slug": "empresa-x",
            "name": "Empresa X",
            "business_type": "general",
            "active": True,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient admin privileges."


def test_superadmin_role_can_create_company(monkeypatch) -> None:
    class FakeRepository:
        def get_company_by_slug(self, slug: str):
            return None

        def create_company(self, **kwargs) -> int:
            return 99

        def get_company(self, company_id: int) -> dict:
            return {
                "id": company_id,
                "slug": "empresa-y",
                "name": "Empresa Y",
                "business_type": "general",
                "active": 1,
                "created_at": "2026-04-06 12:00:00",
                "updated_at": None,
            }

        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", None)
    monkeypatch.setattr(settings, "admin_operator_token", None)
    monkeypatch.setattr(settings, "admin_superadmin_token", "superadmin-token")
    monkeypatch.setattr(admin_routes, "repository", FakeRepository())

    response = client.post(
        "/admin/companies",
        headers={
            "Authorization": "Bearer superadmin-token",
            "X-Admin-Actor": "ops-lead",
        },
        json={
            "slug": "empresa-y",
            "name": "Empresa Y",
            "business_type": "general",
            "active": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == 99
    assert response.json()["slug"] == "empresa-y"


def test_cleanup_audit_events_requires_superadmin_and_returns_deleted_count(monkeypatch) -> None:
    class FakeRepository:
        def purge_audit_events_older_than(self, older_than_days: int) -> int:
            assert older_than_days == 45
            return 7

        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "admin_viewer_token", None)
    monkeypatch.setattr(settings, "admin_operator_token", None)
    monkeypatch.setattr(settings, "admin_superadmin_token", "superadmin-token")
    monkeypatch.setattr(settings, "audit_retention_days", 90)
    monkeypatch.setattr(admin_routes, "repository", FakeRepository())

    response = client.post(
        "/admin/audit-events/cleanup",
        params={"older_than_days": 45},
        headers={"Authorization": "Bearer superadmin-token"},
    )

    assert response.status_code == 200
    assert response.json()["retention_days"] == 45
    assert response.json()["deleted_count"] == 7
