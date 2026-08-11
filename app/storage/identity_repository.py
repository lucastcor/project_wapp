"""Persistência de principals administrativos e credenciais de tenant.

Nenhuma função devolve token em claro: a emissão acontece no serviço e só o
hash chega aqui.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.core.tenancy import require_company_id
from app.storage.sql_support import execute, execute_rowcount, fetch_all, fetch_one

GLOBAL_ROLES = ("viewer", "operator", "superadmin")

_PRINCIPAL_COLUMNS = """
    id,
    principal_key,
    display_name,
    global_role,
    is_global,
    active,
    auth_epoch,
    created_by,
    created_at,
    updated_at
"""

_ACCESS_RECORD_COLUMNS = """
    id,
    principal_id,
    token_hash,
    token_prefix,
    description,
    active,
    created_by,
    created_at,
    expires_at,
    revoked_at,
    last_used_at
"""

_CREDENTIAL_COLUMNS = """
    id,
    company_id,
    token_hash,
    token_prefix,
    description,
    scopes_json,
    active,
    created_by,
    created_at,
    expires_at,
    revoked_at,
    last_used_at
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_expired(expires_at: Any) -> bool:
    if not expires_at:
        return False
    raw = str(expires_at)
    for candidate in (raw, raw.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed <= datetime.now(UTC)
    return False


def is_usable(row: dict[str, Any] | None) -> bool:
    """Credencial utilizável: ativa, não revogada e não expirada."""
    if not row:
        return False
    if not int(row.get("active") or 0):
        return False
    if row.get("revoked_at"):
        return False
    return not _is_expired(row.get("expires_at"))


# --- principals ----------------------------------------------------------------


def create_principal(
    *,
    principal_key: str,
    display_name: str,
    global_role: str,
    is_global: bool = False,
    created_by: str | None = None,
) -> int:
    if global_role not in GLOBAL_ROLES:
        raise ValueError(f"Invalid global_role: {global_role}")
    return execute(
        """
        INSERT INTO platform_principals (
            principal_key,
            display_name,
            global_role,
            is_global,
            active,
            created_by
        )
        VALUES (?, ?, ?, ?, 1, ?)
        """,
        (principal_key, display_name, global_role, int(is_global), created_by),
    )


def get_principal(principal_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT {_PRINCIPAL_COLUMNS} FROM platform_principals WHERE id = ?",
        (principal_id,),
    )


def get_principal_by_key(principal_key: str) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT {_PRINCIPAL_COLUMNS} FROM platform_principals WHERE principal_key = ?",
        (principal_key,),
    )


def list_principals() -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT {_PRINCIPAL_COLUMNS} FROM platform_principals ORDER BY id DESC"
    )


def deactivate_principal(principal_id: int) -> bool:
    return set_principal_active(principal_id=principal_id, active=False)


def set_principal_active(*, principal_id: int, active: bool) -> bool:
    """Ativa/desativa o principal e move a época de autenticação.

    Mover a época é o que faz as sessões já emitidas morrerem junto. Sem
    isso, desativar o principal só valia para novas autenticações.
    """
    return (
        execute_rowcount(
            """
            UPDATE platform_principals
            SET active = ?,
                auth_epoch = auth_epoch + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(bool(active)), principal_id),
        )
        > 0
    )


def bump_auth_epoch(*, principal_id: int) -> int:
    """Invalida todas as sessões do principal sem mexer nas credenciais."""
    execute_rowcount(
        """
        UPDATE platform_principals
        SET auth_epoch = auth_epoch + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (principal_id,),
    )
    row = fetch_one("SELECT auth_epoch FROM platform_principals WHERE id = ?", (principal_id,))
    return int((row or {}).get("auth_epoch") or 0)


def delete_principal(*, principal_id: int) -> bool:
    """Remove o principal e tudo que dependia dele.

    Tokens e memberships saem junto: deixar credencial órfã apontando para um
    principal inexistente é exatamente o caso que caía em principal legado.
    """
    execute_rowcount(
        "DELETE FROM platform_access_tokens WHERE principal_id = ?",
        (principal_id,),
    )
    execute_rowcount(
        "DELETE FROM platform_company_memberships WHERE principal_id = ?",
        (principal_id,),
    )
    return (
        execute_rowcount("DELETE FROM platform_principals WHERE id = ?", (principal_id,)) > 0
    )


# --- tokens administrativos -------------------------------------------------------


def create_access_token(
    *,
    principal_id: int,
    token_hash: str,
    token_prefix: str,
    description: str | None = None,
    expires_at: str | None = None,
    created_by: str | None = None,
) -> int:
    return execute(
        """
        INSERT INTO platform_access_tokens (
            principal_id,
            token_hash,
            token_prefix,
            description,
            active,
            created_by,
            expires_at
        )
        VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (principal_id, token_hash, token_prefix, description, created_by, expires_at),
    )


def get_token_by_hash(token_hash: str) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT {_ACCESS_RECORD_COLUMNS} FROM platform_access_tokens WHERE token_hash = ?",
        (token_hash,),
    )


def get_access_token(token_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT {_ACCESS_RECORD_COLUMNS} FROM platform_access_tokens WHERE id = ?",
        (token_id,),
    )


def list_access_tokens(*, principal_id: int) -> list[dict[str, Any]]:
    """Metadados dos tokens. O hash nunca é exposto por esta função."""
    return fetch_all(
        """
        SELECT id, principal_id, token_prefix, description, active,
               created_by, created_at, expires_at, revoked_at, last_used_at
        FROM platform_access_tokens
        WHERE principal_id = ?
        ORDER BY id DESC
        """,
        (principal_id,),
    )


def revoke_access_token(*, token_id: int) -> bool:
    return (
        execute_rowcount(
            """
            UPDATE platform_access_tokens
            SET active = 0, revoked_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND revoked_at IS NULL
            """,
            (token_id,),
        )
        > 0
    )


def touch_access_token(*, token_id: int) -> None:
    execute(
        "UPDATE platform_access_tokens SET last_used_at = ? WHERE id = ?",
        (_now_iso(), token_id),
    )


# --- memberships --------------------------------------------------------------------


def upsert_membership(
    *,
    principal_id: int,
    company_id: int,
    role: str,
    created_by: str | None = None,
) -> int:
    resolved_company_id = require_company_id(company_id, context="principal membership")
    existing = fetch_one(
        "SELECT id FROM platform_company_memberships WHERE principal_id = ? AND company_id = ?",
        (principal_id, resolved_company_id),
    )
    if existing:
        execute(
            "UPDATE platform_company_memberships SET role = ? WHERE id = ?",
            (role, int(existing["id"])),
        )
        return int(existing["id"])

    return execute(
        """
        INSERT INTO platform_company_memberships (principal_id, company_id, role, created_by)
        VALUES (?, ?, ?, ?)
        """,
        (principal_id, resolved_company_id, role, created_by),
    )


def remove_membership(*, principal_id: int, company_id: int) -> bool:
    return (
        execute_rowcount(
            "DELETE FROM platform_company_memberships WHERE principal_id = ? AND company_id = ?",
            (principal_id, company_id),
        )
        > 0
    )


def get_memberships(*, principal_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT id, principal_id, company_id, role, created_at
        FROM platform_company_memberships
        WHERE principal_id = ?
        ORDER BY company_id ASC
        """,
        (principal_id,),
    )


# --- credenciais de tenant (ingestão) -------------------------------------------------


def create_tenant_credential(
    *,
    company_id: int,
    token_hash: str,
    token_prefix: str,
    scopes: list[str],
    description: str | None = None,
    expires_at: str | None = None,
    created_by: str | None = None,
) -> int:
    resolved_company_id = require_company_id(company_id, context="tenant credential creation")
    return execute(
        """
        INSERT INTO tenant_api_credentials (
            company_id,
            token_hash,
            token_prefix,
            description,
            scopes_json,
            active,
            created_by,
            expires_at
        )
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            resolved_company_id,
            token_hash,
            token_prefix,
            description,
            json.dumps(sorted(set(scopes)), ensure_ascii=False),
            created_by,
            expires_at,
        ),
    )


def get_tenant_credential_by_hash(token_hash: str) -> dict[str, Any] | None:
    row = fetch_one(
        f"SELECT {_CREDENTIAL_COLUMNS} FROM tenant_api_credentials WHERE token_hash = ?",
        (token_hash,),
    )
    if not row:
        return None
    return {**row, "scopes": json.loads(row.get("scopes_json") or "[]")}


def list_tenant_credentials(*, company_id: int) -> list[dict[str, Any]]:
    """Metadados das credenciais. Nunca devolve `token_hash`."""
    resolved_company_id = require_company_id(company_id, context="tenant credential listing")
    rows = fetch_all(
        """
        SELECT id, company_id, token_prefix, description, scopes_json, active,
               created_by, created_at, expires_at, revoked_at, last_used_at
        FROM tenant_api_credentials
        WHERE company_id = ?
        ORDER BY id DESC
        """,
        (resolved_company_id,),
    )
    return [{**row, "scopes": json.loads(row.get("scopes_json") or "[]")} for row in rows]


def revoke_tenant_credential(*, company_id: int, credential_id: int) -> bool:
    resolved_company_id = require_company_id(company_id, context="tenant credential revocation")
    return (
        execute_rowcount(
            """
            UPDATE tenant_api_credentials
            SET active = 0, revoked_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND company_id = ?
              AND revoked_at IS NULL
            """,
            (credential_id, resolved_company_id),
        )
        > 0
    )


def touch_tenant_credential(*, credential_id: int) -> None:
    execute(
        "UPDATE tenant_api_credentials SET last_used_at = ? WHERE id = ?",
        (_now_iso(), credential_id),
    )
