"""Vínculo verificável entre um canal de provider e uma empresa.

A resolução de tenant em endpoints públicos deve partir daqui, e não de
`company_id`/`company_slug` informados pelo chamador.
"""

from __future__ import annotations

from typing import Any

from app.core.tenancy import require_company_id
from app.storage.sql_support import (
    execute,
    execute_rowcount,
    fetch_all,
    fetch_one,
    optional_equals,
)

_COLUMNS = """
    id,
    company_id,
    provider,
    channel_type,
    external_id,
    label,
    active,
    created_at,
    updated_at
"""


def normalize_external_id(external_id: str | None) -> str:
    return str(external_id or "").strip()


def create_channel_binding(
    *,
    company_id: int,
    provider: str,
    external_id: str,
    channel_type: str = "whatsapp",
    label: str | None = None,
    active: bool = True,
) -> int:
    resolved_company_id = require_company_id(company_id, context="channel binding creation")
    normalized_external_id = normalize_external_id(external_id)
    if not normalized_external_id:
        raise ValueError("external_id must be a non-empty string.")

    existing = fetch_one(
        f"SELECT {_COLUMNS} FROM company_channel_bindings WHERE provider = ? AND external_id = ?",
        (provider, normalized_external_id),
    )
    if existing:
        execute(
            """
            UPDATE company_channel_bindings
            SET company_id = ?,
                channel_type = ?,
                label = ?,
                active = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                resolved_company_id,
                channel_type,
                label,
                int(active),
                int(existing["id"]),
            ),
        )
        return int(existing["id"])

    return execute(
        """
        INSERT INTO company_channel_bindings (
            company_id,
            provider,
            channel_type,
            external_id,
            label,
            active
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            resolved_company_id,
            provider,
            channel_type,
            normalized_external_id,
            label,
            int(active),
        ),
    )


def get_channel_bindings(*, company_id: int | None = None) -> list[dict[str, Any]]:
    company_condition, company_params = optional_equals("company_id", company_id)
    return fetch_all(
        f"""
        SELECT {_COLUMNS}
        FROM company_channel_bindings
        WHERE {company_condition}
        ORDER BY id DESC
        """,
        company_params,
    )


def resolve_company_id_for_channel(*, provider: str, external_id: str | None) -> int | None:
    """Empresa vinculada a um canal ativo, ou None se não houver vínculo."""
    normalized_external_id = normalize_external_id(external_id)
    if not normalized_external_id:
        return None

    row = fetch_one(
        """
        SELECT company_id
        FROM company_channel_bindings
        WHERE provider = ?
          AND external_id = ?
          AND active = 1
        """,
        (provider, normalized_external_id),
    )
    if not row or row.get("company_id") is None:
        return None
    return int(row["company_id"])


def deactivate_channel_binding(*, binding_id: int, company_id: int) -> bool:
    resolved_company_id = require_company_id(company_id, context="channel binding deactivation")
    return (
        execute_rowcount(
            """
            UPDATE company_channel_bindings
            SET active = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND company_id = ?
            """,
            (binding_id, resolved_company_id),
        )
        > 0
    )
