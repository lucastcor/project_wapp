import asyncio
import hmac
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.responses import StreamingResponse

from app.api.schemas import (
    IdentityInvitationAcceptPayload,
    IdentityInvitationCreatePayload,
    IdentityMembershipUpdatePayload,
    IdentityRegistrationPayload,
    PasswordResetConfirmPayload,
    PasswordResetRequestPayload,
    PlatformSessionLoginPayload,
    PlatformSessionResponse,
)
from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.services.identity_service import (
    IdentityAuthorizationError,
    IdentityConflictError,
    IdentityError,
    accept_invitation,
    confirm_password_reset,
    create_invitation,
    create_user_with_company,
    issue_user_session,
    list_company_members,
    request_password_reset,
    update_company_member,
    verify_session_csrf,
)
from app.services.platform_session_service import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    PlatformSessionError,
    attach_platform_session_cookie,
    authenticate_platform_login,
    clear_platform_session_cookie,
    read_platform_session,
)
from app.services.rate_limit_service import get_request_client_ip
from app.services.realtime_service import hub
from app.services.transactional_email_service import (
    TransactionalEmailError,
    send_invitation_email,
    send_password_reset_email,
)

PLATFORM_DIR = Path(__file__).resolve().parent.parent / "platform"
PLATFORM_ASSETS_DIR = PLATFORM_DIR / "assets"
# Saída do build do Automation Studio (`frontend/automation-studio`, Vite).
# Fica ao lado dos assets da plataforma porque é servido pelo mesmo host: o
# editor usa o cookie de sessão canônico, e um domínio separado exigiria
# CORS e um segundo mecanismo de autenticação só para ele.
AUTOMATION_STUDIO_DIR = PLATFORM_ASSETS_DIR.parent / "automations"

platform_router = APIRouter(include_in_schema=False)
logger = get_logger(__name__)


def require_platform_session(request: Request) -> dict[str, Any]:
    """Resolve the authenticated platform session or reject the connection."""
    try:
        return read_platform_session(request.cookies.get(COOKIE_NAME))
    except PlatformSessionError as exc:
        raise HTTPException(status_code=401, detail="Platform session required.") from exc


def require_identity_session(request: Request, *, csrf: bool = False) -> dict[str, Any]:
    session = require_platform_session(request)
    if session.get("auth_mode") != "identity" or not session.get("user_id"):
        raise HTTPException(status_code=403, detail="A user identity session is required.")
    if csrf:
        csrf_header = request.headers.get("X-CSRF-Token")
        csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
        if (
            not csrf_header
            or not csrf_cookie
            or not hmac.compare_digest(csrf_header, csrf_cookie)
            or not verify_session_csrf(session, csrf_header)
        ):
            raise HTTPException(status_code=403, detail="CSRF validation failed.")
    return session


def _client_ip(request: Request) -> str | None:
    resolved = get_request_client_ip(request)
    return resolved if resolved != "unknown" else None


def _session_response(session: dict[str, Any]) -> PlatformSessionResponse:
    return PlatformSessionResponse(
        authenticated=True,
        role=str(session["role"]),
        actor=str(session["actor"]),
        auth_mode=str(session.get("auth_mode") or "session"),
        expires_at=int(session["expires_at"]),
        user_id=int(session["user_id"]) if session.get("user_id") is not None else None,
        email=str(session["email"]) if session.get("email") else None,
        memberships=list(session.get("memberships") or []),
    )


@platform_router.get("/platform")
def read_platform_shell() -> FileResponse:
    return FileResponse(PLATFORM_DIR / "index.html")


@platform_router.get("/platform/")
def read_platform_shell_with_slash() -> RedirectResponse:
    return RedirectResponse(url="/platform")


def automation_studio_is_built() -> bool:
    return (AUTOMATION_STUDIO_DIR / "index.html").is_file()


@platform_router.get("/platform/automations", response_model=None)
def read_automation_studio_shell() -> FileResponse | HTMLResponse:
    """Casca do Automation Studio.

    Sem o bundle construído, responde 503 com a instrução de build em vez de
    404. A causa é a build ausente, e um 404 apontaria para o lugar errado —
    quem visse pensaria em rota faltando, não em passo de deploy não executado.
    """
    if not automation_studio_is_built():
        return HTMLResponse(
            """
            <!doctype html>
            <html lang="pt-BR"><head><meta charset="utf-8">
            <title>Automation Studio - build ausente</title></head>
            <body style="font-family:system-ui;background:#07100f;color:#f3f7fa;padding:40px">
              <h1 style="font-size:20px">Automation Studio nao foi construido</h1>
              <p style="color:#aebcc6;max-width:60ch;line-height:1.5">
                O bundle ainda nao existe em <code>app/platform/automations</code>.
                Gere-o com:
              </p>
              <pre style="background:#101b21;padding:12px;border-radius:10px">cd frontend/automation-studio
npm ci
npm run build</pre>
              <p><a href="/platform" style="color:#1fd2bd">Voltar para a plataforma</a></p>
            </body></html>
            """,
            status_code=503,
        )
    return FileResponse(AUTOMATION_STUDIO_DIR / "index.html")


@platform_router.get("/platform/automations/")
def read_automation_studio_shell_with_slash() -> RedirectResponse:
    return RedirectResponse(url="/platform/automations")


@platform_router.post("/platform/auth/session", response_model=PlatformSessionResponse)
def create_platform_session(
    payload: PlatformSessionLoginPayload,
    request: Request,
    response: Response,
) -> PlatformSessionResponse:
    try:
        session = authenticate_platform_login(
            admin_token=payload.admin_token,
            actor=payload.admin_actor,
            email=payload.email,
            password=payload.password,
            user_agent=request.headers.get("User-Agent"),
            ip_address=_client_ip(request),
        )
    except PlatformSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    attach_platform_session_cookie(response, session)
    return _session_response(session)


@platform_router.get("/platform/auth/session", response_model=PlatformSessionResponse)
def read_platform_session_state(request: Request) -> PlatformSessionResponse:
    try:
        session = read_platform_session(request.cookies.get(COOKIE_NAME))
    except PlatformSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return _session_response(session)


@platform_router.get("/platform/auth/capabilities")
def read_platform_auth_capabilities() -> dict[str, bool]:
    return {
        "self_service_signup": settings.enable_self_service_signup,
        "password_reset": (
            settings.transactional_email_provider == "resend"
            or settings.identity_expose_action_tokens
        ),
    }


@platform_router.delete("/platform/auth/session")
def delete_platform_session(request: Request, response: Response) -> dict[str, bool]:
    token = request.cookies.get(COOKIE_NAME)
    if token and "." not in token:
        require_identity_session(request, csrf=True)
    clear_platform_session_cookie(response, token)
    return {"authenticated": False}


@platform_router.post("/platform/auth/register", response_model=PlatformSessionResponse)
def register_platform_account(
    payload: IdentityRegistrationPayload,
    request: Request,
    response: Response,
) -> PlatformSessionResponse:
    try:
        registration = create_user_with_company(
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            company_name=payload.company_name,
            company_slug=payload.company_slug,
            business_type=payload.business_type,
        )
        user = registration["user"]
        session = issue_user_session(
            user_id=int(user["id"]),
            user_agent=request.headers.get("User-Agent"),
            ip_address=_client_ip(request),
        )
        platform_session = {
            **session,
            "role": "superadmin",
            "identity_role": "owner",
            "actor": str(user["display_name"]),
            "email": str(user["email"]),
            "user_id": int(user["id"]),
            "memberships": registration["memberships"],
            "company_ids": [int(registration["company_id"])],
            "auth_mode": "identity",
        }
    except IdentityError as exc:
        status_code = 403 if "disabled" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    attach_platform_session_cookie(response, platform_session)
    return _session_response(platform_session)


@platform_router.post("/platform/auth/invitations/accept", response_model=PlatformSessionResponse)
def accept_platform_invitation(
    payload: IdentityInvitationAcceptPayload,
    request: Request,
    response: Response,
) -> PlatformSessionResponse:
    try:
        accepted = accept_invitation(
            token=payload.token,
            password=payload.password,
            display_name=payload.display_name,
        )
        user = accepted["user"]
        session = issue_user_session(
            user_id=int(user["id"]),
            user_agent=request.headers.get("User-Agent"),
            ip_address=_client_ip(request),
        )
        memberships = accepted["memberships"]
        highest_role = max(
            memberships,
            key=lambda item: {"viewer": 1, "operator": 2, "admin": 3, "owner": 4}.get(
                str(item["role"]),
                0,
            ),
        )
        identity_role = str(highest_role["role"])
        platform_session = {
            **session,
            "role": "superadmin" if identity_role in {"owner", "admin"} else identity_role,
            "identity_role": identity_role,
            "actor": str(user["display_name"]),
            "email": str(user["email"]),
            "user_id": int(user["id"]),
            "memberships": memberships,
            "company_ids": [int(item["company_id"]) for item in memberships],
            "auth_mode": "identity",
        }
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    attach_platform_session_cookie(response, platform_session)
    return _session_response(platform_session)


@platform_router.post("/platform/identity/invitations")
def create_platform_invitation(
    payload: IdentityInvitationCreatePayload,
    request: Request,
) -> dict[str, Any]:
    session = require_identity_session(request, csrf=True)
    try:
        invitation = create_invitation(
            company_id=payload.company_id,
            email=payload.email,
            role=payload.role,
            invited_by_user_id=int(session["user_id"]),
        )
    except IdentityAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    action_token = invitation.pop("token")
    try:
        delivery = send_invitation_email(
            recipient=str(invitation["email"]),
            company_name=str(invitation["company_name"]),
            role=str(invitation["role"]),
            token=action_token,
        )
    except TransactionalEmailError as exc:
        log_event(
            logger,
            logging.ERROR,
            "identity.invitation.delivery_failed",
            company_id=payload.company_id,
            error_type=type(exc).__name__,
        )
        if not settings.identity_expose_action_tokens:
            raise HTTPException(
                status_code=503,
                detail="Invitation was created but email delivery is unavailable.",
            ) from exc
        delivery = {"delivered": False, "provider": None}
    return {
        **invitation,
        "delivery_status": "delivered" if delivery["delivered"] else "development_token",
        "action_token": action_token if settings.identity_expose_action_tokens else None,
    }


@platform_router.get("/platform/identity/members")
def read_platform_members(
    request: Request,
    company_id: int,
) -> list[dict[str, Any]]:
    session = require_identity_session(request)
    try:
        return list_company_members(
            company_id=company_id,
            requested_by_user_id=int(session["user_id"]),
        )
    except IdentityAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@platform_router.patch("/platform/identity/members/{membership_id}")
def update_platform_member(
    membership_id: int,
    payload: IdentityMembershipUpdatePayload,
    request: Request,
    company_id: int,
) -> dict[str, Any]:
    session = require_identity_session(request, csrf=True)
    try:
        return update_company_member(
            company_id=company_id,
            membership_id=membership_id,
            requested_by_user_id=int(session["user_id"]),
            role=payload.role,
            active=payload.active,
        )
    except IdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IdentityAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@platform_router.post("/platform/auth/password-reset/request")
def request_platform_password_reset(payload: PasswordResetRequestPayload) -> dict[str, Any]:
    action = request_password_reset(payload.email)
    delivery_status = "accepted"
    if action:
        try:
            send_password_reset_email(
                recipient=str(action["email"]),
                display_name=str(action["display_name"]),
                token=str(action["token"]),
            )
            delivery_status = "delivered"
        except TransactionalEmailError as exc:
            # Do not disclose whether the address exists or whether its
            # delivery failed. Operators can diagnose the structured event.
            log_event(
                logger,
                logging.ERROR,
                "identity.password_reset.delivery_failed",
                error_type=type(exc).__name__,
            )
            if settings.identity_expose_action_tokens:
                delivery_status = "development_token"
    return {
        "accepted": True,
        "delivery_status": delivery_status,
        "action_token": (
            action["token"]
            if action and settings.identity_expose_action_tokens
            else None
        ),
    }


@platform_router.post("/platform/auth/password-reset/confirm")
def confirm_platform_password_reset(payload: PasswordResetConfirmPayload) -> dict[str, bool]:
    try:
        confirm_password_reset(token=payload.token, password=payload.password)
    except IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"updated": True}


@platform_router.get("/platform/events")
async def sse_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events stream for real-time platform notifications.

    Requires a valid platform session; the tenant scope comes from that session
    and never from query parameters supplied by the client.
    """
    session = require_platform_session(request)
    company_ids = session.get("company_ids")
    subscription = hub.subscribe(company_ids)
    log_event(
        logger,
        logging.INFO,
        "platform.events.subscribed",
        actor_id=session.get("actor"),
        admin_role=session.get("role"),
        company_scope="all" if company_ids is None else ",".join(str(item) for item in company_ids),
    )

    async def event_generator() -> AsyncIterator[str]:
        try:
            # Send initial heartbeat so the client knows the connection is alive
            yield "event: connected\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(subscription.queue.get(), timeout=25.0)
                    yield f"data: {payload}\n\n"
                except TimeoutError:
                    # Keep-alive ping every 25s to prevent proxy timeouts
                    yield ": heartbeat\n\n"
        finally:
            hub.unsubscribe(subscription)
            log_event(
                logger,
                logging.INFO,
                "platform.events.unsubscribed",
                actor_id=session.get("actor"),
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
