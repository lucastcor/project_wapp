---
title: API Admin
type: module
tags: [módulo, api, admin, rbac]
updated: 2026-04-28
---

# Módulo: API Admin

`app/api/admin_routes.py` (~1 891 linhas) — toda operação administrativa, sob prefixo `/admin` e `Depends(require_admin_access)`.

## Segurança

Cada request passa por `require_admin_access` (em `app/api/admin_security.py`):

1. Se `ENABLE_ADMIN_AUTH=false`, sessão é `disabled` e papel = `superadmin`.
2. Se houver cookie de sessão (`project_wapp_platform_session`), valida com `read_platform_session`.
3. Caso contrário, valida token via `Authorization: Bearer ...` ou `X-Admin-Token: ...`.
4. Falha → 401 + `audit_events`. Sucesso → carrega `request.state.admin_role` e `admin_actor`.

Para endpoints sensíveis, `require_admin_role("operator"|"superadmin")` (`build_require_admin_role`) bloqueia se papel insuficiente. Detalhe em [[30-API/05-RBAC e Papéis]] e [[50-Security/02-Auth Admin]].

## Categorias de endpoint

### 1. Estado / observabilidade
- `GET /admin/state` — runtime.
- `GET /admin/observability/summary` — totais por entidade.
- `GET /admin/observability/metrics` — Prometheus text.
- `GET /admin/audit-events` (filtros: `limit`, `action`, `resource_type`).
- `POST /admin/audit-events/cleanup` (superadmin).
- `GET /admin/system/migrations`.
- `GET /admin/system/profile`.
- `GET /admin/system/readiness`.
- `GET /admin/system/onboarding?company_id=&company_slug=`.

### 2. Empresas
- `POST /admin/companies` (superadmin).
- `GET /admin/companies`.
- `GET /admin/companies/{id}`.

### 3. Company context
- `GET /admin/company-context?company_slug=`.
- `PUT /admin/company-context` (superadmin) — payload `{data: {...}}`.

### 4. Pending questions
- `GET /admin/pending-questions`.
- `GET /admin/pending-questions/{id}`.
- `POST /admin/pending-questions/{id}/answer` (operator).

### 5. Knowledge entries
- `GET /admin/knowledge-entries`.
- `GET /admin/knowledge-entries/{id}`.

### 6. Message examples
- `GET /admin/message-examples`.
- `GET /admin/message-examples/{id}`.
- `POST /admin/message-examples` (operator).

### 7. Dataset & fine-tuning
- `GET /admin/training-dataset/json|jsonl|split|review`.
- `GET /admin/finetuning-dataset/train-jsonl|validation-jsonl|bundle`.
- `POST /admin/finetuning/jobs` (operator).
- `GET /admin/finetuning/jobs`.
- `GET /admin/finetuning/jobs/{job_id}`.
- `GET /admin/finetuning/jobs-db`.
- `GET /admin/finetuning/jobs-db/{job_id}`.
- `POST /admin/training-import` (operator).

### 8. Outbound
- `GET /admin/outbound-messages`.
- `GET /admin/outbound-messages/{id}`.
- `POST /admin/outbound-messages/{id}/mark-sent` (operator).
- `POST /admin/outbound-messages/{id}/dispatch?channel=` (operator).
- `POST /admin/outbound-messages/dispatch-pending?channel=` (operator).

### 9. Contacts
- `GET /admin/contacts?q=&company_id=`.
- `GET /admin/contacts/{contact_id}`.
- `PATCH /admin/contacts/{contact_id}` (operator).

### 10. Calendar
- `GET /admin/calendar/providers`.
- `GET /admin/calendar/profile?company_id=`.
- `GET /admin/calendar/integrations?company_id=`.
- `POST /admin/calendar/integrations` (superadmin).
- `POST /admin/calendar/availability/query` (operator).
- `GET /admin/calendar/bookings?company_id=`.
- `GET /admin/calendar/timeline?start_date=&days=`.
- `POST /admin/calendar/bookings` (operator).
- `POST /admin/calendar/bookings/{id}/reschedule` (operator).
- `POST /admin/calendar/bookings/{id}/cancel` (operator).
- `GET /admin/calendar/queue?company_id=`.
- `POST /admin/calendar/queue` (operator).
- `GET /admin/calendar/queue-offers?company_id=`.
- `POST /admin/calendar/queue-offers/{id}/respond` (operator).
- `GET /admin/reminders?company_id=`.
- `POST /admin/reminders` (operator).
- `POST /admin/reminders/dispatch-due?audience=` (operator).

### 11. Analytics
- `GET /admin/analytics/dashboard?company_id=`.
- `POST /admin/analytics/bookings/{id}/outcome`.
- `POST /admin/analytics/bookings/{id}/cancellation-reason`.
- `GET /admin/analytics/trend?company_id=&...`.
- `GET /admin/analytics/reminder-effectiveness?company_id=`.

### 12. Onboarding
- `GET /admin/onboarding/presets`.
- `POST /admin/onboarding/setup` (superadmin).
- `GET /admin/onboarding/setup-status/{company_id}`.

### 13. Billing
- `GET /admin/billing/plans`.
- `POST /admin/billing/trial`.
- `POST /admin/billing/activate`.
- `POST /admin/billing/cancel`.
- `POST /admin/billing/reactivate`.
- `POST /admin/billing/change-plan`.
- `GET /admin/billing/usage?company_id=`.
- `POST /admin/billing/usage/record`.
- `GET /admin/billing/check-limit?company_id=&metric=`.

## Auditoria

Endpoints que mudam estado chamam `_record_audit_event(...)` → grava em `audit_events` com:
- `actor_id` (header `X-Admin-Actor`)
- `request_id` (`X-Request-ID`)
- `action`, `resource_type`, `resource_id`, `status`, `metadata`

Detalhe em [[50-Security/07-Auditoria]].

## Pontos de atenção para mexer aqui

- Arquivo gigante (~1 891 linhas) → mudanças laterais são frequentes; sempre rodar suíte completa.
- Usa `repository` como singleton de módulo — dependência implícita.
- Schemas Pydantic em `app/api/schemas.py` — qualquer payload novo precisa entrar lá.

## Próximas leituras

- [[30-API/02-Endpoints Admin]]
- [[30-API/05-RBAC e Papéis]]
- [[50-Security/02-Auth Admin]]
