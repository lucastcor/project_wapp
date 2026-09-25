"""Conexão do número WhatsApp da empresa pelo Embedded Signup da Meta.

Router separado do `admin_routes` para que a feature evolua sem tocar no
arquivo compartilhado. Herda a autenticação administrativa e o escopo de
empresa por prefixo `/admin/whatsapp` (política tenant), exceto `/config`,
declarada como catálogo sem empresa em `company_scope.py`.

O `code` do popup, o token de negócio e o PIN nunca entram em auditoria,
log ou resposta: a auditoria guarda identificadores mascarados e a origem
da credencial, que é o que uma investigação precisa.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.admin_routes import _record_audit_event, require_admin_access, require_admin_role
from app.api.company_scope import require_authorized_company_id
from app.core.logging import get_logger, log_event, mask_identifier
from app.services.whatsapp_signup_service import (
    SignupConflictError,
    SignupError,
    complete_embedded_signup,
    disconnect_embedded_signup,
    disconnect_targets,
    embedded_signup_config,
)
from app.storage.repository import RuntimeRepository

logger = get_logger(__name__)
repository = RuntimeRepository()

whatsapp_signup_router = APIRouter(
    prefix="/admin/whatsapp/embedded-signup",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)


class EmbeddedSignupCompletePayload(BaseModel):
    """O que o popup da Meta entrega ao painel; os ids são opcionais.

    `event` é o nome do evento final do popup (`FINISH`, `FINISH_ONLY_WABA`,
    ...) e serve só para diagnóstico. `coexistence` marca o fluxo em que o
    dono continua usando o app WhatsApp Business no celular.
    """

    code: str = Field(min_length=1)
    phone_number_id: str | None = None
    waba_id: str | None = None
    business_id: str | None = None
    event: str | None = None
    coexistence: bool = False


def _require_existing_company(company_id: int) -> int:
    if not repository.get_company(int(company_id)):
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    return int(company_id)


@whatsapp_signup_router.get("/config")
def read_embedded_signup_config() -> dict[str, Any]:
    """Ids públicos do app da Meta e se a conexão pelo painel está habilitada."""
    return embedded_signup_config()


@whatsapp_signup_router.post(
    "/complete",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def complete_embedded_signup_endpoint(
    payload: EmbeddedSignupCompletePayload,
    request: Request,
) -> dict[str, Any]:
    company_id = _require_existing_company(require_authorized_company_id(request))
    actor = getattr(request.state, "admin_actor", None)
    try:
        status = complete_embedded_signup(
            company_id,
            code=payload.code,
            phone_number_id=payload.phone_number_id,
            waba_id=payload.waba_id,
            business_id=payload.business_id,
            event=payload.event,
            coexistence=payload.coexistence,
            actor=actor,
        )
    except SignupError as exc:
        _record_audit_event(
            request=request,
            action="whatsapp_connected",
            resource_type="whatsapp_channel_binding",
            company_id=company_id,
            status="conflict" if isinstance(exc, SignupConflictError) else "failure",
            metadata={
                "kind": exc.kind,
                "provider_code": exc.provider_code,
                "phone_number_id": mask_identifier(payload.phone_number_id),
                "waba_id": mask_identifier(payload.waba_id),
                "coexistence": payload.coexistence,
            },
        )
        log_event(
            logger,
            logging.WARNING,
            "admin.whatsapp.signup.failed",
            company_id=company_id,
            kind=exc.kind,
            provider_code=exc.provider_code,
            http_status=exc.status_code,
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    binding = status.get("binding") or {}
    _record_audit_event(
        request=request,
        action="whatsapp_connected",
        resource_type="whatsapp_channel_binding",
        resource_id=str(binding["id"]) if binding else None,
        company_id=company_id,
        metadata={
            # Já mascarados pelo status; a origem diz "popup" vs "manual".
            "phone_number_id": binding.get("phone_number_id"),
            "waba_id": status.get("waba_id_masked"),
            "credential_source": status.get("connection_source"),
            "coexistence": payload.coexistence,
        },
    )
    log_event(
        logger,
        logging.INFO,
        "admin.whatsapp.signup.connected",
        company_id=company_id,
        binding_id=binding.get("id"),
        credential_source=status.get("connection_source"),
    )
    return status


@whatsapp_signup_router.post(
    "/disconnect",
    dependencies=[Depends(require_admin_role("superadmin"))],
)
def disconnect_embedded_signup_endpoint(request: Request) -> dict[str, Any]:
    company_id = _require_existing_company(require_authorized_company_id(request))
    actor = getattr(request.state, "admin_actor", None)
    # Lidos antes de desconectar: depois as linhas perdem o token e o status
    # não as devolve mais; a auditoria precisa dos ids e dos ids mascarados.
    # Normalmente é um binding; mais de um quando uma linha extra ainda
    # guardava token (vínculo manual por cima da conexão, endpoint legado).
    targets = disconnect_targets(company_id)
    previous = targets[0] if targets else None
    try:
        status = disconnect_embedded_signup(company_id, actor=actor)
    except SignupError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    metadata: dict[str, Any] = {
        "phone_number_id": mask_identifier(str(previous.get("phone_number_id") or "")) if previous else None,
        "waba_id": mask_identifier(str(previous.get("waba_id") or "")) if previous else None,
        "credential_source": previous.get("credential_source") if previous else None,
    }
    if len(targets) > 1:
        metadata["binding_ids"] = [int(target["id"]) for target in targets]
    _record_audit_event(
        request=request,
        action="whatsapp_disconnected",
        resource_type="whatsapp_channel_binding",
        resource_id=str(previous["id"]) if previous else None,
        company_id=company_id,
        metadata=metadata,
    )
    log_event(
        logger,
        logging.INFO,
        "admin.whatsapp.signup.disconnected",
        company_id=company_id,
        binding_id=previous["id"] if previous else None,
        binding_count=len(targets),
    )
    return status
