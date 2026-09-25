from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.core.company_context import get_company_context_status
from app.core.config import is_missing_or_placeholder, is_production_like, settings
from app.core.credential_vault import vault_is_configured
from app.services.calendar_service import get_calendar_profile
from app.services.ingress_auth_service import (
    get_configured_internal_ingress_secret,
    internal_ingress_enforced,
    normalized_whatsapp_ingress_enabled,
    normalized_whatsapp_ingress_is_safe,
)
from app.services.rate_limit_service import check_shared_state_connection
from app.services.training_export_service import get_finetuning_readiness_report
from app.storage import company_config_repository, whatsapp_credentials_repository
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


def _global_whatsapp_credentials_are_real() -> bool:
    return all(
        _has_real_secret(value)
        for value in [
            settings.whatsapp_api_base_url,
            settings.whatsapp_api_token,
            settings.whatsapp_phone_number_id,
        ]
    )


def _embedded_signup_is_configured() -> bool:
    """A instalação pode conectar números pelo painel (Embedded Signup).

    Exige o cofre com chave explícita: sem ela o token de negócio de cada
    empresa não teria onde ser guardado com segurança.
    """
    return (
        _has_real_secret(settings.meta_app_id)
        and _has_real_secret(settings.meta_embedded_signup_config_id)
        and _has_real_secret(settings.whatsapp_app_secret)
        and vault_is_configured()
    )


def _whatsapp_provider_credentials_are_real() -> bool:
    """Há um caminho real para enviar: credencial global ou conexão por empresa."""
    return _global_whatsapp_credentials_are_real() or _embedded_signup_is_configured()


def _credential_vault_is_required() -> bool:
    """Produção com app da Meta ou projeto Google configurado guarda segredos por empresa."""
    return is_production_like() and (
        _has_real_secret(settings.meta_app_id) or _has_real_secret(settings.google_calendar_client_id)
    )


def _stripe_configuration_status() -> dict[str, Any]:
    required_values = {
        "STRIPE_SECRET_KEY": settings.stripe_secret_key,
        "STRIPE_WEBHOOK_SECRET": settings.stripe_webhook_secret,
        "STRIPE_SUCCESS_URL": settings.stripe_success_url,
        "STRIPE_CANCEL_URL": settings.stripe_cancel_url,
        "STRIPE_PORTAL_RETURN_URL": settings.stripe_portal_return_url,
        "STRIPE_PRICE_ESSENCIAL_MONTHLY": settings.stripe_price_essencial_monthly,
        "STRIPE_PRICE_ESSENCIAL_ANNUAL": settings.stripe_price_essencial_annual,
        "STRIPE_PRICE_AVANCADO_MONTHLY": settings.stripe_price_avancado_monthly,
        "STRIPE_PRICE_AVANCADO_ANNUAL": settings.stripe_price_avancado_annual,
        "STRIPE_PRICE_PREMIUM_MONTHLY": settings.stripe_price_premium_monthly,
        "STRIPE_PRICE_PREMIUM_ANNUAL": settings.stripe_price_premium_annual,
    }
    missing = [
        key
        for key, value in required_values.items()
        if not _has_real_secret(value)
    ]
    invalid_urls = []
    for key in ("STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL", "STRIPE_PORTAL_RETURN_URL"):
        value = str(required_values[key] or "").strip()
        parsed = urlparse(value)
        if value and (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or str(parsed.hostname or "").endswith("example.com")
            or (
                _production_guard_enabled()
                and parsed.scheme != "https"
            )
        ):
            invalid_urls.append(key)
    invalid_identifiers = []
    expected_prefixes = {
        "STRIPE_SECRET_KEY": "sk_",
        "STRIPE_WEBHOOK_SECRET": "whsec_",
        "STRIPE_PRICE_ESSENCIAL_MONTHLY": "price_",
        "STRIPE_PRICE_ESSENCIAL_ANNUAL": "price_",
        "STRIPE_PRICE_AVANCADO_MONTHLY": "price_",
        "STRIPE_PRICE_AVANCADO_ANNUAL": "price_",
        "STRIPE_PRICE_PREMIUM_MONTHLY": "price_",
        "STRIPE_PRICE_PREMIUM_ANNUAL": "price_",
    }
    for key, expected_prefix in expected_prefixes.items():
        value = str(required_values[key] or "").strip()
        if value and not value.startswith(expected_prefix):
            invalid_identifiers.append(key)
    return {
        "ok": not missing and not invalid_urls and not invalid_identifiers,
        "missing": missing,
        "invalid_urls": invalid_urls,
        "invalid_identifiers": invalid_identifiers,
    }


def _transactional_email_configuration_status() -> dict[str, Any]:
    provider = settings.transactional_email_provider
    sender = str(settings.transactional_email_from or "").strip()
    public_url = str(settings.platform_public_url or "").strip()
    parsed_url = urlparse(public_url)
    url_ok = bool(
        parsed_url.scheme in {"http", "https"}
        and parsed_url.netloc
        and not str(parsed_url.hostname or "").endswith("example.com")
        and (
            not _production_guard_enabled()
            or parsed_url.scheme == "https"
        )
    )
    return {
        "ok": bool(
            provider == "resend"
            and _has_real_secret(settings.resend_api_key)
            and "@" in sender
            and url_ok
        ),
        "provider": provider,
        "sender_configured": "@" in sender,
        "public_url_valid": url_ok,
    }


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


def _whatsapp_channel_binding_status() -> dict[str, Any]:
    """The configured business number must map to exactly one active company.

    A signed Meta event resolves its tenant from this binding, so zero or
    multiple matches means inbound traffic would fail closed.
    """
    configured_phone_number_id = str(settings.whatsapp_phone_number_id or "").strip()
    if not configured_phone_number_id:
        return _company_connection_binding_status()

    try:
        bindings = repository.get_whatsapp_bindings(active_only=True)
    except Exception as exc:  # pragma: no cover - defensive readiness probe
        return {"ok": False, "detail": f"Falha ao ler os vínculos de canal: {exc}"}

    matching = [
        binding
        for binding in bindings
        if str(binding.get("phone_number_id") or "") == configured_phone_number_id
    ]
    if not matching:
        return {
            "ok": False,
            "detail": (
                f"Nenhum vínculo ativo para o phone_number_id configurado "
                f"({_mask_phone_number_id(configured_phone_number_id)})."
            ),
        }
    if len(matching) > 1:
        return {
            "ok": False,
            "detail": (
                f"Vínculo ambíguo: {len(matching)} empresas ativas para o mesmo phone_number_id "
                f"({_mask_phone_number_id(configured_phone_number_id)})."
            ),
        }

    return {
        "ok": True,
        "detail": (
            f"phone_number_id {_mask_phone_number_id(configured_phone_number_id)} vinculado à "
            f"empresa {matching[0]['company_id']}."
        ),
    }


def _company_connection_binding_status() -> dict[str, Any]:
    """Sem número global, o canal prova-se pelas empresas que conectaram.

    Nenhuma conexão ainda é um aviso, não um bloqueio: uma instalação nova
    com Embedded Signup configurado está pronta para o primeiro cliente
    conectar o próprio número pela tela Canal. `warn_only` diz ao check
    para não escalar isso a `fail` mesmo quando o canal é obrigatório.
    """
    try:
        connected = whatsapp_credentials_repository.count_connected_companies()
    except Exception as exc:  # pragma: no cover - defensive readiness probe
        return {"ok": False, "detail": f"Falha ao ler as conexões WhatsApp por empresa: {type(exc).__name__}."}

    if connected > 0:
        return {
            "ok": True,
            "detail": (
                f"{connected} empresa(s) com número conectado pelo painel; "
                "eventos do Meta resolvem a empresa pelo phone_number_id de cada vínculo."
            ),
        }
    return {
        "ok": False,
        "warn_only": True,
        "detail": (
            "WHATSAPP_PHONE_NUMBER_ID não configurado e nenhuma empresa conectou um número ainda."
        ),
    }


def _legacy_context_drift_status() -> dict[str, Any]:
    """Empresas com edição legada posterior à versão publicada (D12)."""
    try:
        company_ids = company_config_repository.list_companies_with_newer_legacy_context()
    except Exception as exc:  # pragma: no cover - defensive readiness probe
        return {
            "ok": False,
            "detail": f"Falha ao comparar contexto legado e versões publicadas: {type(exc).__name__}.",
        }
    if company_ids:
        return {
            "ok": False,
            "detail": (
                f"{len(company_ids)} empresa(s) com edição no contexto legado depois da última "
                "publicação; o atendimento usa a versão publicada."
            ),
        }
    return {"ok": True, "detail": "Nenhuma empresa com contexto legado mais novo que a versão publicada."}


def _mask_phone_number_id(phone_number_id: str) -> str:
    if len(phone_number_id) <= 4:
        return phone_number_id
    return f"***{phone_number_id[-4:]}"


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
        "billing": {
            "provider": "stripe",
            "configured": bool(_stripe_configuration_status()["ok"]),
            "enforcement_enabled": settings.enable_billing_enforcement,
            "grace_period_days": settings.billing_grace_period_days,
        },
        "identity": {
            "self_service_signup_enabled": settings.enable_self_service_signup,
            "transactional_email_provider": settings.transactional_email_provider,
            "transactional_email_configured": bool(
                _transactional_email_configuration_status()["ok"]
            ),
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
            "whatsapp_embedded_signup_configured": _embedded_signup_is_configured(),
            "credential_vault_configured": vault_is_configured(),
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
    shared_state_probe = check_shared_state_connection()
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
    shared_state_required = settings.require_shared_state
    shared_state_ok = bool(shared_state_probe["ok"])
    checks.append(
        _make_check(
            code="shared_state",
            title="Estado distribuído",
            status="pass" if shared_state_ok else ("fail" if shared_state_required else "warn"),
            severity="blocker" if shared_state_required else "warning",
            detail=str(shared_state_probe["detail"]),
            recommendation=(
                "Configurar REDIS_URL e REQUIRE_SHARED_STATE=true antes de escalar "
                "API/realtime/rate limiting horizontalmente."
            )
            if not shared_state_ok
            else None,
        )
    )
    stripe_status = _stripe_configuration_status()
    stripe_required = settings.enable_billing_enforcement
    stripe_detail = (
        "Stripe Checkout, Customer Portal, webhook e catálogo de preços configurados."
        if stripe_status["ok"]
        else (
            "Configuração Stripe incompleta. "
            f"Ausentes: {', '.join(stripe_status['missing']) or 'nenhum'}; "
            f"URLs inválidas: {', '.join(stripe_status['invalid_urls']) or 'nenhuma'}; "
            "identificadores inválidos: "
            f"{', '.join(stripe_status['invalid_identifiers']) or 'nenhum'}."
        )
    )
    checks.append(
        _make_check(
            code="stripe_billing",
            title="Cobrança Stripe",
            status=(
                "pass"
                if stripe_status["ok"]
                else ("fail" if stripe_required else "warn")
            ),
            severity="blocker" if stripe_required else "warning",
            detail=stripe_detail,
            recommendation=(
                "Configurar chaves, URLs HTTPS, webhook e os seis Price IDs do Stripe "
                "antes de habilitar ENABLE_BILLING_ENFORCEMENT=true."
            )
            if not stripe_status["ok"]
            else None,
        )
    )
    email_status = _transactional_email_configuration_status()
    email_required = settings.enable_self_service_signup
    checks.append(
        _make_check(
            code="transactional_email",
            title="E-mail transacional",
            status=(
                "pass"
                if email_status["ok"]
                else ("fail" if email_required else "warn")
            ),
            severity="blocker" if email_required else "warning",
            detail=(
                "Resend configurado para convites e redefinição de senha."
                if email_status["ok"]
                else (
                    f"Provider: {email_status['provider']}; "
                    f"remetente configurado: {email_status['sender_configured']}; "
                    f"URL pública válida: {email_status['public_url_valid']}."
                )
            ),
            recommendation=(
                "Configurar TRANSACTIONAL_EMAIL_PROVIDER=resend, RESEND_API_KEY, "
                "TRANSACTIONAL_EMAIL_FROM e PLATFORM_PUBLIC_URL antes de habilitar signup."
            )
            if not email_status["ok"]
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

    context_persisted_in_database = str(global_context.get("source")) == "database"
    checks.append(
        _make_check(
            code="company_context_persistence",
            title="Persistência do company context",
            status="pass" if context_persisted_in_database else ("fail" if production_guard_enabled else "warn"),
            severity="blocker" if production_guard_enabled else "warning",
            detail=(
                "Company context global carregado do banco compartilhado."
                if context_persisted_in_database
                else f"Company context global resolvido de: {global_context.get('source')}."
            ),
            recommendation=(
                "Executar `python -m app.tools.bootstrap_company_contexts` para importar os "
                "contextos de seed para o banco antes de operar em produção."
            )
            if not context_persisted_in_database
            else None,
        )
    )

    # D12: o runtime lê a versão publicada. Edição legada posterior à
    # publicação não tem efeito — sem este alerta, a empresa atenderia com a
    # versão antiga e ninguém saberia.
    legacy_drift = _legacy_context_drift_status()
    checks.append(
        _make_check(
            code="company_context_legacy_newer_than_published",
            title="Contexto legado mais novo que a versão publicada",
            status="pass" if legacy_drift["ok"] else "warn",
            severity="warning",
            detail=str(legacy_drift["detail"]),
            recommendation=(
                "Revisar e republicar o contexto da empresa (a API legada agora publica versão)."
                if not legacy_drift["ok"]
                else None
            ),
        )
    )

    normalized_ingress_safe = normalized_whatsapp_ingress_is_safe()
    checks.append(
        _make_check(
            code="normalized_ingress_security",
            title="Ingress normalizado",
            status="pass" if normalized_ingress_safe else "fail",
            severity="blocker",
            detail=(
                "Ingress normalizado desabilitado."
                if not normalized_whatsapp_ingress_enabled()
                else "Ingress normalizado habilitado com INTERNAL_INGRESS_SECRET real."
                if normalized_ingress_safe
                else "ENABLE_NORMALIZED_WHATSAPP_INGRESS=true sem INTERNAL_INGRESS_SECRET válido."
            ),
            recommendation="Definir INTERNAL_INGRESS_SECRET forte ou desabilitar ENABLE_NORMALIZED_WHATSAPP_INGRESS."
            if not normalized_ingress_safe
            else None,
        )
    )

    internal_ingress_secret_configured = get_configured_internal_ingress_secret() is not None
    internal_ingress_status = (
        "pass"
        if internal_ingress_secret_configured
        else ("fail" if internal_ingress_enforced() else "warn")
    )
    checks.append(
        _make_check(
            code="internal_ingress_secret",
            title="Segredo de ingress interno",
            status=internal_ingress_status,
            severity="blocker" if internal_ingress_enforced() else "warning",
            detail=(
                "INTERNAL_INGRESS_SECRET configurado com valor não-placeholder."
                if internal_ingress_secret_configured
                else "INTERNAL_INGRESS_SECRET ausente ou usando valor placeholder."
            ),
            recommendation="Definir INTERNAL_INGRESS_SECRET para autenticar /messages/incoming fora de desenvolvimento."
            if not internal_ingress_secret_configured
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
        settings.classification_provider == "openai"
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
    binding_status = _whatsapp_channel_binding_status()
    binding_required = settings.outbound_provider == "whatsapp" or production_guard_enabled
    binding_warn_only = bool(binding_status.get("warn_only"))
    binding_check_status = (
        "pass"
        if binding_status["ok"]
        else ("warn" if binding_warn_only or not binding_required else "fail")
    )
    checks.append(
        _make_check(
            code="whatsapp_channel_binding",
            title="Vínculo do número WhatsApp",
            status=binding_check_status,
            severity="blocker" if binding_required and not binding_warn_only else "warning",
            detail=str(binding_status["detail"]),
            recommendation=(
                (
                    "Conecte o número pela tela Canal (Embedded Signup) ou defina "
                    "WHATSAPP_PHONE_NUMBER_ID e vincule-o via PUT /admin/whatsapp/bindings."
                )
                if binding_warn_only
                else (
                    "Vincular o WHATSAPP_PHONE_NUMBER_ID a exatamente uma empresa ativa via "
                    "PUT /admin/whatsapp/bindings antes de receber eventos reais do Meta."
                )
            )
            if not binding_status["ok"]
            else None,
        )
    )
    vault_required = _credential_vault_is_required()
    vault_configured = vault_is_configured()
    checks.append(
        _make_check(
            code="credential_vault",
            title="Cofre de credenciais",
            status="pass" if vault_configured else ("fail" if vault_required else "warn"),
            severity="blocker" if vault_required else "warning",
            detail=(
                "CREDENTIALS_ENCRYPTION_KEY configurada; tokens por empresa são cifrados no banco."
                if vault_configured
                else (
                    "CREDENTIALS_ENCRYPTION_KEY ausente ou inválida; conexões WhatsApp/Google por "
                    "empresa não podem guardar credenciais em produção."
                    if vault_required
                    else "CREDENTIALS_ENCRYPTION_KEY ausente; fora de produção uma chave derivada é usada."
                )
            ),
            recommendation=(
                "Gerar uma chave com app.core.credential_vault.generate_key() e definir "
                "CREDENTIALS_ENCRYPTION_KEY antes de conectar números ou agendas pelo painel."
            )
            if not vault_configured
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

    from app.services.local_runtime_readiness import additional_runtime_checks
    checks.extend(additional_runtime_checks())

    has_blockers = any(check["status"] == "fail" and check["severity"] == "blocker" for check in checks)
    has_warnings = any(check["status"] == "warn" for check in checks)

    overall_status = "blocked" if has_blockers else ("warning" if has_warnings else "ready")
    pilot_ready = not has_blockers
    scale_ready = pilot_ready and bool(postgres_probe["ok"]) and shared_state_ok

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
                f"Contexto válido carregado de {context_status.get('source') or 'desconhecido'}."
                if context_status["is_valid"]
                else str(context_status["error"])
            ),
            recommendation="Revisar o contexto e garantir todas as chaves obrigatórias."
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
