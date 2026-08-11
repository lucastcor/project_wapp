---
title: Multi-tenant
type: data
tags: [dados, multi-tenant, scopes]
updated: 2026-07-25
---

# Multi-tenant

## Modelo

**Tenancy por coluna**: `companies.id` é injetada como `company_id` em todas as tabelas operacionais. Não há schema/DB por tenant.

## Resolução de tenant

Em todo request a sequência é:

1. `company_id` query param.
2. `X-Company-Id` header.
3. `company_slug` query param.
4. `X-Company-Slug` header.
5. `repository.ensure_default_company()` (cria/promove a primeira empresa ativa).

Implementação:
- `_resolve_request_company` em `app/api/routes.py:33`.
- `_resolve_company_scope` em `app/api/admin_routes.py:117`.
- `repository.resolve_company_id(company_id, company_slug)`.

## Tabelas com `company_id`

Listadas em `SQLITE_ID_TABLES` (`app/storage/db.py:9-28`):

```
messages, pending_questions, knowledge_entries, message_examples,
outbound_messages, finetuning_jobs, companies, audit_events,
calendar_integrations, appointment_bookings, conversation_flows,
customer_reminders, appointment_queue_entries, appointment_queue_offers,
billing_plans, billing_subscriptions, billing_usage_records,
inbound_message_receipts.
```

## Contatos compartilhados

`contacts` tem `contact_id` como PK global (não tem `company_id` na chave). Atributos tenant-scoped (perfil, summary, pipeline_stage, last_classification etc.) ficam em `contact_company_profiles` (PK composta).

Por quê? Porque o mesmo número de WhatsApp pode atender empresas diferentes em um mesmo runtime. Cada empresa tem seu próprio histórico.

## Contexto por empresa

`get_company_context(company_slug=...)` avalia o slug e depois o contexto global,
nesta ordem por chave:
1. Linha em `company_contexts` para a chave.
2. Arquivo de seed correspondente — **somente** se seeds estiverem habilitados
   (fora de `ENVIRONMENT=production` e de `STRICT_RUNTIME_VALIDATION=true`).

Produção nunca cai em arquivo local mutável; sem linha no banco a leitura falha e
o readiness reporta blocker.

Ver [[40-Data/05-Company Context]].

## Isolamento

| Camada | Mecanismo |
|--------|-----------|
| API | `company_id` injetado em queries das tabelas operacionais. |
| Repository | Métodos aceitam `company_id` como filtro/escopo. |
| Conversação | `orchestrator` usa `company_slug` para resolver contexto JSON. |
| Calendar | Profile e bookings escopados por empresa. |
| Auditoria | `audit_events.company_id` registra escopo da operação. |

> ⚠️ Hoje, **vazamentos cross-tenant** são prevenidos por disciplina nas queries do `repository.py`, não por RLS (Row Level Security) nativo do Postgres. Tracking em [[80-Improvements/03-Melhorias de Segurança]].

## Testes

`tests/test_tenant_isolation.py` valida que listagens por tenant não vazam dados de outras empresas.

## Próximas leituras

- [[40-Data/02-Schema do Banco]]
- [[40-Data/05-Company Context]]
- [[80-Improvements/03-Melhorias de Segurança]]
