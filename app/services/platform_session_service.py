import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Response

from app.core.config import settings
from app.services.admin_auth_service import get_role_token_map, resolve_admin_role_from_token


class PlatformSessionError(Exception):
    pass


COOKIE_NAME = "project_wapp_platform_session"


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

    payload = _decode(token)
    role = str(payload.get("role") or "").strip()
    actor = str(payload.get("actor") or "").strip() or "platform-user"
    exp = int(payload.get("exp") or 0)
    if role not in {"viewer", "operator", "superadmin"}:
        raise PlatformSessionError("Invalid platform session role.")
    if exp <= int(time.time()):
        raise PlatformSessionError("Platform session expired.")
    return {
        "role": role,
        "actor": actor,
        "expires_at": exp,
        "issued_at": int(payload.get("iat") or 0),
    }


def authenticate_platform_login(
    *,
    admin_token: str | None,
    actor: str | None,
) -> dict[str, Any]:
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


def clear_platform_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
    )
