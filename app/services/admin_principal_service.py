"""Principal administrativo e autorização por empresa.

Antes desta camada, papel era global: qualquer credencial autenticada podia
escolher qualquer `company_id`. Agora:

- o principal vem da credencial, nunca do header `X-Admin-Actor`;
- `superadmin` pode ter acesso global explícito;
- `operator` e `viewer` só alcançam empresas presentes em sua membership;
- resolver `company_id` não autoriza: a autorização é uma checagem separada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.core.config import is_production_like, settings
from app.core.credentials import hash_token, issue_token
from app.core.logging import get_logger, log_event
from app.services.admin_auth_service import ADMIN_ROLE_HIERARCHY
from app.storage import identity_repository

logger = get_logger(__name__)

# Rótulo público que identifica o tipo de credencial na emissão. Não é
# segredo: aparece em auditoria e no prefixo armazenado.
CREDENTIAL_LABEL = "wappadm"

AUTH_MODE_PRINCIPAL = "principal_token"
AUTH_MODE_LEGACY = "legacy_token"
AUTH_MODE_SESSION = "session"
AUTH_MODE_DISABLED = "disabled"


class AdminPrincipalError(Exception):
    pass


class SessionRevokedError(Exception):
    """Sessão apresentada não tem mais base de identidade válida."""


@dataclass(frozen=True)
class AdminPrincipal:
    """Identidade autenticada de uma requisição administrativa."""

    principal_id: int | None
    actor: str
    global_role: str
    is_global: bool
    allowed_company_ids: frozenset[int]
    auth_mode: str
    token_id: int | None = None
    auth_epoch: int | None = None
    # Papel por empresa, vindo da membership. Guardado como par ordenado
    # porque o dataclass é `frozen` e precisa ser hasheável.
    company_roles: tuple[tuple[int, str], ...] = ()

    def can_access_company(self, company_id: int) -> bool:
        if self.is_global:
            return True
        return int(company_id) in self.allowed_company_ids

    def role_in_company(self, company_id: int | None) -> str:
        """Papel **efetivo** dentro de uma empresa.

        `global_role` autoriza operações globais. Dentro de uma empresa vale o
        papel da membership — e vale como **teto**, nunca como elevação: uma
        membership `superadmin` não promove um principal global `viewer`.

        Antes desta regra, `platform_company_memberships.role` era gravado,
        devolvido pela query e descartado no carregamento do escopo. Rebaixar
        alguém em uma empresa não tinha efeito nenhum.
        """
        if self.is_global:
            return self.global_role
        if company_id is None:
            return self.global_role

        membership_role = dict(self.company_roles).get(int(company_id))
        if membership_role is None:
            return self.global_role

        global_weight = ADMIN_ROLE_HIERARCHY.get(self.global_role, 0)
        membership_weight = ADMIN_ROLE_HIERARCHY.get(membership_role, 0)
        return membership_role if membership_weight < global_weight else self.global_role

    def has_role(self, required_role: str, *, company_id: int | None = None) -> bool:
        """Compara com o papel efetivo na empresa, quando há uma."""
        current = ADMIN_ROLE_HIERARCHY.get(self.role_in_company(company_id), 0)
        required = ADMIN_ROLE_HIERARCHY.get(required_role, 0)
        return current >= required

    def as_audit_metadata(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "global_role": self.global_role,
            "auth_mode": self.auth_mode,
            "is_global": self.is_global,
            "company_scope_size": len(self.allowed_company_ids),
        }


def _load_principal_scope(
    principal_id: int,
    global_role: str,
    is_global: bool,
) -> tuple[frozenset[int], tuple[tuple[int, str], ...]]:
    """Empresas alcançáveis e o papel em cada uma.

    O papel vem junto de propósito: carregar só o `company_id` foi o que fez
    `platform_company_memberships.role` virar um campo decorativo.
    """
    if is_global:
        return frozenset(), ()

    memberships = identity_repository.get_memberships(principal_id=principal_id)
    company_ids = frozenset(int(row["company_id"]) for row in memberships)
    roles = tuple(
        (int(row["company_id"]), str(row.get("role") or global_role))
        for row in sorted(memberships, key=lambda entry: int(entry["company_id"]))
    )
    return company_ids, roles


def authenticate_principal_token(token: str | None) -> AdminPrincipal | None:
    """Resolve o principal a partir de um token de plataforma."""
    normalized = str(token or "").strip()
    if not normalized:
        return None

    record = identity_repository.get_token_by_hash(hash_token(normalized))
    if not record or not identity_repository.is_usable(record):
        return None

    principal = identity_repository.get_principal(int(record["principal_id"]))
    if not principal or not int(principal.get("active") or 0):
        return None

    identity_repository.touch_access_token(token_id=int(record["id"]))

    is_global = bool(int(principal.get("is_global") or 0))
    global_role = str(principal.get("global_role") or "viewer")
    # Acesso global só faz sentido para superadmin.
    if is_global and global_role != "superadmin":
        is_global = False

    scope_company_ids, scope_company_roles = _load_principal_scope(
        int(principal["id"]), global_role, is_global
    )
    return AdminPrincipal(
        principal_id=int(principal["id"]),
        actor=str(principal.get("principal_key") or "principal"),
        global_role=global_role,
        is_global=is_global,
        allowed_company_ids=scope_company_ids,
        company_roles=scope_company_roles,
        auth_mode=AUTH_MODE_PRINCIPAL,
        token_id=int(record["id"]),
        auth_epoch=int(principal.get("auth_epoch") or 1),
    )


def principal_from_session_claims(
    *,
    principal_id: int,
    token_id: int | None,
    auth_epoch: int | None,
) -> AdminPrincipal:
    """Reconstrói o principal de uma sessão, ou recusa a sessão.

    Falha fechada em todos os casos: principal removido, principal desativado,
    época movida ou credencial de origem revogada. Nunca cai para principal
    legado — era exatamente esse fallback que mantinha viva a sessão de quem
    já tinha perdido o acesso.
    """
    record = identity_repository.get_principal(int(principal_id))
    if not record:
        raise SessionRevokedError("Session principal no longer exists.")
    if not int(record.get("active") or 0):
        raise SessionRevokedError("Session principal is deactivated.")

    current_epoch = int(record.get("auth_epoch") or 1)
    if int(auth_epoch or 0) != current_epoch:
        raise SessionRevokedError("Session was issued before the current auth epoch.")

    if token_id is not None:
        credential = identity_repository.get_access_token(int(token_id))
        if not identity_repository.is_usable(credential):
            raise SessionRevokedError("Credential that issued this session was revoked.")
        if int((credential or {}).get("principal_id") or 0) != int(principal_id):
            raise SessionRevokedError("Session credential belongs to another principal.")

    global_role = str(record.get("global_role") or "viewer")
    is_global = bool(int(record.get("is_global") or 0)) and global_role == "superadmin"

    session_company_ids, session_company_roles = _load_principal_scope(
        int(record["id"]), global_role, is_global
    )
    return AdminPrincipal(
        principal_id=int(record["id"]),
        actor=str(record.get("principal_key") or "principal"),
        global_role=global_role,
        is_global=is_global,
        allowed_company_ids=session_company_ids,
        company_roles=session_company_roles,
        auth_mode=AUTH_MODE_SESSION,
        token_id=int(token_id) if token_id is not None else None,
        auth_epoch=current_epoch,
    )


def build_legacy_principal(role: str, actor: str, *, auth_mode: str) -> AdminPrincipal:
    """Principal derivado de token/sessão legados, sem membership.

    Em produção apenas o superadmin legado permanece global. `operator` e
    `viewer` legados ficam sem nenhuma empresa: o operador precisa criar um
    principal com membership explícita.
    """
    normalized_role = role if role in ADMIN_ROLE_HIERARCHY else "viewer"
    is_global = normalized_role == "superadmin"

    if not is_global and not is_production_like() and settings.allow_legacy_global_admin_tokens:
        # Ergonomia de desenvolvimento, explicitamente configurável.
        is_global = True

    return AdminPrincipal(
        principal_id=None,
        actor=actor,
        global_role=normalized_role,
        is_global=is_global,
        allowed_company_ids=frozenset(),
        auth_mode=auth_mode,
    )


def build_auth_disabled_principal(actor: str) -> AdminPrincipal:
    """Usado apenas quando `ENABLE_ADMIN_AUTH=false` (desenvolvimento)."""
    return AdminPrincipal(
        principal_id=None,
        actor=actor,
        global_role="superadmin",
        is_global=True,
        allowed_company_ids=frozenset(),
        auth_mode=AUTH_MODE_DISABLED,
    )


# --- gestão de principals -----------------------------------------------------------


def create_principal_with_token(
    *,
    principal_key: str,
    display_name: str,
    global_role: str,
    is_global: bool = False,
    company_ids: list[int] | None = None,
    description: str | None = None,
    expires_at: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Cria principal + token. O token em claro é devolvido uma única vez."""
    if global_role not in ADMIN_ROLE_HIERARCHY:
        raise AdminPrincipalError(f"Invalid global_role: {global_role}")
    if is_global and global_role != "superadmin":
        raise AdminPrincipalError("Only superadmin principals can be global.")

    if identity_repository.get_principal_by_key(principal_key):
        raise AdminPrincipalError(f"Principal '{principal_key}' already exists.")

    principal_id = identity_repository.create_principal(
        principal_key=principal_key,
        display_name=display_name,
        global_role=global_role,
        is_global=is_global,
        created_by=created_by,
    )

    for company_id in company_ids or []:
        identity_repository.upsert_membership(
            principal_id=principal_id,
            company_id=int(company_id),
            role=global_role,
            created_by=created_by,
        )

    issued = issue_token(prefix=CREDENTIAL_LABEL)
    token_id = identity_repository.create_access_token(
        principal_id=principal_id,
        token_hash=issued.token_hash,
        token_prefix=issued.token_prefix,
        description=description,
        expires_at=expires_at,
        created_by=created_by,
    )

    log_event(
        logger,
        logging.INFO,
        "admin_principal.created",
        principal_id=principal_id,
        global_role=global_role,
        is_global=is_global,
        company_count=len(company_ids or []),
        token_prefix=issued.token_prefix,
    )

    return {
        "principal_id": principal_id,
        "principal_key": principal_key,
        "global_role": global_role,
        "is_global": is_global,
        "company_ids": [int(company_id) for company_id in company_ids or []],
        "token_id": token_id,
        "token_prefix": issued.token_prefix,
        "token": issued.token,
        "warning": "Guarde este token agora: ele não pode ser recuperado depois.",
    }


def revoke_principal_token(*, token_id: int) -> bool:
    revoked = identity_repository.revoke_access_token(token_id=token_id)
    if revoked:
        log_event(
            logger,
            logging.WARNING,
            "admin_principal.token_revoked",
            token_id=token_id,
        )
    return revoked


def grant_membership(
    *,
    principal_id: int,
    company_id: int,
    role: str,
    created_by: str | None = None,
) -> int:
    if role not in ADMIN_ROLE_HIERARCHY:
        raise AdminPrincipalError(f"Invalid role: {role}")
    membership_id = identity_repository.upsert_membership(
        principal_id=principal_id,
        company_id=company_id,
        role=role,
        created_by=created_by,
    )
    log_event(
        logger,
        logging.INFO,
        "admin_principal.membership_granted",
        principal_id=principal_id,
        company_id=company_id,
        membership_role=role,
    )
    return membership_id


def revoke_membership(*, principal_id: int, company_id: int) -> bool:
    removed = identity_repository.remove_membership(
        principal_id=principal_id,
        company_id=company_id,
    )
    if removed:
        log_event(
            logger,
            logging.WARNING,
            "admin_principal.membership_revoked",
            principal_id=principal_id,
            company_id=company_id,
        )
    return removed


def describe_principal(principal_id: int) -> dict[str, Any] | None:
    principal = identity_repository.get_principal(principal_id)
    if not principal:
        return None
    return {
        "principal_id": int(principal["id"]),
        "principal_key": principal["principal_key"],
        "display_name": principal["display_name"],
        "global_role": principal["global_role"],
        "is_global": bool(int(principal.get("is_global") or 0)),
        "active": bool(int(principal.get("active") or 0)),
        "memberships": identity_repository.get_memberships(principal_id=principal_id),
        "tokens": identity_repository.list_access_tokens(principal_id=principal_id),
    }


def list_principals() -> list[dict[str, Any]]:
    return identity_repository.list_principals()
