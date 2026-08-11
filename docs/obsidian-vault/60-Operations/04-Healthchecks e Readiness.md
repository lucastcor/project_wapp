---
title: Healthchecks e Readiness
type: operations
tags: [operação, health, readiness]
updated: 2026-07-25
---

# Healthchecks e Readiness

## Healthcheck simples

`GET /health` — `{status:"ok"}`. Apenas confirma que o processo está respondendo.

## Readiness

`GET /health/ready` em `app/main.py`. Checks:

1. `check_runtime_database_connection()` — `SELECT 1` no banco.
2. `get_runtime_blockers()` — checa readiness consolidado, retorna apenas itens `severity=blocker, status=fail`.
3. `get_system_readiness()` para `overall_status`, `pilot_ready`, `scale_ready`.

Retorna **200** se OK. **503** se algum blocker existe ou DB inacessível.

Body:
```json
{
  "status": "ok | degraded",
  "database_ok": true,
  "overall_status": "ready | warn | blocked",
  "pilot_ready": true,
  "scale_ready": false,
  "blocker_count": 0,
  "blockers": ["code1","code2"]
}
```

## Readiness consolidado (`/admin/system/readiness`)

`app/services/system_readiness_service.py:get_system_readiness`. Retorna:

```json
{
  "overall_status": "...",
  "pilot_ready": true,
  "scale_ready": false,
  "checks": [
    { "code": "...", "title": "...", "status": "pass|warn|fail",
      "severity": "blocker|warning|info", "detail": "...", "recommendation": "..." }
  ],
  "blocking_items": [...],
  "warning_items": [...]
}
```

Checks principais:
- `runtime_database` (blocker)
- `global_company_context` (blocker)
- `company_context_persistence` (blocker em prod) — **novo**
- `normalized_ingress_security` (blocker sempre) — **novo**
- `internal_ingress_secret` (blocker em prod) — **novo**
- `whatsapp_channel_binding` (blocker quando o provider é whatsapp ou em prod) — **novo**
- `admin_auth` + `admin_roles` + `admin_token_quality` + `platform_session_secret` (blocker em prod)
- `structured_logging`
- `postgres_target` (probe de Postgres dedicado)
- `whatsapp_provider` + `outbound_provider`
- `calendar_provider`
- `rate_limiting`, `postgres_credentials`, `openai_configuration`, `reminder_worker`, `audit_retention`, `operational_hygiene`

### Checks de segurança do Release 1A

| Código | Falha quando | Severidade |
|--------|--------------|-----------|
| `normalized_ingress_security` | `ENABLE_NORMALIZED_WHATSAPP_INGRESS=true` sem `INTERNAL_INGRESS_SECRET` real (ausente ou `change-me*`) | blocker em **qualquer** ambiente |
| `internal_ingress_secret` | `INTERNAL_INGRESS_SECRET` ausente/placeholder | blocker em produção ou `STRICT_RUNTIME_VALIDATION=true`; warning fora disso |
| `platform_session_secret` | `PLATFORM_SESSION_SECRET` ausente/placeholder | blocker em produção; warning fora disso |
| `company_context_persistence` | Contexto global resolvido de arquivo de seed em vez do banco | blocker em produção; warning fora disso |
| `whatsapp_channel_binding` | `WHATSAPP_PHONE_NUMBER_ID` ausente, sem vínculo ativo, ou vinculado a mais de uma empresa | blocker quando `OUTBOUND_PROVIDER=whatsapp` ou em produção; warning fora disso |

`company_context_persistence` recomenda `python -m app.tools.bootstrap_company_contexts`
para importar os seeds para o banco. Ver [[40-Data/05-Company Context]].

> Defaults de desenvolvimento continuam convenientes: fora de produção esses
> quatro checks são warnings (exceto `normalized_ingress_security`, que é blocker
> sempre porque só dispara quando alguém ligou explicitamente um ingress inseguro).

## Strict runtime validation

`runtime_validation_enabled()` retorna true se `STRICT_RUNTIME_VALIDATION=true` **OR** `ENVIRONMENT=production`.

Quando ativo, `app/services/runtime_guard_service.validate_runtime_environment("api")` em `lifespan` levanta `RuntimeError` se há blocker — derruba o boot. Vale para `api` e `worker`.

Como o worker roda o mesmo gate, o compose passa a ele `PLATFORM_SESSION_SECRET`,
`ENABLE_NORMALIZED_WHATSAPP_INGRESS` e `INTERNAL_INGRESS_SECRET`, além das
variáveis que já usava.

Além disso, `STRICT_RUNTIME_VALIDATION=true` fora de produção também:
- exige o segredo de ingress em `POST /messages/incoming`;
- desabilita o fallback para arquivos de seed do company context.

## Readiness no CI

O job `docker` trata `/health/ready` como autoritativo: uma resposta não-2xx,
`database_ok=false` ou qualquer blocker faz o job falhar. Antes desta release a
chamada terminava em `|| true` e o CI passava mesmo com a readiness quebrada.
O job também confirma, consultando `schema_migrations` no PostgreSQL, que as
migrações realmente rodaram contra o banco — não apenas contra o SQLite.

## Pre-flight CLI

```bash
python -m app.tools.preflight_check
python -m app.tools.preflight_check --require-scale-ready
```

Saída:
- `ok` — tudo verde.
- `warn` — há warnings, suba mas observe.
- `blocked` — não suba.

`--require-scale-ready` força que `scale_ready=true` (mais checks que `pilot_ready`).

## Onboarding por empresa

`GET /admin/system/onboarding?company_id=` ou `?company_slug=` ou nenhum (escopo global).

Retorna checklist específico:
- existência e validade do company_context (com a origem: `database` ou `seed_file`);
- exemplos suficientes para fine-tuning;
- backlog humano em aberto;
- próxima ação recomendada.

## Próximas leituras

- [[60-Operations/06-Runbook Produção]]
- [[20-Modules/05-Services]] (system_readiness)
