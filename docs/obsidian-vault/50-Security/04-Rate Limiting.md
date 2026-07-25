---
title: Rate Limiting
type: security
tags: [segurança, rate-limit]
updated: 2026-04-28
---

# Rate Limiting

Implementado em `app/services/rate_limit_service.py`. Aplicado pelo middleware em `app/main.py`.

## Modelo

- **In-memory por processo** — não compartilhado entre réplicas.
- Contagem por **(scope, client_ip)** em janela fixa (`RATE_LIMIT_WINDOW_SECONDS`, default 60s).
- Lock thread-safe (`threading.Lock`) para concorrência.

## Escopos

| Path | Scope | Default (req/min) | Env |
|------|-------|-------------------|-----|
| `/platform/auth/session` | `platform_auth` | 10 | `RATE_LIMIT_AUTH_REQUESTS` |
| `/admin/*` | `admin` | 180 | `RATE_LIMIT_ADMIN_REQUESTS` |
| `/messages/incoming` | `incoming_message` | 120 | `RATE_LIMIT_INCOMING_REQUESTS` |
| `/webhooks/whatsapp` | `whatsapp_webhook` | 300 | `RATE_LIMIT_WEBHOOK_REQUESTS` |
| Outros | — | sem limite | — |

## Identificação do cliente

`get_request_client_ip(request)`:
1. Primeiro hop de `X-Forwarded-For`, se presente.
2. `request.client.host`.
3. `"unknown"` como fallback.

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

## Limitações

| Limite | Comentário |
|--------|-----------|
| Por processo | Múltiplas réplicas têm contagens independentes. |
| Janela fixa | Permite burst no início da janela. |
| Sem persistência | Restart zera contagem. |
| Sem custom keys (ex.: por token) | Apenas por IP. |

## Recomendações de evolução

1. **Migrar para Redis** com sliding window log ou token bucket — ver [[80-Improvements/04-Melhorias de Escalabilidade]].
2. **Rate limit por contact_id** para webhook (mais útil que por IP do Meta).
3. **Differentiated quotas** por tenant (já existe `billing_service.check_billing_limit` para limites de uso, mas não está acoplado ao rate-limit em si).

## Habilitar em produção

```env
ENABLE_RATE_LIMITING=true
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_AUTH_REQUESTS=10
RATE_LIMIT_ADMIN_REQUESTS=180
RATE_LIMIT_INCOMING_REQUESTS=120
RATE_LIMIT_WEBHOOK_REQUESTS=300
RATE_LIMIT_EXEMPT_LOCALHOST=false
```

`compose.yaml` já liga por padrão (`ENABLE_RATE_LIMITING=true`).

## Próximas leituras

- [[50-Security/01-Modelo de Ameaças]]
- [[80-Improvements/04-Melhorias de Escalabilidade]]
