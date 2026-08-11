import hmac
from typing import Any

ADMIN_ROLE_HIERARCHY = {
    "viewer": 1,
    "operator": 2,
    "superadmin": 3,
}


def extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None

    normalized = authorization.strip()
    if not normalized.lower().startswith("bearer "):
        return None

    token = normalized[7:].strip()
    return token or None


def get_role_token_map(settings_obj: Any) -> dict[str, str]:
    role_token_map: dict[str, str] = {}

    if settings_obj.admin_viewer_token:
        role_token_map["viewer"] = settings_obj.admin_viewer_token.strip()
    if settings_obj.admin_operator_token:
        role_token_map["operator"] = settings_obj.admin_operator_token.strip()

    superadmin_token = (
        settings_obj.admin_superadmin_token.strip()
        if settings_obj.admin_superadmin_token
        else None
    )
    fallback_superadmin_token = (
        settings_obj.admin_api_token.strip()
        if settings_obj.admin_api_token
        else None
    )
    if superadmin_token:
        role_token_map["superadmin"] = superadmin_token
    elif fallback_superadmin_token:
        role_token_map["superadmin"] = fallback_superadmin_token

    return role_token_map


def secrets_match(provided: str | None, configured: str | None) -> bool:
    """Constant-time secret comparison that tolerates non-ASCII values."""
    if not provided or not configured:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), configured.encode("utf-8"))


def resolve_admin_role_from_token(
    provided_token: str | None,
    *,
    settings_obj: Any,
) -> str | None:
    if not provided_token:
        return None

    resolved_role: str | None = None
    # Every configured token is compared so the response time does not reveal which role matched.
    for role, configured_token in get_role_token_map(settings_obj).items():
        if secrets_match(provided_token, configured_token) and resolved_role is None:
            resolved_role = role
    return resolved_role
