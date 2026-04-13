import logging
from collections.abc import Callable

from fastapi import Header, HTTPException, Request

from app.core.logging import get_request_context, log_event
from app.services.admin_auth_service import (
    ADMIN_ROLE_HIERARCHY,
    extract_bearer_token,
    get_role_token_map,
    resolve_admin_role_from_token,
)
from app.services.platform_session_service import COOKIE_NAME, PlatformSessionError, read_platform_session


def build_require_admin_access(
    *,
    settings_obj: Any,
    logger: logging.Logger,
    repository_getter: Callable[[], Any],
):
    def require_admin_access(
        request: Request,
        authorization: str | None = Header(default=None),
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
        x_admin_actor: str | None = Header(default=None, alias="X-Admin-Actor"),
    ) -> None:
        actor = (x_admin_actor or "admin").strip() or "admin"
        request.state.admin_actor = actor
        request.state.admin_role = "superadmin"

        if not settings_obj.enable_admin_auth:
            request.state.admin_auth_mode = "disabled"
            return

        repository = repository_getter()
        role_token_map = get_role_token_map(settings_obj)
        provided_token = x_admin_token or extract_bearer_token(authorization)
        cookie_token = request.cookies.get(COOKIE_NAME)

        if not role_token_map:
            log_event(
                logger,
                logging.ERROR,
                "admin.auth.misconfigured",
                actor_id=actor,
            )
            repository.create_audit_event(
                actor_id=actor,
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

        resolved_role = None
        auth_mode = None

        if cookie_token:
            try:
                session = read_platform_session(cookie_token)
                resolved_role = session["role"]
                request.state.admin_actor = session["actor"]
                auth_mode = "session"
            except PlatformSessionError:
                resolved_role = None

        if resolved_role is None:
            resolved_role = resolve_admin_role_from_token(provided_token, settings_obj=settings_obj)
            if resolved_role is not None:
                auth_mode = "token"

        if resolved_role is None:
            log_event(
                logger,
                logging.WARNING,
                "admin.auth.failed",
                actor_id=actor,
            )
            repository.create_audit_event(
                actor_id=actor,
                action="authenticate",
                resource_type="admin_access",
                status="failed",
                request_id=str(get_request_context().get("request_id") or ""),
                metadata={"reason": "invalid_admin_credentials"},
            )
            raise HTTPException(status_code=401, detail="Invalid admin credentials.")

        request.state.admin_auth_mode = auth_mode or "token"
        request.state.admin_role = resolved_role
        log_event(
            logger,
            logging.INFO,
            "admin.auth.succeeded",
            actor_id=getattr(request.state, "admin_actor", actor),
            auth_mode=request.state.admin_auth_mode,
            admin_role=resolved_role,
        )

    return require_admin_access


def build_require_admin_role(
    required_role: str,
    *,
    logger: logging.Logger,
    repository_getter: Callable[[], Any],
):
    def dependency(request: Request) -> None:
        current_role = getattr(request.state, "admin_role", "viewer")
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
