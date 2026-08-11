import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.responses import StreamingResponse

from app.api.admin_security import principal_from_session
from app.api.schemas import PlatformSessionLoginPayload, PlatformSessionResponse
from app.services.admin_principal_service import AdminPrincipal
from app.services.platform_session_service import (
    COOKIE_NAME,
    PlatformSessionError,
    attach_platform_session_cookie,
    authenticate_platform_login,
    clear_platform_session_cookie,
    read_platform_session,
)
from app.services.realtime_service import hub
from app.storage.repository import RuntimeRepository

PLATFORM_DIR = Path(__file__).resolve().parent.parent / "platform"
PLATFORM_ASSETS_DIR = PLATFORM_DIR / "assets"
# Bundle do Automation Studio, gerado por `npm run build` em
# `frontend/automation-studio`. Mesma origem de propósito: é o que permite
# reutilizar o cookie HTTP-only sem abrir CORS nem expor token ao JavaScript.
AUTOMATION_STUDIO_DIR = PLATFORM_DIR / "automations"

platform_router = APIRouter(include_in_schema=False)
repository = RuntimeRepository()


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

    Sem o bundle construído, responde uma página explicando como gerá-lo em
    vez de um 404 — a origem do problema é a build, e um 404 esconderia isso.
    """
    if not automation_studio_is_built():
        return HTMLResponse(
            """
            <!doctype html>
            <html lang="pt-BR"><head><meta charset="utf-8">
            <title>Automation Studio · build ausente</title></head>
            <body style="font-family:system-ui;background:#07100f;color:#f3f7fa;padding:40px">
              <h1 style="font-size:20px">Automation Studio não foi construído</h1>
              <p style="color:#aebcc6;max-width:60ch;line-height:1.5">
                O bundle ainda não existe em <code>app/platform/automations</code>.
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
def create_platform_session(payload: PlatformSessionLoginPayload, response: Response) -> PlatformSessionResponse:
    try:
        session = authenticate_platform_login(
            admin_token=payload.admin_token,
            actor=payload.admin_actor,
        )
    except PlatformSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    attach_platform_session_cookie(response, session)
    return PlatformSessionResponse(
        authenticated=True,
        role=str(session["role"]),
        actor=str(session["actor"]),
        auth_mode=str(session["auth_mode"]),
        expires_at=int(session["expires_at"]),
    )


@platform_router.get("/platform/auth/session", response_model=PlatformSessionResponse)
def read_platform_session_state(request: Request) -> PlatformSessionResponse:
    try:
        session = read_platform_session(request.cookies.get(COOKIE_NAME))
    except PlatformSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return PlatformSessionResponse(
        authenticated=True,
        role=str(session["role"]),
        actor=str(session["actor"]),
        auth_mode="session",
        expires_at=int(session["expires_at"]),
    )


@platform_router.delete("/platform/auth/session")
def delete_platform_session(response: Response) -> dict[str, bool]:
    clear_platform_session_cookie(response)
    return {"authenticated": False}


def _resolve_events_scope(
    principal: AdminPrincipal,
    company_id: int | None,
    company_slug: str | None,
) -> int | None:
    """Escopo de empresa do stream, com a mesma autorização das rotas admin.

    Escopo global só existe para principal global. Qualquer outro precisa de
    uma empresa da sua membership.
    """
    if company_id is None and not company_slug:
        if principal.is_global:
            return None
        if len(principal.allowed_company_ids) == 1:
            return next(iter(principal.allowed_company_ids))
        if not principal.allowed_company_ids:
            raise HTTPException(
                status_code=403,
                detail="Company access denied for this principal.",
            )
        raise HTTPException(
            status_code=400,
            detail="company_id or company_slug is required to subscribe to platform events.",
        )

    if principal.is_global:
        resolved_company_id = repository.resolve_company_id(
            company_id=company_id,
            company_slug=company_slug,
        )
        if resolved_company_id is None:
            raise HTTPException(status_code=404, detail="Company not found.")
        return int(resolved_company_id)

    resolved_company_id = repository.resolve_company_id(
        company_id=company_id,
        company_slug=company_slug,
    )
    if resolved_company_id is None or not principal.can_access_company(int(resolved_company_id)):
        raise HTTPException(status_code=403, detail="Company access denied for this principal.")
    return int(resolved_company_id)


@platform_router.get("/platform/events")
async def sse_stream(
    request: Request,
    company_id: int | None = Query(default=None),
    company_slug: str | None = Query(default=None),
) -> StreamingResponse:
    """Stream SSE autenticado e escopado por empresa."""
    try:
        session = read_platform_session(request.cookies.get(COOKIE_NAME))
    except PlatformSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    principal = principal_from_session(session)
    scope_company_id = _resolve_events_scope(principal, company_id, company_slug)
    subscription = hub.subscribe(
        company_id=scope_company_id,
        role=principal.global_role,
    )

    async def event_generator():
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

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
