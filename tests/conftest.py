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

import pytest

from app.services.rate_limit_service import reset_rate_limit_state
from app.storage.db import init_db


@pytest.fixture(autouse=True, scope="session")
def _bootstrap_test_database() -> pytest.FixtureRequest:
    init_db()
    yield
    if _TEST_DB_PATH is not None and _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()


@pytest.fixture(autouse=True)
def _reset_rate_limit_between_tests() -> None:
    reset_rate_limit_state()
    yield
    reset_rate_limit_state()


@pytest.fixture()
def company_factory():
    """Cria empresas isoladas por teste, com slug único.

    Slug derivado de uuid4 e não de um contador: testes que rodam em paralelo
    ou que reusam o mesmo banco entre arquivos colidiriam num contador, e a
    colisão aparece como violação de índice único num teste que nada tem a ver
    com o assunto.
    """
    from uuid import uuid4

    from app.storage.repository import RuntimeRepository

    repository = RuntimeRepository()

    def _create(prefix: str = "tenant", business_type: str | None = "general") -> int:
        suffix = uuid4().hex[:12]
        return repository.create_company(
            slug=f"{prefix}-{suffix}",
            name=f"{prefix.title()} {suffix}",
            business_type=business_type,
        )

    return _create


@pytest.fixture()
def admin_principal_factory():
    """Cria um principal administrativo canônico: usuário + membership + sessão.

    O modelo canônico não tem token por principal — quem administra é uma
    pessoa (`saas_users`) com papel *na empresa* (`company_memberships`), e a
    credencial é uma sessão opaca com CSRF. A fábrica devolve o que uma
    requisição autenticada precisa: o cookie de sessão e o token CSRF.

    `global_role` é traduzido para o vocabulário canônico porque é assim que a
    autorização de rota raciocina: `superadmin` global vira `owner` na empresa,
    e não um papel de plataforma paralelo aos memberships.
    """
    from uuid import uuid4

    from app.services.identity_service import hash_password, issue_user_session
    from app.storage.db import db_transaction

    role_translation = {"superadmin": "owner", "admin": "admin", "operator": "operator", "viewer": "viewer"}

    def _create(
        *,
        global_role: str = "viewer",
        company_ids: list[int] | None = None,
        is_global: bool = False,
        company_roles: dict[int, str] | None = None,
    ) -> dict:
        suffix = uuid4().hex[:10]
        identity_role = role_translation.get(global_role, "viewer")

        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO saas_users (
                    email, email_normalized, display_name, password_hash, active
                )
                VALUES (?, ?, ?, ?, 1)
                """,
                (
                    f"{global_role}-{suffix}@example.com",
                    f"{global_role}-{suffix}@example.com",
                    f"{global_role} {suffix}",
                    hash_password(f"Sess-{suffix}-Aa1!"),
                ),
            )
            user_id = int(cursor.lastrowid)

            memberships = {int(cid): identity_role for cid in (company_ids or [])}
            # `company_roles` sobrepõe o papel dentro de uma empresa: é o que
            # permite testar o papel efetivo — um superadmin com membership
            # `viewer` numa empresa tem de ser recusado ali.
            memberships.update({int(cid): role for cid, role in (company_roles or {}).items()})
            for cid, role in memberships.items():
                cursor.execute(
                    """
                    INSERT INTO company_memberships (user_id, company_id, role, active)
                    VALUES (?, ?, ?, 1)
                    """,
                    (user_id, cid, role),
                )

        session = issue_user_session(user_id=user_id)
        return {
            "user_id": user_id,
            "identity_role": identity_role,
            "is_global": is_global,
            "company_ids": sorted(memberships),
            "token": session["token"],
            "csrf": session["csrf_token"],
        }

    return _create
