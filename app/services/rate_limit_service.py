import hashlib
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any

from fastapi import Request

from app.core.config import settings

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


_RATE_LIMIT_STATE: dict[str, tuple[int, int]] = {}
_RATE_LIMIT_LOCK = Lock()
_REDIS_CLIENT: Any | None = None
_REDIS_CLIENT_URL: str | None = None
_REDIS_FIXED_WINDOW_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {count, ttl}
"""


def _get_redis_client() -> Any | None:
    global _REDIS_CLIENT, _REDIS_CLIENT_URL
    redis_url = str(settings.redis_url or "").strip()
    if not redis_url:
        return None
    if _REDIS_CLIENT is not None and redis_url == _REDIS_CLIENT_URL:
        return _REDIS_CLIENT
    try:
        import redis
    except ImportError as exc:
        if settings.require_shared_state:
            raise RuntimeError("REDIS_URL is configured but the redis package is unavailable.") from exc
        return None
    _REDIS_CLIENT = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )
    _REDIS_CLIENT_URL = redis_url
    return _REDIS_CLIENT


RATE_LIMIT_KEY_PREFIX = "project_wapp:rate_limit:"


def clear_rate_limit_counters() -> None:
    """Apaga também os contadores de janela que vivem no Redis.

    Separada de `reset_rate_limit_state` de propósito. Quando o Redis está
    configurado, o contador é **estado compartilhado**: descartá-lo é o oposto
    de simular uma réplica nova, e é justamente a diferença entre as duas
    funções.

    Isolamento de teste precisa desta; sem ela, um teste que consome o limite
    deixa a janela correndo para o próximo, que falha por um motivo que nada
    tem a ver com o que ele testa.
    """
    client = _get_redis_client()
    if client is not None:
        try:
            # SCAN, e não KEYS: o mesmo Redis pode servir outra coisa, e
            # bloquear o servidor inteiro para limpar contador é caro demais.
            for key in client.scan_iter(match=f"{RATE_LIMIT_KEY_PREFIX}*", count=500):
                client.delete(key)
        except Exception:
            # Redis fora do ar não pode transformar limpeza em erro.
            pass
    reset_rate_limit_state()


def reset_rate_limit_state() -> None:
    """Descarta o estado **local** deste processo.

    Não toca no Redis: com estado compartilhado, um processo que reinicia
    encontra as janelas em curso exatamente onde estavam. É por isso que esta
    função serve para simular uma réplica nova — e por que ela sozinha não
    isola um teste do anterior.
    """
    global _REDIS_CLIENT, _REDIS_CLIENT_URL
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_STATE.clear()
    if _REDIS_CLIENT is not None:
        try:
            _REDIS_CLIENT.close()
        except Exception:
            pass
    _REDIS_CLIENT = None
    _REDIS_CLIENT_URL = None


def check_shared_state_connection() -> dict[str, str | bool]:
    client = _get_redis_client()
    if client is None:
        return {
            "ok": False,
            "backend": "memory",
            "detail": "REDIS_URL is not configured; shared state uses process memory.",
        }
    try:
        client.ping()
    except Exception as exc:
        return {
            "ok": False,
            "backend": "redis",
            "detail": f"Redis ping failed: {type(exc).__name__}.",
        }
    return {
        "ok": True,
        "backend": "redis",
        "detail": "Redis accepted a PING probe.",
    }


def get_request_client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        forwarded_for = str(
            request.headers.get("X-Vercel-Forwarded-For")
            or request.headers.get("X-Forwarded-For")
            or ""
        ).strip()
        if forwarded_for:
            first_hop = forwarded_for.split(",", 1)[0].strip()
            if first_hop:
                return first_hop
    if request.client and request.client.host:
        return str(request.client.host).strip() or "unknown"
    return "unknown"


SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _resolve_scope_and_limit(request_path: str, method: str | None = None) -> tuple[str, int] | None:
    if request_path.startswith("/platform/auth/"):
        # O limite estrito existe contra tentativa de senha/token: vale para o que
        # recebe credencial ou muda sessão (login, cadastro, convite, redefinição,
        # logout). Ler a própria sessão e as capacidades acontece a cada carga do
        # painel; no mesmo orçamento, operadores atrás de um único IP (clínica)
        # eram mandados para a tela de login por 429. Leitura tem escopo próprio,
        # com o orçamento administrativo. Chamada sem método fica no estrito.
        if method is not None and method.upper() in SAFE_HTTP_METHODS:
            return "platform_auth_read", max(1, settings.rate_limit_admin_requests)
        return "platform_auth", max(1, settings.rate_limit_auth_requests)
    if request_path.startswith("/platform/integrations/"):
        # Callbacks OAuth (Google Agenda): navegação de topo com cookie de
        # sessão. Orçamento administrativo, e escopo próprio para que um
        # retorno em loop não consuma a cota das chamadas do painel.
        return "platform_integrations", max(1, settings.rate_limit_admin_requests)
    if request_path.startswith("/admin"):
        return "admin", max(1, settings.rate_limit_admin_requests)
    if request_path == "/messages/incoming":
        return "incoming_message", max(1, settings.rate_limit_incoming_requests)
    if request_path == "/webhooks/whatsapp":
        return "whatsapp_webhook", max(1, settings.rate_limit_webhook_requests)
    if request_path == "/webhooks/stripe":
        return "stripe_webhook", max(1, settings.rate_limit_webhook_requests)
    return None


def evaluate_rate_limit(request: Request) -> RateLimitDecision | None:
    if not settings.enable_rate_limiting:
        return None

    scope_config = _resolve_scope_and_limit(request.url.path, request.method)
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
    storage_key = f"{scope}:{client_id}"

    redis_client = _get_redis_client()
    if redis_client is not None:
        client_digest = hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:24]
        redis_key = f"{RATE_LIMIT_KEY_PREFIX}{scope}:{window_start}:{client_digest}"
        try:
            stored_count, ttl = redis_client.eval(
                _REDIS_FIXED_WINDOW_SCRIPT,
                1,
                redis_key,
                window_seconds + 1,
            )
            count = int(stored_count)
            retry_after = max(1, int(ttl)) if int(ttl) > 0 else max(1, window_reset_at - now)
            allowed = count <= limit
            return RateLimitDecision(
                scope=scope,
                limit=limit,
                window_seconds=window_seconds,
                client_id=client_id,
                allowed=allowed,
                remaining=max(0, limit - count),
                reset_at=window_reset_at,
                retry_after=0 if allowed else retry_after,
            )
        except Exception as exc:
            if settings.require_shared_state:
                raise RuntimeError(
                    f"Shared Redis rate-limit state is unavailable: {type(exc).__name__}."
                ) from exc

    with _RATE_LIMIT_LOCK:
        stored_window_start, stored_count = _RATE_LIMIT_STATE.get(storage_key, (window_start, 0))
        if stored_window_start != window_start:
            stored_window_start = window_start
            stored_count = 0

        if stored_count >= limit:
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

        stored_count += 1
        _RATE_LIMIT_STATE[storage_key] = (stored_window_start, stored_count)

    return RateLimitDecision(
        scope=scope,
        limit=limit,
        window_seconds=window_seconds,
        client_id=client_id,
        allowed=True,
        remaining=max(0, limit - stored_count),
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
