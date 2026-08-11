"""Readiness detecta configuração insegura da plataforma multiempresa."""

from app.core.config import settings
from app.services import system_readiness_service


def _checks_by_code(monkeypatch, **overrides) -> dict[str, dict]:
    monkeypatch.setattr(
        system_readiness_service,
        "check_runtime_database_connection",
        lambda: {"ok": True, "detail": "runtime ok"},
    )
    monkeypatch.setattr(
        system_readiness_service,
        "check_postgres_target_connection",
        lambda: {"ok": True, "detail": "postgres ok"},
    )
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)

    readiness = system_readiness_service.get_system_readiness()
    return {check["code"]: check for check in readiness["checks"]}


def test_memory_shared_state_is_blocked_in_production(monkeypatch) -> None:
    checks = _checks_by_code(
        monkeypatch,
        environment="production",
        shared_state_backend="memory",
    )

    check = checks["shared_state_backend"]
    assert check["status"] == "fail"
    assert check["severity"] == "blocker"
    assert "não valem entre workers" in check["detail"]


def test_redis_shared_state_passes(monkeypatch) -> None:
    checks = _checks_by_code(
        monkeypatch,
        environment="production",
        shared_state_backend="redis",
        redis_url="redis://cache:6379/0",
    )

    assert checks["shared_state_backend"]["status"] == "pass"


def test_open_normalized_webhook_is_blocked_in_production(monkeypatch) -> None:
    checks = _checks_by_code(
        monkeypatch,
        environment="production",
        allow_normalized_whatsapp_webhook=True,
        whatsapp_app_secret=None,
    )

    check = checks["webhook_payload_hardening"]
    assert check["status"] == "fail"
    assert check["severity"] == "blocker"


def test_hardened_webhook_passes(monkeypatch) -> None:
    checks = _checks_by_code(
        monkeypatch,
        environment="production",
        allow_normalized_whatsapp_webhook=False,
        whatsapp_app_secret="segredo-real",
    )

    assert checks["webhook_payload_hardening"]["status"] == "pass"


def test_untrusted_tenant_hints_are_blocked_in_production(monkeypatch) -> None:
    checks = _checks_by_code(
        monkeypatch,
        environment="production",
        allow_untrusted_tenant_hints=True,
    )

    check = checks["tenant_resolution_hardening"]
    assert check["status"] == "fail"
    assert check["severity"] == "blocker"


def test_trusted_tenant_resolution_passes(monkeypatch) -> None:
    checks = _checks_by_code(
        monkeypatch,
        environment="production",
        allow_untrusted_tenant_hints=False,
    )

    assert checks["tenant_resolution_hardening"]["status"] == "pass"


def test_missing_channel_bindings_are_reported(monkeypatch) -> None:
    checks = _checks_by_code(monkeypatch, environment="development")

    check = checks["channel_bindings"]
    assert check["code"] == "channel_bindings"
    assert check["status"] in {"pass", "warn"}


# --- Automation Studio -----------------------------------------------------------


def _automation_check(readiness: dict, code: str) -> dict:
    return next(check for check in readiness["checks"] if check["code"] == code)


def test_a_disabled_engine_is_not_a_blocker(monkeypatch) -> None:
    """Desligado é o estado padrão e correto; não pode reprovar o readiness."""
    from app.core.config import settings
    from app.services.system_readiness_service import get_system_readiness

    monkeypatch.setattr(settings, "automation_engine_enabled", False)
    readiness = get_system_readiness()

    check = _automation_check(readiness, "automation_engine")
    assert check["status"] == "pass"
    assert "nenhum fluxo é executado" in check["detail"]
    # Com o motor desligado, as demais verificações nem aparecem.
    codes = {entry["code"] for entry in readiness["checks"]}
    assert "automation_worker" not in codes


def test_an_enabled_engine_without_a_worker_blocks(monkeypatch) -> None:
    from app.core.config import settings
    from app.services.system_readiness_service import get_system_readiness

    monkeypatch.setattr(settings, "automation_engine_enabled", True)
    monkeypatch.setattr(settings, "automation_worker_enabled", False)
    readiness = get_system_readiness()

    check = _automation_check(readiness, "automation_worker")
    assert check["status"] == "fail"
    assert check["severity"] == "blocker"
    assert "nunca processados" in check["detail"]


def test_an_enabled_engine_with_a_worker_passes(monkeypatch) -> None:
    from app.core.config import settings
    from app.services.system_readiness_service import get_system_readiness

    monkeypatch.setattr(settings, "automation_engine_enabled", True)
    monkeypatch.setattr(settings, "automation_worker_enabled", True)
    readiness = get_system_readiness()

    assert _automation_check(readiness, "automation_worker")["status"] == "pass"


def test_invalid_lease_and_retry_limits_block(monkeypatch) -> None:
    from app.core.config import settings
    from app.services.system_readiness_service import get_system_readiness

    monkeypatch.setattr(settings, "automation_engine_enabled", True)
    monkeypatch.setattr(settings, "automation_worker_enabled", True)
    # Lease curto demais reprocessaria um passo ainda em voo.
    monkeypatch.setattr(settings, "automation_job_lease_seconds", 1)
    readiness = get_system_readiness()

    check = _automation_check(readiness, "automation_limits")
    assert check["status"] == "fail"
    assert check["severity"] == "blocker"


def test_the_automation_runtime_database_is_reported_honestly(monkeypatch) -> None:
    """SQLite é sinalizado; PostgreSQL passa.

    O teste é consciente do backend porque a suíte roda nos dois: em SQLite
    (rápido, padrão) e em PostgreSQL real via `WAPP_TEST_DATABASE_URL`.
    Afirmar "SQLite" incondicionalmente faria o teste falhar exatamente no
    backend de produção.
    """
    from app.core.config import settings
    from app.services.system_readiness_service import get_system_readiness
    from app.storage.db import get_database_backend

    monkeypatch.setattr(settings, "automation_engine_enabled", True)
    monkeypatch.setattr(settings, "automation_worker_enabled", True)
    readiness = get_system_readiness()

    check = _automation_check(readiness, "automation_runtime_database")
    if get_database_backend() == "postgres":
        assert check["status"] == "pass"
        assert "SKIP LOCKED" in check["detail"]
    else:
        # Fora de produção o SQLite é aviso, não bloqueio.
        assert check["status"] in {"warn", "fail"}
        assert "SQLite" in check["detail"]


def test_an_active_deployment_without_a_version_blocks(
    monkeypatch,
    company_factory,
) -> None:
    """Cenário do readiness: nenhuma versão ativa inválida."""
    from app.core.config import settings
    from app.services.system_readiness_service import get_system_readiness
    from app.storage import automation_repository

    monkeypatch.setattr(settings, "automation_engine_enabled", True)
    monkeypatch.setattr(settings, "automation_worker_enabled", True)

    company_id = company_factory("readiness-automation")
    workflow_id = automation_repository.create_workflow(
        company_id=company_id,
        workflow_key="readiness-orfao",
        name="Órfão",
    )
    automation_repository.upsert_deployment(
        company_id=company_id,
        workflow_id=workflow_id,
        version_id=999_999,  # versão inexistente
        trigger_type="trigger.manual",
        state="active",
    )

    try:
        check = _automation_check(get_system_readiness(), "automation_active_versions")
        assert check["status"] == "fail"
        assert check["severity"] == "blocker"
    finally:
        from app.storage.sql_support import execute

        execute("DELETE FROM automation_deployments WHERE company_id = ?", (company_id,))
