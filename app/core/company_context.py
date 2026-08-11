"""Company context storage.

The runtime store of record is the shared database (``company_contexts``), so
the API and the worker always observe the same configuration and every change
is transactional, versioned and part of the database backup.

The JSON files under ``app/data`` are **seed** material only: they bootstrap a
fresh database (migration ``026_company_context_seed_import``) and can be read
as a fallback in development. Production and strict-validation runtimes never
fall back to them.
"""

import json
import re
from pathlib import Path
from typing import Any

from app.core.config import settings

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COMPANY_CONTEXT_PATH = DATA_DIR / "company_context.json"
COMPANY_CONTEXTS_DIR = DATA_DIR / "company_contexts"

# Storage key used for the shared/global context that has no company slug.
GLOBAL_CONTEXT_KEY = "__global__"
COMPANY_CONTEXT_SCHEMA_VERSION = 1


class CompanyContextError(Exception):
    pass


class CompanyContextConflictError(CompanyContextError):
    """Raised when an update is based on a stale revision."""


REQUIRED_TOP_LEVEL_KEYS = {
    "company_name",
    "brand_tone",
    "enabled_modules",
    "business_identity",
    "contact_channels",
    "business_hours",
    "response_policy",
    "commercial_policy",
    "catalog",
    "customer_intake",
    "business_rules",
    "llm_guidelines",
}


def _normalize_company_slug(company_slug: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", company_slug.strip().lower())
    normalized = normalized.strip("-")
    if not normalized:
        raise CompanyContextError("company_slug must resolve to a non-empty normalized value.")
    return normalized


def _get_company_context_path(company_slug: str | None = None) -> Path:
    if not company_slug:
        return COMPANY_CONTEXT_PATH

    normalized_slug = _normalize_company_slug(company_slug)
    return COMPANY_CONTEXTS_DIR / f"{normalized_slug}.json"


def _validate_company_context(data: dict[str, Any]) -> None:
    missing_keys = sorted(REQUIRED_TOP_LEVEL_KEYS - set(data.keys()))
    if missing_keys:
        raise CompanyContextError(
            f"Company context is missing required keys: {', '.join(missing_keys)}"
        )

    if not isinstance(data.get("company_name"), str) or not data["company_name"].strip():
        raise CompanyContextError("company_name must be a non-empty string.")

    if not isinstance(data.get("business_identity"), dict):
        raise CompanyContextError("business_identity must be an object.")

    if not isinstance(data.get("catalog"), dict):
        raise CompanyContextError("catalog must be an object.")

    catalog_items = data["catalog"].get("items")
    if not isinstance(catalog_items, list):
        raise CompanyContextError("catalog.items must be a list.")

    if not isinstance(data.get("business_rules"), list):
        raise CompanyContextError("business_rules must be a list.")

    if not isinstance(data.get("enabled_modules"), dict):
        raise CompanyContextError("enabled_modules must be an object.")

    scheduling_policy = data.get("scheduling_policy")
    if scheduling_policy is not None and not isinstance(scheduling_policy, dict):
        raise CompanyContextError("scheduling_policy must be an object when provided.")

    profiles = scheduling_policy.get("profiles") if isinstance(scheduling_policy, dict) else None
    if profiles is not None:
        if not isinstance(profiles, list):
            raise CompanyContextError("scheduling_policy.profiles must be a list when provided.")
        profile_keys: set[str] = set()
        for index, profile in enumerate(profiles):
            if not isinstance(profile, dict):
                raise CompanyContextError(f"scheduling_policy.profiles[{index}] must be an object.")
            profile_key = str(profile.get("key") or "").strip()
            if not profile_key:
                raise CompanyContextError(
                    f"scheduling_policy.profiles[{index}].key must be a non-empty string."
                )
            if profile_key in profile_keys:
                raise CompanyContextError(
                    f"scheduling_policy.profiles contains duplicate key: {profile_key}"
                )
            profile_keys.add(profile_key)

    conversation_pipelines = data.get("conversation_pipelines")
    if conversation_pipelines is not None:
        if not isinstance(conversation_pipelines, list):
            raise CompanyContextError("conversation_pipelines must be a list when provided.")

        pipeline_keys: set[str] = set()
        for index, pipeline in enumerate(conversation_pipelines):
            if not isinstance(pipeline, dict):
                raise CompanyContextError(f"conversation_pipelines[{index}] must be an object.")

            pipeline_key = str(pipeline.get("key") or "").strip()
            if not pipeline_key:
                raise CompanyContextError(f"conversation_pipelines[{index}].key must be a non-empty string.")
            if pipeline_key in pipeline_keys:
                raise CompanyContextError(f"conversation_pipelines contains duplicate key: {pipeline_key}")
            pipeline_keys.add(pipeline_key)

            label = pipeline.get("label")
            if label is not None and (not isinstance(label, str) or not label.strip()):
                raise CompanyContextError(f"conversation_pipelines[{index}].label must be a non-empty string when provided.")

            trigger_terms = pipeline.get("trigger_terms")
            if trigger_terms is not None and not isinstance(trigger_terms, list):
                raise CompanyContextError(f"conversation_pipelines[{index}].trigger_terms must be a list when provided.")

            next_keys = pipeline.get("next_keys")
            if next_keys is not None and not isinstance(next_keys, list):
                raise CompanyContextError(f"conversation_pipelines[{index}].next_keys must be a list when provided.")

            canvas_position = pipeline.get("canvas_position")
            if canvas_position is not None:
                if not isinstance(canvas_position, dict):
                    raise CompanyContextError(f"conversation_pipelines[{index}].canvas_position must be an object when provided.")
                x = canvas_position.get("x")
                y = canvas_position.get("y")
                if x is not None and not isinstance(x, (int, float)):
                    raise CompanyContextError(f"conversation_pipelines[{index}].canvas_position.x must be numeric when provided.")
                if y is not None and not isinstance(y, (int, float)):
                    raise CompanyContextError(f"conversation_pipelines[{index}].canvas_position.y must be numeric when provided.")

        for index, pipeline in enumerate(conversation_pipelines):
            next_keys = pipeline.get("next_keys") or []
            for next_key in next_keys:
                normalized_next_key = str(next_key or "").strip()
                if normalized_next_key and normalized_next_key not in pipeline_keys:
                    raise CompanyContextError(
                        f"conversation_pipelines[{index}].next_keys references unknown key: {normalized_next_key}"
                    )


# ── Storage keys and seed files ───────────────────────────────────────


def context_storage_key(company_slug: str | None) -> str:
    """Return the ``company_contexts.company_slug`` key for a tenant."""
    if not company_slug:
        return GLOBAL_CONTEXT_KEY
    return _normalize_company_slug(company_slug)


def seed_contexts_enabled() -> bool:
    """Seed files are readable only outside production/strict runtimes."""
    if settings.strict_runtime_validation:
        return False
    return settings.environment.strip().lower() != "production"


def _read_seed_context(company_slug: str | None) -> dict[str, Any] | None:
    context_path = _get_company_context_path(company_slug)
    if not context_path.exists():
        return None

    try:
        with context_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError):
        return None

    return data if isinstance(data, dict) else None


def _seed_context_files() -> list[tuple[str, str | None, Path]]:
    """Return ``(storage_key, company_slug, path)`` for every seed context file."""
    seeds: list[tuple[str, str | None, Path]] = []
    if COMPANY_CONTEXT_PATH.exists():
        seeds.append((GLOBAL_CONTEXT_KEY, None, COMPANY_CONTEXT_PATH))

    if COMPANY_CONTEXTS_DIR.exists():
        for context_path in sorted(COMPANY_CONTEXTS_DIR.glob("*.json")):
            slug = context_path.stem
            try:
                storage_key = _normalize_company_slug(slug)
            except CompanyContextError:
                continue
            seeds.append((storage_key, slug, context_path))
    return seeds


# ── Database-backed access ────────────────────────────────────────────


def _get_repository() -> Any:
    from app.storage.repository import RuntimeRepository

    return RuntimeRepository()


def _resolve_company_id(company_slug: str | None) -> int | None:
    if not company_slug:
        return None
    company = _get_repository().get_company_by_slug(company_slug)
    return int(company["id"]) if company else None


def _record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(row["context_json"])
    except (TypeError, ValueError) as exc:
        raise CompanyContextError("Stored company context is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise CompanyContextError("Stored company context must be a JSON object.")

    return {
        "company_id": row.get("company_id"),
        "company_slug": row.get("company_slug"),
        "revision": int(row.get("revision") or 0),
        "schema_version": int(row.get("schema_version") or COMPANY_CONTEXT_SCHEMA_VERSION),
        "created_at": str(row["created_at"]) if row.get("created_at") is not None else None,
        "updated_at": str(row["updated_at"]) if row.get("updated_at") is not None else None,
        "updated_by": row.get("updated_by"),
        "source": "database",
        "source_path": None,
        "data": data,
    }


def _record_from_seed(company_slug: str | None, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": None,
        "company_slug": context_storage_key(company_slug),
        "revision": 0,
        "schema_version": COMPANY_CONTEXT_SCHEMA_VERSION,
        "created_at": None,
        "updated_at": None,
        "updated_by": None,
        "source": "seed_file",
        "source_path": str(_get_company_context_path(company_slug)),
        "data": data,
    }


def _resolution_candidates(company_slug: str | None) -> list[str | None]:
    if company_slug:
        return [company_slug, None]
    return [None]


def _load_context_record(company_slug: str | None = None) -> dict[str, Any] | None:
    """Resolve the effective context record without validating its payload.

    Database rows always win; seed files are consulted only when they are
    enabled for the current runtime.
    """
    repository = _get_repository()
    seeds_allowed = seed_contexts_enabled()

    for candidate_slug in _resolution_candidates(company_slug):
        row = repository.get_company_context_record(context_storage_key(candidate_slug))
        if row:
            return _record_from_row(row)
        if seeds_allowed:
            seed_data = _read_seed_context(candidate_slug)
            if seed_data is not None:
                return _record_from_seed(candidate_slug, seed_data)

    return None


def get_company_context_record(company_slug: str | None = None) -> dict[str, Any]:
    """Return the validated effective context record for a tenant."""
    record = _load_context_record(company_slug)
    if record is None:
        raise CompanyContextError(
            "No company context is available. Import the seed contexts or create one via the admin API."
        )

    _validate_company_context(record["data"])
    return record


def get_company_context(company_slug: str | None = None) -> dict[str, Any]:
    return get_company_context_record(company_slug=company_slug)["data"]


def get_company_context_status(company_slug: str | None = None) -> dict[str, Any]:
    requested_path = _get_company_context_path(company_slug)
    status: dict[str, Any] = {
        "company_slug": company_slug,
        "company_id": None,
        "requested_path": str(requested_path),
        "resolved_path": None,
        "exists": False,
        "fallback_used": False,
        "is_valid": False,
        "source": "missing",
        "revision": 0,
        "schema_version": COMPANY_CONTEXT_SCHEMA_VERSION,
        "updated_at": None,
        "updated_by": None,
        "top_level_keys": [],
        "error": None,
    }

    try:
        record = _load_context_record(company_slug)
    except CompanyContextError as exc:
        status["error"] = str(exc)
        return status

    if record is None:
        status["error"] = (
            "Company context not found in the database"
            + ("" if seed_contexts_enabled() else " (seed files are disabled in this runtime)")
            + "."
        )
        return status

    status["exists"] = True
    status["company_id"] = record["company_id"]
    status["source"] = record["source"]
    status["resolved_path"] = record["source_path"]
    status["revision"] = record["revision"]
    status["schema_version"] = record["schema_version"]
    status["updated_at"] = record["updated_at"]
    status["updated_by"] = record["updated_by"]
    status["fallback_used"] = bool(
        company_slug and record["company_slug"] == GLOBAL_CONTEXT_KEY
    )

    try:
        _validate_company_context(record["data"])
    except CompanyContextError as exc:
        status["error"] = str(exc)
        return status

    status["is_valid"] = True
    status["top_level_keys"] = sorted(record["data"].keys())
    return status


def save_company_context_record(
    data: dict[str, Any],
    company_slug: str | None = None,
    *,
    expected_revision: int | None = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    """Validate and transactionally persist a company context.

    An invalid payload is rejected before any write, so the last valid version
    is never replaced. A stale ``expected_revision`` raises a conflict.
    """
    _validate_company_context(data)

    storage_key = context_storage_key(company_slug)
    row = _get_repository().save_company_context_record(
        company_slug=storage_key,
        company_id=_resolve_company_id(company_slug),
        context_json=json.dumps(data, ensure_ascii=False),
        schema_version=COMPANY_CONTEXT_SCHEMA_VERSION,
        expected_revision=expected_revision,
        updated_by=updated_by,
    )
    if row is None:
        raise CompanyContextConflictError(
            "Company context was modified by another writer. Reload the current revision and retry."
        )
    return _record_from_row(row)


def save_company_context(
    data: dict[str, Any],
    company_slug: str | None = None,
    *,
    expected_revision: int | None = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    record = save_company_context_record(
        data,
        company_slug=company_slug,
        expected_revision=expected_revision,
        updated_by=updated_by,
    )
    return record["data"]


# ── Bootstrap / seed import ───────────────────────────────────────────


def import_seed_contexts_with_cursor(cursor: Any) -> dict[str, Any]:
    """Import seed context files using an open migration cursor.

    Idempotent: a storage key that already has a row is left untouched, so a
    newer database context is never overwritten by an older seed file.
    """
    imported: list[str] = []
    skipped_existing: list[str] = []
    skipped_invalid: list[str] = []

    for storage_key, company_slug, context_path in _seed_context_files():
        cursor.execute(
            "SELECT id FROM company_contexts WHERE company_slug = ?",
            (storage_key,),
        )
        if cursor.fetchone():
            skipped_existing.append(storage_key)
            continue

        seed_data = _read_seed_context(company_slug)
        if seed_data is None:
            skipped_invalid.append(storage_key)
            continue
        try:
            _validate_company_context(seed_data)
        except CompanyContextError:
            skipped_invalid.append(storage_key)
            continue

        company_id = None
        if company_slug:
            cursor.execute("SELECT id FROM companies WHERE slug = ?", (company_slug,))
            company_row = cursor.fetchone()
            if company_row:
                company_id = int(company_row["id"])

        cursor.execute(
            """
            INSERT INTO company_contexts (
                company_id,
                company_slug,
                context_json,
                schema_version,
                revision,
                created_at,
                updated_at,
                updated_by
            )
            VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            """,
            (
                company_id,
                storage_key,
                json.dumps(seed_data, ensure_ascii=False),
                COMPANY_CONTEXT_SCHEMA_VERSION,
                f"seed:{context_path.name}",
            ),
        )
        imported.append(storage_key)

    return {
        "imported": imported,
        "skipped_existing": skipped_existing,
        "skipped_invalid": skipped_invalid,
    }


def plan_seed_context_import() -> dict[str, list[str]]:
    """Report what :func:`bootstrap_company_contexts` would do, without writing."""
    repository = _get_repository()
    plan: dict[str, list[str]] = {
        "imported": [],
        "skipped_existing": [],
        "skipped_invalid": [],
    }

    for storage_key, company_slug, _context_path in _seed_context_files():
        if repository.get_company_context_record(storage_key):
            plan["skipped_existing"].append(storage_key)
            continue

        seed_data = _read_seed_context(company_slug)
        if seed_data is None:
            plan["skipped_invalid"].append(storage_key)
            continue
        try:
            _validate_company_context(seed_data)
        except CompanyContextError:
            plan["skipped_invalid"].append(storage_key)
            continue

        plan["imported"].append(storage_key)

    return plan


def bootstrap_company_contexts() -> dict[str, Any]:
    """Idempotently import seed context files in their own transaction."""
    from app.storage.db import db_transaction

    with db_transaction() as connection:
        return import_seed_contexts_with_cursor(connection.cursor())
