import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.admin_routes import admin_router
from app.api.routes import router
from app.core.config import settings
from app.core.logging import (
    clear_request_context,
    configure_logging,
    get_logger,
    log_event,
    set_request_context,
)
from app.storage.db import check_runtime_database_connection, init_db

configure_logging()
logger = get_logger(__name__)

init_db()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="API base para orquestração simples de chatbot.",
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
def readiness_check() -> dict[str, str | bool]:
    database_probe = check_runtime_database_connection()
    return {
        "status": "ok" if database_probe["ok"] else "degraded",
        "database_ok": bool(database_probe["ok"]),
    }
