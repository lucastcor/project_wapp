from fastapi.testclient import TestClient

from app.api import admin_routes
from app.core.config import settings
from app.main import app

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
        def get_audit_events(
            self,
            limit: int,
            action: str | None,
            resource_type: str | None,
            *,
            company_id: int | None = None,
        ) -> list[dict]:
            assert limit == 50
            assert action == "create"
            assert resource_type == "company"
            assert company_id is None
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


def test_create_finetuning_job_requires_resolved_company(monkeypatch) -> None:
    class FakeRepository:
        def resolve_company_id(self, company_id=None, company_slug=None):
            return None

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())

    response = client.post("/admin/finetuning/jobs")

    assert response.status_code == 400
    assert "company_id or company_slug" in response.json()["detail"]


def test_create_finetuning_job_passes_resolved_company_scope(monkeypatch) -> None:
    captured_audit_events: list[dict] = []

    class FakeRepository:
        def resolve_company_id(self, company_id=None, company_slug=None):
            assert company_slug == "empresa-a"
            return 5

        def create_audit_event(self, **kwargs) -> int:
            captured_audit_events.append(kwargs)
            return 1

    class FakeFineTuningService:
        def create_finetuning_job(self, *, company_id: int | None):
            assert company_id == 5
            return {
                "job_id": "ftjob-empresa-a",
                "status": "validating_files",
                "base_model": "gpt-4o-mini-2024-07-18",
            }

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    monkeypatch.setattr(admin_routes, "_get_finetuning_service", FakeFineTuningService)

    response = client.post("/admin/finetuning/jobs", params={"company_slug": "empresa-a"})

    assert response.status_code == 200
    assert response.json()["job_id"] == "ftjob-empresa-a"
    assert captured_audit_events[0]["admin_role"] == "superadmin"
    assert captured_audit_events[0]["auth_mode"] == "disabled"
    assert captured_audit_events[0]["http_method"] == "POST"
    assert captured_audit_events[0]["http_path"] == "/admin/finetuning/jobs"
    assert captured_audit_events[0]["client_ip"] == "testclient"


def test_list_finetuning_jobs_uses_company_scope(monkeypatch) -> None:
    class FakeRepository:
        def resolve_company_id(self, company_id=None, company_slug=None):
            assert company_id == 8
            return 8

    class FakeFineTuningService:
        def list_finetuning_jobs(self, limit: int = 10, *, company_id: int | None = None):
            assert limit == 20
            assert company_id == 8
            return [
                {
                    "job_id": "ftjob-empresa-8",
                    "status": "succeeded",
                    "base_model": "gpt-4o-mini-2024-07-18",
                    "fine_tuned_model": "ft:gpt-4o-mini:empresa-8",
                    "created_at": 1773709705,
                    "finished_at": 1773709805,
                }
            ]

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    monkeypatch.setattr(admin_routes, "_get_finetuning_service", FakeFineTuningService)

    response = client.get("/admin/finetuning/jobs", params={"company_id": 8, "limit": 20})

    assert response.status_code == 200
    assert response.json()[0]["job_id"] == "ftjob-empresa-8"


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


def test_read_calendar_timeline(monkeypatch) -> None:
    monkeypatch.setattr(
        admin_routes,
        "get_calendar_timeline",
        lambda company_id=None, start_date=None, days=7: {
            "timezone": "America/Sao_Paulo",
            "start_date": start_date or "2026-04-07",
            "days": [
                {
                    "date": "2026-04-07",
                    "label": "07/04",
                    "weekday_label": "segunda",
                    "booking_count": 1,
                    "segments": [
                        {
                            "booking_id": 22,
                            "title": "Consultoria inicial",
                            "start_minutes": 600,
                            "end_minutes": 660,
                            "duration_minutes": 60,
                            "column_index": 0,
                            "column_count": 1,
                        }
                    ],
                }
            ],
            "summary": {"booking_count": 1, "confirmed_count": 1, "pending_count": 0},
        },
    )

    response = client.get("/admin/calendar/timeline", params={"company_id": 1, "start_date": "2026-04-07", "days": 7})

    assert response.status_code == 200
    payload = response.json()
    assert payload["timezone"] == "America/Sao_Paulo"
    assert payload["days"][0]["segments"][0]["booking_id"] == 22


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


def test_create_calendar_booking_endpoint_requests_bot_confirmation(monkeypatch) -> None:
    outbound_calls: list[dict] = []
    dispatch_calls: list[int] = []

    class FakeRepository:
        def create_audit_event(self, **kwargs) -> int:
            return 1

        def create_outbound_message(self, **kwargs) -> int:
            outbound_calls.append(kwargs)
            return 777

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    monkeypatch.setattr(
        admin_routes,
        "create_calendar_booking",
        lambda **kwargs: {
            "id": 55,
            "provider": "mock",
            "company_id": 1,
            "contact_id": "contato-55",
            "starts_at": "2026-04-20T14:00:00+00:00",
            "ends_at": "2026-04-20T15:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        admin_routes,
        "dispatch_outbound_message",
        lambda outbound_message_id, channel=None, company_id=None: dispatch_calls.append(
            {
                "outbound_message_id": outbound_message_id,
                "channel": channel,
                "company_id": company_id,
            }
        ) or {"status": "sent"},
    )

    response = client.post(
        "/admin/calendar/bookings",
        json={
            "contact_id": "contato-55",
            "title": "Sessão premium",
            "starts_at": "2026-04-20T11:00:00-03:00",
            "ends_at": "2026-04-20T12:00:00-03:00",
            "timezone": "America/Sao_Paulo",
            "description": "Manual com confirmação",
            "request_bot_confirmation": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 55
    assert body["confirmation_outbound_message_id"] == 777
    assert len(outbound_calls) == 1
    assert outbound_calls[0]["contact_id"] == "contato-55"
    assert outbound_calls[0]["source"] == "manual_booking_confirmation"
    assert "Sessão premium" in outbound_calls[0]["message"]
    assert dispatch_calls == [
        {
            "outbound_message_id": 777,
            "channel": None,
            "company_id": 1,
        }
    ]


def test_create_appointment_queue_entry_endpoint(monkeypatch) -> None:
    class FakeRepository:
        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    monkeypatch.setattr(
        admin_routes,
        "create_queue_entry",
        lambda **kwargs: {
            "id": 30,
            "company_id": kwargs["company_id"],
            "contact_id": kwargs["contact_id"],
            "desired_title": kwargs["desired_title"],
            "priority_tier": kwargs["priority_tier"],
            "priority_score": kwargs["priority_score"],
            "status": "active",
            "original_booking_id": kwargs["original_booking_id"],
        },
    )

    response = client.post(
        "/admin/calendar/queue",
        json={
            "contact_id": "5511999999999",
            "desired_title": "Consulta inicial",
            "priority_tier": "high",
            "priority_score": 25,
            "original_booking_id": None,
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == 30
    assert response.json()["priority_tier"] == "high"


def test_dispatch_single_outbound_message_endpoint_passes_company_scope(monkeypatch) -> None:
    captured_dispatch_calls: list[dict] = []

    class FakeRepository:
        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    monkeypatch.setattr(
        admin_routes,
        "dispatch_outbound_message",
        lambda outbound_message_id, channel=None, company_id=None: captured_dispatch_calls.append(
            {
                "outbound_message_id": outbound_message_id,
                "channel": channel,
                "company_id": company_id,
            }
        ) or {
            "outbound_message_id": outbound_message_id,
            "delivery_status": "sent",
        },
    )

    response = client.post(
        "/admin/outbound-messages/99/dispatch",
        params={"company_id": 12, "channel": "whatsapp"},
    )

    assert response.status_code == 200
    assert captured_dispatch_calls == [
        {
            "outbound_message_id": 99,
            "channel": "whatsapp",
            "company_id": 12,
        }
    ]


def test_cancel_calendar_booking_endpoint_triggers_queue_recovery(monkeypatch) -> None:
    class FakeRepository:
        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    # A API age como operador (A08): o kwarg `actor` é repassado ao serviço.
    monkeypatch.setattr(
        admin_routes,
        "release_booking_slot",
        lambda booking_id, actor="customer": {
            "booking": {
                "id": booking_id,
                "company_id": 1,
                "status": "cancelled",
            },
            "cancelled_reminders": 2,
            "queue_recovery": {
                "released_booking_id": booking_id,
                "candidate_count": 1,
                "created_offer": {"id": 77, "queue_entry_id": 12},
            },
        },
    )

    response = client.post(
        "/admin/calendar/bookings/22/cancel",
        json={
            "trigger_queue_recovery": True,
            "reason": "Cliente cancelou",
        },
    )

    assert response.status_code == 200
    assert response.json()["booking"]["status"] == "cancelled"
    assert response.json()["queue_recovery"]["created_offer"]["id"] == 77


def test_reschedule_calendar_booking_endpoint(monkeypatch) -> None:
    class FakeRepository:
        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    monkeypatch.setattr(
        admin_routes,
        "reschedule_calendar_booking",
        lambda **kwargs: {
            "id": kwargs["booking_id"],
            "company_id": 1,
            "starts_at": "2026-04-09T13:00:00+00:00",
            "ends_at": "2026-04-09T14:00:00+00:00",
            "scheduling_profile_key": "retorno",
            "customer_profile": "returning_customer",
            "external_sync": {"status": "updated"},
        },
    )

    response = client.post(
        "/admin/calendar/bookings/22/reschedule",
        json={
            "starts_at": "2026-04-09T10:00:00-03:00",
            "ends_at": "2026-04-09T11:00:00-03:00",
            "scheduling_profile_key": "retorno",
            "customer_profile": "returning_customer",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == 22
    assert response.json()["external_sync"]["status"] == "updated"


def test_respond_to_appointment_queue_offer_endpoint(monkeypatch) -> None:
    class FakeRepository:
        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    monkeypatch.setattr(
        admin_routes,
        "respond_to_queue_offer",
        lambda **kwargs: {
            "offer": {
                "id": kwargs["offer_id"],
                "company_id": 1,
                "status": kwargs["response_status"],
            },
            "entry": {
                "id": 12,
                "status": "converted",
            },
            "new_booking": {"id": 88},
            "next_offer": None,
        },
    )

    response = client.post(
        "/admin/calendar/queue-offers/9/respond",
        json={
            "response_status": "accepted",
            "response_notes": "Cliente aceitou.",
        },
    )

    assert response.status_code == 200
    assert response.json()["offer"]["status"] == "accepted"
    assert response.json()["new_booking"]["id"] == 88


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


def test_update_contact_endpoint(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.contact = {
                "contact_id": "5511999999999",
                "contact_name": "Maria",
                "company_id": 2,
                "customer_profile": None,
                "notes": None,
            }

        def get_contact(self, contact_id: str, *, company_id: int | None = None):
            if contact_id == self.contact["contact_id"]:
                if company_id is not None and self.contact.get("company_id") != company_id:
                    return None
                return dict(self.contact)
            return None

        def update_contact(self, contact_id: str, **kwargs) -> None:
            assert contact_id == self.contact["contact_id"]
            self.contact.update(kwargs)

        def create_audit_event(self, **kwargs) -> int:
            return 1

    fake_repository = FakeRepository()
    monkeypatch.setattr(admin_routes, "repository", fake_repository)

    response = client.patch(
        "/admin/contacts/5511999999999",
        params={"company_id": 2},
        json={
            "contact_name": "Maria Silva",
            "customer_profile": "returning_customer",
            "notes": "Cliente prefere atendimento a tarde.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["contact_name"] == "Maria Silva"
    assert payload["customer_profile"] == "returning_customer"
    assert payload["notes"] == "Cliente prefere atendimento a tarde."


def test_training_import_endpoint(monkeypatch) -> None:
    class FakeRepository:
        def create_audit_event(self, **kwargs) -> int:
            return 1

    monkeypatch.setattr(admin_routes, "repository", FakeRepository())
    monkeypatch.setattr(
        admin_routes,
        "import_training_records",
        lambda **kwargs: {
            "dry_run": kwargs["dry_run"],
            "company_id": kwargs["company_id"],
            "parsed_records": 1,
            "contacts_to_update": 1,
            "message_examples_to_create": 1,
            "eligible_examples": 1,
            "skipped_examples": 0,
            "errors": [],
            "preview": [
                {
                    "row_number": 1,
                    "contact_id": "5511999999999",
                    "contact_name": "Maria Silva",
                    "customer_profile": "returning_customer",
                    "profile_name": "returning_customer",
                    "scenario_type": "reschedule",
                    "eligible_for_training": True,
                    "used_for_training": True,
                }
            ],
        },
    )

    response = client.post(
        "/admin/training-import",
        params={"company_id": 3},
        json={
            "format": "csv",
            "raw_text": (
                "contact_id,contact_name,customer_profile,customer_message,approved_reply\n"
                "5511999999999,Maria Silva,returning_customer,Oi,Claro, posso ajudar\n"
            ),
            "dry_run": True,
            "update_contacts": True,
            "create_message_examples": True,
            "auto_promote_eligible_examples": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["company_id"] == 3
    assert payload["parsed_records"] == 1
    assert payload["preview"][0]["customer_profile"] == "returning_customer"
