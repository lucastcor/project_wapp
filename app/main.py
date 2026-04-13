import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.admin_routes import admin_router
from app.api.platform_routes import PLATFORM_ASSETS_DIR, platform_router
from app.api.routes import router
from app.core.config import settings
from app.core.logging import (
    clear_request_context,
    configure_logging,
    get_logger,
    log_event,
    set_request_context,
)
from app.services.runtime_guard_service import get_runtime_blockers, validate_runtime_environment
from app.services.system_readiness_service import get_system_readiness
from app.storage.db import check_runtime_database_connection, init_db

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    validate_runtime_environment("api")
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="API base para orquestração simples de chatbot.",
    lifespan=lifespan,
)


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", "").strip() or uuid.uuid4().hex
        start_time = time.perf_counter()

        set_request_context(
            request_id=request_id,
            http_method=request.method,
            http_path=request.url.path,
        )
        request.state.request_id = request_id

        log_event(
            logger,
            logging.INFO,
            "http.request.started",
            client_ip=request.client.host if request.client else None,
            query_string=str(request.url.query) or None,
        )

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            log_event(
                logger,
                logging.ERROR,
                "http.request.failed",
                duration_ms=duration_ms,
            )
            clear_request_context()
            raise

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        log_event(
            logger,
            logging.INFO,
            "http.request.completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        clear_request_context()
        return response


app.add_middleware(RequestObservabilityMiddleware)
app.include_router(router)
app.include_router(admin_router)
app.include_router(platform_router)
app.mount("/platform/assets", StaticFiles(directory=str(PLATFORM_ASSETS_DIR)), name="platform-assets")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    log_event(
        logger,
        logging.ERROR,
        "http.unhandled_exception",
        request_id=request_id,
        exception_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error.",
            "request_id": request_id,
        },
    )


@app.get("/health", tags=["health"])
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness_check() -> Any:
    blockers = get_runtime_blockers()
    readiness = get_system_readiness()
    database_probe = check_runtime_database_connection()
    payload = {
        "status": "ok" if database_probe["ok"] else "degraded",
        "database_ok": bool(database_probe["ok"]),
        "overall_status": readiness["overall_status"],
        "pilot_ready": readiness["pilot_ready"],
        "scale_ready": readiness["scale_ready"],
        "blocker_count": len(blockers),
        "blockers": [str(check.get("code")) for check in blockers],
    }
    if database_probe["ok"] and not blockers:
        return payload
    payload["status"] = "degraded"
    return JSONResponse(status_code=503, content=payload)
