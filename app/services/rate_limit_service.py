import time
from dataclasses import dataclass

from fastapi import Request

from app.core.config import settings
from app.services.shared_state import get_counter_store, reset_counter_store

LOCAL_CLIENT_IDENTIFIERS = {"127.0.0.1", "::1", "localhost", "testclient"}


@dataclass(frozen=True)
class RateLimitDecision:
    scope: str
    limit: int
    window_seconds: int
    client_id: str
    allowed: bool
    remaining: int
    reset_at: int
    retry_after: int


def reset_rate_limit_state() -> None:
    reset_counter_store()


def get_request_client_ip(request: Request) -> str:
    forwarded_for = str(request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded_for:
        first_hop = forwarded_for.split(",", 1)[0].strip()
        if first_hop:
            return first_hop
    if request.client and request.client.host:
        return str(request.client.host).strip() or "unknown"
    return "unknown"


def _resolve_scope_and_limit(request_path: str) -> tuple[str, int] | None:
    if request_path == "/platform/auth/session":
        return "platform_auth", max(1, settings.rate_limit_auth_requests)
    if request_path.startswith("/admin"):
        return "admin", max(1, settings.rate_limit_admin_requests)
    if request_path == "/messages/incoming":
        return "incoming_message", max(1, settings.rate_limit_incoming_requests)
    if request_path == "/webhooks/whatsapp":
        return "whatsapp_webhook", max(1, settings.rate_limit_webhook_requests)
    return None


def evaluate_rate_limit(request: Request) -> RateLimitDecision | None:
    if not settings.enable_rate_limiting:
        return None

    scope_config = _resolve_scope_and_limit(request.url.path)
    if scope_config is None:
        return None

    client_id = get_request_client_ip(request)
    if settings.rate_limit_exempt_localhost and client_id in LOCAL_CLIENT_IDENTIFIERS:
        return None

    scope, limit = scope_config
    window_seconds = max(1, settings.rate_limit_window_seconds)
    now = int(time.time())
    window_start = (now // window_seconds) * window_seconds
    window_reset_at = window_start + window_seconds
    # A janela entra na chave para que o contador expire naturalmente e o
    # mesmo contrato sirva ao backend em memória e ao compartilhado.
    storage_key = f"project_wapp:ratelimit:{scope}:{client_id}:{window_start}"

    current_count = get_counter_store().increment(storage_key, window_seconds)

    if current_count > limit:
        return RateLimitDecision(
            scope=scope,
            limit=limit,
            window_seconds=window_seconds,
            client_id=client_id,
            allowed=False,
            remaining=0,
            reset_at=window_reset_at,
            retry_after=max(1, window_reset_at - now),
        )

    return RateLimitDecision(
        scope=scope,
        limit=limit,
        window_seconds=window_seconds,
        client_id=client_id,
        allowed=True,
        remaining=max(0, limit - current_count),
        reset_at=window_reset_at,
        retry_after=0,
    )


def build_rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    headers = {
        "X-RateLimit-Scope": decision.scope,
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(max(0, decision.remaining)),
        "X-RateLimit-Reset": str(decision.reset_at),
    }
    if not decision.allowed:
        headers["Retry-After"] = str(max(1, decision.retry_after))
    return headers
