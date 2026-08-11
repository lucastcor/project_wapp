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
from app.api.automation_routes import automation_router
from app.api.platform_admin_routes import platform_admin_router
from app.api.platform_routes import (
    AUTOMATION_STUDIO_DIR,
    PLATFORM_ASSETS_DIR,
    platform_router,
)
from app.api.routes import router
from app.core.config import is_missing_or_placeholder, settings
from app.core.exceptions import TenantResourceNotFoundError
from app.core.logging import (
    clear_request_context,
    configure_logging,
    get_logger,
    log_event,
    set_request_context,
)
from app.core.tenancy import TenantScopeError
from app.services.rate_limit_service import build_rate_limit_headers, evaluate_rate_limit, get_request_client_ip
from app.services.runtime_guard_service import get_runtime_blockers, validate_runtime_environment
from app.services.system_readiness_service import get_system_readiness
from app.storage.db import check_runtime_database_connection, close_postgres_pool, init_db

configure_logging()
logger = get_logger(__name__)


def _validate_production_config() -> None:
    warnings = []
    if settings.environment == "production":
        if is_missing_or_placeholder(settings.platform_session_secret):
            warnings.append("PLATFORM_SESSION_SECRET is not set or uses placeholder value")
        if settings.enable_admin_auth:
            tokens = [settings.admin_api_token, settings.admin_viewer_token,
                      settings.admin_operator_token, settings.admin_superadmin_token]
            if all(t is None for t in tokens):
                warnings.append("ENABLE_ADMIN_AUTH=true but no admin tokens configured")
            for t in tokens:
                if t and is_missing_or_placeholder(t):
                    warnings.append("Admin token uses placeholder 'change-me' value")
                    break
        if is_missing_or_placeholder(settings.openai_api_key) and settings.classification_provider == "openai":
            warnings.append("CLASSIFICATION_PROVIDER=openai but OPENAI_API_KEY is not set")
    if settings.outbound_provider == "mock":
        warnings.append("OUTBOUND_PROVIDER=mock — WhatsApp messages will not be delivered")
    if settings.calendar_provider == "mock":
        warnings.append("CALENDAR_PROVIDER=mock — calendar operations use fake data")
    for w in warnings:
        logger.warning(f"[CONFIG] {w}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _validate_production_config()
    init_db()
    validate_runtime_environment("api")
    try:
        yield
    finally:
        close_postgres_pool()


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
        client_ip = get_request_client_ip(request)

        set_request_context(
            request_id=request_id,
            http_method=request.method,
            http_path=request.url.path,
            client_ip=client_ip,
        )
        request.state.request_id = request_id

        rate_limit_decision = evaluate_rate_limit(request)
        if rate_limit_decision and not rate_limit_decision.allowed:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            log_event(
                logger,
                logging.WARNING,
                "http.request.rate_limited",
                client_ip=client_ip,
                rate_limit_scope=rate_limit_decision.scope,
                rate_limit_limit=rate_limit_decision.limit,
                retry_after=rate_limit_decision.retry_after,
                duration_ms=duration_ms,
            )
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests.",
                    "request_id": request_id,
                    "retry_after_seconds": rate_limit_decision.retry_after,
                },
            )
            response.headers["X-Request-ID"] = request_id
            for header_name, header_value in build_rate_limit_headers(rate_limit_decision).items():
                response.headers[header_name] = header_value
            clear_request_context()
            return response

        log_event(
            logger,
            logging.INFO,
            "http.request.started",
            client_ip=client_ip,
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
        if rate_limit_decision:
            for header_name, header_value in build_rate_limit_headers(rate_limit_decision).items():
                response.headers[header_name] = header_value
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
app.include_router(platform_admin_router)
app.include_router(automation_router)
app.include_router(platform_router)
app.mount("/platform/assets", StaticFiles(directory=str(PLATFORM_ASSETS_DIR)), name="platform-assets")

# O bundle do Automation Studio só é montado quando existe. Montar um
# diretório ausente derrubaria o processo inteiro por causa do frontend.
if AUTOMATION_STUDIO_DIR.is_dir():
    app.mount(
        "/platform/automations",
        StaticFiles(directory=str(AUTOMATION_STUDIO_DIR), html=True),
        name="automation-studio",
    )


@app.exception_handler(TenantScopeError)
async def tenant_scope_error_handler(request: Request, exc: TenantScopeError) -> JSONResponse:
    """Empresa ausente ou inválida numa borda interna é **400**, não 500.

    Sem este handler, `require_company_id` recusando um valor virava erro
    interno: culpava o servidor por um erro do cliente, acionava alerta
    operacional para um caso esperado, e era o único status cuja resposta podia
    carregar detalhe interno. A mensagem da exceção cita contexto e tabela e
    fica no log; o corpo devolve só a categoria.
    """
    log_event(
        logger,
        logging.WARNING,
        "http.tenant_scope.rejected",
        http_path=request.url.path,
        reason=str(exc),
    )
    return JSONResponse(
        status_code=400,
        content={"detail": "A valid company scope is required for this operation."},
    )


@app.exception_handler(TenantResourceNotFoundError)
async def tenant_resource_not_found_handler(
    request: Request,
    exc: TenantResourceNotFoundError,
) -> JSONResponse:
    """Recurso fora do escopo autorizado responde 404, sempre.

    Registrado no app inteiro, e não em cada rota, porque a garantia é de
    classe: um handler novo que carregue recurso por id herda o
    comportamento correto sem precisar lembrar de nada. Não existe caminho em
    que "é de outro tenant" pareça diferente de "não existe".
    """
    log_event(
        logger,
        logging.INFO,
        "http.resource.not_found_in_scope",
        request_id=getattr(request.state, "request_id", None),
        http_path=request.url.path,
    )
    return JSONResponse(status_code=404, content={"detail": "Resource not found."})


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
