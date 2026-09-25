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
from app.api.billing_routes import billing_router
from app.api.google_calendar_routes import google_calendar_router
from app.api.platform_admin_routes import platform_admin_router
from app.api.platform_routes import (
    AUTOMATION_STUDIO_DIR,
    PLATFORM_ASSETS_DIR,
    platform_router,
)
from app.api.routes import router
from app.api.whatsapp_signup_routes import whatsapp_signup_router
from app.core.config import is_missing_or_placeholder, settings
from app.core.logging import (
    clear_request_context,
    configure_logging,
    get_logger,
    log_event,
    set_request_context,
)
from app.core.tenancy import TenantScopeError
from app.services.rate_limit_service import (
    build_rate_limit_headers,
    evaluate_rate_limit,
    get_request_client_ip,
)
from app.services.realtime_service import close_realtime_broker, start_realtime_broker
from app.services.runtime_guard_service import (
    get_runtime_blockers,
    validate_runtime_environment,
)
from app.services.system_readiness_service import get_system_readiness
from app.storage.db import (
    check_database_schema_current,
    check_runtime_database_connection,
    close_postgres_pool,
    init_db,
)

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
        if is_missing_or_placeholder(settings.internal_ingress_secret):
            warnings.append("INTERNAL_INGRESS_SECRET is not set — internal ingress is closed")
    if settings.enable_normalized_whatsapp_ingress and is_missing_or_placeholder(settings.internal_ingress_secret):
        warnings.append(
            "ENABLE_NORMALIZED_WHATSAPP_INGRESS=true without a valid INTERNAL_INGRESS_SECRET"
        )
    if settings.outbound_provider == "mock":
        warnings.append("OUTBOUND_PROVIDER=mock — WhatsApp messages will not be delivered")
    if settings.calendar_provider == "mock":
        warnings.append("CALENDAR_PROVIDER=mock — calendar operations use fake data")
    for w in warnings:
        logger.warning(f"[CONFIG] {w}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _validate_production_config()
    if settings.run_migrations_on_startup:
        init_db()
    elif (
        not check_runtime_database_connection()["ok"]
        or not check_database_schema_current()["ok"]
    ):
        raise RuntimeError(
            "Database is unavailable or not migrated. Run `python -m app.tools.run_migrations` "
            "as a release step before starting the API."
        )
    validate_runtime_environment("api")
    start_realtime_broker()
    try:
        yield
    finally:
        close_realtime_broker()
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

        # O retorno do OAuth traz o código de autorização e o state na query;
        # nenhum dos dois pode parar no log.
        loggable_query = None if request.url.path.startswith("/platform/integrations/") else (str(request.url.query) or None)
        log_event(
            logger,
            logging.INFO,
            "http.request.started",
            client_ip=client_ip,
            query_string=loggable_query,
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
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # O SDK da Meta (Embedded Signup do WhatsApp) é o único script externo
        # permitido: ele abre o popup de conexão e conversa com facebook.com
        # via XHR e iframe. Tudo o mais continua restrito à própria origem.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self' https://connect.facebook.net; "
            "connect-src 'self' https://www.facebook.com https://web.facebook.com "
            "https://graph.facebook.com https://connect.facebook.net; "
            "frame-src https://www.facebook.com https://web.facebook.com https://staticxx.facebook.com"
        )
        if settings.environment.strip().lower() == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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
app.include_router(automation_router)
app.include_router(platform_admin_router)
app.include_router(billing_router)
app.include_router(whatsapp_signup_router)
app.include_router(google_calendar_router)
app.include_router(platform_router)
app.mount("/platform/assets", StaticFiles(directory=str(PLATFORM_ASSETS_DIR)), name="platform-assets")

# O bundle do Automation Studio é servido pelo mesmo host da API, e não de um
# domínio próprio: assim o cookie de sessão canônico (HttpOnly, SameSite) e o
# CSRF valem sem CORS nem um segundo mecanismo de autenticação para o editor.
#
# O mount é condicional porque o bundle é produto de um passo de build. Montar
# um diretório ausente levanta na importação e derrubaria a API inteira por
# causa do frontend — o backend tem de subir mesmo sem Studio compilado.
if AUTOMATION_STUDIO_DIR.is_dir():
    app.mount(
        "/platform/automations",
        StaticFiles(directory=str(AUTOMATION_STUDIO_DIR), html=True),
        name="automation-studio",
    )


@app.exception_handler(TenantScopeError)
async def tenant_scope_error_handler(_request: Request, exc: TenantScopeError) -> JSONResponse:
    """Empresa ausente ou inválida numa borda interna é **400**, não 500.

    `TenantScopeError` significa que o chamador não informou empresa, ou
    informou uma inválida — erro do cliente. Sem este handler ele sobe como
    exceção não tratada e vira 500, o que faz um erro de uso parecer falha do
    servidor em alerta e em log.
    """
    return JSONResponse(status_code=400, content={"detail": str(exc)})


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
