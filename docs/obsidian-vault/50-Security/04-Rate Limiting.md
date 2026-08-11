---
title: Rate Limiting
type: security
tags: [segurança, rate-limit]
updated: 2026-07-26
---

# Rate Limiting

Implementado em `app/services/rate_limit_service.py`. Aplicado pelo middleware em `app/main.py`.

## Modelo

- **Redis atômico** quando `REDIS_URL` está configurado.
- Fallback **in-memory por processo** apenas quando
  `REQUIRE_SHARED_STATE=false`.
- Contagem por **(scope, client_ip)** em janela fixa (`RATE_LIMIT_WINDOW_SECONDS`, default 60s).
- Lock thread-safe (`threading.Lock`) para concorrência.

## Escopos

| Path | Scope | Default (req/min) | Env |
|------|-------|-------------------|-----|
| `/platform/auth/*` | `platform_auth` | 10 | `RATE_LIMIT_AUTH_REQUESTS` |
| `/admin/*` | `admin` | 180 | `RATE_LIMIT_ADMIN_REQUESTS` |
| `/messages/incoming` | `incoming_message` | 120 | `RATE_LIMIT_INCOMING_REQUESTS` |
| `/webhooks/whatsapp` | `whatsapp_webhook` | 300 | `RATE_LIMIT_WEBHOOK_REQUESTS` |
| `/webhooks/stripe` | `stripe_webhook` | 300 | `RATE_LIMIT_WEBHOOK_REQUESTS` |
| Outros | — | sem limite | — |

## Identificação do cliente

`get_request_client_ip(request)`:
1. Primeiro hop de `X-Vercel-Forwarded-For`/`X-Forwarded-For` somente quando
   `TRUST_PROXY_HEADERS=true`.
2. `request.client.host`.
3. `"unknown"` como fallback.

Em Docker direto o default é não confiar em headers; no Vercel o default é
confiar no proxy da plataforma.

## Bypass localhost

`RATE_LIMIT_EXEMPT_LOCALHOST=true` ignora `127.0.0.1`, `::1`, `localhost`, `testclient`. Útil em dev.

## Headers

Sempre que rate limiting está ativo:
- `X-RateLimit-Scope`
- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset`

Quando bloqueado (429):
- `Retry-After`

## Comportamento sob limite

```python
return RateLimitDecision(allowed=False, retry_after=N)
```

Middleware retorna `JSONResponse(status_code=429, ...)` direto, sem chegar nas rotas.

## Redis e falha

O contador usa um script Lua (`INCR` + `EXPIRE`) para que duas réplicas
compartilhem exatamente a mesma janela. O identificador do cliente é
hashado antes de entrar na chave Redis.

Se Redis falhar:

- `REQUIRE_SHARED_STATE=true`: a requisição falha, sem contagem local divergente.
- `REQUIRE_SHARED_STATE=false`: usa fallback in-memory e readiness avisa que a
  instância não está `scale_ready`.

## Limitações

| Limite | Comentário |
|--------|-----------|
| Janela fixa | Permite burst no início da janela. |
| Sem custom keys (ex.: por token) | Apenas por IP. |

## Recomendações de evolução

1. Avaliar sliding window/token bucket se bursts de borda virarem problema real.
2. Rate limit por tenant/contact para webhooks, além do limite de transporte.
3. Manter quotas comerciais separadas do rate limit técnico.

## Habilitar em produção

```env
ENABLE_RATE_LIMITING=true
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_AUTH_REQUESTS=10
RATE_LIMIT_ADMIN_REQUESTS=180
RATE_LIMIT_INCOMING_REQUESTS=120
RATE_LIMIT_WEBHOOK_REQUESTS=300
RATE_LIMIT_EXEMPT_LOCALHOST=false
REDIS_URL=redis://redis:6379/0
REQUIRE_SHARED_STATE=true
TRUST_PROXY_HEADERS=false
```

`compose.yaml` já liga por padrão (`ENABLE_RATE_LIMITING=true`).

## Próximas leituras

- [[50-Security/01-Modelo de Ameaças]]
- [[80-Improvements/04-Melhorias de Escalabilidade]]
