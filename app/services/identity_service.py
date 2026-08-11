"""Durable SaaS identity, tenant membership and revocable session primitives."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.storage.db import db_session, db_transaction


IDENTITY_ROLES = ("viewer", "operator", "admin", "owner")
ROLE_WEIGHT = {"viewer": 1, "operator": 2, "admin": 3, "owner": 4}
ADMIN_ROLE_MAP = {
    "viewer": "viewer",
    "operator": "operator",
    "admin": "superadmin",
    "owner": "superadmin",
}
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
_PASSWORD_SCHEME = "scrypt"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


class IdentityError(Exception):
    """Base class for safe identity-domain failures."""


class IdentityAuthenticationError(IdentityError):
    pass


class IdentityAuthorizationError(IdentityError):
    pass


class IdentityConflictError(IdentityError):
    pass


def normalize_email(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if len(normalized) > 254 or not _EMAIL_PATTERN.fullmatch(normalized):
        raise IdentityError("A valid email address is required.")
    return normalized


def normalize_role(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in IDENTITY_ROLES:
        raise IdentityError(f"role must be one of: {', '.join(IDENTITY_ROLES)}.")
    return normalized


def normalize_company_slug(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SLUG_PATTERN.fullmatch(normalized):
        raise IdentityError(
            "Company slug must contain 3-63 lowercase letters, numbers or hyphens."
        )
    return normalized


def validate_password(password: str) -> None:
    if len(password) < 12:
        raise IdentityError("Password must contain at least 12 characters.")
    if len(password) > 256:
        raise IdentityError("Password must contain at most 256 characters.")
    character_groups = (
        any(item.islower() for item in password),
        any(item.isupper() for item in password),
        any(item.isdigit() for item in password),
        any(not item.isalnum() for item in password),
    )
    if sum(character_groups) < 3:
        raise IdentityError(
            "Password must use at least three of: lowercase, uppercase, number and symbol."
        )


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return (
        f"{_PASSWORD_SCHEME}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}"
        f"${salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded_hash.split("$", 5)
        if scheme != _PASSWORD_SCHEME:
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(raw_salt),
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(bytes.fromhex(raw_digest)),
        )
        return hmac.compare_digest(digest, bytes.fromhex(raw_digest))
    except (TypeError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _public_user(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "email": str(row["email"]),
        "display_name": str(row["display_name"]),
        "active": bool(row["active"]),
        "email_verified_at": row.get("email_verified_at"),
        "last_login_at": row.get("last_login_at"),
        "created_at": row.get("created_at"),
    }


def _fetch_user_by_email(email_normalized: str) -> dict[str, Any] | None:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT *
            FROM saas_users
            WHERE email_normalized = ?
            """,
            (email_normalized,),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def get_user(user_id: int) -> dict[str, Any] | None:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM saas_users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
    return _public_user(dict(row)) if row else None


def get_user_memberships(user_id: int) -> list[dict[str, Any]]:
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                membership.id,
                membership.company_id,
                membership.user_id,
                membership.role,
                membership.active,
                membership.created_at,
                membership.updated_at,
                company.slug AS company_slug,
                company.name AS company_name,
                company.active AS company_active
            FROM company_memberships AS membership
            JOIN companies AS company ON company.id = membership.company_id
            WHERE membership.user_id = ?
              AND membership.active = 1
              AND company.active = 1
            ORDER BY company.name ASC, company.id ASC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
    return [
        {
            **dict(row),
            "id": int(row["id"]),
            "company_id": int(row["company_id"]),
            "user_id": int(row["user_id"]),
            "active": bool(row["active"]),
            "company_active": bool(row["company_active"]),
        }
        for row in rows
    ]


def create_user_with_company(
    *,
    email: str,
    password: str,
    display_name: str,
    company_name: str,
    company_slug: str,
    business_type: str | None = None,
) -> dict[str, Any]:
    if not settings.enable_self_service_signup:
        raise IdentityAuthorizationError("Self-service signup is disabled.")

    email_normalized = normalize_email(email)
    normalized_slug = normalize_company_slug(company_slug)
    normalized_display_name = str(display_name or "").strip()
    normalized_company_name = str(company_name or "").strip()
    if not normalized_display_name:
        raise IdentityError("Display name is required.")
    if not normalized_company_name:
        raise IdentityError("Company name is required.")
    password_hash = hash_password(password)

    if _fetch_user_by_email(email_normalized):
        raise IdentityConflictError("An account already exists for this email.")

    try:
        with db_transaction() as connection:
            cursor = connection.cursor()
            cursor.execute("SELECT id FROM companies WHERE slug = ?", (normalized_slug,))
            if cursor.fetchone():
                raise IdentityConflictError("Company slug already exists.")
            cursor.execute(
                """
                INSERT INTO companies (slug, name, business_type, active, updated_at)
                VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
                """,
                (normalized_slug, normalized_company_name, business_type),
            )
            company_id = int(cursor.lastrowid)
            cursor.execute(
                """
                INSERT INTO saas_users (
                    email,
                    email_normalized,
                    display_name,
                    password_hash,
                    active,
                    email_verified_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (email_normalized, email_normalized, normalized_display_name, password_hash),
            )
            user_id = int(cursor.lastrowid)
            cursor.execute(
                """
                INSERT INTO company_memberships (company_id, user_id, role, active)
                VALUES (?, ?, 'owner', 1)
                """,
                (company_id, user_id),
            )
    except IdentityError:
        raise
    except Exception as exc:
        if _fetch_user_by_email(email_normalized):
            raise IdentityConflictError("An account already exists for this email.") from exc
        raise IdentityConflictError("Unable to create account; company slug may already exist.") from exc

    return {
        "user": get_user(user_id),
        "company_id": company_id,
        "company_slug": normalized_slug,
        "memberships": get_user_memberships(user_id),
    }


def authenticate_user(email: str, password: str) -> dict[str, Any]:
    try:
        email_normalized = normalize_email(email)
    except IdentityError as exc:
        raise IdentityAuthenticationError("Invalid email or password.") from exc

    user = _fetch_user_by_email(email_normalized)
    encoded_hash = str(user.get("password_hash") or "") if user else ""
    if not user or not bool(user.get("active")) or not verify_password(password, encoded_hash):
        raise IdentityAuthenticationError("Invalid email or password.")

    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE saas_users
            SET last_login_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(user["id"]),),
        )
    return {
        "user": get_user(int(user["id"])),
        "memberships": get_user_memberships(int(user["id"])),
    }


def issue_user_session(
    *,
    user_id: int,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    max_age = max(300, int(settings.identity_session_ttl_seconds))
    expires_at = datetime.now(UTC) + timedelta(seconds=max_age)

    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO user_sessions (
                user_id,
                token_hash,
                csrf_token_hash,
                expires_at,
                user_agent,
                ip_address
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                _token_hash(token),
                _token_hash(csrf_token),
                _timestamp(expires_at),
                str(user_agent or "")[:512] or None,
                str(ip_address or "")[:128] or None,
            ),
        )

    return {
        "token": token,
        "csrf_token": csrf_token,
        "max_age": max_age,
        "expires_at": int(expires_at.timestamp()),
    }


def read_user_session(token: str | None) -> dict[str, Any]:
    if not token:
        raise IdentityAuthenticationError("Missing user session.")
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                session.id AS session_id,
                session.user_id,
                session.csrf_token_hash,
                session.expires_at,
                session.revoked_at,
                account.email,
                account.display_name,
                account.active
            FROM user_sessions AS session
            JOIN saas_users AS account ON account.id = session.user_id
            WHERE session.token_hash = ?
            """,
            (_token_hash(token),),
        )
        row = cursor.fetchone()

    if not row or row["revoked_at"] is not None or not bool(row["active"]):
        raise IdentityAuthenticationError("Invalid user session.")
    if _parse_timestamp(row["expires_at"]) <= datetime.now(UTC):
        raise IdentityAuthenticationError("User session expired.")

    user_id = int(row["user_id"])
    memberships = get_user_memberships(user_id)
    membership_roles = {
        int(item["company_id"]): str(item["role"])
        for item in memberships
    }
    highest_identity_role = max(
        membership_roles.values(),
        key=lambda role: ROLE_WEIGHT.get(role, 0),
        default="viewer",
    )
    return {
        "auth_mode": "identity",
        "session_id": int(row["session_id"]),
        "user_id": user_id,
        "email": str(row["email"]),
        "actor": str(row["display_name"]),
        "role": ADMIN_ROLE_MAP[highest_identity_role],
        "identity_role": highest_identity_role,
        "company_ids": sorted(membership_roles),
        "membership_roles": membership_roles,
        "memberships": memberships,
        "csrf_token_hash": str(row["csrf_token_hash"]),
        "expires_at": int(_parse_timestamp(row["expires_at"]).timestamp()),
    }


def verify_session_csrf(session: dict[str, Any], csrf_token: str | None) -> bool:
    if not csrf_token:
        return False
    expected = str(session.get("csrf_token_hash") or "")
    return bool(expected) and hmac.compare_digest(expected, _token_hash(csrf_token))


def revoke_user_session(token: str | None) -> None:
    if not token:
        return
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE user_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE token_hash = ?
              AND revoked_at IS NULL
            """,
            (_token_hash(token),),
        )


def create_invitation(
    *,
    company_id: int,
    email: str,
    role: str,
    invited_by_user_id: int,
) -> dict[str, Any]:
    email_normalized = normalize_email(email)
    normalized_role = normalize_role(role)
    if normalized_role == "owner":
        raise IdentityAuthorizationError("Owner access cannot be granted by invitation.")

    inviter_memberships = get_user_memberships(invited_by_user_id)
    inviter_roles = {
        int(item["company_id"]): str(item["role"])
        for item in inviter_memberships
    }
    inviter_role = inviter_roles.get(company_id)
    if inviter_role not in {"owner", "admin"}:
        raise IdentityAuthorizationError("Only a company owner or admin can invite members.")
    if inviter_role == "admin" and normalized_role == "admin":
        raise IdentityAuthorizationError("Only an owner can invite another admin.")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(
        hours=max(1, int(settings.identity_invitation_ttl_hours))
    )
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE user_invitations
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE company_id = ?
              AND email_normalized = ?
              AND accepted_at IS NULL
              AND revoked_at IS NULL
            """,
            (company_id, email_normalized),
        )
        cursor.execute(
            """
            INSERT INTO user_invitations (
                company_id,
                email,
                email_normalized,
                role,
                token_hash,
                invited_by_user_id,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                email_normalized,
                email_normalized,
                normalized_role,
                _token_hash(token),
                invited_by_user_id,
                _timestamp(expires_at),
            ),
        )
        invitation_id = int(cursor.lastrowid)
    return {
        "id": invitation_id,
        "company_id": company_id,
        "company_name": str(
            next(
                item["company_name"]
                for item in inviter_memberships
                if int(item["company_id"]) == company_id
            )
        ),
        "email": email_normalized,
        "role": normalized_role,
        "expires_at": _timestamp(expires_at),
        "token": token,
    }


def accept_invitation(
    *,
    token: str,
    password: str,
    display_name: str,
) -> dict[str, Any]:
    validate_password(password)
    token_digest = _token_hash(str(token or ""))
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT *
            FROM user_invitations
            WHERE token_hash = ?
            """,
            (token_digest,),
        )
        row = cursor.fetchone()
        if (
            not row
            or row["accepted_at"] is not None
            or row["revoked_at"] is not None
            or _parse_timestamp(row["expires_at"]) <= datetime.now(UTC)
        ):
            raise IdentityAuthenticationError("Invitation is invalid or expired.")

        invitation = dict(row)
        cursor.execute(
            "SELECT * FROM saas_users WHERE email_normalized = ?",
            (invitation["email_normalized"],),
        )
        existing_user = cursor.fetchone()
        if existing_user:
            user_id = int(existing_user["id"])
            if not bool(existing_user["active"]):
                raise IdentityAuthorizationError("This account is inactive.")
        else:
            normalized_display_name = str(display_name or "").strip()
            if not normalized_display_name:
                raise IdentityError("Display name is required.")
            cursor.execute(
                """
                INSERT INTO saas_users (
                    email,
                    email_normalized,
                    display_name,
                    password_hash,
                    active,
                    email_verified_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    invitation["email"],
                    invitation["email_normalized"],
                    normalized_display_name,
                    hash_password(password),
                ),
            )
            user_id = int(cursor.lastrowid)

        cursor.execute(
            """
            INSERT INTO company_memberships (
                company_id,
                user_id,
                role,
                active,
                invited_by_user_id,
                updated_at
            )
            VALUES (?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (company_id, user_id)
            DO UPDATE SET
                role = excluded.role,
                active = 1,
                invited_by_user_id = excluded.invited_by_user_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(invitation["company_id"]),
                user_id,
                invitation["role"],
                int(invitation["invited_by_user_id"]),
            ),
        )
        cursor.execute(
            """
            UPDATE user_invitations
            SET accepted_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND accepted_at IS NULL
              AND revoked_at IS NULL
            """,
            (int(invitation["id"]),),
        )
        if cursor.rowcount != 1:
            raise IdentityConflictError("Invitation was already consumed.")

    return {
        "user": get_user(user_id),
        "memberships": get_user_memberships(user_id),
        "company_id": int(invitation["company_id"]),
    }


def list_company_members(
    *, company_id: int, requested_by_user_id: int
) -> list[dict[str, Any]]:
    requester_roles = {
        int(item["company_id"]): str(item["role"])
        for item in get_user_memberships(requested_by_user_id)
    }
    if requester_roles.get(company_id) not in {"owner", "admin"}:
        raise IdentityAuthorizationError("Only a company owner or admin can list members.")
    with db_session() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                membership.id,
                membership.company_id,
                membership.user_id,
                membership.role,
                membership.active,
                membership.created_at,
                membership.updated_at,
                account.email,
                account.display_name
            FROM company_memberships AS membership
            JOIN saas_users AS account ON account.id = membership.user_id
            WHERE membership.company_id = ?
            ORDER BY membership.active DESC, account.display_name ASC, account.id ASC
            """,
            (company_id,),
        )
        rows = cursor.fetchall()
    return [
        {
            **dict(row),
            "id": int(row["id"]),
            "company_id": int(row["company_id"]),
            "user_id": int(row["user_id"]),
            "active": bool(row["active"]),
        }
        for row in rows
    ]


def update_company_member(
    *,
    company_id: int,
    membership_id: int,
    requested_by_user_id: int,
    role: str | None = None,
    active: bool | None = None,
) -> dict[str, Any]:
    requester_roles = {
        int(item["company_id"]): str(item["role"])
        for item in get_user_memberships(requested_by_user_id)
    }
    requester_role = requester_roles.get(company_id)
    if requester_role not in {"owner", "admin"}:
        raise IdentityAuthorizationError("Only a company owner or admin can manage members.")
    normalized_role = normalize_role(role) if role is not None else None
    if requester_role != "owner" and normalized_role in {"owner", "admin"}:
        raise IdentityAuthorizationError("Only an owner can grant owner or admin access.")

    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT *
            FROM company_memberships
            WHERE id = ?
              AND company_id = ?
            """,
            (membership_id, company_id),
        )
        row = cursor.fetchone()
        if not row:
            raise IdentityError("Membership not found.")
        membership = dict(row)
        target_role = normalized_role or str(membership["role"])
        target_active = bool(membership["active"]) if active is None else bool(active)
        removes_owner = str(membership["role"]) == "owner" and (
            target_role != "owner" or not target_active
        )
        if removes_owner:
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM company_memberships
                WHERE company_id = ?
                  AND role = 'owner'
                  AND active = 1
                  AND id != ?
                """,
                (company_id, membership_id),
            )
            if int(cursor.fetchone()["count"]) == 0:
                raise IdentityConflictError("A company must keep at least one active owner.")
        cursor.execute(
            """
            UPDATE company_memberships
            SET role = ?,
                active = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND company_id = ?
            """,
            (target_role, 1 if target_active else 0, membership_id, company_id),
        )

    members = list_company_members(
        company_id=company_id,
        requested_by_user_id=requested_by_user_id,
    )
    return next(item for item in members if int(item["id"]) == membership_id)


def request_password_reset(email: str) -> dict[str, Any] | None:
    try:
        email_normalized = normalize_email(email)
    except IdentityError:
        return None
    user = _fetch_user_by_email(email_normalized)
    if not user or not bool(user.get("active")):
        return None
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(
        minutes=max(5, int(settings.identity_password_reset_ttl_minutes))
    )
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE password_reset_tokens
            SET consumed_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
              AND consumed_at IS NULL
            """,
            (int(user["id"]),),
        )
        cursor.execute(
            """
            INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
            VALUES (?, ?, ?)
            """,
            (int(user["id"]), _token_hash(token), _timestamp(expires_at)),
        )
    return {
        "token": token,
        "expires_at": _timestamp(expires_at),
        "email": email_normalized,
        "display_name": str(user["display_name"]),
    }


def confirm_password_reset(*, token: str, password: str) -> None:
    password_hash = hash_password(password)
    with db_transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT * FROM password_reset_tokens WHERE token_hash = ?",
            (_token_hash(str(token or "")),),
        )
        row = cursor.fetchone()
        if (
            not row
            or row["consumed_at"] is not None
            or _parse_timestamp(row["expires_at"]) <= datetime.now(UTC)
        ):
            raise IdentityAuthenticationError("Password reset token is invalid or expired.")
        user_id = int(row["user_id"])
        cursor.execute(
            """
            UPDATE saas_users
            SET password_hash = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (password_hash, user_id),
        )
        cursor.execute(
            """
            UPDATE password_reset_tokens
            SET consumed_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
              AND consumed_at IS NULL
            """,
            (user_id,),
        )
        cursor.execute(
            """
            UPDATE user_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
              AND revoked_at IS NULL
            """,
            (user_id,),
        )
