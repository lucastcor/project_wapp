"""Persistência de casos, execuções e resultados de avaliação."""

from __future__ import annotations

import json
from typing import Any

from app.core.tenancy import require_company_id
from app.storage.sql_support import execute, fetch_all, fetch_one, transaction

_CASE_COLUMNS = """
    id,
    company_id,
    case_key,
    version,
    scenario_type,
    customer_profile,
    customer_message,
    context_json,
    expected_behavior,
    expected_keywords_json,
    forbidden_keywords_json,
    requires_handoff,
    requires_source_grounding,
    requires_action_tool,
    active,
    created_by,
    created_at,
    updated_at
"""

_RUN_COLUMNS = """
    id,
    company_id,
    run_key,
    approach,
    execution_mode,
    promotion_eligible,
    model_ref,
    base_model,
    dataset_version_id,
    finetuning_job_id,
    prompt_contract_version,
    retrieval_config_version,
    case_suite_hash,
    subject_fingerprint,
    executor_kind,
    provider,
    attestation_json,
    status,
    total_cases,
    passed_cases,
    scores_json,
    created_by,
    created_at
"""


def _deserialize_case(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "context": json.loads(row.get("context_json") or "[]"),
        "expected_keywords": json.loads(row.get("expected_keywords_json") or "[]"),
        "forbidden_keywords": json.loads(row.get("forbidden_keywords_json") or "[]"),
    }


def upsert_case(
    *,
    company_id: int,
    case_key: str,
    scenario_type: str,
    customer_message: str,
    version: int = 1,
    customer_profile: str | None = None,
    context: list[dict[str, str]] | None = None,
    expected_behavior: str | None = None,
    expected_keywords: list[str] | None = None,
    forbidden_keywords: list[str] | None = None,
    requires_handoff: bool = False,
    requires_source_grounding: bool = False,
    requires_action_tool: bool = False,
    created_by: str | None = None,
) -> int:
    resolved_company_id = require_company_id(company_id, context="evaluation case write")
    existing = fetch_one(
        "SELECT id FROM evaluation_cases WHERE company_id = ? AND case_key = ? AND version = ?",
        (resolved_company_id, case_key, version),
    )
    payload = (
        scenario_type,
        customer_profile,
        customer_message,
        json.dumps(context or [], ensure_ascii=False),
        expected_behavior,
        json.dumps(expected_keywords or [], ensure_ascii=False),
        json.dumps(forbidden_keywords or [], ensure_ascii=False),
        int(requires_handoff),
        int(requires_source_grounding),
        int(requires_action_tool),
    )
    if existing:
        execute(
            """
            UPDATE evaluation_cases
            SET scenario_type = ?,
                customer_profile = ?,
                customer_message = ?,
                context_json = ?,
                expected_behavior = ?,
                expected_keywords_json = ?,
                forbidden_keywords_json = ?,
                requires_handoff = ?,
                requires_source_grounding = ?,
                requires_action_tool = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*payload, int(existing["id"])),
        )
        return int(existing["id"])

    return execute(
        """
        INSERT INTO evaluation_cases (
            company_id,
            case_key,
            version,
            scenario_type,
            customer_profile,
            customer_message,
            context_json,
            expected_behavior,
            expected_keywords_json,
            forbidden_keywords_json,
            requires_handoff,
            requires_source_grounding,
            requires_action_tool,
            created_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (resolved_company_id, case_key, version, *payload, created_by),
    )


def list_cases(*, company_id: int, active_only: bool = True) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="evaluation case listing")
    rows = fetch_all(
        f"""
        SELECT {_CASE_COLUMNS}
        FROM evaluation_cases
        WHERE company_id = ?
          AND (? = 0 OR active = 1)
        ORDER BY case_key ASC, version DESC
        """,
        (resolved_company_id, int(active_only)),
    )
    return [_deserialize_case(row) for row in rows]


def _deserialize_run(row: dict[str, Any]) -> dict[str, Any]:
    """Normaliza o run para o contrato da API.

    `promotion_eligible` é INTEGER no banco nos dois backends; expor 0/1 para
    o cliente convidaria a comparações frouxas num campo que decide promoção.
    """
    return {
        **row,
        "scores": json.loads(row.get("scores_json") or "{}"),
        "promotion_eligible": bool(int(row.get("promotion_eligible") or 0)),
        "attestation": json.loads(row.get("attestation_json") or "null"),
    }


def create_run(
    *,
    company_id: int,
    run_key: str,
    approach: str,
    model_ref: str | None,
    dataset_version_id: int | None,
    total_cases: int,
    passed_cases: int,
    scores: dict[str, Any],
    results: list[dict[str, Any]],
    created_by: str | None = None,
    execution_mode: str = "simulation",
    promotion_eligible: bool = False,
    base_model: str | None = None,
    finetuning_job_id: str | None = None,
    prompt_contract_version: str | None = None,
    retrieval_config_version: str | None = None,
    case_suite_hash: str | None = None,
    subject_fingerprint: str | None = None,
    executor_kind: str | None = None,
    provider: str | None = None,
    attestation: dict[str, Any] | None = None,
) -> int:
    resolved_company_id = require_company_id(company_id, context="evaluation run write")

    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO evaluation_runs (
                company_id,
                run_key,
                approach,
                execution_mode,
                promotion_eligible,
                model_ref,
                base_model,
                dataset_version_id,
                finetuning_job_id,
                prompt_contract_version,
                retrieval_config_version,
                case_suite_hash,
                subject_fingerprint,
                executor_kind,
                provider,
                attestation_json,
                status,
                total_cases,
                passed_cases,
                scores_json,
                created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_company_id,
                run_key,
                approach,
                execution_mode,
                int(promotion_eligible),
                model_ref,
                base_model,
                dataset_version_id,
                finetuning_job_id,
                prompt_contract_version,
                retrieval_config_version,
                case_suite_hash,
                subject_fingerprint,
                executor_kind,
                provider,
                json.dumps(attestation, ensure_ascii=False) if attestation else None,
                "completed",
                int(total_cases),
                int(passed_cases),
                json.dumps(scores, ensure_ascii=False),
                created_by,
            ),
        )
        run_id = int(cursor.lastrowid or 0)

        for result in results:
            cursor.execute(
                """
                INSERT INTO evaluation_results (
                    company_id,
                    run_id,
                    case_id,
                    passed,
                    dimension_scores_json,
                    failure_reasons_json,
                    response_excerpt,
                    sources_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_company_id,
                    run_id,
                    int(result["case_id"]),
                    int(bool(result["passed"])),
                    json.dumps(result.get("dimension_scores") or {}, ensure_ascii=False),
                    json.dumps(result.get("failure_reasons") or [], ensure_ascii=False),
                    result.get("response_excerpt"),
                    json.dumps(result.get("sources") or [], ensure_ascii=False),
                ),
            )

    return run_id


def get_run(*, company_id: int, run_id: int) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="evaluation run read")
    row = fetch_one(
        f"SELECT {_RUN_COLUMNS} FROM evaluation_runs WHERE id = ? AND company_id = ?",
        (run_id, resolved_company_id),
    )
    if not row:
        return None
    return _deserialize_run(row)


def list_runs(*, company_id: int, limit: int = 50) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="evaluation run listing")
    rows = fetch_all(
        f"""
        SELECT {_RUN_COLUMNS}
        FROM evaluation_runs
        WHERE company_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (resolved_company_id, limit),
    )
    return [_deserialize_run(row) for row in rows]


def get_latest_run_for_approach(
    *,
    company_id: int,
    approach: str,
    promotion_eligible_only: bool = False,
) -> dict[str, Any] | None:
    resolved_company_id = require_company_id(company_id, context="evaluation run read")
    row = fetch_one(
        f"""
        SELECT {_RUN_COLUMNS}
        FROM evaluation_runs
        WHERE company_id = ?
          AND approach = ?
          AND (? = 0 OR promotion_eligible = 1)
        ORDER BY id DESC
        LIMIT 1
        """,
        (resolved_company_id, approach, int(promotion_eligible_only)),
    )
    if not row:
        return None
    return _deserialize_run(row)


def get_run_results(*, company_id: int, run_id: int) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="evaluation result read")
    rows = fetch_all(
        """
        SELECT
            id,
            company_id,
            run_id,
            case_id,
            passed,
            dimension_scores_json,
            failure_reasons_json,
            response_excerpt,
            sources_json,
            created_at
        FROM evaluation_results
        WHERE company_id = ?
          AND run_id = ?
        ORDER BY id ASC
        """,
        (resolved_company_id, run_id),
    )
    return [
        {
            **row,
            "dimension_scores": json.loads(row.get("dimension_scores_json") or "{}"),
            "failure_reasons": json.loads(row.get("failure_reasons_json") or "[]"),
            "sources": json.loads(row.get("sources_json") or "[]"),
        }
        for row in rows
    ]
