"""Booking operations: proposal, confirmation and execution state (069).

Contract: docs/piloto/CONTRATOS.md, "Operações e propostas". Every state change
is a compare-and-set in the database, so a lost race returns ``False``/``None``
and leaves the row untouched. Nothing here performs network I/O, and
``last_error`` only ever stores a short error code, never customer text.

Timestamps are UTC ISO-8601 with fixed microsecond precision
(``2026-09-13T12:00:00.000000+00:00``). Expiry is compared as text in SQL; a
fixed width keeps text order identical to time order on every collation, which
``datetime.isoformat()`` alone does not (it drops a zero fraction).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.core.tenancy import require_company_id
from app.storage.db import get_database_backend, is_unique_violation
from app.storage.sql_support import execute_rowcount, fetch_all, fetch_one, transaction

KINDS = frozenset({"create", "cancel", "reschedule"})
TARGETED_KINDS = frozenset({"cancel", "reschedule"})
SOURCES = frozenset({"conversation", "admin_api", "operator", "automation"})
STATUSES = frozenset({
    "proposed", "confirmed", "executing", "completed", "rejected",
    "expired", "superseded", "failed", "review",
})
# A redelivered confirmation carrying the same reference is a replay: it must
# be reported as such and never confirm (or execute) the operation again.
REPLAY_STATUSES = frozenset({"confirmed", "executing", "completed", "failed", "review"})
REVIEW_OUTCOMES = frozenset({"completed", "failed"})
MAX_TTL_MINUTES = 1440
MAX_LIST_LIMIT = 500
MAX_REFERENCE_LENGTH = 200
IDEMPOTENCY_CONSTRAINT = "uq_booking_operations_idempotency"
CONFIRMATION_REF_INDEX = "uq_booking_operations_confirmation_ref"
_ERROR_CODE_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,80}")


class OperationConflictError(Exception):
    """An idempotency key was reused for a different operation."""


@dataclass(frozen=True)
class AcceptResult:
    outcome: str  # accepted | replay | expired | superseded | mismatch | not_found | not_open
    operation: dict[str, Any] | None


# ── validation and encoding ───────────────────────────────────────────


def _resolve_now(now: datetime | None) -> datetime:
    moment = datetime.now(UTC) if now is None else now
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime.")
    return moment.astimezone(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _require_positive_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _optional_positive_int(value: Any, *, name: str) -> int | None:
    return None if value is None else _require_positive_int(value, name=name)


def _require_contact_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("contact_id must be a non-empty string.")
    return value


def _require_reference(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_REFERENCE_LENGTH:
        raise ValueError(f"{name} must be a non-empty string of at most {MAX_REFERENCE_LENGTH} characters.")
    return value


def _optional_reference(value: Any, *, name: str) -> str | None:
    return None if value is None else _require_reference(value, name=name)


def _require_error_code(value: Any) -> str:
    if not isinstance(value, str) or not _ERROR_CODE_PATTERN.fullmatch(value):
        raise ValueError("error_code must be a short code (letters, digits, _ . : -), never free text.")
    return value


def _require_dict(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a dict.")
    return value


def _decode(row: Any) -> dict[str, Any]:
    operation = dict(row)
    params_json = operation.pop("params_json")
    result_json = operation.pop("result_json")
    operation["params"] = json.loads(params_json)
    operation["result"] = None if result_json is None else json.loads(result_json)
    return operation


@contextmanager
def _write_transaction() -> Iterator[Any]:
    """Transaction that holds the write lock before its first read.

    SQLite: a deferred transaction that reads and then writes can fail with
    "database is locked" when two connections try to upgrade at once, so the
    reserved lock is taken up front (the same approach as ``init_db``).
    PostgreSQL relies on row locks plus the advisory lock in ``create_proposal``.
    """
    with transaction() as cursor:
        if get_database_backend() == "sqlite":
            cursor.execute("BEGIN IMMEDIATE")
        yield cursor


def _lock_contact(cursor: Any, company_id: int, contact_id: str) -> None:
    # Serializes proposal creation per (company, contact) on PostgreSQL. Without
    # it two concurrent proposals both see "nothing open", and the second insert
    # hits the partial unique index instead of superseding the first. A hash
    # collision only serializes two unrelated contacts; it cannot corrupt state.
    if get_database_backend() != "postgres":
        return
    cursor.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
        (f"booking_operations:{company_id}:{contact_id}",),
    )
    cursor.fetchone()


# ── public API ────────────────────────────────────────────────────────


def compute_params_hash(
    *,
    kind: str,
    booking_id: int | None,
    resource_key: str | None,
    params: dict[str, Any],
    target_version: str | None,
) -> str:
    material = {
        "booking_id": booking_id,
        "kind": kind,
        "params": _require_dict(params, name="params"),
        "resource_key": resource_key,
        "target_version": target_version,
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def create_proposal(
    *,
    company_id: int,
    contact_id: str,
    kind: str,
    params: dict[str, Any],
    ttl_minutes: int,
    booking_id: int | None = None,
    resource_key: str | None = None,
    target_version: str | None = None,
    source: str = "conversation",
    flow_id: int | None = None,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    cid = require_company_id(company_id, context="booking operation proposal")
    contact = _require_contact_id(contact_id)
    if kind not in KINDS:
        raise ValueError(f"Unknown operation kind: {kind!r}.")
    if source not in SOURCES:
        raise ValueError(f"Unknown operation source: {source!r}.")
    if isinstance(ttl_minutes, bool) or not isinstance(ttl_minutes, int) or not 1 <= ttl_minutes <= MAX_TTL_MINUTES:
        raise ValueError(f"ttl_minutes must be an integer between 1 and {MAX_TTL_MINUTES}.")
    target_booking = _optional_positive_int(booking_id, name="booking_id")
    if kind in TARGETED_KINDS and target_booking is None:
        raise ValueError(f"A {kind} operation requires booking_id.")
    resource = _optional_reference(resource_key, name="resource_key")
    version = _optional_reference(target_version, name="target_version")
    flow = _optional_positive_int(flow_id, name="flow_id")
    explicit_key = _optional_reference(idempotency_key, name="idempotency_key")
    key = explicit_key if explicit_key is not None else uuid4().hex
    moment = _resolve_now(now)
    params_json = _canonical_json(_require_dict(params, name="params"))
    params_hash = compute_params_hash(
        kind=kind, booking_id=target_booking, resource_key=resource, params=params, target_version=version,
    )
    created_at = _iso(moment)
    expires_at = _iso(moment + timedelta(minutes=ttl_minutes))

    # Two attempts: a concurrent writer can commit the same explicit key (for
    # another contact, so outside the contact lock) between our check and our
    # insert. The second attempt sees that row and resolves it as replay/conflict.
    for attempt in range(2):
        try:
            with _write_transaction() as cursor:
                _lock_contact(cursor, cid, contact)
                if explicit_key is not None:
                    cursor.execute(
                        "SELECT * FROM booking_operations WHERE company_id = ? AND idempotency_key = ?",
                        (cid, key),
                    )
                    existing = cursor.fetchone()
                    if existing is not None:
                        # Same key, same contact, same parameters: return it as is.
                        # Another contact's operation is never handed back.
                        if existing["contact_id"] == contact and existing["params_hash"] == params_hash:
                            return _decode(existing)
                        raise OperationConflictError("idempotency_key was already used for a different operation.")
                cursor.execute(
                    """
                    UPDATE booking_operations SET status = 'superseded', updated_at = ?
                    WHERE company_id = ? AND contact_id = ? AND status = 'proposed'
                    """,
                    (created_at, cid, contact),
                )
                cursor.execute(
                    """
                    INSERT INTO booking_operations (
                        company_id, contact_id, kind, status, source, booking_id, resource_key,
                        params_json, params_hash, target_version, flow_id, idempotency_key,
                        expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (cid, contact, kind, source, target_booking, resource, params_json, params_hash,
                     version, flow, key, expires_at, created_at, created_at),
                )
                operation_id = cursor.lastrowid
                cursor.execute(
                    "SELECT * FROM booking_operations WHERE id = ? AND company_id = ?",
                    (operation_id, cid),
                )
                return _decode(cursor.fetchone())
        except OperationConflictError:
            raise
        except Exception as error:
            retryable = explicit_key is not None and attempt == 0 and is_unique_violation(
                error,
                postgres_constraint=IDEMPOTENCY_CONSTRAINT,
                sqlite_qualified_column="booking_operations.idempotency_key",
            )
            if not retryable:
                raise
    raise RuntimeError("create_proposal exhausted its attempts.")  # pragma: no cover - loop always returns or raises


def get_operation(*, company_id: int, operation_id: int) -> dict[str, Any] | None:
    cid = require_company_id(company_id, context="booking operation lookup")
    row = fetch_one(
        "SELECT * FROM booking_operations WHERE id = ? AND company_id = ?",
        (_require_positive_int(operation_id, name="operation_id"), cid),
    )
    return None if row is None else _decode(row)


def get_open_proposal(*, company_id: int, contact_id: str, now: datetime | None = None) -> dict[str, Any] | None:
    cid = require_company_id(company_id, context="booking operation open proposal")
    contact = _require_contact_id(contact_id)
    moment = _iso(_resolve_now(now))
    row = fetch_one(
        """
        SELECT * FROM booking_operations
        WHERE company_id = ? AND contact_id = ? AND status = 'proposed'
        ORDER BY id DESC LIMIT 1
        """,
        (cid, contact),
    )
    if row is None:
        return None
    if row["expires_at"] > moment:
        return _decode(row)
    execute_rowcount(
        """
        UPDATE booking_operations SET status = 'expired', updated_at = ?
        WHERE id = ? AND company_id = ? AND status = 'proposed' AND expires_at <= ?
        """,
        (moment, row["id"], cid, moment),
    )
    return None


def accept_proposal(
    *,
    company_id: int,
    contact_id: str,
    operation_id: int,
    params_hash: str,
    confirmation_ref: str,
    now: datetime | None = None,
) -> AcceptResult:
    cid = require_company_id(company_id, context="booking operation acceptance")
    contact = _require_contact_id(contact_id)
    op_id = _require_positive_int(operation_id, name="operation_id")
    expected_hash = _require_reference(params_hash, name="params_hash")
    reference = _require_reference(confirmation_ref, name="confirmation_ref")
    moment = _iso(_resolve_now(now))

    try:
        return _accept_once(cid, contact, op_id, expected_hash, reference, moment)
    except Exception as error:
        # A concurrent acceptance consumed the same confirmation for another
        # operation between our check and our update: that is a replay too.
        if not is_unique_violation(
            error,
            postgres_constraint=CONFIRMATION_REF_INDEX,
            sqlite_qualified_column="booking_operations.confirmation_ref",
        ):
            raise
        prior = fetch_one(
            "SELECT * FROM booking_operations WHERE company_id = ? AND confirmation_ref = ?",
            (cid, reference),
        )
        if prior is not None and prior["contact_id"] == contact:
            return AcceptResult("replay", _decode(prior))
        return AcceptResult("not_open", None)


def _accept_once(
    cid: int,
    contact: str,
    op_id: int,
    expected_hash: str,
    reference: str,
    moment: str,
) -> AcceptResult:
    with _write_transaction() as cursor:
        # A confirmation reference is consumed at most once per company. A
        # redelivered "sim" that already confirmed operation 1 must not confirm
        # operation 2 proposed afterwards; it reports the original instead.
        cursor.execute(
            "SELECT * FROM booking_operations WHERE company_id = ? AND confirmation_ref = ?",
            (cid, reference),
        )
        prior = cursor.fetchone()
        if prior is not None:
            if prior["contact_id"] == contact:
                return AcceptResult("replay", _decode(prior))
            return AcceptResult("not_open", None)

        cursor.execute(
            """
            UPDATE booking_operations
            SET status = 'confirmed', confirmed_at = ?, confirmation_ref = ?, updated_at = ?
            WHERE id = ? AND company_id = ? AND contact_id = ? AND status = 'proposed'
              AND params_hash = ? AND expires_at > ?
            RETURNING *
            """,
            (moment, reference, moment, op_id, cid, contact, expected_hash, moment),
        )
        accepted = cursor.fetchall()
        if accepted:
            return AcceptResult("accepted", _decode(accepted[0]))

        cursor.execute("SELECT * FROM booking_operations WHERE id = ? AND company_id = ?", (op_id, cid))
        row = cursor.fetchone()
        if row is None or row["contact_id"] != contact:
            return AcceptResult("not_found", None)
        status = row["status"]
        if status in REPLAY_STATUSES and row["confirmation_ref"] == reference:
            return AcceptResult("replay", _decode(row))
        if status == "expired":
            return AcceptResult("expired", _decode(row))
        if status == "proposed" and row["expires_at"] <= moment:
            cursor.execute(
                """
                UPDATE booking_operations SET status = 'expired', updated_at = ?
                WHERE id = ? AND company_id = ? AND status = 'proposed' AND expires_at <= ?
                RETURNING *
                """,
                (moment, op_id, cid, moment),
            )
            expired = cursor.fetchall()
            return AcceptResult("expired", _decode(expired[0] if expired else row))
        if status == "superseded":
            return AcceptResult("superseded", _decode(row))
        if status == "proposed" and row["params_hash"] != expected_hash:
            return AcceptResult("mismatch", _decode(row))
        return AcceptResult("not_open", _decode(row))


def reject_proposal(*, company_id: int, contact_id: str, operation_id: int, now: datetime | None = None) -> bool:
    cid = require_company_id(company_id, context="booking operation rejection")
    moment = _iso(_resolve_now(now))
    return execute_rowcount(
        """
        UPDATE booking_operations SET status = 'rejected', updated_at = ?
        WHERE id = ? AND company_id = ? AND contact_id = ? AND status = 'proposed' AND expires_at > ?
        """,
        (moment, _require_positive_int(operation_id, name="operation_id"), cid,
         _require_contact_id(contact_id), moment),
    ) == 1


def claim_for_execution(*, company_id: int, operation_id: int, now: datetime | None = None) -> dict[str, Any] | None:
    cid = require_company_id(company_id, context="booking operation claim")
    moment = _iso(_resolve_now(now))
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE booking_operations
            SET status = 'executing', attempt_count = attempt_count + 1, started_at = ?, updated_at = ?
            WHERE id = ? AND company_id = ? AND status = 'confirmed'
            RETURNING *
            """,
            (moment, moment, _require_positive_int(operation_id, name="operation_id"), cid),
        )
        rows = cursor.fetchall()
    return _decode(rows[0]) if rows else None


def complete_operation(
    *,
    company_id: int,
    operation_id: int,
    result: dict[str, Any],
    booking_id: int | None = None,
    external_ref: str | None = None,
    now: datetime | None = None,
) -> bool:
    cid = require_company_id(company_id, context="booking operation completion")
    moment = _iso(_resolve_now(now))
    return execute_rowcount(
        """
        UPDATE booking_operations
        SET status = 'completed', result_json = ?, booking_id = COALESCE(?, booking_id),
            external_ref = COALESCE(?, external_ref), completed_at = ?, updated_at = ?
        WHERE id = ? AND company_id = ? AND status = 'executing'
        """,
        (_canonical_json(_require_dict(result, name="result")),
         _optional_positive_int(booking_id, name="booking_id"),
         _optional_reference(external_ref, name="external_ref"),
         moment, moment, _require_positive_int(operation_id, name="operation_id"), cid),
    ) == 1


def fail_operation(*, company_id: int, operation_id: int, error_code: str, now: datetime | None = None) -> bool:
    cid = require_company_id(company_id, context="booking operation failure")
    moment = _iso(_resolve_now(now))
    return execute_rowcount(
        """
        UPDATE booking_operations SET status = 'failed', last_error = ?, updated_at = ?
        WHERE id = ? AND company_id = ? AND status = 'executing'
        """,
        (_require_error_code(error_code), moment, _require_positive_int(operation_id, name="operation_id"), cid),
    ) == 1


def mark_review(*, company_id: int, operation_id: int, error_code: str, now: datetime | None = None) -> bool:
    cid = require_company_id(company_id, context="booking operation review")
    moment = _iso(_resolve_now(now))
    return execute_rowcount(
        """
        UPDATE booking_operations SET status = 'review', last_error = ?, updated_at = ?
        WHERE id = ? AND company_id = ? AND status = 'executing'
        """,
        (_require_error_code(error_code), moment, _require_positive_int(operation_id, name="operation_id"), cid),
    ) == 1


def resolve_review(
    *,
    company_id: int,
    operation_id: int,
    outcome: str,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    now: datetime | None = None,
) -> bool:
    cid = require_company_id(company_id, context="booking operation review resolution")
    if outcome not in REVIEW_OUTCOMES:
        raise ValueError(f"Review outcome must be one of {sorted(REVIEW_OUTCOMES)}.")
    op_id = _require_positive_int(operation_id, name="operation_id")
    result_json = None if result is None else _canonical_json(_require_dict(result, name="result"))
    code = None if error_code is None else _require_error_code(error_code)
    moment = _iso(_resolve_now(now))
    if outcome == "completed":
        return execute_rowcount(
            """
            UPDATE booking_operations
            SET status = 'completed', result_json = COALESCE(?, result_json),
                last_error = COALESCE(?, last_error), completed_at = ?, updated_at = ?
            WHERE id = ? AND company_id = ? AND status = 'review'
            """,
            (result_json, code, moment, moment, op_id, cid),
        ) == 1
    return execute_rowcount(
        """
        UPDATE booking_operations
        SET status = 'failed', result_json = COALESCE(?, result_json),
            last_error = COALESCE(?, last_error), updated_at = ?
        WHERE id = ? AND company_id = ? AND status = 'review'
        """,
        (result_json, code, moment, op_id, cid),
    ) == 1


def fail_stale_confirmations(
    *,
    older_than_minutes: int = 10,
    now: datetime | None = None,
    company_id: int | None = None,
) -> int:
    """`confirmed` que nunca foi reivindicado para execução vira `failed`.

    Aceite e claim são transações separadas; uma queda entre as duas deixaria a
    operação em `confirmed` para sempre. Nenhum efeito ocorreu (o efeito só roda
    depois do claim), então falhar é seguro e a reentrega deixa de responder
    "em processamento" indefinidamente.
    """
    if isinstance(older_than_minutes, bool) or not isinstance(older_than_minutes, int) or older_than_minutes < 1:
        raise ValueError("older_than_minutes must be a positive integer.")
    moment = _resolve_now(now)
    threshold = _iso(moment - timedelta(minutes=older_than_minutes))
    stamp = _iso(moment)
    if company_id is None:
        return execute_rowcount(
            """
            UPDATE booking_operations SET status = 'failed', last_error = 'confirmation_not_executed', updated_at = ?
            WHERE status = 'confirmed' AND confirmed_at <= ?
            """,
            (stamp, threshold),
        )
    cid = require_company_id(company_id, context="booking operation stale confirmation")
    return execute_rowcount(
        """
        UPDATE booking_operations SET status = 'failed', last_error = 'confirmation_not_executed', updated_at = ?
        WHERE status = 'confirmed' AND confirmed_at <= ? AND company_id = ?
        """,
        (stamp, threshold, cid),
    )


def expire_open_proposals(*, now: datetime | None = None, company_id: int | None = None) -> int:
    moment = _iso(_resolve_now(now))
    if company_id is None:
        return execute_rowcount(
            """
            UPDATE booking_operations SET status = 'expired', updated_at = ?
            WHERE status = 'proposed' AND expires_at <= ?
            """,
            (moment, moment),
        )
    cid = require_company_id(company_id, context="booking operation expiry")
    return execute_rowcount(
        """
        UPDATE booking_operations SET status = 'expired', updated_at = ?
        WHERE status = 'proposed' AND expires_at <= ? AND company_id = ?
        """,
        (moment, moment, cid),
    )


def list_operations(
    *,
    company_id: int,
    contact_id: str | None = None,
    statuses: Iterable[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    cid = require_company_id(company_id, context="booking operation listing")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIST_LIMIT:
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIST_LIMIT}.")
    params: list[Any] = [cid]
    contact_clause = ""
    if contact_id is not None:
        contact_clause = " AND contact_id = ?"
        params.append(_require_contact_id(contact_id))
    status_clause = ""
    if statuses is not None:
        requested = sorted(set(statuses))
        unknown = [status for status in requested if status not in STATUSES]
        if unknown:
            raise ValueError("Unknown operation status filter.")
        if not requested:
            return []
        # Only literal placeholders reach the SQL text; the values are bound.
        placeholders = ", ".join("?" for _status in requested)
        status_clause = " AND status IN (" + placeholders + ")"
        params.extend(requested)
    params.append(limit)
    query = (
        "SELECT * FROM booking_operations WHERE company_id = ?"
        + contact_clause
        + status_clause
        + " ORDER BY id DESC LIMIT ?"
    )
    return [_decode(row) for row in fetch_all(query, params)]
