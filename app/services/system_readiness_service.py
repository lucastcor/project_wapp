from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.core.company_context import get_company_context_status
from app.core.config import is_missing_or_placeholder, settings
from app.services.calendar_service import get_calendar_profile
from app.services.training_export_service import get_finetuning_readiness_report
from app.storage.db import (
    check_postgres_target_connection,
    check_runtime_database_connection,
    get_database_backend,
    get_postgres_target_summary,
    get_runtime_database_summary,
)
from app.storage.repository import RuntimeRepository


repository = RuntimeRepository()


def _make_check(
    *,
    code: str,
    title: str,
    status: str,
    severity: str,
    detail: str,
    recommendation: str | None = None,
) -> dict[str, str | None]:
    return {
        "code": code,
        "title": title,
        "status": status,
        "severity": severity,
        "detail": detail,
        "recommendation": recommendation,
    }


def _production_guard_enabled() -> bool:
    return settings.strict_runtime_validation or settings.environment.lower() == "production"


def _configured_admin_roles() -> list[str]:
    roles: list[str] = []
    if settings.admin_viewer_token:
        roles.append("viewer")
    if settings.admin_operator_token:
        roles.append("operator")
    if settings.admin_superadmin_token or settings.admin_api_token:
        roles.append("superadmin")
    return roles


def _required_admin_roles_configured() -> bool:
    required_roles = {"viewer", "operator", "superadmin"}
    return required_roles.issubset(set(_configured_admin_roles()))


def _has_real_secret(value: str | None) -> bool:
    return not is_missing_or_placeholder(value)


def _configured_admin_tokens_are_real() -> bool:
    configured_values = [
        settings.admin_viewer_token,
        settings.admin_operator_token,
        settings.admin_superadmin_token or settings.admin_api_token,
    ]
    return all(_has_real_secret(value) for value in configured_values if value is not None)


def _whatsapp_provider_credentials_are_real() -> bool:
    return all(
        _has_real_secret(value)
        for value in [
            settings.whatsapp_api_base_url,
            settings.whatsapp_api_token,
            settings.whatsapp_phone_number_id,
        ]
    )


def _configured_postgres_urls() -> list[str]:
    urls: list[str] = []
    for configured_url in [settings.database_url, settings.postgres_database_url]:
        normalized = str(configured_url or "").strip()
        if not normalized:
            continue
        if urlparse(normalized).scheme not in {"postgres", "postgresql"}:
            continue
        if normalized not in urls:
            urls.append(normalized)
    return urls


def _postgres_credentials_are_real() -> bool:
    configured_urls = _configured_postgres_urls()
    if not configured_urls:
        return False
    for configured_url in configured_urls:
        parsed = urlparse(configured_url)
        if not str(parsed.username or "").strip():
            return False
        if is_missing_or_placeholder(parsed.password):
            return False
    return True


def _configured_reminder_worker_audiences() -> list[str]:
    values = [
        item.strip().lower()
        for item in settings.reminder_worker_audiences.split(",")
        if item.strip()
    ]
    normalized: list[str] = []
    for item in values:
        if item not in {"customer", "internal"}:
            continue
        if item not in normalized:
            normalized.append(item)
    return normalized or ["customer", "internal"]


def get_system_profile() -> dict[str, Any]:
    runtime_database = get_runtime_database_summary()
    postgres_target = get_postgres_target_summary()
    configured_roles = _configured_admin_roles()
    calendar_profile = get_calendar_profile()

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "app": {
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "debug": settings.debug,
            "strict_runtime_validation": settings.strict_runtime_validation,
        },
        "database": {
            "runtime": runtime_database,
            "postgres_target": postgres_target,
        },
        "security": {
            "admin_auth_enabled": settings.enable_admin_auth,
            "configured_roles": configured_roles,
            "legacy_superadmin_fallback_enabled": bool(
                settings.admin_api_token and not settings.admin_superadmin_token
            ),
            "rate_limiting_enabled": settings.enable_rate_limiting,
            "rate_limit_window_seconds": settings.rate_limit_window_seconds,
            "audit_retention_days": settings.audit_retention_days,
        },
        "ai": {
            "openai_configured": _has_real_secret(settings.openai_api_key),
            "classification_provider": settings.classification_provider,
            "openai_model": settings.openai_model,
            "finetuned_model_configured": bool(settings.openai_finetuned_model),
            "use_finetuned_model_for_replies": settings.use_finetuned_model_for_replies,
            "finetuning_pipeline_enabled": settings.enable_finetuning_pipeline,
            "finetuning_base_model": settings.openai_finetuning_base_model,
        },
        "channels": {
            "outbound_provider": settings.outbound_provider,
            "default_channel": settings.outbound_default_channel,
            "whatsapp_configured": _whatsapp_provider_credentials_are_real(),
            "whatsapp_webhook_verification_configured": _has_real_secret(settings.whatsapp_webhook_verify_token),
            "whatsapp_signature_validation_configured": _has_real_secret(settings.whatsapp_app_secret),
            "calendar_provider": settings.calendar_provider,
            "calendar_timezone": settings.calendar_timezone,
            "calendar_ready": calendar_profile["provider_readiness"]["ready"],
        },
        "observability": {
            "log_level": settings.log_level,
            "log_json": settings.log_json,
            "request_correlation_header": "X-Request-ID",
        },
        "automation": {
            "reminder_worker_enabled": settings.reminder_worker_enabled,
            "reminder_worker_poll_seconds": settings.reminder_worker_poll_seconds,
            "reminder_worker_dispatch_immediately": settings.reminder_worker_dispatch_immediately,
            "reminder_worker_audiences": _configured_reminder_worker_audiences(),
            "reminder_worker_channel": settings.reminder_worker_channel,
            "appointment_queue_auto_dispatch": settings.appointment_queue_auto_dispatch,
            "appointment_queue_response_window_minutes": settings.appointment_queue_response_window_minutes,
            "appointment_queue_wait_followup_minutes": settings.appointment_queue_wait_followup_minutes,
        },
        "operations": {
            "backup_dir": settings.operations_backup_dir,
            "data_hygiene": repository.get_operational_hygiene_summary(),
        },
    }


def get_system_readiness() -> dict[str, Any]:
    runtime_backend = get_database_backend()
    runtime_database = get_runtime_database_summary()
    runtime_probe = check_runtime_database_connection()
    postgres_target = get_postgres_target_summary()
    postgres_probe = check_postgres_target_connection()
    global_context = get_company_context_status()
    configured_roles = _configured_admin_roles()
    calendar_profile = get_calendar_profile()
    hygiene_summary = repository.get_operational_hygiene_summary()
    production_guard_enabled = _production_guard_enabled()

    checks: list[dict[str, str | None]] = []

    checks.append(
        _make_check(
            code="runtime_database",
            title="Banco de runtime",
            status="pass" if runtime_probe["ok"] else "fail",
            severity="blocker",
            detail=str(runtime_probe["detail"]),
            recommendation=f"Garantir acesso consistente de leitura e escrita ao banco de runtime {runtime_backend}."
            if not runtime_probe["ok"]
            else None,
        )
    )
    checks.append(
        _make_check(
            code="global_company_context",
            title="Contexto global",
            status="pass" if global_context["is_valid"] else "fail",
            severity="blocker",
            detail=str(
                "Company context global validado."
                if global_context["is_valid"]
                else global_context["error"]
            ),
            recommendation="Corrigir ou recriar o company_context global."
            if not global_context["is_valid"]
            else None,
        )
    )

    admin_auth_status = "pass" if settings.enable_admin_auth else ("fail" if production_guard_enabled else "warn")
    admin_auth_detail = (
        f"Autenticacao admin habilitada com papéis: {', '.join(configured_roles) or 'nenhum'}."
        if settings.enable_admin_auth
        else "Autenticacao admin desabilitada."
    )
    checks.append(
        _make_check(
            code="admin_auth",
            title="Autenticacao administrativa",
            status=admin_auth_status,
            severity="blocker" if production_guard_enabled else "warning",
            detail=admin_auth_detail,
            recommendation="Habilitar ENABLE_ADMIN_AUTH e definir tokens por papel antes de operar clientes reais."
            if not settings.enable_admin_auth
            else None,
        )
    )

    role_tokens_status = "pass" if _required_admin_roles_configured() else ("fail" if production_guard_enabled else "warn")
    checks.append(
        _make_check(
            code="admin_roles",
            title="RBAC administrativo",
            status=role_tokens_status,
            severity="blocker" if production_guard_enabled else "warning",
            detail=(
                f"Papéis configurados: {', '.join(configured_roles)}."
                if configured_roles
                else "Nenhum token de papel configurado."
            ),
            recommendation="Configurar viewer, operator e superadmin para separar operação com RBAC completo."
            if not _required_admin_roles_configured()
            else None,
        )
    )

    session_secret_status = "pass" if _has_real_secret(settings.platform_session_secret) else ("fail" if production_guard_enabled else "warn")
    checks.append(
        _make_check(
            code="platform_session_secret",
            title="Segredo da sessão web",
            status=session_secret_status,
            severity="blocker" if production_guard_enabled else "warning",
            detail=(
                "PLATFORM_SESSION_SECRET configurado com valor não-placeholder."
                if _has_real_secret(settings.platform_session_secret)
                else "PLATFORM_SESSION_SECRET ausente ou usando valor placeholder."
            ),
            recommendation="Definir PLATFORM_SESSION_SECRET forte e exclusivo para assinar a sessão da plataforma."
            if not _has_real_secret(settings.platform_session_secret)
            else None,
        )
    )

    admin_token_quality_status = "pass" if _configured_admin_tokens_are_real() else ("fail" if production_guard_enabled else "warn")
    checks.append(
        _make_check(
            code="admin_token_quality",
            title="Qualidade dos segredos admin",
            status=admin_token_quality_status,
            severity="blocker" if production_guard_enabled else "warning",
            detail=(
                "Todos os tokens admin configurados usam valores reais."
                if _configured_admin_tokens_are_real()
                else "Há token admin ausente ou com valor placeholder."
            ),
            recommendation="Substituir todos os tokens admin placeholder antes de operar em produção."
            if not _configured_admin_tokens_are_real()
            else None,
        )
    )

    logging_status = "pass" if settings.log_json else "warn"
    checks.append(
        _make_check(
            code="structured_logging",
            title="Logging estruturado",
            status=logging_status,
            severity="warning",
            detail="Logs estruturados em JSON habilitados."
            if settings.log_json
            else "LOG_JSON=false reduz padronização operacional.",
            recommendation="Ativar LOG_JSON=true em ambientes com coleta centralizada."
            if not settings.log_json
            else None,
        )
    )
    rate_limit_status = "pass" if settings.enable_rate_limiting else ("fail" if production_guard_enabled else "warn")
    checks.append(
        _make_check(
            code="rate_limiting",
            title="Proteção de rate limiting",
            status=rate_limit_status,
            severity="blocker" if production_guard_enabled else "warning",
            detail=(
                f"Rate limiting habilitado com janela de {settings.rate_limit_window_seconds}s."
                if settings.enable_rate_limiting
                else "Rate limiting desabilitado."
            ),
            recommendation="Ativar ENABLE_RATE_LIMITING e ajustar limites por superfície HTTP antes de expor a API publicamente."
            if not settings.enable_rate_limiting
            else None,
        )
    )
    postgres_credentials_status = "pass" if _postgres_credentials_are_real() else ("fail" if production_guard_enabled else "warn")
    checks.append(
        _make_check(
            code="postgres_credentials",
            title="Segredo do Postgres",
            status=postgres_credentials_status,
            severity="blocker" if production_guard_enabled else "warning",
            detail=(
                "Credenciais do Postgres configuradas com valores não-placeholder."
                if _postgres_credentials_are_real()
                else "DATABASE_URL/POSTGRES_DATABASE_URL usam senha ausente ou placeholder."
            ),
            recommendation="Substituir a senha do Postgres por valor forte e sincronizar DATABASE_URL, POSTGRES_DATABASE_URL e POSTGRES_PASSWORD."
            if not _postgres_credentials_are_real()
            else None,
        )
    )

    openai_required = (
        production_guard_enabled
        or settings.classification_provider == "openai"
        or settings.enable_finetuning_pipeline
    )
    openai_status = "pass" if _has_real_secret(settings.openai_api_key) else ("fail" if openai_required else "warn")
    checks.append(
        _make_check(
            code="openai_configuration",
            title="Configuração OpenAI",
            status=openai_status,
            severity="blocker" if openai_required else "warning",
            detail="OPENAI_API_KEY configurada com valor não-placeholder."
            if _has_real_secret(settings.openai_api_key)
            else "OPENAI_API_KEY ausente ou usando valor placeholder.",
            recommendation="Definir OPENAI_API_KEY para usar classificação, reescrita e fine-tuning com provider real."
            if not _has_real_secret(settings.openai_api_key)
            else None,
        )
    )

    outbound_provider_ready = settings.outbound_provider == "whatsapp"
    outbound_provider_status = "pass" if outbound_provider_ready else ("fail" if production_guard_enabled else "warn")
    checks.append(
        _make_check(
            code="outbound_provider",
            title="Provider outbound",
            status=outbound_provider_status,
            severity="blocker" if production_guard_enabled else "warning",
            detail=(
                "Provider outbound configurado para WhatsApp."
                if outbound_provider_ready
                else f"Provider atual: {settings.outbound_provider}."
            ),
            recommendation="Definir OUTBOUND_PROVIDER=whatsapp para dispatch automático em produção."
            if not outbound_provider_ready
            else None,
        )
    )

    whatsapp_required = settings.outbound_provider == "whatsapp" or production_guard_enabled
    whatsapp_configured = _whatsapp_provider_credentials_are_real()
    whatsapp_status = "pass" if whatsapp_configured else ("fail" if whatsapp_required else "warn")
    checks.append(
        _make_check(
            code="whatsapp_provider",
            title="Canal WhatsApp",
            status=whatsapp_status,
            severity="blocker" if whatsapp_required else "warning",
            detail="Credenciais de WhatsApp configuradas."
            if whatsapp_configured
            else f"Provider atual: {settings.outbound_provider}.",
            recommendation="Configurar credenciais reais do WhatsApp antes de vender operação em produção."
            if not whatsapp_configured
            else None,
        )
    )
    whatsapp_webhook_verify_status = (
        "pass"
        if _has_real_secret(settings.whatsapp_webhook_verify_token)
        else ("fail" if production_guard_enabled else "warn")
    )
    checks.append(
        _make_check(
            code="whatsapp_webhook_verification",
            title="Verificação do webhook WhatsApp",
            status=whatsapp_webhook_verify_status,
            severity="blocker" if production_guard_enabled else "warning",
            detail=(
                "Token de verificação do webhook configurado."
                if _has_real_secret(settings.whatsapp_webhook_verify_token)
                else "WHATSAPP_WEBHOOK_VERIFY_TOKEN ausente ou usando valor placeholder."
            ),
            recommendation="Definir WHATSAPP_WEBHOOK_VERIFY_TOKEN para concluir o handshake do provedor real."
            if not _has_real_secret(settings.whatsapp_webhook_verify_token)
            else None,
        )
    )
    whatsapp_signature_status = (
        "pass"
        if _has_real_secret(settings.whatsapp_app_secret)
        else ("fail" if production_guard_enabled else "warn")
    )
    checks.append(
        _make_check(
            code="whatsapp_signature_validation",
            title="Assinatura do webhook WhatsApp",
            status=whatsapp_signature_status,
            severity="blocker" if production_guard_enabled else "warning",
            detail=(
                "App secret configurado para validar assinatura do webhook."
                if _has_real_secret(settings.whatsapp_app_secret)
                else "WHATSAPP_APP_SECRET ausente ou usando valor placeholder."
            ),
            recommendation="Definir WHATSAPP_APP_SECRET para validar a origem do POST do Meta."
            if not _has_real_secret(settings.whatsapp_app_secret)
            else None,
        )
    )
    calendar_required = production_guard_enabled and settings.calendar_provider.strip().lower() != "mock"
    calendar_ready = bool(calendar_profile["provider_readiness"]["ready"])
    checks.append(
        _make_check(
            code="calendar_provider",
            title="Canal de agenda",
            status="pass" if calendar_ready else ("fail" if calendar_required else "warn"),
            severity="blocker" if calendar_required else "warning",
            detail=str(calendar_profile["provider_readiness"]["details"]),
            recommendation="Configurar integração Google Calendar ou CalDAV para agenda ao vivo."
            if not calendar_ready
            else None,
        )
    )
    checks.append(
        _make_check(
            code="reminder_worker",
            title="Worker de lembretes",
            status="pass" if settings.reminder_worker_enabled else ("fail" if production_guard_enabled else "warn"),
            severity="blocker" if production_guard_enabled else "warning",
            detail=(
                "Worker recorrente habilitado para processar lembretes vencidos."
                if settings.reminder_worker_enabled
                else "Worker recorrente de lembretes desabilitado."
            ),
            recommendation="Habilitar REMINDER_WORKER_ENABLED=true para operar lembretes automáticos sem ação manual."
            if not settings.reminder_worker_enabled
            else None,
        )
    )

    postgres_in_runtime = runtime_backend == "postgres"
    postgres_required = production_guard_enabled or postgres_in_runtime
    postgres_status = "pass" if postgres_probe["ok"] and postgres_in_runtime else ("fail" if postgres_required else "warn")
    postgres_detail = (
        "Postgres já é o banco de runtime e respondeu ao probe."
        if postgres_in_runtime and postgres_probe["ok"]
        else "O runtime ainda não está usando Postgres."
        if production_guard_enabled and not postgres_in_runtime
        else str(postgres_probe["detail"])
        if postgres_target["configured"]
        else "POSTGRES_DATABASE_URL ainda não foi configurada."
    )
    checks.append(
        _make_check(
            code="postgres_target",
            title="Runtime/target Postgres",
            status=postgres_status,
            severity="blocker" if production_guard_enabled else "warning",
            detail=postgres_detail,
            recommendation="Configurar DATABASE_URL e POSTGRES_DATABASE_URL para Postgres e validar conectividade antes da operação real."
            if not (postgres_probe["ok"] and postgres_in_runtime)
            else None,
        )
    )

    retention_status = "pass" if settings.audit_retention_days > 0 else "fail"
    checks.append(
        _make_check(
            code="audit_retention",
            title="Retenção de auditoria",
            status=retention_status,
            severity="blocker",
            detail=f"Janela atual: {settings.audit_retention_days} dias.",
            recommendation="Definir AUDIT_RETENTION_DAYS com valor positivo."
            if settings.audit_retention_days <= 0
            else None,
        )
    )
    hygiene_counts = hygiene_summary["counts"]
    hygiene_issue_count = sum(int(value) for value in hygiene_counts.values())
    checks.append(
        _make_check(
            code="operational_hygiene",
            title="Higiene operacional",
            status="pass" if hygiene_issue_count == 0 else "warn",
            severity="warning",
            detail=(
                "Base operacional sem placeholders ativos nem backlog obsoleto."
                if hygiene_issue_count == 0
                else (
                    f"Encontrados {hygiene_counts['active_placeholder_companies']} placeholder(s) ativos, "
                    f"{hygiene_counts['stale_pending_questions']} pending question(s) antigas, "
                    f"{hygiene_counts['stale_outbound_messages']} outbound(s) pendentes antigas e "
                    f"{hygiene_counts['stale_failed_finetuning_jobs']} job(s) de fine-tuning falhados antigos."
                )
            ),
            recommendation="Executar limpeza operacional segura com backup previo antes do go-live."
            if hygiene_issue_count > 0
            else None,
        )
    )

    has_blockers = any(check["status"] == "fail" and check["severity"] == "blocker" for check in checks)
    has_warnings = any(check["status"] == "warn" for check in checks)

    overall_status = "blocked" if has_blockers else ("warning" if has_warnings else "ready")
    pilot_ready = not has_blockers
    scale_ready = pilot_ready and bool(postgres_probe["ok"])

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "overall_status": overall_status,
        "pilot_ready": pilot_ready,
        "scale_ready": scale_ready,
        "runtime_database": runtime_database,
        "postgres_target": postgres_target,
        "checks": checks,
        "blocking_items": [check for check in checks if check["status"] == "fail"],
        "warning_items": [check for check in checks if check["status"] == "warn"],
    }


def get_onboarding_status(
    *,
    company_id: int | None = None,
    company_slug: str | None = None,
) -> dict[str, Any]:
    resolved_company = None
    if company_id is not None:
        resolved_company = repository.get_company(company_id)
    elif company_slug:
        resolved_company = repository.get_company_by_slug(company_slug)

    resolved_company_id = int(resolved_company["id"]) if resolved_company else None
    resolved_company_slug = (
        str(resolved_company["slug"])
        if resolved_company
        else (company_slug.strip() if company_slug else None)
    )
    context_status = get_company_context_status(company_slug=resolved_company_slug)
    operational_snapshot = repository.get_operational_snapshot(company_id=resolved_company_id)
    finetuning_readiness = get_finetuning_readiness_report(company_id=resolved_company_id)

    checklist: list[dict[str, Any]] = []

    if resolved_company:
        checklist.append(
            _make_check(
                code="company_registered",
                title="Empresa cadastrada",
                status="pass",
                severity="blocker",
                detail=f"Empresa {resolved_company['name']} encontrada com slug {resolved_company['slug']}.",
            )
        )
        checklist.append(
            _make_check(
                code="company_active",
                title="Empresa ativa",
                status="pass" if resolved_company.get("active") else "warn",
                severity="warning",
                detail="Empresa ativa para operação." if resolved_company.get("active") else "Empresa está inativa.",
                recommendation="Ativar a empresa antes de iniciar operação comercial."
                if not resolved_company.get("active")
                else None,
            )
        )
    elif company_id is not None or company_slug:
        checklist.append(
            _make_check(
                code="company_registered",
                title="Empresa cadastrada",
                status="fail",
                severity="blocker",
                detail="Empresa solicitada não foi encontrada.",
                recommendation="Criar a empresa no painel antes do onboarding operacional.",
            )
        )
    else:
        total_companies = len(repository.get_companies())
        checklist.append(
            _make_check(
                code="companies_catalog",
                title="Base de empresas",
                status="pass" if total_companies > 0 else "warn",
                severity="warning",
                detail=f"Total de empresas cadastradas: {total_companies}.",
                recommendation="Cadastrar pelo menos uma empresa para iniciar operação multiempresa."
                if total_companies == 0
                else None,
            )
        )

    checklist.append(
        _make_check(
            code="company_context",
            title="Contexto operacional",
            status="pass" if context_status["is_valid"] else "fail",
            severity="blocker",
            detail=(
                f"Contexto válido carregado de {context_status['resolved_path']}."
                if context_status["is_valid"]
                else str(context_status["error"])
            ),
            recommendation="Revisar o JSON de contexto e garantir todas as chaves obrigatórias."
            if not context_status["is_valid"]
            else None,
        )
    )

    curated_count = int(finetuning_readiness["eligible_examples_count"])
    checklist.append(
        _make_check(
            code="message_examples",
            title="Exemplos aprovados",
            status="pass" if curated_count > 0 else "warn",
            severity="warning",
            detail=f"Exemplos elegíveis para treino: {curated_count}.",
            recommendation="Cadastrar exemplos aprovados para guiar o estilo e o fine-tuning."
            if curated_count == 0
            else None,
        )
    )
    checklist.append(
        _make_check(
            code="finetuning_readiness",
            title="Readiness de fine-tuning",
            status="pass" if finetuning_readiness["is_ready_for_finetuning"] else "warn",
            severity="warning",
            detail=(
                "Dataset já atingiu o mínimo recomendado."
                if finetuning_readiness["is_ready_for_finetuning"]
                else f"Faltam {finetuning_readiness['missing_examples_count']} exemplos elegíveis."
            ),
            recommendation="Aumentar exemplos aprovados e cobrir cenários sub-representados."
            if not finetuning_readiness["is_ready_for_finetuning"]
            else None,
        )
    )

    pending_count = int(operational_snapshot["counts"]["pending_questions"])
    checklist.append(
        _make_check(
            code="pending_questions_backlog",
            title="Backlog humano",
            status="warn" if pending_count > 0 else "pass",
            severity="warning",
            detail=f"Pending questions em aberto no escopo: {pending_count}.",
            recommendation="Resolver o backlog antes de iniciar uma operação nova com SLA curto."
            if pending_count > 0
            else None,
        )
    )

    has_blockers = any(item["status"] == "fail" for item in checklist)
    has_warnings = any(item["status"] == "warn" for item in checklist)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "company_id": resolved_company_id,
            "company_slug": resolved_company_slug,
            "company_name": resolved_company["name"] if resolved_company else None,
            "mode": "company" if (company_id is not None or company_slug) else "global",
        },
        "overall_status": "blocked" if has_blockers else ("warning" if has_warnings else "ready"),
        "context_status": context_status,
        "operational_snapshot": operational_snapshot,
        "finetuning_readiness": finetuning_readiness,
        "checklist": checklist,
        "next_actions": [
            item["recommendation"]
            for item in checklist
            if item.get("recommendation")
        ],
    }
