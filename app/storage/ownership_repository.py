"""Posse da conversa por empresa e contato (migration 072).

Tomar e devolver são compare-and-set: dois operadores clicando ao mesmo tempo
não assumem a mesma conversa, e um operador não devolve conversa de outro.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.tenancy import require_company_id
from app.storage.sql_support import fetch_all, fetch_one, transaction

MAX_REASON_LENGTH = 200


@dataclass(frozen=True)
class OwnershipResult:
    outcome: str  # taken | already_yours | owned_by_other | released | not_owned
    ownership: dict[str, Any] | None


def _iso(now: datetime | None) -> str:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _require_contact(contact_id: Any) -> str:
    if not isinstance(contact_id, str) or not contact_id.strip():
        raise ValueError("contact_id must be a non-empty string.")
    return contact_id


def _require_user(user_id: Any) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("user_id must be a positive integer.")
    return user_id


def _select(cursor: Any, company_id: int, contact_id: str) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT company_id, contact_id, owner, owner_user_id, reason, version, taken_at, released_at, updated_at
        FROM conversation_ownerships
        WHERE company_id = ? AND contact_id = ?
        """,
        (company_id, contact_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def get_ownership(*, company_id: int, contact_id: str) -> dict[str, Any] | None:
    cid = require_company_id(company_id, context="conversation ownership")
    return fetch_one(
        """
        SELECT company_id, contact_id, owner, owner_user_id, reason, version, taken_at, released_at, updated_at
        FROM conversation_ownerships
        WHERE company_id = ? AND contact_id = ?
        """,
        (cid, _require_contact(contact_id)),
    )


def is_human_owned(*, company_id: int, contact_id: str) -> bool:
    ownership = get_ownership(company_id=company_id, contact_id=contact_id)
    return bool(ownership and ownership.get("owner") == "human")


def take_ownership(
    *,
    company_id: int,
    contact_id: str,
    user_id: int,
    reason: str | None = None,
    now: datetime | None = None,
) -> OwnershipResult:
    cid = require_company_id(company_id, context="conversation ownership take")
    contact = _require_contact(contact_id)
    user = _require_user(user_id)
    stamp = _iso(now)
    note = (reason or "").strip()[:MAX_REASON_LENGTH] or None
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO conversation_ownerships (company_id, contact_id, owner, updated_at)
            VALUES (?, ?, 'bot', ?)
            ON CONFLICT (company_id, contact_id) DO NOTHING
            """,
            (cid, contact, stamp),
        )
        cursor.execute(
            """
            UPDATE conversation_ownerships
            SET owner = 'human', owner_user_id = ?, reason = ?, taken_at = ?, released_at = NULL,
                version = version + 1, updated_at = ?
            WHERE company_id = ? AND contact_id = ? AND owner = 'bot'
            """,
            (user, note, stamp, stamp, cid, contact),
        )
        taken = cursor.rowcount == 1
        current = _select(cursor, cid, contact)
    if taken:
        return OwnershipResult("taken", current)
    if current and current.get("owner_user_id") == user:
        return OwnershipResult("already_yours", current)
    return OwnershipResult("owned_by_other", current)


def release_ownership(
    *,
    company_id: int,
    contact_id: str,
    user_id: int,
    allow_other_owner: bool = False,
    now: datetime | None = None,
) -> OwnershipResult:
    """Devolve a conversa ao bot.

    Só quem assumiu devolve, salvo `allow_other_owner` (papel com autoridade
    sobre a equipe, decidido na rota).
    """
    cid = require_company_id(company_id, context="conversation ownership release")
    contact = _require_contact(contact_id)
    user = _require_user(user_id)
    stamp = _iso(now)
    with transaction() as cursor:
        if allow_other_owner:
            cursor.execute(
                """
                UPDATE conversation_ownerships
                SET owner = 'bot', released_at = ?, version = version + 1, updated_at = ?
                WHERE company_id = ? AND contact_id = ? AND owner = 'human'
                """,
                (stamp, stamp, cid, contact),
            )
        else:
            cursor.execute(
                """
                UPDATE conversation_ownerships
                SET owner = 'bot', released_at = ?, version = version + 1, updated_at = ?
                WHERE company_id = ? AND contact_id = ? AND owner = 'human' AND owner_user_id = ?
                """,
                (stamp, stamp, cid, contact, user),
            )
        released = cursor.rowcount == 1
        current = _select(cursor, cid, contact)
    if released:
        return OwnershipResult("released", current)
    if current and current.get("owner") == "human":
        return OwnershipResult("owned_by_other", current)
    return OwnershipResult("not_owned", current)


def list_human_owned(*, company_id: int, limit: int = 100) -> list[dict[str, Any]]:
    cid = require_company_id(company_id, context="conversation ownership listing")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500.")
    return fetch_all(
        """
        SELECT company_id, contact_id, owner, owner_user_id, reason, version, taken_at, released_at, updated_at
        FROM conversation_ownerships
        WHERE company_id = ? AND owner = 'human'
        ORDER BY taken_at ASC
        LIMIT ?
        """,
        (cid, limit),
    )
