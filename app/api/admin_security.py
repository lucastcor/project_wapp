import logging
from collections.abc import Callable
from typing import Any

from fastapi import Header, HTTPException, Request

from app.core.credentials import extract_bearer_token
from app.core.logging import get_request_context, log_event
from app.services.admin_auth_service import (
    ADMIN_ROLE_HIERARCHY,
    get_role_token_map,
    resolve_admin_role_from_token,
)
from app.services.admin_principal_service import (
    AUTH_MODE_LEGACY,
    AUTH_MODE_SESSION,
    AdminPrincipal,
    SessionRevokedError,
    authenticate_principal_token,
    build_auth_disabled_principal,
    build_legacy_principal,
    principal_from_session_claims,
)
from app.services.platform_session_service import COOKIE_NAME, PlatformSessionError, read_platform_session


def get_request_principal(request: Request) -> AdminPrincipal:
    """Principal autenticado da requisição.

    A ausência é erro de programação: a dependência de autenticação roda antes
    de qualquer rota administrativa.
    """
    principal = getattr(request.state, "admin_principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="Admin principal is not resolved.")
    return principal


def principal_from_session(session: dict[str, Any]) -> AdminPrincipal:
    """Principal de uma sessão da plataforma.

    Quando a sessão foi emitida a partir de um principal, o escopo é
    recarregado do banco e a identidade é reconferida. Se o principal sumiu,
    foi desativado, teve a época movida ou a credencial revogada, a sessão é
    recusada — nunca rebaixada para principal legado.
    """
    session_principal_id = session.get("principal_id")
    if session_principal_id is not None:
        return principal_from_session_claims(
            principal_id=int(session_principal_id),
            token_id=(
                int(session["token_id"]) if session.get("token_id") is not None else None
            ),
            auth_epoch=(
                int(session["auth_epoch"]) if session.get("auth_epoch") is not None else None
            ),
        )

    return build_legacy_principal(
        str(session.get("role") or "viewer"),
        str(session.get("actor") or "platform-user"),
        auth_mode=AUTH_MODE_SESSION,
    )


def build_require_admin_access(
    *,
    settings_obj: Any,
    logger: logging.Logger,
    repository_getter: Callable[[], Any],
):
    def _store(request: Request, principal: AdminPrincipal) -> None:
        request.state.admin_principal = principal
        # Campos legados mantidos para auditoria e código existente.
        request.state.admin_actor = principal.actor
        request.state.admin_role = principal.global_role
        request.state.admin_auth_mode = principal.auth_mode

    def require_admin_access(
        request: Request,
        authorization: str | None = Header(default=None),
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
        x_admin_actor: str | None = Header(default=None, alias="X-Admin-Actor"),
    ) -> None:
        # `X-Admin-Actor` é apenas um rótulo operacional. Nunca é identidade:
        # a identidade vem sempre da credencial apresentada.
        actor_hint = (x_admin_actor or "").strip() or "admin"

        if not settings_obj.enable_admin_auth:
            _store(request, build_auth_disabled_principal(actor_hint))
            return

        repository = repository_getter()
        provided_token = x_admin_token or extract_bearer_token(authorization)
        cookie_token = request.cookies.get(COOKIE_NAME)

        # 1. Credencial de principal (caminho preferencial).
        principal = authenticate_principal_token(provided_token)
        if principal is not None:
            _store(request, principal)
            log_event(
                logger,
                logging.INFO,
                "admin.auth.succeeded",
                actor_id=principal.actor,
                auth_mode=principal.auth_mode,
                admin_role=principal.global_role,
                principal_id=principal.principal_id,
            )
            return

        role_token_map = get_role_token_map(settings_obj)
        if not role_token_map:
            log_event(
                logger,
                logging.ERROR,
                "admin.auth.misconfigured",
                actor_id=actor_hint,
            )
            repository.create_audit_event(
                actor_id=actor_hint,
                action="authenticate",
                resource_type="admin_access",
                status="misconfigured",
                request_id=str(get_request_context().get("request_id") or ""),
                metadata={"reason": "missing_admin_role_tokens"},
            )
            raise HTTPException(
                status_code=500,
                detail="Admin authentication is enabled but no admin tokens are configured.",
            )

        # 2. Sessão da plataforma (cookie HTTP-only).
        if cookie_token:
            try:
                session = read_platform_session(cookie_token)
            except PlatformSessionError:
                session = None
            if session is not None:
                try:
                    session_principal = principal_from_session(session)
                except SessionRevokedError as exc:
                    # Uma sessão cuja identidade foi revogada é 401, nunca um
                    # rebaixamento silencioso para o principal legado.
                    log_event(
                        logger,
                        logging.WARNING,
                        "admin.auth.session_revoked",
                        actor_id=str(session.get("actor") or actor_hint),
                        principal_id=session.get("principal_id"),
                    )
                    repository.create_audit_event(
                        actor_id=str(session.get("actor") or actor_hint),
                        action="authenticate",
                        resource_type="admin_access",
                        status="revoked",
                        request_id=str(get_request_context().get("request_id") or ""),
                        metadata={"reason": "session_identity_revoked"},
                    )
                    raise HTTPException(
                        status_code=401,
                        detail="Platform session is no longer valid.",
                    ) from exc
                _store(request, session_principal)
                log_event(
                    logger,
                    logging.INFO,
                    "admin.auth.succeeded",
                    actor_id=session_principal.actor,
                    auth_mode=session_principal.auth_mode,
                    admin_role=session_principal.global_role,
                    principal_id=session_principal.principal_id,
                )
                return

        # 3. Token legado global por papel.
        resolved_role = resolve_admin_role_from_token(provided_token, settings_obj=settings_obj)
        if resolved_role is not None:
            legacy_principal = build_legacy_principal(
                resolved_role,
                actor_hint,
                auth_mode=AUTH_MODE_LEGACY,
            )
            _store(request, legacy_principal)
            log_event(
                logger,
                logging.INFO,
                "admin.auth.succeeded",
                actor_id=legacy_principal.actor,
                auth_mode=legacy_principal.auth_mode,
                admin_role=legacy_principal.global_role,
                legacy_global=legacy_principal.is_global,
            )
            return

        log_event(
            logger,
            logging.WARNING,
            "admin.auth.failed",
            actor_id=actor_hint,
        )
        repository.create_audit_event(
            actor_id=actor_hint,
            action="authenticate",
            resource_type="admin_access",
            status="failed",
            request_id=str(get_request_context().get("request_id") or ""),
            metadata={"reason": "invalid_admin_credentials"},
        )
        raise HTTPException(status_code=401, detail="Invalid admin credentials.")

    return require_admin_access


def build_require_admin_role(
    required_role: str,
    *,
    logger: logging.Logger,
    repository_getter: Callable[[], Any],
):
    def dependency(request: Request) -> None:
        principal = getattr(request.state, "admin_principal", None)
        # O papel comparado é o **efetivo na empresa** desta requisição, não o
        # global. `require_company_scope` já resolveu a empresa, e a membership
        # pode rebaixar o principal ali — era o que não acontecia antes.
        scoped_company_id = getattr(request.state, "authorized_company_id", None)
        current_role = (
            principal.role_in_company(scoped_company_id) if principal else "viewer"
        )
        current_weight = ADMIN_ROLE_HIERARCHY.get(current_role, 0)
        required_weight = ADMIN_ROLE_HIERARCHY.get(required_role, 0)
        if current_weight >= required_weight:
            return

        repository = repository_getter()
        log_event(
            logger,
            logging.WARNING,
            "admin.authorization.denied",
            actor_id=getattr(request.state, "admin_actor", "admin"),
            current_role=current_role,
            required_role=required_role,
        )
        repository.create_audit_event(
            actor_id=getattr(request.state, "admin_actor", "admin"),
            action="authorize",
            resource_type="admin_access",
            status="denied",
            request_id=str(get_request_context().get("request_id") or ""),
            metadata={
                "current_role": current_role,
                "required_role": required_role,
            },
        )
        raise HTTPException(status_code=403, detail="Insufficient admin privileges.")

    return dependency
