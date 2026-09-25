import pytest

from app.core.config import settings
from app.services import local_runtime_readiness as readiness
from app.services import runtime_guard_service
from app.services import worker_health_service as health
from app.storage.sql_support import execute_rowcount


@pytest.fixture(autouse=True)
def clean_heartbeats():
    execute_rowcount("DELETE FROM worker_heartbeats")
    yield
    execute_rowcount("DELETE FROM worker_heartbeats")


def checks():
    return {c["code"]: c for c in readiness.additional_runtime_checks()}


def test_automation_missing_and_stale_worker_are_blockers(monkeypatch):
    monkeypatch.setattr(settings, "automation_engine_enabled", True)
    monkeypatch.setattr(settings, "automation_worker_enabled", True)
    assert checks()["automation_worker_heartbeat"]["status"] == "fail"
    health.record_heartbeat("automation", "test-worker")
    assert checks()["automation_worker_heartbeat"]["status"] == "pass"
    execute_rowcount("UPDATE worker_heartbeats SET expires_at = '2000-01-01'")
    assert checks()["automation_worker_heartbeat"]["status"] == "fail"


def test_automation_engine_cannot_advertise_unconfigured_worker(monkeypatch):
    monkeypatch.setattr(settings, "automation_engine_enabled", True)
    monkeypatch.setattr(settings, "automation_worker_enabled", False)
    assert checks()["automation_worker_configuration"]["status"] == "fail"


def test_local_production_requires_pii_but_not_openai_key(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "classification_provider", "ollama")
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(readiness, "probe_ollama", lambda: {"ok": True, "detail": "test model"})
    monkeypatch.setattr(settings, "pii_pseudonymization_key", None)
    assert checks()["pii_pseudonymization"]["status"] == "fail"
    monkeypatch.setattr(settings, "pii_pseudonymization_key", "unique-test-pii-secret")
    assert checks()["pii_pseudonymization"]["status"] == "pass"
    assert checks()["ollama_configuration"]["status"] == "pass"


def test_heartbeat_does_not_bypass_startup_configuration(monkeypatch):
    monkeypatch.setattr(settings, "strict_runtime_validation", True)
    missing_worker = {"code": "inbound_worker_heartbeat", "detail": "not running"}
    monkeypatch.setattr(runtime_guard_service, "get_runtime_blockers", lambda: [missing_worker])
    runtime_guard_service.validate_runtime_environment("inbound_worker")
    monkeypatch.setattr(runtime_guard_service, "get_runtime_blockers", lambda: [missing_worker, {"code": "pii_pseudonymization", "detail": "missing"}])
    with pytest.raises(RuntimeError, match="pii_pseudonymization"):
        runtime_guard_service.validate_runtime_environment("inbound_worker")


def test_inbound_heartbeat_is_independent_of_automation(monkeypatch):
    monkeypatch.setattr(settings, "inbound_worker_enabled", True)
    health.record_heartbeat("automation", "automation-only")
    assert checks()["inbound_worker_heartbeat"]["status"] == "fail"
    health.record_heartbeat("inbound", "inbound-worker")
    assert checks()["inbound_worker_heartbeat"]["status"] == "pass"


def test_validate_only_does_not_forge_heartbeat(monkeypatch):
    from app.tools import inbound_worker
    monkeypatch.setattr(settings, "inbound_worker_enabled", True)
    monkeypatch.setattr("sys.argv", ["inbound_worker", "--validate-only"])
    inbound_worker.main()
    assert health.worker_is_alive("inbound") is False
