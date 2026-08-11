import hmac
import logging
from collections.abc import Callable
from typing import Any

from fastapi import Header, HTTPException, Request

from app.core.logging import get_request_context, log_event
from app.services.admin_auth_service import (
    ADMIN_ROLE_HIERARCHY,
    extract_bearer_token,
    get_role_token_map,
    resolve_admin_role_from_token,
)
from app.services.identity_service import ADMIN_ROLE_MAP, verify_session_csrf
from app.services.platform_session_service import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    PlatformSessionError,
    read_platform_session,
)


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

        resolved_role = None
        auth_mode = None
        session = None

        if cookie_token:
            try:
                session = read_platform_session(cookie_token)
                resolved_role = session["role"]
                request.state.admin_actor = session["actor"]
                auth_mode = str(session.get("auth_mode") or "session")
            except PlatformSessionError:
                resolved_role = None

        if resolved_role is None:
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
                    detail=(
                        "Admin authentication is enabled but no identity session "
                        "or administrator tokens are configured."
                    ),
                )
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
        request.state.admin_company_ids = None
        request.state.admin_membership_roles = {}
        request.state.admin_user_id = None

        if auth_mode == "identity" and session is not None:
            membership_roles = {
                int(company_id): str(role)
                for company_id, role in dict(session.get("membership_roles") or {}).items()
            }
            request.state.admin_company_ids = sorted(membership_roles)
            request.state.admin_membership_roles = membership_roles
            request.state.admin_user_id = int(session["user_id"])
            request.state.admin_email = str(session.get("email") or "")

            if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                csrf_header = request.headers.get("X-CSRF-Token")
                csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
                if (
                    not csrf_header
                    or not csrf_cookie
                    or not hmac.compare_digest(csrf_header, csrf_cookie)
                    or not verify_session_csrf(session, csrf_header)
                ):
                    raise HTTPException(status_code=403, detail="CSRF validation failed.")

            target_company_id: int | None = None
            raw_company_id = request.query_params.get("company_id")
            raw_company_slug = request.query_params.get("company_slug")
            if raw_company_id:
                try:
                    target_company_id = int(raw_company_id)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="Invalid company_id.") from exc
            elif raw_company_slug:
                company = repository.get_company_by_slug(raw_company_slug)
                if not company:
                    raise HTTPException(status_code=404, detail="Company not found.")
                target_company_id = int(company["id"])
            elif request.url.path.startswith("/admin/companies/") or request.url.path.startswith("/admin/onboarding/setup-status/"):
                try:
                    target_company_id = int(request.url.path.rsplit("/", 1)[-1])
                except ValueError:
                    target_company_id = None

            global_identity_paths = {
                "/admin/companies",
                "/admin/system/migrations",
                "/admin/system/profile",
                "/admin/system/readiness",
                "/admin/calendar/providers",
                "/admin/onboarding/presets",
                "/admin/billing/plans",
            }
            tenant_scoped_prefixes = (
                "/admin/state",
                "/admin/observability",
                "/admin/audit-events",
                "/admin/system/onboarding",
                "/admin/calendar",
                "/admin/messages",
                "/admin/contacts",
                "/admin/company-context",
                "/admin/pending-questions",
                "/admin/knowledge-entries",
                "/admin/message-examples",
                "/admin/training-",
                "/admin/finetuning",
                "/admin/outbound-messages",
                "/admin/analytics",
                "/admin/reminders",
                "/admin/billing",
                "/admin/whatsapp",
            )
            if (
                target_company_id is None
                and request.url.path not in global_identity_paths
                and request.url.path.startswith(tenant_scoped_prefixes)
            ):
                raise HTTPException(
                    status_code=400,
                    detail="An explicit company_id or company_slug is required.",
                )

            if target_company_id is not None:
                identity_role = membership_roles.get(target_company_id)
                if identity_role is None:
                    raise HTTPException(
                        status_code=403,
                        detail="You do not have access to this company.",
                    )
                request.state.admin_role = ADMIN_ROLE_MAP[identity_role]
                request.state.admin_identity_role = identity_role
                request.state.admin_company_id = target_company_id

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

    # Tag the dependency so the permission matrix can be verified by tests.
    dependency.__admin_required_role__ = required_role  # type: ignore[attr-defined]
    return dependency
