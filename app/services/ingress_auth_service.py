"""Authentication rules for internal (non-Meta) inbound message ingress.

Two public entry points can receive messages without a Meta signature:

* ``POST /webhooks/whatsapp`` in *normalized* payload mode.
* ``POST /messages/incoming``.

Both are protected by a dedicated shared secret presented either through the
``X-Internal-Ingress-Secret`` header or as ``Authorization: Bearer <secret>``.
Normalized mode is disabled by default and always requires the secret;
``/messages/incoming`` requires it whenever the runtime is production or strict
validation is enabled.

This module holds the policy only; ``app/api/ingress_security.py`` maps the
outcome onto HTTP responses.
"""

from app.core.config import is_missing_or_placeholder, settings
from app.services.admin_auth_service import extract_bearer_token, secrets_match

INTERNAL_INGRESS_HEADER = "X-Internal-Ingress-Secret"

# Denial reasons surfaced to the HTTP layer. They never contain secret material.
REASON_INGRESS_DISABLED = "ingress_disabled"
REASON_MISCONFIGURED = "misconfigured"
REASON_UNVERIFIABLE_CREDENTIAL = "unverifiable_credential"
REASON_MISSING_CREDENTIALS = "missing_credentials"
REASON_INVALID_CREDENTIALS = "invalid_credentials"


class IngressAuthError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def internal_ingress_enforced() -> bool:
    """Whether anonymous internal ingress is forbidden for this runtime."""
    return settings.strict_runtime_validation or settings.environment.strip().lower() == "production"


def get_configured_internal_ingress_secret() -> str | None:
    """Return the configured secret, or ``None`` when absent/placeholder."""
    secret = str(settings.internal_ingress_secret or "").strip()
    if is_missing_or_placeholder(secret):
        return None
    return secret


def normalized_whatsapp_ingress_enabled() -> bool:
    return bool(settings.enable_normalized_whatsapp_ingress)


def normalized_whatsapp_ingress_is_safe() -> bool:
    """Normalized ingress is safe when it is off, or on with a real secret."""
    if not normalized_whatsapp_ingress_enabled():
        return True
    return get_configured_internal_ingress_secret() is not None


def _extract_presented_secret(
    header_secret: str | None,
    authorization: str | None,
) -> str | None:
    presented = str(header_secret or "").strip()
    if presented:
        return presented
    return extract_bearer_token(authorization)


def resolve_internal_ingress_auth(
    *,
    header_secret: str | None,
    authorization: str | None,
    require_credentials: bool,
) -> str:
    """Return the resolved auth mode or raise :class:`IngressAuthError`.

    Comparison is constant-time and a credential is never accepted when there
    is no configured secret to verify it against.
    """
    configured_secret = get_configured_internal_ingress_secret()
    presented_secret = _extract_presented_secret(header_secret, authorization)

    if configured_secret is None:
        if require_credentials:
            raise IngressAuthError(
                REASON_MISCONFIGURED,
                "Internal ingress authentication is not configured.",
            )
        if presented_secret:
            raise IngressAuthError(
                REASON_UNVERIFIABLE_CREDENTIAL,
                "Invalid internal ingress credentials.",
            )
        return "anonymous"

    if not presented_secret:
        if require_credentials:
            raise IngressAuthError(
                REASON_MISSING_CREDENTIALS,
                "Missing internal ingress credentials.",
            )
        return "anonymous"

    if not secrets_match(presented_secret, configured_secret):
        raise IngressAuthError(
            REASON_INVALID_CREDENTIALS,
            "Invalid internal ingress credentials.",
        )

    return "internal_secret"


def resolve_normalized_whatsapp_ingress_auth(
    *,
    header_secret: str | None,
    authorization: str | None,
) -> str:
    """Gate the normalized WhatsApp webhook mode; it is opt-in and always authenticated."""
    if not normalized_whatsapp_ingress_enabled():
        raise IngressAuthError(
            REASON_INGRESS_DISABLED,
            "Normalized webhook ingress is disabled.",
        )

    return resolve_internal_ingress_auth(
        header_secret=header_secret,
        authorization=authorization,
        require_credentials=True,
    )
