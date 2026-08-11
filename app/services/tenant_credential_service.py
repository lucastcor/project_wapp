"""Autenticação da API interna de ingestão de mensagens.

O tenant vem da credencial apresentada, nunca de `company_id`/`company_slug`
enviados pelo chamador. Um identificador divergente é tratado como tentativa
de acesso cruzado e recebe 403.

Esta autenticação é independente da assinatura do webhook do Meta: são
superfícies distintas, com ciclos de rotação distintos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.core.credentials import hash_token, issue_token, mask_secret
from app.core.logging import get_logger, log_event
from app.core.tenancy import require_company_id
from app.storage import identity_repository

logger = get_logger(__name__)

SCOPE_MESSAGES_WRITE = "messages:write"
VALID_SCOPES = frozenset({SCOPE_MESSAGES_WRITE})

# Rótulo público que identifica o tipo de credencial na emissão.
CREDENTIAL_LABEL = "wapp"


class TenantCredentialError(Exception):
    """Falha de autenticação da credencial de tenant."""


@dataclass(frozen=True)
class TenantCredential:
    credential_id: int
    company_id: int
    scopes: frozenset[str]
    token_prefix: str

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def issue_tenant_credential(
    *,
    company_id: int,
    scopes: list[str] | None = None,
    description: str | None = None,
    expires_at: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Emite uma credencial. O token em claro é devolvido uma única vez."""
    resolved_company_id = require_company_id(company_id, context="tenant credential issue")
    requested_scopes = scopes or [SCOPE_MESSAGES_WRITE]
    unknown = sorted(set(requested_scopes) - VALID_SCOPES)
    if unknown:
        raise TenantCredentialError(f"Unknown scopes: {', '.join(unknown)}")

    issued = issue_token(prefix=CREDENTIAL_LABEL)
    credential_id = identity_repository.create_tenant_credential(
        company_id=resolved_company_id,
        token_hash=issued.token_hash,
        token_prefix=issued.token_prefix,
        scopes=requested_scopes,
        description=description,
        expires_at=expires_at,
        created_by=created_by,
    )

    log_event(
        logger,
        logging.INFO,
        "tenant_credential.issued",
        company_id=resolved_company_id,
        credential_id=credential_id,
        token_prefix=issued.token_prefix,
        scopes=sorted(set(requested_scopes)),
    )

    return {
        "credential_id": credential_id,
        "company_id": resolved_company_id,
        "token_prefix": issued.token_prefix,
        "scopes": sorted(set(requested_scopes)),
        # Único momento em que o segredo existe fora do cliente.
        "token": issued.token,
        "warning": "Guarde este token agora: ele não pode ser recuperado depois.",
    }


def revoke_tenant_credential(*, company_id: int, credential_id: int) -> bool:
    resolved_company_id = require_company_id(company_id, context="tenant credential revoke")
    revoked = identity_repository.revoke_tenant_credential(
        company_id=resolved_company_id,
        credential_id=credential_id,
    )
    if revoked:
        log_event(
            logger,
            logging.WARNING,
            "tenant_credential.revoked",
            company_id=resolved_company_id,
            credential_id=credential_id,
        )
    return revoked


def list_tenant_credentials(*, company_id: int) -> list[dict[str, Any]]:
    resolved_company_id = require_company_id(company_id, context="tenant credential list")
    credentials = identity_repository.list_tenant_credentials(company_id=resolved_company_id)
    return [
        {
            key: value
            for key, value in credential.items()
            if key not in {"scopes_json", "token_hash"}
        }
        for credential in credentials
    ]


def authenticate_tenant_credential(
    token: str | None,
    *,
    required_scope: str = SCOPE_MESSAGES_WRITE,
) -> TenantCredential:
    """Resolve a credencial. Falha fechada em qualquer situação anômala."""
    normalized = str(token or "").strip()
    if not normalized:
        raise TenantCredentialError("Missing tenant API credential.")

    record = identity_repository.get_tenant_credential_by_hash(hash_token(normalized))
    if not record:
        # A mensagem não distingue token inexistente de token inválido.
        raise TenantCredentialError("Invalid tenant API credential.")

    if not identity_repository.is_usable(record):
        raise TenantCredentialError("Tenant API credential is inactive, revoked or expired.")

    scopes = frozenset(str(scope) for scope in record.get("scopes") or [])
    if required_scope not in scopes:
        raise TenantCredentialError(
            f"Tenant API credential does not carry the required scope '{required_scope}'."
        )

    company_id = record.get("company_id")
    if company_id is None:
        raise TenantCredentialError("Tenant API credential is not bound to a company.")

    identity_repository.touch_tenant_credential(credential_id=int(record["id"]))

    return TenantCredential(
        credential_id=int(record["id"]),
        company_id=int(company_id),
        scopes=scopes,
        token_prefix=str(record.get("token_prefix") or ""),
    )


def describe_credential_for_audit(credential: TenantCredential) -> dict[str, Any]:
    """Metadados seguros para log e auditoria — nunca o token."""
    return {
        "credential_id": credential.credential_id,
        "company_id": credential.company_id,
        "token_prefix": mask_secret(credential.token_prefix, keep=3),
        "scopes": sorted(credential.scopes),
    }
