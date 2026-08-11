"""Global pytest setup: isolate environment and bootstrap the test database.

The app reads configuration from environment variables at import time (see
``app.core.config.Settings``), so we must pin env vars BEFORE any ``app.*``
module is imported. ``DISABLE_DOTENV=true`` also stops ``app.core.config`` from
loading ``.env`` at all, so the suite never inherits a developer's local
secrets or a production-shaped configuration.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_ENV_DEFAULTS: dict[str, str] = {
    "DISABLE_DOTENV": "true",
    "ENVIRONMENT": "test",
    "STRICT_RUNTIME_VALIDATION": "false",
    "ENABLE_RATE_LIMITING": "false",
    "ENABLE_ADMIN_AUTH": "false",
    "OUTBOUND_PROVIDER": "mock",
    "CLASSIFICATION_PROVIDER": "mock",
    "CALENDAR_PROVIDER": "mock",
    "REMINDER_WORKER_ENABLED": "false",
    "ENABLE_NORMALIZED_WHATSAPP_INGRESS": "false",
    "PLATFORM_SESSION_SECRET": "test-platform-session-secret",
}
for key, value in _TEST_ENV_DEFAULTS.items():
    os.environ[key] = value

# Security-relevant variables must never be inherited from a developer's .env;
# each test that needs one sets it explicitly (usually via monkeypatch).
_TEST_ENV_CLEARED: tuple[str, ...] = (
    "ADMIN_API_TOKEN",
    "ADMIN_VIEWER_TOKEN",
    "ADMIN_OPERATOR_TOKEN",
    "ADMIN_SUPERADMIN_TOKEN",
    "ADMIN_VIEWER_COMPANY_IDS",
    "ADMIN_OPERATOR_COMPANY_IDS",
    "ADMIN_SUPERADMIN_COMPANY_IDS",
    "INTERNAL_INGRESS_SECRET",
    "OPENAI_API_KEY",
    "WHATSAPP_API_BASE_URL",
    "WHATSAPP_API_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
    "GOOGLE_CALENDAR_CLIENT_ID",
    "GOOGLE_CALENDAR_CLIENT_SECRET",
    "GOOGLE_CALENDAR_REFRESH_TOKEN",
    "GOOGLE_CALENDAR_ID",
    "CALDAV_URL",
    "CALDAV_USERNAME",
    "CALDAV_PASSWORD",
)
for key in _TEST_ENV_CLEARED:
    os.environ.pop(key, None)

# Set TEST_DATABASE_URL to run the same suite against PostgreSQL; the default is
# a throwaway SQLite file so the suite needs no external service.
_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
_TEST_DB_PATH: Path | None = None
if _TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
else:
    _TEST_DB_PATH = Path(tempfile.gettempdir()) / "project_wapp_test.db"
    if _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()
    os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ.pop("POSTGRES_DATABASE_URL", None)

import pytest  # noqa: E402

from app.services.rate_limit_service import reset_rate_limit_state  # noqa: E402
from app.storage.db import init_db  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _bootstrap_test_database() -> "pytest.FixtureRequest":
    init_db()
    yield
    if _TEST_DB_PATH is not None and _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()


@pytest.fixture(autouse=True)
def _reset_rate_limit_between_tests() -> None:
    reset_rate_limit_state()
    yield
    reset_rate_limit_state()
