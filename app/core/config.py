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


settings = Settings()
