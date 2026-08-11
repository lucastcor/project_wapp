---
title: Endpoints Admin
type: api
tags: [api, admin, rbac]
updated: 2026-07-25
---

# Endpoints Admin

Prefixo `/admin`. Auth obrigatório quando `ENABLE_ADMIN_AUTH=true`.
Implementação em `app/api/admin_routes.py`; a política de papel por rota vive em
`app/api/admin_permissions.py` e é verificada por
`tests/test_admin_rbac_matrix.py`. Detalhe em [[30-API/05-RBAC e Papéis]].

> Autenticar **não** autoriza mutação. Toda rota `POST`/`PUT`/`PATCH`/`DELETE`
> carrega explicitamente `Depends(require_admin_role(...))`; o papel `viewer`
> recebe `403` em qualquer uma delas.

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
| GET | `/admin/company-context/record?company_slug=` | viewer |
| PUT | `/admin/company-context` | superadmin |

Body PUT: `{"data": {...contexto completo...}, "expected_revision": 3}`.
`expected_revision` é opcional; quando enviado e desatualizado, a resposta é `409`
e nada é gravado. `GET .../record` devolve `revision`, `schema_version`, `source`,
`company_id`, `updated_at`, `updated_by` e `data` — use para obter a revisão atual.

Persistência e concorrência em [[40-Data/05-Company Context]].

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
| POST | `/admin/billing/trial` | superadmin |
| POST | `/admin/billing/activate` | superadmin |
| POST | `/admin/billing/cancel` | superadmin |
| POST | `/admin/billing/reactivate` | superadmin |
| POST | `/admin/billing/change-plan` | superadmin |
| GET | `/admin/billing/usage?company_id=` | viewer |
| POST | `/admin/billing/usage/record` | superadmin |
| GET | `/admin/billing/check-limit?company_id=&metric=` | viewer |

Toda administração de billing exige `superadmin` até existir billing por dono de
tenant. `POST /admin/billing/usage/record` grava `audit_events`
(`action=record_usage`, `resource_type=billing_usage`), com `status=skipped`
quando não há assinatura ativa.

### Vínculos de canal WhatsApp

| GET | `/admin/whatsapp/bindings?company_id=&active_only=` | viewer |
| PUT | `/admin/whatsapp/bindings` | superadmin |
| POST | `/admin/whatsapp/bindings/{binding_id}/state` | superadmin |

Body `PUT`: `{"company_id": 1, "phone_number_id": "<id do Meta>", "display_phone_number": "5511...", "active": true}`.
Body `POST .../state`: `{"active": false}`.

- `409` quando o `phone_number_id` já está ativamente vinculado a **outra** empresa —
  tanto no `PUT` quanto ao reativar um vínculo histórico via `POST .../state`.
  A decisão é do índice único do banco, então requisições concorrentes também
  resultam em exatamente um dono ativo.
- `404` quando a empresa (no `PUT`) ou o vínculo (no `POST .../state`) não existe.
- Respostas e auditoria carregam apenas identificadores — nunca token de acesso
  ou app secret.
- Criação, atualização, ativação e desativação geram `audit_events`
  (`resource_type=whatsapp_channel_binding`).

Detalhe do uso em [[30-API/03-Webhook WhatsApp]].

### Simulador

| POST | `/admin/simulator/messages?company_id=&company_slug=` | operator |

Body: `{"contact_id":"5511999999999","contact_name":"Cliente","message":"oi"}`.

Processa a mensagem pelo runtime real. Substitui a chamada anônima que o Bot Studio
fazia a `POST /messages/incoming`. A empresa é resolvida no servidor e precisa
existir (`400` se `company_id`/`company_slug` não resolver). Registra
`audit_events` com `action=simulate_message` e `contact_id` mascarado.

## Códigos de erro padrão

- 400 — regra de negócio / configuração / provider inválido.
- 401 — credencial admin inválida.
- 403 — papel insuficiente, ou HMAC webhook inválido.
- 404 — não encontrado.
- 409 — conflito de revisão do company context.
- 422 — payload inválido (Pydantic).
- 429 — rate limit.
- 500 — erro inesperado (com `request_id`).
- 503 — config crítica ausente (ex.: `WHATSAPP_APP_SECRET`).

## Próximas leituras

- [[30-API/03-Webhook WhatsApp]]
- [[30-API/05-RBAC e Papéis]]
- [[50-Security/02-Auth Admin]]
