"""Conexão da agenda Google da empresa por OAuth (início do fluxo e gestão).

O callback do Google é uma navegação de topo do navegador e vive em
`platform_routes.py` (cookie de sessão, sem cabeçalho CSRF). Este router
concentra o que o painel chama por `fetch`: iniciar a autorização, listar
calendários, escolher o calendário e desconectar.

Herda a autenticação administrativa; o escopo de empresa vem da política
`/admin/calendar` (tenant), exceto `/config`, declarada sem empresa em
`company_scope.py`. As rotas mutantes exigem superadmin (owner/admin da
empresa numa sessão de identidade) e resolvem a empresa por
`require_authorized_company_id`, nunca por um parâmetro solto.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.admin_routes import _record_audit_event, require_admin_access, require_admin_role
from app.api.company_scope import require_authorized_company_id
from app.services import google_calendar_oauth_service as oauth_service
from app.services.google_calendar_oauth_service import (
    GoogleOAuthClientError,
    GoogleOAuthConfigError,
    GoogleOAuthError,
    GoogleOAuthNotConnectedError,
)

google_calendar_router = APIRouter(
    prefix="/admin/calendar/google",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)


class GoogleCalendarSelectionPayload(BaseModel):
    calendar_id: str = Field(min_length=1, max_length=oauth_service.MAX_CALENDAR_ID_LENGTH)


def _principal(request: Request) -> dict:
    return {
        "user_id": getattr(request.state, "admin_user_id", None),
        "actor": getattr(request.state, "admin_actor", None),
        "auth_mode": getattr(request.state, "admin_auth_mode", None),
    }


def _http_error(exc: GoogleOAuthError) -> HTTPException:
    """Mapa fechado de motivo → status; o `detail` já é texto para o dono."""
    if isinstance(exc, GoogleOAuthNotConnectedError):
        return HTTPException(status_code=400, detail=exc.detail)
    if exc.reason == "company_not_found":
        return HTTPException(status_code=404, detail=exc.detail)
    if isinstance(exc, GoogleOAuthConfigError) or exc.reason == "vault_unavailable":
        return HTTPException(status_code=503, detail=exc.detail)
    if isinstance(exc, GoogleOAuthClientError):
        return HTTPException(status_code=502, detail=exc.detail)
    return HTTPException(status_code=400, detail=exc.detail)


@google_calendar_router.get("/config")
def read_google_oauth_config() -> dict:
    return oauth_service.google_oauth_config()


@google_calendar_router.post(
    "/authorize",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def start_google_authorization(request: Request) -> dict:
    company_id = require_authorized_company_id(request)
    principal = _principal(request)
    try:
        authorization_url = oauth_service.build_authorization_url(
            company_id=company_id,
            user_id=principal["user_id"],
            actor=principal["actor"],
            auth_mode=principal["auth_mode"],
        )
    except GoogleOAuthError as exc:
        # Config ausente → 503; empresa inexistente (token de plataforma) → 404.
        raise _http_error(exc) from exc
    _record_audit_event(
        request=request,
        action="google_calendar_authorize_started",
        resource_type="calendar_integration",
        company_id=company_id,
        metadata={"provider": oauth_service.PROVIDER},
    )
    return {"authorization_url": authorization_url}


@google_calendar_router.get("/calendars")
def read_google_calendars(request: Request) -> list[dict]:
    company_id = require_authorized_company_id(request)
    try:
        return oauth_service.list_calendars(company_id)
    except GoogleOAuthError as exc:
        raise _http_error(exc) from exc


@google_calendar_router.patch(
    "/calendar",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def select_google_calendar(payload: GoogleCalendarSelectionPayload, request: Request) -> dict:
    company_id = require_authorized_company_id(request)
    try:
        integration = oauth_service.set_calendar(
            company_id,
            payload.calendar_id,
            actor=_principal(request)["actor"],
        )
    except GoogleOAuthError as exc:
        raise _http_error(exc) from exc
    _record_audit_event(
        request=request,
        action="google_calendar_selected",
        resource_type="calendar_integration",
        resource_id=str(integration.get("id")),
        company_id=company_id,
        # O id do calendário pode ser um e-mail; a auditoria guarda só o mascarado.
        metadata={"calendar_id": integration.get("external_calendar_id")},
    )
    return integration


@google_calendar_router.post(
    "/disconnect",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def disconnect_google_calendar(request: Request) -> dict:
    company_id = require_authorized_company_id(request)
    try:
        result = oauth_service.disconnect(company_id, actor=_principal(request)["actor"])
    except GoogleOAuthError as exc:
        raise _http_error(exc) from exc
    _record_audit_event(
        request=request,
        action="google_calendar_disconnected",
        resource_type="calendar_integration",
        resource_id=str(result["integration_id"]),
        company_id=company_id,
        metadata={"revoked": result["revoked"], "account_email": result.get("account_email")},
    )
    return {"disconnected": True, "integration_id": result["integration_id"], "revoked": result["revoked"]}
