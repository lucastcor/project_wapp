import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Response

from app.core.config import settings
from app.services.admin_auth_service import (
    get_role_token_map,
    resolve_admin_role_from_token,
)
from app.services.identity_service import (
    IdentityAuthenticationError,
    authenticate_user,
    issue_user_session,
    read_user_session,
    revoke_user_session,
)


class PlatformSessionError(Exception):
    pass


COOKIE_NAME = "project_wapp_platform_session"
CSRF_COOKIE_NAME = "project_wapp_csrf"
PLATFORM_ROLES = ("viewer", "operator", "superadmin")


def _parse_company_ids(raw_value: str | None) -> list[int] | None:
    """Parse ``ADMIN_<ROLE>_COMPANY_IDS``; empty/unset means every company."""
    normalized = str(raw_value or "").strip()
    if not normalized:
        return None

    company_ids: list[int] = []
    for item in normalized.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            parsed = int(candidate)
        except ValueError:
            continue
        if parsed not in company_ids:
            company_ids.append(parsed)
    # An explicitly configured but unparsable value fails closed (no company).
    return company_ids


def resolve_role_company_scope(role: str) -> list[int] | None:
    """Resolve the tenant scope of a platform role. ``None`` means all companies."""
    configured_scope = {
        "viewer": settings.admin_viewer_company_ids,
        "operator": settings.admin_operator_company_ids,
        "superadmin": settings.admin_superadmin_company_ids,
    }.get(role)
    return _parse_company_ids(configured_scope)


def _get_session_secret() -> str:
    secret = (settings.platform_session_secret or "").strip()
    if secret:
        return secret

    fallback_source = (
        settings.admin_superadmin_token
        or settings.admin_api_token
        or settings.whatsapp_app_secret
        or "project-wapp-platform-dev-secret"
    )
    return str(fallback_source)


def _sign(payload_segment: str) -> str:
    digest = hmac.new(
        _get_session_secret().encode("utf-8"),
        payload_segment.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _encode(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_segment = base64.urlsafe_b64encode(serialized).decode("utf-8").rstrip("=")
    signature_segment = _sign(payload_segment)
    return f"{payload_segment}.{signature_segment}"


def _decode(token: str) -> dict[str, Any]:
    try:
        payload_segment, signature_segment = token.split(".", 1)
    except ValueError as exc:
        raise PlatformSessionError("Invalid platform session token.") from exc

    expected_signature = _sign(payload_segment)
    if not hmac.compare_digest(expected_signature, signature_segment):
        raise PlatformSessionError("Invalid platform session signature.")

    padded_payload = payload_segment + "=" * (-len(payload_segment) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(padded_payload.encode("utf-8"))
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise PlatformSessionError("Invalid platform session payload.") from exc

    if not isinstance(payload, dict):
        raise PlatformSessionError("Invalid platform session payload.")
    return payload


def issue_platform_session(
    *,
    role: str,
    actor: str,
) -> dict[str, Any]:
    now = int(time.time())
    max_age = max(300, int(settings.platform_session_ttl_seconds))
    expires_at = now + max_age
    payload = {
        "role": role,
        "actor": actor,
        "iat": now,
        "exp": expires_at,
    }
    payload["token"] = _encode(payload)
    payload["max_age"] = max_age
    payload["expires_at"] = expires_at
    return payload


def read_platform_session(token: str | None) -> dict[str, Any]:
    if not token:
        raise PlatformSessionError("Missing platform session.")

    # Opaque identity sessions never contain a period; the legacy signed
    # break-glass cookie always has ``payload.signature``.
    if "." not in token:
        try:
            return read_user_session(token)
        except IdentityAuthenticationError as exc:
            raise PlatformSessionError(str(exc)) from exc

    payload = _decode(token)
    role = str(payload.get("role") or "").strip()
    actor = str(payload.get("actor") or "").strip() or "platform-user"
    exp = int(payload.get("exp") or 0)
    if role not in PLATFORM_ROLES:
        raise PlatformSessionError("Invalid platform session role.")
    if exp <= int(time.time()):
        raise PlatformSessionError("Platform session expired.")
    return {
        "role": role,
        "actor": actor,
        "auth_mode": "session",
        "expires_at": exp,
        "issued_at": int(payload.get("iat") or 0),
        # Scope is re-derived from server configuration on every read so a stale
        # cookie can never widen the tenant scope of a session.
        "company_ids": resolve_role_company_scope(role),
    }


def authenticate_platform_login(
    *,
    admin_token: str | None,
    actor: str | None,
    email: str | None = None,
    password: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    if email or password:
        if not email or not password:
            raise PlatformSessionError("Email and password are both required.")
        try:
            authenticated = authenticate_user(email, password)
            user = authenticated["user"]
            memberships = authenticated["memberships"]
            if not memberships:
                raise PlatformSessionError("This account has no active company memberships.")
            session_payload = issue_user_session(
                user_id=int(user["id"]),
                user_agent=user_agent,
                ip_address=ip_address,
            )
        except IdentityAuthenticationError as exc:
            raise PlatformSessionError(str(exc)) from exc
        highest_role = max(
            memberships,
            key=lambda item: {"viewer": 1, "operator": 2, "admin": 3, "owner": 4}.get(
                str(item["role"]),
                0,
            ),
        )
        identity_role = str(highest_role["role"])
        return {
            "role": "superadmin" if identity_role in {"owner", "admin"} else identity_role,
            "identity_role": identity_role,
            "actor": str(user["display_name"]),
            "email": str(user["email"]),
            "user_id": int(user["id"]),
            "memberships": memberships,
            "company_ids": [int(item["company_id"]) for item in memberships],
            "auth_mode": "identity",
            **session_payload,
        }

    resolved_actor = str(actor or "platform-user").strip() or "platform-user"

    if not settings.enable_admin_auth:
        session_payload = issue_platform_session(role="superadmin", actor=resolved_actor)
        return {
            "role": "superadmin",
            "actor": resolved_actor,
            "auth_mode": "session_dev",
            "token": session_payload["token"],
            "max_age": session_payload["max_age"],
            "expires_at": session_payload["expires_at"],
        }

    role_token_map = get_role_token_map(settings)
    if not role_token_map:
        raise PlatformSessionError("Admin authentication is enabled but no admin tokens are configured.")

    resolved_role = resolve_admin_role_from_token(admin_token, settings_obj=settings)
    if not resolved_role:
        raise PlatformSessionError("Invalid admin credentials.")

    session_payload = issue_platform_session(role=resolved_role, actor=resolved_actor)
    return {
        "role": resolved_role,
        "actor": resolved_actor,
        "auth_mode": "session",
        "token": session_payload["token"],
        "max_age": session_payload["max_age"],
        "expires_at": session_payload["expires_at"],
    }


def attach_platform_session_cookie(response: Response, session_data: dict[str, Any]) -> None:
    secure_cookie = settings.environment.strip().lower() == "production"
    response.set_cookie(
        key=COOKIE_NAME,
        value=str(session_data["token"]),
        max_age=int(session_data["max_age"]),
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
    )
    csrf_token = session_data.get("csrf_token")
    if csrf_token:
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=str(csrf_token),
            max_age=int(session_data["max_age"]),
            httponly=False,
            secure=secure_cookie,
            samesite="lax",
            path="/",
        )


def clear_platform_session_cookie(response: Response, token: str | None = None) -> None:
    if token and "." not in token:
        revoke_user_session(token)
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        httponly=False,
        samesite="lax",
    )
