---
title: Migrações
type: data
tags: [dados, migrações, schema]
updated: 2026-04-28
---

# Migrações

Definidas em `app/storage/db.py:MIGRATIONS` como lista versionada `(version, name, fn)`. Aplicadas em bootstrap (`init_db()`) e registradas em `schema_migrations`.

## Lista atual (até 2026-04-28)

| Versão | Nome | Função |
|--------|------|--------|
| 001 | Create runtime tables and columns | `_create_runtime_tables` |
| 002 | Create audit events table | `_create_audit_tables` |
| 003 | Create operational indexes | `_create_indexes` |
| 004 | Create scale-oriented indexes | `_create_scale_indexes` |
| 005 | Create calendar integrations and bookings | `_create_calendar_tables` |
| 006 | Create calendar indexes | `_create_calendar_indexes` |
| 007 | Create conversation flows and reminders | `_create_workflow_tables` |
| 008 | Create workflow indexes | `_create_workflow_indexes` |
| 009 | Add audience and booking columns to reminders | reusa `_create_workflow_tables` |
| 010 | Add reminder booking index | reusa `_create_workflow_indexes` |
| 011 | Create appointment queue tables | `_create_queue_tables` |
| 012 | Create appointment queue indexes | `_create_queue_indexes` |
| 013 | Add booking outcome tracking columns | `_add_booking_outcome_columns` |
| 014 | Create analytics-oriented indexes | `_create_analytics_indexes` |
| 015 | Create billing plans, subscriptions and usage tables | `_create_billing_tables` |
| 016 | Create billing indexes | `_create_billing_indexes` |
| 017 | Ensure queue entry columns exist | `_repair_queue_columns` |
| 018 | Create inbound message receipts table | `_create_inbound_message_receipts` |
| 019 | Create inbound message receipt indexes | `_create_inbound_message_receipt_indexes` |
| 020 | Add admin and HTTP context columns to audit events | `_add_audit_event_context_columns` |
| 021 | Create audit event context indexes | `_create_audit_event_context_indexes` |
| 022 | Create tenant-scoped contact profiles | `_create_contact_company_profiles` |
| 023 | Create contact profile indexes | `_create_contact_company_profile_indexes` |

## Como adicionar uma migração

1. Escrever função `_minha_migration(cursor)` em `db.py`. Use `_ensure_*_column` para adicionar colunas idempotentemente.
2. Adicionar tupla em `MIGRATIONS` com versão sequencial (`024_*`).
3. Subir o app — `_run_migrations()` aplica e registra automaticamente em `schema_migrations`.

## Como inspecionar

```http
GET /admin/system/migrations
```

Retorna lista com `version`, `name`, `applied_at`.

## Idempotência

- Tabelas usam `CREATE TABLE IF NOT EXISTS`.
- Colunas usam helpers `_ensure_postgres_column` / `_ensure_sqlite_column`.
- Reaplicar uma migração já aplicada é safe (registro em `schema_migrations` é checado antes).

## Limites

- Sem **down migration** — não há rollback automático.
- Sem **lint/dry-run** — execução acoplada ao boot.
- Sem **Alembic** — funcional para fase atual; evoluir é tracking em [[80-Improvements/04-Melhorias de Escalabilidade]].

## Próximas leituras

- [[40-Data/02-Schema do Banco]]
- [[60-Operations/06-Runbook Produção]]
