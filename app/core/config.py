import os

from dotenv import load_dotenv

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
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///project_wapp.db")
    postgres_database_url: str | None = os.getenv("POSTGRES_DATABASE_URL")
    database_pool_size: int = int(os.getenv("DATABASE_POOL_SIZE", "10"))
    database_statement_timeout_ms: int = int(os.getenv("DATABASE_STATEMENT_TIMEOUT_MS", "5000"))
    operations_backup_dir: str = os.getenv("OPERATIONS_BACKUP_DIR", "backups")
    enable_admin_auth: bool = os.getenv("ENABLE_ADMIN_AUTH", "false").lower() == "true"
    admin_api_token: str | None = os.getenv("ADMIN_API_TOKEN")
    admin_viewer_token: str | None = os.getenv("ADMIN_VIEWER_TOKEN")
    admin_operator_token: str | None = os.getenv("ADMIN_OPERATOR_TOKEN")
    admin_superadmin_token: str | None = os.getenv("ADMIN_SUPERADMIN_TOKEN")
    platform_session_secret: str | None = os.getenv("PLATFORM_SESSION_SECRET")
    platform_session_ttl_seconds: int = int(os.getenv("PLATFORM_SESSION_TTL_SECONDS", "28800"))
    audit_retention_days: int = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))
    reminder_worker_enabled: bool = os.getenv("REMINDER_WORKER_ENABLED", "false").lower() == "true"
    reminder_worker_poll_seconds: int = int(os.getenv("REMINDER_WORKER_POLL_SECONDS", "60"))
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

    # --- Multiempresa: resolução de tenant e superfícies públicas ---
    # Aceitar company_id/company_slug informados pelo chamador só é seguro fora
    # de produção. Em produção o tenant vem do binding do canal.
    allow_untrusted_tenant_hints: bool = os.getenv("ALLOW_UNTRUSTED_TENANT_HINTS", "true").lower() == "true"
    # Payload normalizado do webhook não tem assinatura do Meta.
    allow_normalized_whatsapp_webhook: bool = os.getenv(
        "ALLOW_NORMALIZED_WHATSAPP_WEBHOOK",
        "true",
    ).lower() == "true"

    # --- Knowledge / RAG ---
    knowledge_retrieval_enabled: bool = os.getenv("KNOWLEDGE_RETRIEVAL_ENABLED", "true").lower() == "true"
    knowledge_max_chunks: int = int(os.getenv("KNOWLEDGE_MAX_CHUNKS", "6"))
    knowledge_min_score: float = float(os.getenv("KNOWLEDGE_MIN_SCORE", "0.05"))
    # `none` mantém a recuperação lexical determinística; `openai` habilita embeddings.
    embeddings_provider: str = os.getenv("EMBEDDINGS_PROVIDER", "none")
    embeddings_model: str = os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-small")
    enable_vector_search: bool = os.getenv("ENABLE_VECTOR_SEARCH", "false").lower() == "true"

    # --- Importação e governança de dataset ---
    import_max_bytes: int = int(os.getenv("IMPORT_MAX_BYTES", "5242880"))
    import_max_messages_per_batch: int = int(os.getenv("IMPORT_MAX_MESSAGES_PER_BATCH", "20000"))
    import_session_gap_minutes: int = int(os.getenv("IMPORT_SESSION_GAP_MINUTES", "360"))

    # --- Fine-tuning readiness ---
    finetuning_min_examples: int = int(os.getenv("FINETUNING_MIN_EXAMPLES", "50"))
    finetuning_min_scenarios: int = int(os.getenv("FINETUNING_MIN_SCENARIOS", "3"))
    finetuning_min_examples_per_scenario: int = int(os.getenv("FINETUNING_MIN_EXAMPLES_PER_SCENARIO", "5"))
    finetuning_min_eval_pass_rate: float = float(os.getenv("FINETUNING_MIN_EVAL_PASS_RATE", "0.7"))

    # --- Estado compartilhado entre workers ---
    # `memory` é local ao processo e só é adequado para desenvolvimento/testes.
    shared_state_backend: str = os.getenv("SHARED_STATE_BACKEND", "memory")
    redis_url: str | None = os.getenv("REDIS_URL")

    # --- Identidade e autorização por tenant ---
    # Ingestão sem credencial só é aceitável em desenvolvimento local, e mesmo
    # assim o tenant nunca vem do chamador: cai na empresa padrão.
    allow_unauthenticated_ingest: bool = os.getenv(
        "ALLOW_UNAUTHENTICATED_INGEST",
        "false",
    ).lower() == "true"
    # Tokens administrativos legados por papel são globais por natureza.
    # Só o de superadmin permanece global; `viewer` e `operator` legados ficam
    # sem empresa até receberem membership explícita. Ambientes de
    # desenvolvimento podem reabrir isso de forma consciente.
    allow_legacy_global_admin_tokens: bool = os.getenv(
        "ALLOW_LEGACY_GLOBAL_ADMIN_TOKENS",
        "false",
    ).lower() == "true"

    # --- Pseudonimização ---
    pii_pseudonymization_key: str | None = os.getenv("PII_PSEUDONYMIZATION_KEY")
    pii_pseudonymization_key_version: str = os.getenv("PII_PSEUDONYMIZATION_KEY_VERSION", "v1")

    # --- Contexto multi-turno ---
    import_max_context_turns: int = int(os.getenv("IMPORT_MAX_CONTEXT_TURNS", "6"))
    import_max_context_chars: int = int(os.getenv("IMPORT_MAX_CONTEXT_CHARS", "2000"))

    # --- Deduplicação ---
    # Distância de Hamming máxima entre SimHashes de 64 bits para considerar
    # dois pares como near-duplicates.
    near_duplicate_max_distance: int = int(os.getenv("NEAR_DUPLICATE_MAX_DISTANCE", "4"))

    # --- Avaliação ---
    # Avaliação model-backed executa o modelo real e é a única que pode
    # liberar promoção. Desabilitada por padrão: CI nunca chama provider.
    enable_real_model_evaluation: bool = os.getenv(
        "ENABLE_REAL_MODEL_EVALUATION",
        "false",
    ).lower() == "true"
    # Override emergencial do gate de promoção. Desabilitado por padrão.
    allow_promotion_gate_override: bool = os.getenv(
        "ALLOW_PROMOTION_GATE_OVERRIDE",
        "false",
    ).lower() == "true"

    # --- Automation Studio ---
    # Desligado por padrão, nos dois planos. Nenhuma empresa existente passa a
    # executar automação por causa de um deploy: habilitar é decisão explícita
    # e, mesmo habilitado, cada workflow ainda precisa ser ativado.
    automation_engine_enabled: bool = os.getenv(
        "AUTOMATION_ENGINE_ENABLED",
        "false",
    ).lower() == "true"
    automation_worker_enabled: bool = os.getenv(
        "AUTOMATION_WORKER_ENABLED",
        "false",
    ).lower() == "true"
    # Teto de passos por execução: um grafo acíclico já termina, mas o limite
    # protege contra grafo grande e contra bug de avanço.
    automation_max_steps_per_execution: int = int(
        os.getenv("AUTOMATION_MAX_STEPS_PER_EXECUTION", "50")
    )
    automation_max_attempts: int = int(os.getenv("AUTOMATION_MAX_ATTEMPTS", "5"))
    automation_job_lease_seconds: int = int(os.getenv("AUTOMATION_JOB_LEASE_SECONDS", "60"))
    automation_gate_lease_seconds: int = int(os.getenv("AUTOMATION_GATE_LEASE_SECONDS", "300"))
    automation_execution_retention_days: int = int(
        os.getenv("AUTOMATION_EXECUTION_RETENTION_DAYS", "90")
    )
    automation_max_event_bytes: int = int(os.getenv("AUTOMATION_MAX_EVENT_BYTES", "16000"))
    automation_worker_poll_seconds: float = float(
        os.getenv("AUTOMATION_WORKER_POLL_SECONDS", "2.0")
    )
    automation_retry_base_seconds: int = int(os.getenv("AUTOMATION_RETRY_BASE_SECONDS", "10"))
    automation_retry_max_seconds: int = int(os.getenv("AUTOMATION_RETRY_MAX_SECONDS", "3600"))


settings = Settings()


def is_production_like() -> bool:
    """Ambiente onde fallbacks permissivos são proibidos."""
    return settings.environment.strip().lower() == "production" or settings.strict_runtime_validation
