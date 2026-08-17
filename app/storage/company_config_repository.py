"""Persistência do company context versionado.

Substitui o JSON mutável no filesystem como fonte operacional. Os arquivos em
`app/data/company_contexts/` seguem válidos como template/seed de bootstrap.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.tenancy import require_company_id
from app.storage.sql_support import execute, execute_rowcount, fetch_all, fetch_one

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_ARCHIVED = "archived"

_COLUMNS = """
    id,
    company_id,
    version,
    status,
    context_json,
    capabilities_json,
    policies_json,
    vertical_pack,
    content_hash,
    author,
    change_note,
    created_at,
    published_at
"""


def compute_content_hash(
    context: dict[str, Any],
    capabilities: dict[str, Any],
    policies: dict[str, Any],
) -> str:
    payload = json.dumps(
        {"context": context, "capabilities": capabilities, "policies": policies},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _deserialize(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "company_id": int(row["company_id"]),
        "version": int(row["version"]),
        "status": str(row["status"]),
        "context": json.loads(row["context_json"]),
        "capabilities": json.loads(row["capabilities_json"] or "{}"),
        "policies": json.loads(row["policies_json"] or "{}"),
        "vertical_pack": row.get("vertical_pack"),
        "content_hash": row.get("content_hash"),
        "author": row.get("author"),
        "change_note": row.get("change_note"),
        "created_at": row.get("created_at"),
        "published_at": row.get("published_at"),
    }


def get_next_version(company_id: int) -> int:
    row = fetch_one(
        "SELECT MAX(version) AS max_version FROM company_context_versions WHERE company_id = ?",
        (company_id,),
    )
    current = (row or {}).get("max_version")
    return int(current or 0) + 1


def create_context_version(
    *,
    company_id: int,
    context: dict[str, Any],
    capabilities: dict[str, Any],
    policies: dict[str, Any],
    vertical_pack: str | None,
    author: str | None = None,
    change_note: str | None = None,
    status: str = STATUS_DRAFT,
) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="company context versioning")
    version = get_next_version(resolved_company_id)
    content_hash = compute_content_hash(context, capabilities, policies)

    version_id = execute(
        """
        INSERT INTO company_context_versions (
            company_id,
            version,
            status,
            context_json,
            capabilities_json,
            policies_json,
            vertical_pack,
            content_hash,
            author,
            change_note,
            published_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            resolved_company_id,
            version,
            status,
            json.dumps(context, ensure_ascii=False),
            json.dumps(capabilities, ensure_ascii=False),
            json.dumps(policies, ensure_ascii=False),
            vertical_pack,
            content_hash,
            author,
            change_note,
        ),
    )

    if status == STATUS_PUBLISHED:
        _archive_other_published(resolved_company_id, version_id)
        execute(
            "UPDATE company_context_versions SET published_at = CURRENT_TIMESTAMP WHERE id = ?",
            (version_id,),
        )

    stored = get_context_version(company_id=resolved_company_id, version_id=version_id)
    if stored is None:  # pragma: no cover - a linha acabou de ser inserida
        raise RuntimeError(f"Context version {version_id} could not be read back.")
    return stored


def _archive_other_published(company_id: int, keep_version_id: int) -> None:
    execute_rowcount(
        """
        UPDATE company_context_versions
        SET status = ?
        WHERE company_id = ?
          AND status = ?
          AND id <> ?
        """,
        (STATUS_ARCHIVED, company_id, STATUS_PUBLISHED, keep_version_id),
    )


def publish_context_version(
    *,
    company_id: int,
    version_id: int,
    author: str | None = None,
) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="company context publish")
    updated = execute_rowcount(
        """
        UPDATE company_context_versions
        SET status = ?,
            published_at = CURRENT_TIMESTAMP,
            author = COALESCE(?, author)
        WHERE id = ?
          AND company_id = ?
        """,
        (STATUS_PUBLISHED, author, version_id, resolved_company_id),
    )
    if not updated:
        return None

    _archive_other_published(resolved_company_id, version_id)
    return get_context_version(company_id=resolved_company_id, version_id=version_id)


def get_context_version(*, company_id: int, version_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="company context read")
    return _deserialize(
        fetch_one(
            f"SELECT {_COLUMNS} FROM company_context_versions WHERE id = ? AND company_id = ?",
            (version_id, resolved_company_id),
        )
    )


def get_published_context(*, company_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="company context read")
    return _deserialize(
        fetch_one(
            f"""
            SELECT {_COLUMNS}
            FROM company_context_versions
            WHERE company_id = ?
              AND status = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (resolved_company_id, STATUS_PUBLISHED),
        )
    )


def get_latest_context(*, company_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="company context read")
    return _deserialize(
        fetch_one(
            f"""
            SELECT {_COLUMNS}
            FROM company_context_versions
            WHERE company_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (resolved_company_id,),
        )
    )


def list_context_versions(*, company_id: int, limit: int = 50) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="company context history")
    return fetch_all(
        """
        SELECT
            id,
            company_id,
            version,
            status,
            vertical_pack,
            content_hash,
            author,
            change_note,
            created_at,
            published_at
        FROM company_context_versions
        WHERE company_id = ?
        ORDER BY version DESC
        LIMIT ?
        """,
        (resolved_company_id, limit),
    )
