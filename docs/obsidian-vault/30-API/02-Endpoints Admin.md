---
title: Endpoints Admin
type: api
tags: [api, admin, rbac]
updated: 2026-04-28
---

# Endpoints Admin

Prefixo `/admin`. Auth obrigatório quando `ENABLE_ADMIN_AUTH=true`. Implementação em `app/api/admin_routes.py`. Detalhe RBAC em [[30-API/05-RBAC e Papéis]].

## Padrão de auth

```http
Authorization: Bearer <token>
X-Admin-Token: <token>          # alternativa
X-Admin-Actor: <operador>       # opcional, vai para audit_events
```

Ou cookie `project_wapp_platform_session` (válido para sessão web do `/platform`). Detalhe em [[30-API/04-Auth e Sessão Web]].

## Convenções

- Erros 4xx incluem `request_id` no body de exceção 500.
- Endpoints idempotentes retornam o mesmo recurso em GET; POSTs criam efeito colateral persistido.
- `company_id` e `company_slug` aceitos na maioria dos GETs como filtro.

## Tabela completa

### Estado e observabilidade

| Método | Path | Papel mínimo | Resumo |
|--------|------|--------------|--------|
| GET | `/admin/state` | viewer | Snapshot básico do runtime. |
| GET | `/admin/observability/summary` | viewer | Totais por entidade + status. |
| GET | `/admin/observability/metrics` | viewer | Prometheus text. |
| GET | `/admin/audit-events?limit=&action=&resource_type=` | viewer | Trilha de auditoria. |
| POST | `/admin/audit-events/cleanup?older_than_days=` | superadmin | Remove eventos antigos. |
| GET | `/admin/system/migrations` | viewer | Migrações aplicadas. |
| GET | `/admin/system/profile` | viewer | Perfil do deploy. |
| GET | `/admin/system/readiness` | viewer | Checks consolidados. |
| GET | `/admin/system/onboarding?company_id=&company_slug=` | viewer | Checklist por empresa ou global. |

### Empresas

| GET | `/admin/companies` | viewer |
| GET | `/admin/companies/{id}` | viewer |
| POST | `/admin/companies` | superadmin |

Body POST:
```json
{ "slug":"empresa-x", "name":"Empresa X", "business_type":"general", "active":true }
```

### Company context

| GET | `/admin/company-context?company_slug=` | viewer |
| PUT | `/admin/company-context` | superadmin |

Body PUT: `{"data": {...JSON completo do contexto...}}`.

### Pending questions

| GET | `/admin/pending-questions?company_id=` | viewer |
| GET | `/admin/pending-questions/{id}` | viewer |
| POST | `/admin/pending-questions/{id}/answer` | operator |

Body answer:
```json
{
  "owner_answer": "...",
  "resolution_type": "final_answer | needs_customer_clarification | case_specific_exception | global_rule",
  "customer_followup_question": null,
  "applies_globally": false,
  "knowledge_action": "none | store_as_knowledge | promote_to_company_context"
}
```

### Knowledge entries / Message examples

| GET | `/admin/knowledge-entries` | viewer |
| GET | `/admin/knowledge-entries/{id}` | viewer |
| GET | `/admin/message-examples` | viewer |
| GET | `/admin/message-examples/{id}` | viewer |
| POST | `/admin/message-examples` | operator |

### Dataset & fine-tuning

| GET | `/admin/training-dataset/json` | viewer |
| GET | `/admin/training-dataset/jsonl` | viewer |
| GET | `/admin/training-dataset/split` | viewer |
| GET | `/admin/training-dataset/review` | viewer |
| GET | `/admin/finetuning-dataset/train-jsonl` | viewer |
| GET | `/admin/finetuning-dataset/validation-jsonl` | viewer |
| GET | `/admin/finetuning-dataset/bundle` | viewer |
| POST | `/admin/finetuning/jobs` | operator |
| GET | `/admin/finetuning/jobs` | viewer |
| GET | `/admin/finetuning/jobs/{job_id}` | viewer |
| GET | `/admin/finetuning/jobs-db` | viewer |
| GET | `/admin/finetuning/jobs-db/{job_id}` | viewer |
| POST | `/admin/training-import` | operator |

### Outbound

| GET | `/admin/outbound-messages?company_id=` | viewer |
| GET | `/admin/outbound-messages/{id}` | viewer |
| POST | `/admin/outbound-messages/{id}/mark-sent` | operator |
| POST | `/admin/outbound-messages/{id}/dispatch?channel=` | operator |
| POST | `/admin/outbound-messages/dispatch-pending?channel=` | operator |

`channel`: `whatsapp | instagram | generic`.

### Contacts

| GET | `/admin/messages?company_id=` | viewer |
| GET | `/admin/contacts?q=&company_id=` | viewer |
| GET | `/admin/contacts/{contact_id}` | viewer |
| PATCH | `/admin/contacts/{contact_id}` | operator |

Body PATCH:
```json
{ "contact_name":"Maria", "customer_profile":"returning_customer", "notes":"..." }
```

### Calendar

Detalhe em [[10-Architecture/06-Fluxo Agenda]].

| GET | `/admin/calendar/providers` | viewer |
| GET | `/admin/calendar/profile?company_id=` | viewer |
| GET | `/admin/calendar/timeline?start_date=&days=` | viewer |
| GET | `/admin/calendar/integrations?company_id=` | viewer |
| POST | `/admin/calendar/integrations` | superadmin |
| POST | `/admin/calendar/availability/query` | operator |
| GET | `/admin/calendar/bookings?company_id=` | viewer |
| POST | `/admin/calendar/bookings` | operator |
| POST | `/admin/calendar/bookings/{id}/reschedule` | operator |
| POST | `/admin/calendar/bookings/{id}/cancel` | operator |
| GET | `/admin/calendar/queue?company_id=` | viewer |
| POST | `/admin/calendar/queue` | operator |
| GET | `/admin/calendar/queue-offers?company_id=` | viewer |
| POST | `/admin/calendar/queue-offers/{id}/respond` | operator |

### Reminders

| GET | `/admin/reminders?company_id=` | viewer |
| POST | `/admin/reminders` | operator |
| POST | `/admin/reminders/dispatch-due?audience=customer\|internal` | operator |

### Analytics

| GET | `/admin/analytics/dashboard?company_id=` | viewer |
| POST | `/admin/analytics/bookings/{id}/outcome` | operator |
| POST | `/admin/analytics/bookings/{id}/cancellation-reason` | operator |
| GET | `/admin/analytics/trend?company_id=&...` | viewer |
| GET | `/admin/analytics/reminder-effectiveness?company_id=` | viewer |

### Onboarding

| GET | `/admin/onboarding/presets` | viewer |
| POST | `/admin/onboarding/setup` | superadmin |
| GET | `/admin/onboarding/setup-status/{company_id}` | viewer |

### Billing

| GET | `/admin/billing/plans` | viewer |
| POST | `/admin/billing/trial` | operator |
| POST | `/admin/billing/activate` | operator |
| POST | `/admin/billing/cancel` | operator |
| POST | `/admin/billing/reactivate` | operator |
| POST | `/admin/billing/change-plan` | operator |
| GET | `/admin/billing/usage?company_id=` | viewer |
| POST | `/admin/billing/usage/record` | operator |
| GET | `/admin/billing/check-limit?company_id=&metric=` | viewer |

## Códigos de erro padrão

- 400 — regra de negócio / configuração / provider inválido.
- 401 — credencial admin inválida.
- 403 — papel insuficiente, ou HMAC webhook inválido.
- 404 — não encontrado.
- 422 — payload inválido (Pydantic).
- 429 — rate limit.
- 500 — erro inesperado (com `request_id`).
- 503 — config crítica ausente (ex.: `WHATSAPP_APP_SECRET`).

## Próximas leituras

- [[30-API/03-Webhook WhatsApp]]
- [[30-API/05-RBAC e Papéis]]
- [[50-Security/02-Auth Admin]]
