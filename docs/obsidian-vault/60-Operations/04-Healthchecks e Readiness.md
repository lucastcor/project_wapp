---
title: Healthchecks e Readiness
type: operations
tags: [operação, health, readiness]
updated: 2026-04-28
---

# Healthchecks e Readiness

## Healthcheck simples

`GET /health` — `{status:"ok"}`. Apenas confirma que o processo está respondendo.

## Readiness

`GET /health/ready` em `app/main.py:184-201`. Checks:

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
- `admin_auth` + `admin_roles` + `admin_token_quality` + `platform_session_secret` (blocker em prod)
- `structured_logging`
- `postgres_target` (probe de Postgres dedicado)
- `whatsapp_provider` + `outbound_provider`
- `calendar_provider` + `calendar_credentials`
- `finetuning_pipeline_readiness`
- Etc.

## Strict runtime validation

`runtime_validation_enabled()` retorna true se `STRICT_RUNTIME_VALIDATION=true` **OR** `ENVIRONMENT=production`.

Quando ativo, `app/services/runtime_guard_service.validate_runtime_environment("api")` em `lifespan` levanta `RuntimeError` se há blocker — derruba o boot. Vale para `api` e `worker`.

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
- existência de company_context;
- exemplos suficientes para fine-tuning;
- agenda configurada;
- billing ativo;
- contatos cadastrados;
- próxima ação recomendada.

## Próximas leituras

- [[60-Operations/06-Runbook Produção]]
- [[20-Modules/05-Services]] (system_readiness)
