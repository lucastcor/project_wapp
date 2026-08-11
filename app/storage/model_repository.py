"""Registro de modelos por empresa.

Estados: `candidate` -> `shadow` -> `active` -> `retired`. Só existe um modelo
`active` por empresa; ativar um novo aposenta o anterior, o que dá rollback.
"""

from __future__ import annotations

from typing import Any

from app.core.tenancy import require_company_id
from app.storage.sql_support import execute, execute_rowcount, fetch_all, fetch_one, transaction

STATUS_CANDIDATE = "candidate"
STATUS_SHADOW = "shadow"
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"

VALID_STATUSES = {STATUS_CANDIDATE, STATUS_SHADOW, STATUS_ACTIVE, STATUS_RETIRED}

_COLUMNS = """
    id,
    company_id,
    model_ref,
    base_model,
    dataset_version_id,
    finetuning_job_id,
    evaluation_run_id,
    status,
    notes,
    activated_by,
    activated_at,
    retired_at,
    job_status,
    failure_reason,
    promoted_evaluation_run_id,
    created_by,
    created_at,
    updated_at
"""


def register_model(
    *,
    company_id: int,
    model_ref: str,
    base_model: str | None = None,
    dataset_version_id: int | None = None,
    finetuning_job_id: str | None = None,
    evaluation_run_id: int | None = None,
    status: str = STATUS_CANDIDATE,
    notes: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    resolved_company_id = require_company_id(company_id, context="model registration")
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid model status: {status}")

    normalized_ref = str(model_ref or "").strip()
    if not normalized_ref:
        raise ValueError("model_ref must not be blank.")

    existing = fetch_one(
        f"SELECT {_COLUMNS} FROM company_models WHERE company_id = ? AND model_ref = ?",
        (resolved_company_id, normalized_ref),
    )
    if existing:
        execute(
            """
            UPDATE company_models
            SET base_model = COALESCE(?, base_model),
                dataset_version_id = COALESCE(?, dataset_version_id),
                finetuning_job_id = COALESCE(?, finetuning_job_id),
                evaluation_run_id = COALESCE(?, evaluation_run_id),
                notes = COALESCE(?, notes),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                base_model,
                dataset_version_id,
                finetuning_job_id,
                evaluation_run_id,
                notes,
                int(existing["id"]),
            ),
        )
        return get_model(company_id=resolved_company_id, model_id=int(existing["id"])) or {}

    model_id = execute(
        """
        INSERT INTO company_models (
            company_id,
            model_ref,
            base_model,
            dataset_version_id,
            finetuning_job_id,
            evaluation_run_id,
            status,
            notes,
            created_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resolved_company_id,
            normalized_ref,
            base_model,
            dataset_version_id,
            finetuning_job_id,
            evaluation_run_id,
            status,
            notes,
            created_by,
        ),
    )
    return get_model(company_id=resolved_company_id, model_id=model_id) or {}


def get_model(*, company_id: int, model_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="model read")
    return fetch_one(
        f"SELECT {_COLUMNS} FROM company_models WHERE id = ? AND company_id = ?",
        (model_id, resolved_company_id),
    )


def get_active_model(*, company_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="active model read")
    return fetch_one(
        f"""
        SELECT {_COLUMNS}
        FROM company_models
        WHERE company_id = ?
          AND status = ?
        ORDER BY activated_at DESC, id DESC
        LIMIT 1
        """,
        (resolved_company_id, STATUS_ACTIVE),
    )


def list_models(*, company_id: int, limit: int = 100) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="model listing")
    return fetch_all(
        f"""
        SELECT {_COLUMNS}
        FROM company_models
        WHERE company_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (resolved_company_id, limit),
    )


def activate_model(*, company_id: int, model_id: int, activated_by: str | None) -> dict[str, Any] | None:
    """Ativa um modelo e aposenta o ativo anterior, em uma única transação."""
    resolved_company_id = require_company_id(company_id, context="model activation")
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE company_models
            SET status = ?,
                retired_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE company_id = ?
              AND status = ?
              AND id <> ?
            """,
            (STATUS_RETIRED, resolved_company_id, STATUS_ACTIVE, model_id),
        )
        cursor.execute(
            """
            UPDATE company_models
            SET status = ?,
                activated_by = ?,
                activated_at = CURRENT_TIMESTAMP,
                retired_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND company_id = ?
            """,
            (STATUS_ACTIVE, activated_by, model_id, resolved_company_id),
        )
        updated = cursor.rowcount

    if not updated:
        return None
    return get_model(company_id=resolved_company_id, model_id=model_id)


def set_model_status(
    *,
    company_id: int,
    model_id: int,
    status: str,
) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="model status change")
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid model status: {status}")

    updated = execute_rowcount(
        """
        UPDATE company_models
        SET status = ?,
            retired_at = CASE WHEN ? = 'retired' THEN CURRENT_TIMESTAMP ELSE retired_at END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND company_id = ?
        """,
        (status, status, model_id, resolved_company_id),
    )
    if not updated:
        return None
    return get_model(company_id=resolved_company_id, model_id=model_id)


def get_previous_active_model(*, company_id: int, exclude_model_id: int) -> dict[str, Any] | None:
    """Modelo aposentado mais recente, usado como alvo natural de rollback."""
    resolved_company_id = require_company_id(company_id, context="model rollback lookup")
    return fetch_one(
        f"""
        SELECT {_COLUMNS}
        FROM company_models
        WHERE company_id = ?
          AND status = ?
          AND id <> ?
          AND activated_at IS NOT NULL
        ORDER BY retired_at DESC, id DESC
        LIMIT 1
        """,
        (resolved_company_id, STATUS_RETIRED, exclude_model_id),
    )


def get_model_by_job(*, company_id: int, finetuning_job_id: str) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="model job lookup")
    return fetch_one(
        f"""
        SELECT {_COLUMNS}
        FROM company_models
        WHERE company_id = ?
          AND finetuning_job_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (resolved_company_id, finetuning_job_id),
    )


def replace_model_ref(
    *,
    company_id: int,
    model_id: int,
    model_ref: str,
    job_status: str | None = None,
) -> dict[str, Any] | None:
    """Troca o placeholder pelo modelo realmente produzido pelo job."""
    resolved_company_id = require_company_id(company_id, context="model ref update")
    execute_rowcount(
        """
        UPDATE company_models
        SET model_ref = ?,
            job_status = COALESCE(?, job_status),
            failure_reason = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND company_id = ?
        """,
        (model_ref, job_status, model_id, resolved_company_id),
    )
    return get_model(company_id=resolved_company_id, model_id=model_id)


def mark_model_failed(
    *,
    company_id: int,
    model_id: int,
    job_status: str,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="model failure")
    execute_rowcount(
        """
        UPDATE company_models
        SET status = ?,
            job_status = ?,
            failure_reason = ?,
            retired_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND company_id = ?
        """,
        (STATUS_RETIRED, job_status, failure_reason, model_id, resolved_company_id),
    )
    return get_model(company_id=resolved_company_id, model_id=model_id)


def set_job_status(*, company_id: int, model_id: int, job_status: str) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="model job status")
    execute_rowcount(
        """
        UPDATE company_models
        SET job_status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND company_id = ?
        """,
        (job_status, model_id, resolved_company_id),
    )
    return get_model(company_id=resolved_company_id, model_id=model_id)
