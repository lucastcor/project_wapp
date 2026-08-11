"""HTTP adapter for internal inbound message ingress authentication.

Policy lives in ``app/services/ingress_auth_service.py``; this module maps the
outcome onto HTTP status codes and logs the denial reason without ever emitting
the presented or configured secret.
"""

import logging

from fastapi import HTTPException

from app.core.logging import get_logger, log_event
from app.services.ingress_auth_service import (
    INTERNAL_INGRESS_HEADER,
    REASON_INGRESS_DISABLED,
    REASON_MISCONFIGURED,
    IngressAuthError,
    internal_ingress_enforced,
    resolve_internal_ingress_auth,
    resolve_normalized_whatsapp_ingress_auth,
)

__all__ = [
    "INTERNAL_INGRESS_HEADER",
    "authenticate_internal_ingress",
    "authorize_normalized_whatsapp_ingress",
    "internal_ingress_enforced",
]

logger = get_logger(__name__)

_STATUS_BY_REASON = {
    REASON_MISCONFIGURED: 503,
    REASON_INGRESS_DISABLED: 403,
}


def _raise_http_error(surface: str, exc: IngressAuthError) -> None:
    status_code = _STATUS_BY_REASON.get(exc.reason, 401)
    log_event(
        logger,
        logging.ERROR if status_code == 503 else logging.WARNING,
        "api.ingress.auth.denied",
        ingress_surface=surface,
        denial_reason=exc.reason,
        status_code=status_code,
    )
    raise HTTPException(status_code=status_code, detail=exc.detail) from exc


def authenticate_internal_ingress(
    *,
    header_secret: str | None,
    authorization: str | None,
    require_credentials: bool,
    surface: str,
) -> str:
    try:
        return resolve_internal_ingress_auth(
            header_secret=header_secret,
            authorization=authorization,
            require_credentials=require_credentials,
        )
    except IngressAuthError as exc:
        _raise_http_error(surface, exc)
        raise  # pragma: no cover - _raise_http_error always raises


def authorize_normalized_whatsapp_ingress(
    *,
    header_secret: str | None,
    authorization: str | None,
) -> str:
    try:
        return resolve_normalized_whatsapp_ingress_auth(
            header_secret=header_secret,
            authorization=authorization,
        )
    except IngressAuthError as exc:
        _raise_http_error("whatsapp_webhook_normalized", exc)
        raise  # pragma: no cover - _raise_http_error always raises
