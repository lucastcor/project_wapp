from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse

from app.api.schemas import PlatformSessionLoginPayload, PlatformSessionResponse
from app.services.platform_session_service import (
    COOKIE_NAME,
    PlatformSessionError,
    attach_platform_session_cookie,
    authenticate_platform_login,
    clear_platform_session_cookie,
    read_platform_session,
)


PLATFORM_DIR = Path(__file__).resolve().parent.parent / "platform"
PLATFORM_ASSETS_DIR = PLATFORM_DIR / "assets"

platform_router = APIRouter(include_in_schema=False)


@platform_router.get("/platform")
def read_platform_shell() -> FileResponse:
    return FileResponse(PLATFORM_DIR / "index.html")


@platform_router.get("/platform/")
def read_platform_shell_with_slash() -> RedirectResponse:
    return RedirectResponse(url="/platform")


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
