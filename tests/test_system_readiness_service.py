from app.services import system_readiness_service
from app.core.config import settings


def test_get_system_profile_exposes_runtime_and_target_database(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_name", "Project WAPP")
    monkeypatch.setattr(settings, "app_version", "1.2.3")
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "debug", False)
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
    assert profile["automation"]["reminder_worker_enabled"] is True
    assert profile["automation"]["reminder_worker_poll_seconds"] == 45


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
