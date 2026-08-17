import os

from dotenv import load_dotenv

# Automated tests set DISABLE_DOTENV=true so configuration never depends on a
# developer's local .env file.
if os.getenv("DISABLE_DOTENV", "false").strip().lower() != "true":
    load_dotenv()


_PLACEHOLDER_PREFIXES = ("change-me", "changeme", "example-", "example_", "example")
_PLACEHOLDER_VALUES = {
    "project-wapp-platform-dev-secret",
}


def is_missing_or_placeholder(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    if normalized in _PLACEHOLDER_VALUES:
        return True
    return any(normalized.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES)


class Settings:
    app_name: str = os.getenv("APP_NAME", "Chatbot API")
    app_version: str = os.getenv("APP_VERSION", "1.0.0")
    environment: str = os.getenv("ENVIRONMENT", "development")
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_json: bool = os.getenv("LOG_JSON", "true").lower() == "true"
    strict_runtime_validation: bool = os.getenv("STRICT_RUNTIME_VALIDATION", "false").lower() == "true"
    enable_rate_limiting: bool = os.getenv("ENABLE_RATE_LIMITING", "false").lower() == "true"
    rate_limit_window_seconds: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    rate_limit_auth_requests: int = int(os.getenv("RATE_LIMIT_AUTH_REQUESTS", "10"))
    rate_limit_admin_requests: int = int(os.getenv("RATE_LIMIT_ADMIN_REQUESTS", "180"))
    rate_limit_incoming_requests: int = int(os.getenv("RATE_LIMIT_INCOMING_REQUESTS", "120"))
    rate_limit_webhook_requests: int = int(os.getenv("RATE_LIMIT_WEBHOOK_REQUESTS", "300"))
    rate_limit_exempt_localhost: bool = os.getenv("RATE_LIMIT_EXEMPT_LOCALHOST", "false").lower() == "true"
    trust_proxy_headers: bool = os.getenv(
        "TRUST_PROXY_HEADERS",
        "true" if os.getenv("VERCEL") else "false",
    ).lower() == "true"
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///project_wapp.db")
    postgres_database_url: str | None = os.getenv("POSTGRES_DATABASE_URL")
    database_pool_size: int = int(os.getenv("DATABASE_POOL_SIZE", "10"))
    database_statement_timeout_ms: int = int(os.getenv("DATABASE_STATEMENT_TIMEOUT_MS", "5000"))
    run_migrations_on_startup: bool = os.getenv(
        "RUN_MIGRATIONS_ON_STARTUP",
        "false" if os.getenv("VERCEL") else "true",
    ).lower() == "true"
    redis_url: str | None = os.getenv("REDIS_URL")
    require_shared_state: bool = os.getenv("REQUIRE_SHARED_STATE", "false").lower() == "true"
    realtime_redis_channel: str = os.getenv(
        "REALTIME_REDIS_CHANNEL",
        "project_wapp:realtime:v1",
    )
    operations_backup_dir: str = os.getenv("OPERATIONS_BACKUP_DIR", "backups")
    enable_admin_auth: bool = os.getenv("ENABLE_ADMIN_AUTH", "false").lower() == "true"
    admin_api_token: str | None = os.getenv("ADMIN_API_TOKEN")
    admin_viewer_token: str | None = os.getenv("ADMIN_VIEWER_TOKEN")
    admin_operator_token: str | None = os.getenv("ADMIN_OPERATOR_TOKEN")
    admin_superadmin_token: str | None = os.getenv("ADMIN_SUPERADMIN_TOKEN")
    # Optional per-role tenant binding for platform sessions. Empty means "all companies".
    admin_viewer_company_ids: str | None = os.getenv("ADMIN_VIEWER_COMPANY_IDS")
    admin_operator_company_ids: str | None = os.getenv("ADMIN_OPERATOR_COMPANY_IDS")
    admin_superadmin_company_ids: str | None = os.getenv("ADMIN_SUPERADMIN_COMPANY_IDS")
    enable_normalized_whatsapp_ingress: bool = os.getenv(
        "ENABLE_NORMALIZED_WHATSAPP_INGRESS",
        "false",
    ).lower() == "true"
    internal_ingress_secret: str | None = os.getenv("INTERNAL_INGRESS_SECRET")
    platform_session_secret: str | None = os.getenv("PLATFORM_SESSION_SECRET")
    platform_session_ttl_seconds: int = int(os.getenv("PLATFORM_SESSION_TTL_SECONDS", "28800"))
    identity_session_ttl_seconds: int = int(os.getenv("IDENTITY_SESSION_TTL_SECONDS", "43200"))
    identity_invitation_ttl_hours: int = int(os.getenv("IDENTITY_INVITATION_TTL_HOURS", "72"))
    identity_password_reset_ttl_minutes: int = int(
        os.getenv("IDENTITY_PASSWORD_RESET_TTL_MINUTES", "30")
    )
    enable_self_service_signup: bool = os.getenv(
        "ENABLE_SELF_SERVICE_SIGNUP",
        "false",
    ).lower() == "true"
    identity_expose_action_tokens: bool = os.getenv(
        "IDENTITY_EXPOSE_ACTION_TOKENS",
        "false",
    ).lower() == "true"
    platform_public_url: str | None = os.getenv("PLATFORM_PUBLIC_URL")
    transactional_email_provider: str = os.getenv(
        "TRANSACTIONAL_EMAIL_PROVIDER",
        "disabled",
    ).strip().lower()
    transactional_email_from: str | None = os.getenv("TRANSACTIONAL_EMAIL_FROM")
    resend_api_key: str | None = os.getenv("RESEND_API_KEY")
    transactional_email_timeout_seconds: int = int(
        os.getenv("TRANSACTIONAL_EMAIL_TIMEOUT_SECONDS", "10")
    )
    stripe_secret_key: str | None = os.getenv("STRIPE_SECRET_KEY")
    stripe_webhook_secret: str | None = os.getenv("STRIPE_WEBHOOK_SECRET")
    stripe_publishable_key: str | None = os.getenv("STRIPE_PUBLISHABLE_KEY")
    stripe_success_url: str | None = os.getenv("STRIPE_SUCCESS_URL")
    stripe_cancel_url: str | None = os.getenv("STRIPE_CANCEL_URL")
    stripe_portal_return_url: str | None = os.getenv("STRIPE_PORTAL_RETURN_URL")
    stripe_price_essencial_monthly: str | None = os.getenv("STRIPE_PRICE_ESSENCIAL_MONTHLY")
    stripe_price_essencial_annual: str | None = os.getenv("STRIPE_PRICE_ESSENCIAL_ANNUAL")
    stripe_price_avancado_monthly: str | None = os.getenv("STRIPE_PRICE_AVANCADO_MONTHLY")
    stripe_price_avancado_annual: str | None = os.getenv("STRIPE_PRICE_AVANCADO_ANNUAL")
    stripe_price_premium_monthly: str | None = os.getenv("STRIPE_PRICE_PREMIUM_MONTHLY")
    stripe_price_premium_annual: str | None = os.getenv("STRIPE_PRICE_PREMIUM_ANNUAL")
    billing_grace_period_days: int = int(os.getenv("BILLING_GRACE_PERIOD_DAYS", "3"))
    stripe_webhook_claim_ttl_seconds: int = int(
        os.getenv("STRIPE_WEBHOOK_CLAIM_TTL_SECONDS", "300")
    )
    enable_billing_enforcement: bool = os.getenv(
        "ENABLE_BILLING_ENFORCEMENT",
        "false",
    ).lower() == "true"
    audit_retention_days: int = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))
    reminder_worker_enabled: bool = os.getenv("REMINDER_WORKER_ENABLED", "false").lower() == "true"
    reminder_worker_poll_seconds: int = int(os.getenv("REMINDER_WORKER_POLL_SECONDS", "60"))
    reminder_worker_batch_size: int = int(os.getenv("REMINDER_WORKER_BATCH_SIZE", "100"))
    reminder_worker_dispatch_immediately: bool = os.getenv(
        "REMINDER_WORKER_DISPATCH_IMMEDIATELY",
        "true",
    ).lower() == "true"
    reminder_worker_channel: str | None = os.getenv("REMINDER_WORKER_CHANNEL")
    reminder_worker_audiences: str = os.getenv("REMINDER_WORKER_AUDIENCES", "customer,internal")
    appointment_queue_auto_dispatch: bool = os.getenv("APPOINTMENT_QUEUE_AUTO_DISPATCH", "true").lower() == "true"
    appointment_queue_response_window_minutes: int = int(os.getenv("APPOINTMENT_QUEUE_RESPONSE_WINDOW_MINUTES", "30"))
    appointment_queue_wait_followup_minutes: int = int(os.getenv("APPOINTMENT_QUEUE_WAIT_FOLLOWUP_MINUTES", "30"))
    admin_app_api_base_url: str = os.getenv("ADMIN_APP_API_BASE_URL", "http://127.0.0.1:8000")

    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_finetuned_model: str | None = os.getenv("OPENAI_FINETUNED_MODEL")
    use_finetuned_model_for_replies: bool = os.getenv("USE_FINETUNED_MODEL_FOR_REPLIES", "false").lower() == "true"
    openai_finetuning_base_model: str = os.getenv("OPENAI_FINETUNING_BASE_MODEL", "gpt-4o-mini-2024-07-18")
    openai_finetuning_suffix: str = os.getenv("OPENAI_FINETUNING_SUFFIX", "project-wapp-style")
    enable_finetuning_pipeline: bool = os.getenv("ENABLE_FINETUNING_PIPELINE", "false").lower() == "true"
    classification_provider: str = os.getenv("CLASSIFICATION_PROVIDER", "mock")
    outbound_provider: str = os.getenv("OUTBOUND_PROVIDER", "mock")
    outbound_default_channel: str = os.getenv("OUTBOUND_DEFAULT_CHANNEL", "whatsapp")
    outbound_max_attempts: int = int(os.getenv("OUTBOUND_MAX_ATTEMPTS", "5"))
    outbound_retry_base_seconds: int = int(os.getenv("OUTBOUND_RETRY_BASE_SECONDS", "30"))
    outbound_retry_max_seconds: int = int(os.getenv("OUTBOUND_RETRY_MAX_SECONDS", "3600"))
    outbound_claim_ttl_seconds: int = int(os.getenv("OUTBOUND_CLAIM_TTL_SECONDS", "300"))
    worker_lease_ttl_seconds: int = int(os.getenv("WORKER_LEASE_TTL_SECONDS", "300"))
    calendar_provider: str = os.getenv("CALENDAR_PROVIDER", "mock")
    calendar_timezone: str = os.getenv("CALENDAR_TIMEZONE", "America/Sao_Paulo")
    calendar_default_workday_start: str = os.getenv("CALENDAR_DEFAULT_WORKDAY_START", "09:00")
    calendar_default_workday_end: str = os.getenv("CALENDAR_DEFAULT_WORKDAY_END", "18:00")
    calendar_slot_increment_minutes: int = int(os.getenv("CALENDAR_SLOT_INCREMENT_MINUTES", "30"))
    google_calendar_client_id: str | None = os.getenv("GOOGLE_CALENDAR_CLIENT_ID")
    google_calendar_client_secret: str | None = os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET")
    google_calendar_refresh_token: str | None = os.getenv("GOOGLE_CALENDAR_REFRESH_TOKEN")
    google_calendar_id: str | None = os.getenv("GOOGLE_CALENDAR_ID")
    caldav_url: str | None = os.getenv("CALDAV_URL")
    caldav_username: str | None = os.getenv("CALDAV_USERNAME")
    caldav_password: str | None = os.getenv("CALDAV_PASSWORD")
    caldav_calendar_name: str | None = os.getenv("CALDAV_CALENDAR_NAME")

    whatsapp_api_base_url: str | None = os.getenv("WHATSAPP_API_BASE_URL")
    whatsapp_api_token: str | None = os.getenv("WHATSAPP_API_TOKEN")
    whatsapp_phone_number_id: str | None = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_webhook_verify_token: str | None = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN")
    whatsapp_app_secret: str | None = os.getenv("WHATSAPP_APP_SECRET")

    # --- Recuperação de conhecimento (RAG) ---
    #
    # O RAG funciona sem provider de embeddings: a busca lexical é
    # determinística e roda igual em SQLite e PostgreSQL. Vetorial é uma
    # extensão opcional, não um pré-requisito — exigir provider externo para
    # responder com a base da empresa acoplaria o atendimento a um serviço que
    # pode estar fora do ar.
    knowledge_retrieval_enabled: bool = os.getenv(
        "KNOWLEDGE_RETRIEVAL_ENABLED", "true"
    ).lower() == "true"
    knowledge_max_chunks: int = int(os.getenv("KNOWLEDGE_MAX_CHUNKS", "6"))
    # Piso de relevância. Abaixo dele a fonte não entra no prompt e a resposta
    # sinaliza ausência de fonte, em vez de inventar a partir de ruído.
    knowledge_min_score: float = float(os.getenv("KNOWLEDGE_MIN_SCORE", "0.05"))
    embeddings_provider: str = os.getenv("EMBEDDINGS_PROVIDER", "none")
    embeddings_model: str = os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-small")
    enable_vector_search: bool = os.getenv("ENABLE_VECTOR_SEARCH", "false").lower() == "true"
    # Distância de Levenshtein abaixo da qual dois textos contam como o mesmo
    # exemplo. Deduplicar importa para treino: exemplo repetido vira peso
    # duplicado, não sinal novo.
    near_duplicate_max_distance: int = int(os.getenv("NEAR_DUPLICATE_MAX_DISTANCE", "4"))

    # --- Importação de conversas ---
    import_max_bytes: int = int(os.getenv("IMPORT_MAX_BYTES", "5242880"))
    import_max_messages_per_batch: int = int(
        os.getenv("IMPORT_MAX_MESSAGES_PER_BATCH", "20000")
    )
    # Silêncio maior que isto encerra a sessão: é o que separa duas conversas
    # do mesmo contato em vez de fundi-las num diálogo que nunca existiu.
    import_session_gap_minutes: int = int(os.getenv("IMPORT_SESSION_GAP_MINUTES", "360"))
    import_max_context_turns: int = int(os.getenv("IMPORT_MAX_CONTEXT_TURNS", "6"))
    import_max_context_chars: int = int(os.getenv("IMPORT_MAX_CONTEXT_CHARS", "2000"))

    # --- Prontidão de fine-tuning ---
    #
    # Os pisos existem para recusar treino que não pode dar certo: poucos
    # exemplos, poucos cenários ou cenários desbalanceados produzem um modelo
    # pior que o base, e o custo do job é pago de qualquer forma.
    finetuning_min_examples: int = int(os.getenv("FINETUNING_MIN_EXAMPLES", "50"))
    finetuning_min_scenarios: int = int(os.getenv("FINETUNING_MIN_SCENARIOS", "3"))
    finetuning_min_examples_per_scenario: int = int(
        os.getenv("FINETUNING_MIN_EXAMPLES_PER_SCENARIO", "5")
    )
    finetuning_min_eval_pass_rate: float = float(
        os.getenv("FINETUNING_MIN_EVAL_PASS_RATE", "0.7")
    )
    # Avaliação real chama o modelo; simulada não. Uma execução simulada nunca
    # pode habilitar promoção — é o que `promotion_eligible` registra.
    enable_real_model_evaluation: bool = os.getenv(
        "ENABLE_REAL_MODEL_EVALUATION", "false"
    ).lower() == "true"
    # Válvula de escape para promover sem passar no portão. Existe porque
    # emergência acontece, e nasce desligada porque promover às cegas é
    # exatamente o que o portão previne.
    allow_promotion_gate_override: bool = os.getenv(
        "ALLOW_PROMOTION_GATE_OVERRIDE", "false"
    ).lower() == "true"

    # --- Proteção de PII ---
    #
    # A chave de pseudonimização é o que torna o pseudônimo irreversível: sem
    # ela, um pseudônimo derivado só do valor é recuperável por enumeração —
    # basta calcular o hash de todos os CPFs possíveis. Por isso
    # `resolve_pseudonymization_key()` exige a chave em produção e só aceita
    # fallback fora dela.
    pii_pseudonymization_key: str | None = os.getenv("PII_PSEUDONYMIZATION_KEY")
    pii_pseudonymization_key_version: str = os.getenv("PII_PSEUDONYMIZATION_KEY_VERSION", "v1")

    # --- Automation Studio ---
    #
    # O motor nasce desligado. Ativar automação é decisão de operação, não
    # efeito colateral de um deploy que trouxe as migrations: um fluxo mal
    # publicado que comece a executar sozinho na subida é o modo de falha que
    # este default previne.
    automation_engine_enabled: bool = os.getenv(
        "AUTOMATION_ENGINE_ENABLED", "false"
    ).lower() == "true"
    # Teto de passos por execução. Sem ele, um grafo com ciclo — que a
    # validação recusa, mas um bug futuro poderia deixar passar — roda para
    # sempre consumindo worker e orçamento.
    automation_max_steps_per_execution: int = int(
        os.getenv("AUTOMATION_MAX_STEPS_PER_EXECUTION", "50")
    )
    automation_max_attempts: int = int(os.getenv("AUTOMATION_MAX_ATTEMPTS", "5"))
    # Backoff exponencial entre tentativas, com teto.
    automation_retry_base_seconds: int = int(os.getenv("AUTOMATION_RETRY_BASE_SECONDS", "10"))
    automation_retry_max_seconds: int = int(os.getenv("AUTOMATION_RETRY_MAX_SECONDS", "900"))
    # Lease de job: quanto tempo um worker segura o trabalho antes de outro
    # poder retomá-lo. Curto demais duplica trabalho; longo demais atrasa a
    # recuperação quando um worker morre.
    automation_job_lease_seconds: int = int(os.getenv("AUTOMATION_JOB_LEASE_SECONDS", "120"))
    automation_gate_lease_seconds: int = int(os.getenv("AUTOMATION_GATE_LEASE_SECONDS", "300"))
    # Limite de tamanho do evento: o payload é persistido e relido a cada
    # retry, então um evento gigante multiplica custo de I/O por tentativa.
    automation_max_event_bytes: int = int(os.getenv("AUTOMATION_MAX_EVENT_BYTES", "65536"))
    # O worker é um processo separado e também nasce desligado: ligar o
    # motor não pode, sozinho, pôr uma máquina a executar trabalho.
    automation_worker_enabled: bool = os.getenv(
        "AUTOMATION_WORKER_ENABLED", "false"
    ).lower() == "true"
    automation_worker_poll_seconds: int = int(os.getenv("AUTOMATION_WORKER_POLL_SECONDS", "5"))
    automation_worker_batch_size: int = int(os.getenv("AUTOMATION_WORKER_BATCH_SIZE", "10"))


def is_production_like() -> bool:
    """Ambiente onde fallback permissivo é proibido.

    `strict_runtime_validation` entra junto de propósito: staging que roda com
    validação estrita quer o mesmo rigor de produção, e descobrir uma chave
    ausente em staging é muito mais barato do que descobri-la em produção.
    """
    return settings.environment.strip().lower() == "production" or settings.strict_runtime_validation


settings = Settings()
