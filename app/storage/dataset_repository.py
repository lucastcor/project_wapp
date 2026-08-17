"""Persistência de versões de dataset e seus itens."""

from __future__ import annotations

import json
from typing import Any

from app.core.tenancy import require_company_id
from app.storage.sql_support import (
    execute,
    execute_rowcount,
    fetch_all,
    fetch_one,
    optional_equals,
    transaction,
)

STATUS_DRAFT = "draft"
STATUS_REVIEWING = "reviewing"
STATUS_APPROVED = "approved"
STATUS_FROZEN = "frozen"
STATUS_SUPERSEDED = "superseded"

MUTABLE_STATUSES = {STATUS_DRAFT, STATUS_REVIEWING, STATUS_APPROVED}

SPLIT_TRAIN = "train"
SPLIT_VALIDATION = "validation"
SPLIT_HOLDOUT = "holdout"

_VERSION_COLUMNS = """
    id,
    company_id,
    version,
    name,
    status,
    content_hash,
    split_seed,
    validation_ratio,
    holdout_ratio,
    stats_json,
    readiness_json,
    created_by,
    frozen_by,
    created_at,
    frozen_at,
    superseded_at
"""

_ITEM_COLUMNS = """
    id,
    company_id,
    dataset_version_id,
    split,
    group_key,
    source_type,
    source_id,
    scenario_type,
    customer_profile,
    profile_name,
    context_json,
    customer_message,
    approved_reply,
    style_notes,
    quality_score,
    content_hash,
    near_duplicate_key,
    created_at
"""


def next_version(*, company_id: int) -> int:
    resolved_company_id = require_company_id(company_id, context="dataset versioning")
    row = fetch_one(
        "SELECT MAX(version) AS max_version FROM dataset_versions WHERE company_id = ?",
        (resolved_company_id,),
    )
    return int((row or {}).get("max_version") or 0) + 1


def create_version(
    *,
    company_id: int,
    name: str | None,
    split_seed: int,
    validation_ratio: float,
    holdout_ratio: float,
    created_by: str | None,
) -> int:
    resolved_company_id = require_company_id(company_id, context="dataset version creation")
    return execute(
        """
        INSERT INTO dataset_versions (
            company_id,
            version,
            name,
            status,
            split_seed,
            validation_ratio,
            holdout_ratio,
            created_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resolved_company_id,
            next_version(company_id=resolved_company_id),
            name,
            STATUS_DRAFT,
            int(split_seed),
            float(validation_ratio),
            float(holdout_ratio),
            created_by,
        ),
    )


def get_version(*, company_id: int, dataset_version_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="dataset version read")
    row = fetch_one(
        f"SELECT {_VERSION_COLUMNS} FROM dataset_versions WHERE id = ? AND company_id = ?",
        (dataset_version_id, resolved_company_id),
    )
    if not row:
        return None
    return _deserialize_version(row)


def _deserialize_version(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "stats": json.loads(row.get("stats_json") or "{}"),
        "readiness": json.loads(row.get("readiness_json") or "{}"),
    }


def list_versions(*, company_id: int, limit: int = 50) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="dataset version listing")
    rows = fetch_all(
        f"""
        SELECT {_VERSION_COLUMNS}
        FROM dataset_versions
        WHERE company_id = ?
        ORDER BY version DESC
        LIMIT ?
        """,
        (resolved_company_id, limit),
    )
    return [_deserialize_version(row) for row in rows]


def replace_items(
    *,
    company_id: int,
    dataset_version_id: int,
    items: list[dict[str, Any]],
) -> int:
    resolved_company_id = require_company_id(company_id, context="dataset item write")
    with transaction() as cursor:
        cursor.execute(
            "DELETE FROM dataset_version_items WHERE company_id = ? AND dataset_version_id = ?",
            (resolved_company_id, dataset_version_id),
        )
        for item in items:
            cursor.execute(
                """
                INSERT INTO dataset_version_items (
                    company_id,
                    dataset_version_id,
                    split,
                    group_key,
                    source_type,
                    source_id,
                    scenario_type,
                    customer_profile,
                    profile_name,
                    context_json,
                    customer_message,
                    approved_reply,
                    style_notes,
                    quality_score,
                    content_hash,
                    near_duplicate_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_company_id,
                    dataset_version_id,
                    item["split"],
                    item["group_key"],
                    item["source_type"],
                    item.get("source_id"),
                    item["scenario_type"],
                    item.get("customer_profile"),
                    item.get("profile_name"),
                    json.dumps(item.get("context") or [], ensure_ascii=False),
                    item["customer_message"],
                    item["approved_reply"],
                    item.get("style_notes"),
                    float(item.get("quality_score") or 0),
                    item["content_hash"],
                    item.get("near_duplicate_key") or "",
                ),
            )
    return len(items)


def get_items(
    *,
    company_id: int,
    dataset_version_id: int,
    split: str | None = None,
) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="dataset item read")
    split_condition, split_params = optional_equals("split", split)
    rows = fetch_all(
        f"""
        SELECT {_ITEM_COLUMNS}
        FROM dataset_version_items
        WHERE company_id = ?
          AND dataset_version_id = ?
          AND {split_condition}
        ORDER BY id ASC
        """,
        (resolved_company_id, dataset_version_id, *split_params),
    )
    return [{**row, "context": json.loads(row.get("context_json") or "[]")} for row in rows]


def update_version_metadata(
    *,
    company_id: int,
    dataset_version_id: int,
    status: str | None = None,
    content_hash: str | None = None,
    stats: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
    frozen_by: str | None = None,
) -> bool:
    resolved_company_id = require_company_id(company_id, context="dataset version update")
    return (
        execute_rowcount(
            """
            UPDATE dataset_versions
            SET status = COALESCE(?, status),
                content_hash = COALESCE(?, content_hash),
                stats_json = COALESCE(?, stats_json),
                readiness_json = COALESCE(?, readiness_json),
                frozen_by = COALESCE(?, frozen_by),
                frozen_at = CASE WHEN ? = 'frozen' THEN CURRENT_TIMESTAMP ELSE frozen_at END
            WHERE id = ?
              AND company_id = ?
            """,
            (
                status,
                content_hash,
                json.dumps(stats, ensure_ascii=False) if stats is not None else None,
                json.dumps(readiness, ensure_ascii=False) if readiness is not None else None,
                frozen_by,
                status,
                dataset_version_id,
                resolved_company_id,
            ),
        )
        > 0
    )


def supersede_other_frozen_versions(*, company_id: int, keep_version_id: int) -> int:
    resolved_company_id = require_company_id(company_id, context="dataset supersede")
    return execute_rowcount(
        """
        UPDATE dataset_versions
        SET status = ?,
            superseded_at = CURRENT_TIMESTAMP
        WHERE company_id = ?
          AND status = ?
          AND id <> ?
        """,
        (STATUS_SUPERSEDED, resolved_company_id, STATUS_FROZEN, keep_version_id),
    )


def get_latest_frozen_version(*, company_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="dataset version read")
    row = fetch_one(
        f"""
        SELECT {_VERSION_COLUMNS}
        FROM dataset_versions
        WHERE company_id = ?
          AND status = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (resolved_company_id, STATUS_FROZEN),
    )
    return _deserialize_version(row) if row else None
