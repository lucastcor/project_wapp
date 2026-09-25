"""Readiness must fail closed on unsafe ingress, session and context configuration."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import runtime_guard_service, system_readiness_service

client = TestClient(app)


class _HygieneRepository:
    """Readiness stub: healthy hygiene plus one active WhatsApp channel binding."""

    whatsapp_bindings: list[dict] = [
        {"id": 1, "company_id": 1, "phone_number_id": "123456789", "active": 1},
    ]

    def get_whatsapp_bindings(self, *, company_id: int | None = None, active_only: bool = False) -> list[dict]:
        return list(self.whatsapp_bindings)

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


@pytest.fixture
def healthy_production(monkeypatch):
    """A production runtime where every pre-existing check passes."""
    monkeypatch.setattr(system_readiness_service, "repository", _HygieneRepository())
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "strict_runtime_validation", False)
    monkeypatch.setattr(settings, "enable_rate_limiting", True)
    monkeypatch.setattr(settings, "enable_admin_auth", True)
    monkeypatch.setattr(settings, "admin_viewer_token", "viewer-real-token")
    monkeypatch.setattr(settings, "admin_operator_token", "operator-real-token")
    monkeypatch.setattr(settings, "admin_superadmin_token", "superadmin-real-token")
    monkeypatch.setattr(settings, "admin_api_token", None)
    monkeypatch.setattr(settings, "platform_session_secret", "real-session-secret")
    monkeypatch.setattr(settings, "internal_ingress_secret", "real-internal-ingress-secret")
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", False)
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
    monkeypatch.setattr(settings, "calendar_provider", "mock")
    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql://project_wapp:strong-postgres-password@127.0.0.1:5432/project_wapp",
    )
    monkeypatch.setattr(
        settings,
        "postgres_database_url",
        "postgresql://project_wapp:strong-postgres-password@127.0.0.1:5432/project_wapp",
    )
    monkeypatch.setattr(
        system_readiness_service, "check_runtime_database_connection",
        lambda: {"ok": True, "detail": "runtime ok"},
    )
    monkeypatch.setattr(
        system_readiness_service, "get_runtime_database_summary",
        lambda: {"backend": "postgres", "support_level": "active"},
    )
    monkeypatch.setattr(system_readiness_service, "get_database_backend", lambda: "postgres")
    monkeypatch.setattr(
        system_readiness_service, "check_postgres_target_connection",
        lambda: {"ok": True, "detail": "postgres ok"},
    )
    monkeypatch.setattr(
        system_readiness_service, "get_postgres_target_summary",
        lambda: {"configured": True, "backend": "postgres", "support_level": "active"},
    )
    monkeypatch.setattr(
        system_readiness_service, "get_company_context_status",
        lambda company_slug=None: {
            "is_valid": True,
            "error": None,
            "source": "database",
            "resolved_path": None,
        },
    )
    monkeypatch.setattr(
        system_readiness_service, "get_calendar_profile",
        lambda: {"provider_readiness": {"ready": True, "details": "calendar ok"}},
    )
    # A URL de banco acima é fictícia: sondas que consultam o banco são stubadas.
    monkeypatch.setattr(
        system_readiness_service, "_legacy_context_drift_status",
        lambda: {"ok": True, "detail": "legacy ok"},
    )
    return monkeypatch


def _checks() -> dict[str, dict]:
    return {check["code"]: check for check in system_readiness_service.get_system_readiness()["checks"]}


def test_baseline_production_configuration_is_ready(healthy_production) -> None:
    checks = _checks()

    assert checks["normalized_ingress_security"]["status"] == "pass"
    assert checks["internal_ingress_secret"]["status"] == "pass"
    assert checks["platform_session_secret"]["status"] == "pass"
    assert checks["company_context_persistence"]["status"] == "pass"
    assert runtime_guard_service.get_runtime_blockers() == []


def test_normalized_ingress_without_secret_is_a_blocker(healthy_production, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", None)

    checks = _checks()

    assert checks["normalized_ingress_security"]["status"] == "fail"
    assert checks["normalized_ingress_security"]["severity"] == "blocker"
    assert "normalized_ingress_security" in {b["code"] for b in runtime_guard_service.get_runtime_blockers()}


def test_normalized_ingress_with_placeholder_secret_is_a_blocker(healthy_production, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", "change-me-internal-ingress-secret")

    assert _checks()["normalized_ingress_security"]["status"] == "fail"


def test_normalized_ingress_with_real_secret_passes(healthy_production, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", "real-internal-ingress-secret")

    assert _checks()["normalized_ingress_security"]["status"] == "pass"


def test_missing_internal_ingress_secret_is_a_production_blocker(healthy_production, monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_ingress_secret", None)

    checks = _checks()

    assert checks["internal_ingress_secret"]["status"] == "fail"
    assert checks["internal_ingress_secret"]["severity"] == "blocker"


def test_missing_platform_session_secret_is_a_production_blocker(healthy_production, monkeypatch) -> None:
    monkeypatch.setattr(settings, "platform_session_secret", None)

    checks = _checks()

    assert checks["platform_session_secret"]["status"] == "fail"
    assert checks["platform_session_secret"]["severity"] == "blocker"


def test_billing_enforcement_requires_complete_stripe_configuration(
    healthy_production,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "enable_billing_enforcement", True)
    monkeypatch.setattr(settings, "stripe_secret_key", None)

    checks = _checks()

    assert checks["stripe_billing"]["status"] == "fail"
    assert checks["stripe_billing"]["severity"] == "blocker"


def test_complete_stripe_configuration_passes_enforcement_readiness(
    healthy_production,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "enable_billing_enforcement", True)
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_live_real")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_real")
    monkeypatch.setattr(settings, "stripe_success_url", "https://app.acme.test/success")
    monkeypatch.setattr(settings, "stripe_cancel_url", "https://app.acme.test/cancel")
    monkeypatch.setattr(
        settings,
        "stripe_portal_return_url",
        "https://app.acme.test/billing",
    )
    for attribute in (
        "stripe_price_essencial_monthly",
        "stripe_price_essencial_annual",
        "stripe_price_avancado_monthly",
        "stripe_price_avancado_annual",
        "stripe_price_premium_monthly",
        "stripe_price_premium_annual",
    ):
        monkeypatch.setattr(settings, attribute, f"price_{attribute}")

    assert _checks()["stripe_billing"]["status"] == "pass"


def test_self_service_signup_requires_transactional_email(
    healthy_production,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "enable_self_service_signup", True)
    monkeypatch.setattr(settings, "transactional_email_provider", "disabled")

    checks = _checks()

    assert checks["transactional_email"]["status"] == "fail"
    assert checks["transactional_email"]["severity"] == "blocker"


def test_placeholder_platform_session_secret_is_a_production_blocker(healthy_production, monkeypatch) -> None:
    monkeypatch.setattr(settings, "platform_session_secret", "change-me-platform-session-secret")

    assert _checks()["platform_session_secret"]["status"] == "fail"


def test_file_backed_company_context_is_a_production_blocker(healthy_production, monkeypatch) -> None:
    monkeypatch.setattr(
        system_readiness_service, "get_company_context_status",
        lambda company_slug=None: {
            "is_valid": True,
            "error": None,
            "source": "seed_file",
            "resolved_path": "app/data/company_context.json",
        },
    )

    checks = _checks()

    assert checks["company_context_persistence"]["status"] == "fail"
    assert checks["company_context_persistence"]["severity"] == "blocker"
    assert "bootstrap_company_contexts" in str(checks["company_context_persistence"]["recommendation"])


def test_worker_startup_fails_closed_on_unsafe_ingress(healthy_production, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", None)

    with pytest.raises(RuntimeError) as exc_info:
        runtime_guard_service.validate_runtime_environment("worker")

    assert "normalized_ingress_security" in str(exc_info.value)


def test_api_startup_fails_closed_on_unsafe_ingress(healthy_production, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", None)

    with pytest.raises(RuntimeError) as exc_info:
        runtime_guard_service.validate_runtime_environment("api")

    assert "Runtime validation failed for api" in str(exc_info.value)


def test_readiness_endpoint_reports_unsafe_ingress_as_a_blocker(healthy_production, monkeypatch) -> None:
    import app.main as main_module

    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", True)
    monkeypatch.setattr(settings, "internal_ingress_secret", None)
    monkeypatch.setattr(
        main_module, "check_runtime_database_connection",
        lambda: {"ok": True, "detail": "probe ok"},
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert "normalized_ingress_security" in response.json()["blockers"]


def test_development_defaults_stay_convenient(monkeypatch) -> None:
    """Outside production the new secrets are warnings, not blockers."""
    monkeypatch.setattr(system_readiness_service, "repository", _HygieneRepository())
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "strict_runtime_validation", False)
    monkeypatch.setattr(settings, "internal_ingress_secret", None)
    monkeypatch.setattr(settings, "enable_normalized_whatsapp_ingress", False)
    monkeypatch.setattr(
        system_readiness_service, "get_company_context_status",
        lambda company_slug=None: {
            "is_valid": True,
            "error": None,
            "source": "seed_file",
            "resolved_path": "app/data/company_context.json",
        },
    )

    checks = _checks()

    assert checks["internal_ingress_secret"]["status"] == "warn"
    assert checks["internal_ingress_secret"]["severity"] == "warning"
    assert checks["company_context_persistence"]["status"] == "warn"
    assert checks["normalized_ingress_security"]["status"] == "pass"


def test_tests_do_not_inherit_the_local_dotenv() -> None:
    import os

    assert os.environ["DISABLE_DOTENV"] == "true"
    assert settings.environment == "test"
    assert os.environ.get("ADMIN_API_TOKEN") is None
    assert os.environ.get("INTERNAL_INGRESS_SECRET") is None


@pytest.fixture(autouse=True)
def isolate_new_worker_probes(monkeypatch):
    # These readiness unit tests replace DB connectivity with fake URLs.
    # Worker probe behavior is exercised against a real DB in its own tests.
    monkeypatch.setattr(settings, "inbound_worker_enabled", True)
    monkeypatch.setattr(settings, "pii_pseudonymization_key", "test-ready-pii-secret")
    monkeypatch.setattr("app.services.local_runtime_readiness.worker_is_alive", lambda _: True)
    monkeypatch.setattr("app.services.local_runtime_readiness.review_count", lambda: 0)
