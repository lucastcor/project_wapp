"""Checks for inference and independently deployed queue consumers."""
from typing import Any

from app.core.config import is_production_like, settings
from app.llm.providers.ollama_provider import probe_ollama
from app.services.pii_service import pseudonymization_key_is_configured
from app.services.worker_health_service import worker_is_alive
from app.storage.inbound_repository import review_count


def additional_runtime_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(code: str, title: str, ok: bool, detail: str, *, required: bool = True) -> None:
        checks.append({"code": code, "title": title, "status": "pass" if ok else ("fail" if required else "warn"),
                       "severity": "blocker" if required else "warning", "detail": detail, "recommendation": None})

    provider = settings.classification_provider
    add("llm_provider", "Provider de IA", provider in {"openai", "ollama"} or (provider == "mock" and not is_production_like()),
        "Selecione openai ou ollama; mock é restrito ao desenvolvimento.")
    if provider == "ollama":
        probe = probe_ollama()
        add("ollama_configuration", "Modelo local", probe["ok"], probe["detail"])
    add("pii_pseudonymization", "Chave de pseudonimização", pseudonymization_key_is_configured(),
        "PII_PSEUDONYMIZATION_KEY deve ser um segredo próprio, não-placeholder.", required=is_production_like())
    if settings.automation_engine_enabled or settings.automation_worker_enabled:
        configured = settings.automation_engine_enabled and settings.automation_worker_enabled
        add("automation_worker_configuration", "Configuração de automação", configured,
            "AUTOMATION_ENGINE_ENABLED e AUTOMATION_WORKER_ENABLED devem estar habilitados juntos.")
        add("automation_worker_heartbeat", "Worker de automação", worker_is_alive("automation"),
            "O worker precisa registrar heartbeat recente no mesmo banco da API.")
    if settings.inbound_worker_enabled or settings.outbound_provider == "whatsapp" or settings.enable_normalized_whatsapp_ingress:
        add("inbound_worker_configuration", "Configuração da fila de entrada", settings.inbound_worker_enabled,
            "INBOUND_WORKER_ENABLED=true e processo app.tools.inbound_worker são necessários.")
        add("inbound_worker_heartbeat", "Worker de entrada", worker_is_alive("inbound"),
            "O worker de entrada precisa registrar heartbeat recente no mesmo banco da API.")
        try:
            count = review_count()
            add("inbound_review", "Entradas aguardando revisão", count == 0,
                f"{count} entrada(s) aguardam revisão; consulte /admin/inbound/jobs.", required=False)
        except Exception:
            add("inbound_queue_schema", "Schema da fila", False, "Execute as migrations da fila de entrada.")
    return checks
