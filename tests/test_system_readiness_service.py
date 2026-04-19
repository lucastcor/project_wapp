from app.services import system_readiness_service
from app.core.config import settings


def test_get_system_profile_exposes_runtime_and_target_database(monkeypatch) -> None:
    class FakeRepository:
        def get_operational_hygiene_summary(self, stale_after_days: int = 30) -> dict:
            assert stale_after_days == 30
            return {
                "stale_after_days": stale_after_days,
                "counts": {
                    "active_placeholder_companies": 0,
                    "stale_pending_questions": 0,
                    "stale_outbound_messages": 0,
                    "stale_failed_finetuning_jobs": 0,
                },
                "active_placeholder_companies": [],
                "stale_pending_questions": [],
                "stale_outbound_messages": [],
                "stale_failed_finetuning_jobs": [],
            }

    monkeypatch.setattr(system_readiness_service, "repository", FakeRepository())
    monkeypatch.setattr(settings, "app_name", "Project WAPP")
    monkeypatch.setattr(settings, "app_version", "1.2.3")
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "enable_rate_limiting", True)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "viewer-token")
    monkeypatch.setattr(settings, "admin_operator_token", "operator-token")
    monkeypatch.setattr(settings, "admin_superadmin_token", "superadmin-token")
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "audit_retention_days", 30)
    monkeypatch.setattr(settings, "reminder_worker_enabled", True)
    monkeypatch.setattr(settings, "reminder_worker_poll_seconds", 45)
    monkeypatch.setattr(settings, "reminder_worker_dispatch_immediately", True)
    monkeypatch.setattr(settings, "reminder_worker_audiences", "customer,internal")
    monkeypatch.setattr(settings, "reminder_worker_channel", None)
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "classification_provider", "mock")
    monkeypatch.setattr(settings, "outbound_provider", "mock")
    monkeypatch.setattr(
        system_readiness_service,
        "get_runtime_database_summary",
        lambda: {"backend": "sqlite", "support_level": "active"},
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_postgres_target_summary",
        lambda: {"configured": True, "backend": "postgres", "support_level": "target_only"},
    )

    profile = system_readiness_service.get_system_profile()

    assert profile["app"]["version"] == "1.2.3"
    assert profile["database"]["runtime"]["backend"] == "sqlite"
    assert profile["database"]["postgres_target"]["configured"] is True
    assert profile["security"]["configured_roles"] == ["viewer", "operator", "superadmin"]
    assert profile["security"]["rate_limiting_enabled"] is True
    assert profile["automation"]["reminder_worker_enabled"] is True
    assert profile["automation"]["reminder_worker_poll_seconds"] == 45
    assert profile["operations"]["data_hygiene"]["counts"]["stale_pending_questions"] == 0


def test_get_onboarding_status_for_company(monkeypatch) -> None:
    class FakeRepository:
        def get_company(self, company_id: int):
            return {
                "id": company_id,
                "slug": "empresa-a",
                "name": "Empresa A",
                "active": 1,
            }

        def get_company_by_slug(self, slug: str):
            return None

        def get_operational_snapshot(self, company_id: int | None = None) -> dict:
            assert company_id == 5
            return {
                "company_id": 5,
                "counts": {
                    "contacts": 1,
                    "messages": 8,
                    "pending_questions": 0,
                    "knowledge_entries": 2,
                    "message_examples": 4,
                    "outbound_messages": 1,
                    "finetuning_jobs": 0,
                },
                "pending_questions_by_status": {},
                "outbound_by_status": {"pending": 1},
                "knowledge_by_status": {"active": 2},
            }

        def get_companies(self) -> list[dict]:
            return []

    monkeypatch.setattr(system_readiness_service, "repository", FakeRepository())
    monkeypatch.setattr(
        system_readiness_service,
        "get_company_context_status",
        lambda company_slug=None: {
            "company_slug": company_slug,
            "resolved_path": "app/data/company_contexts/empresa-a.json",
            "is_valid": True,
            "error": None,
        },
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_finetuning_readiness_report",
        lambda company_id=None: {
            "is_ready_for_finetuning": False,
            "eligible_examples_count": 4,
            "minimum_required_examples": 10,
            "missing_examples_count": 6,
            "underrepresented_scenarios": {"lead_novo": 1},
        },
    )

    onboarding = system_readiness_service.get_onboarding_status(company_id=5)

    assert onboarding["scope"]["company_id"] == 5
    assert onboarding["scope"]["company_slug"] == "empresa-a"
    assert onboarding["overall_status"] == "warning"
    assert onboarding["operational_snapshot"]["counts"]["messages"] == 8
    assert onboarding["finetuning_readiness"]["missing_examples_count"] == 6


def test_get_system_readiness_blocks_placeholder_secrets_in_production(monkeypatch) -> None:
    class FakeRepository:
        def get_operational_hygiene_summary(self, stale_after_days: int = 30) -> dict:
            return {
                "stale_after_days": stale_after_days,
                "counts": {
                    "active_placeholder_companies": 1,
                    "stale_pending_questions": 2,
                    "stale_outbound_messages": 1,
                    "stale_failed_finetuning_jobs": 3,
                },
                "active_placeholder_companies": [{"id": 1}],
                "stale_pending_questions": [{"id": 11}, {"id": 12}],
                "stale_outbound_messages": [{"id": 21}],
                "stale_failed_finetuning_jobs": [{"id": 31}, {"id": 32}, {"id": 33}],
            }

    monkeypatch.setattr(system_readiness_service, "repository", FakeRepository())
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "strict_runtime_validation", False)
    monkeypatch.setattr(settings, "enable_rate_limiting", False)
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "change-me-viewer-token")
    monkeypatch.setattr(settings, "admin_operator_token", "change-me-operator-token")
    monkeypatch.setattr(settings, "admin_superadmin_token", "change-me-superadmin-token")
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "platform_session_secret", "change-me-session-secret")
    monkeypatch.setattr(settings, "openai_api_key", "change-me-openai-key")
    monkeypatch.setattr(settings, "classification_provider", "openai")
    monkeypatch.setattr(settings, "outbound_provider", "whatsapp")
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.facebook.com")
    monkeypatch.setattr(settings, "whatsapp_api_token", "change-me-whatsapp-token")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "change-me-phone-id")
    monkeypatch.setattr(settings, "whatsapp_webhook_verify_token", "change-me-verify-token")
    monkeypatch.setattr(settings, "whatsapp_app_secret", "change-me-app-secret")
    monkeypatch.setattr(settings, "reminder_worker_enabled", True)
    monkeypatch.setattr(settings, "audit_retention_days", 30)
    monkeypatch.setattr(settings, "log_json", True)
    monkeypatch.setattr(
        system_readiness_service,
        "check_runtime_database_connection",
        lambda: {"ok": True, "detail": "runtime ok"},
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_runtime_database_summary",
        lambda: {"backend": "postgres", "support_level": "active"},
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_database_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(
        system_readiness_service,
        "check_postgres_target_connection",
        lambda: {"ok": True, "detail": "postgres ok"},
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_postgres_target_summary",
        lambda: {"configured": True, "backend": "postgres", "support_level": "active"},
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_company_context_status",
        lambda company_slug=None: {
            "is_valid": True,
            "error": None,
            "resolved_path": "app/data/company_contexts/global.json",
        },
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_calendar_profile",
        lambda: {
            "provider_readiness": {
                "ready": True,
                "details": "calendar ok",
            }
        },
    )

    readiness = system_readiness_service.get_system_readiness()
    checks_by_code = {check["code"]: check for check in readiness["checks"]}

    assert readiness["overall_status"] == "blocked"
    assert checks_by_code["platform_session_secret"]["status"] == "fail"
    assert checks_by_code["admin_token_quality"]["status"] == "fail"
    assert checks_by_code["rate_limiting"]["status"] == "fail"
    assert checks_by_code["openai_configuration"]["status"] == "fail"
    assert checks_by_code["whatsapp_provider"]["status"] == "fail"
    assert checks_by_code["whatsapp_webhook_verification"]["status"] == "fail"
    assert checks_by_code["whatsapp_signature_validation"]["status"] == "fail"
    assert checks_by_code["operational_hygiene"]["status"] == "warn"


def test_get_system_readiness_blocks_placeholder_postgres_and_calendar_in_production(monkeypatch) -> None:
    class FakeRepository:
        def get_operational_hygiene_summary(self, stale_after_days: int = 30) -> dict:
            return {
                "stale_after_days": stale_after_days,
                "counts": {
                    "active_placeholder_companies": 0,
                    "stale_pending_questions": 0,
                    "stale_outbound_messages": 0,
                    "stale_failed_finetuning_jobs": 0,
                },
                "active_placeholder_companies": [],
                "stale_pending_questions": [],
                "stale_outbound_messages": [],
                "stale_failed_finetuning_jobs": [],
            }

    monkeypatch.setattr(system_readiness_service, "repository", FakeRepository())
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "strict_runtime_validation", False)
    monkeypatch.setattr(settings, "enable_rate_limiting", True)
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "viewer-real-token")
    monkeypatch.setattr(settings, "admin_operator_token", "operator-real-token")
    monkeypatch.setattr(settings, "admin_superadmin_token", "superadmin-real-token")
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "platform_session_secret", "real-session-secret")
    monkeypatch.setattr(settings, "openai_api_key", "sk-real-token")
    monkeypatch.setattr(settings, "classification_provider", "openai")
    monkeypatch.setattr(settings, "outbound_provider", "whatsapp")
    monkeypatch.setattr(settings, "whatsapp_api_base_url", "https://graph.facebook.com")
    monkeypatch.setattr(settings, "whatsapp_api_token", "real-whatsapp-token")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "123456789")
    monkeypatch.setattr(settings, "whatsapp_webhook_verify_token", "real-verify-token")
    monkeypatch.setattr(settings, "whatsapp_app_secret", "real-app-secret")
    monkeypatch.setattr(settings, "reminder_worker_enabled", True)
    monkeypatch.setattr(settings, "audit_retention_days", 30)
    monkeypatch.setattr(settings, "log_json", True)
    monkeypatch.setattr(settings, "database_url", "postgresql://project_wapp:change-me-postgres-password@127.0.0.1:5432/project_wapp")
    monkeypatch.setattr(settings, "postgres_database_url", "postgresql://project_wapp:change-me-postgres-password@127.0.0.1:5432/project_wapp")
    monkeypatch.setattr(settings, "calendar_provider", "google_calendar")
    monkeypatch.setattr(
        system_readiness_service,
        "check_runtime_database_connection",
        lambda: {"ok": True, "detail": "runtime ok"},
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_runtime_database_summary",
        lambda: {"backend": "postgres", "support_level": "active"},
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_database_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(
        system_readiness_service,
        "check_postgres_target_connection",
        lambda: {"ok": True, "detail": "postgres ok"},
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_postgres_target_summary",
        lambda: {"configured": True, "backend": "postgres", "support_level": "active"},
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_company_context_status",
        lambda company_slug=None: {
            "is_valid": True,
            "error": None,
            "resolved_path": "app/data/company_contexts/global.json",
        },
    )
    monkeypatch.setattr(
        system_readiness_service,
        "get_calendar_profile",
        lambda: {
            "provider_readiness": {
                "ready": False,
                "details": "Faltam credenciais reais do Google Calendar.",
            }
        },
    )

    readiness = system_readiness_service.get_system_readiness()
    checks_by_code = {check["code"]: check for check in readiness["checks"]}

    assert readiness["overall_status"] == "blocked"
    assert checks_by_code["postgres_credentials"]["status"] == "fail"
    assert checks_by_code["calendar_provider"]["status"] == "fail"
