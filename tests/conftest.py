"""Global pytest setup: isolate environment and bootstrap the test database.

The app reads configuration from environment variables at import time (see
``app.core.config.Settings``), so we must pin env vars BEFORE any ``app.*``
module is imported. For the same reason, ``.env`` is loaded with
``override=False`` by ``app.core.config``, meaning the values we set here win
over whatever ``.env`` contains.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_ENV_DEFAULTS: dict[str, str] = {
    "ENVIRONMENT": "test",
    "STRICT_RUNTIME_VALIDATION": "false",
    "ENABLE_RATE_LIMITING": "false",
    "ENABLE_ADMIN_AUTH": "false",
    "OUTBOUND_PROVIDER": "mock",
    "CLASSIFICATION_PROVIDER": "mock",
    "CALENDAR_PROVIDER": "mock",
    "REMINDER_WORKER_ENABLED": "false",
}
for key, value in _TEST_ENV_DEFAULTS.items():
    os.environ[key] = value

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
    if _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()


@pytest.fixture(autouse=True)
def _reset_rate_limit_between_tests() -> None:
    reset_rate_limit_state()
    yield
    reset_rate_limit_state()
