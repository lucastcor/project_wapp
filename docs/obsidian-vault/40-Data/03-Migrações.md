---
title: Migrações
type: data
tags: [dados, migrações, schema]
updated: 2026-07-26
---

# Migrações

Definidas em `app/storage/db.py:MIGRATIONS` como lista versionada `(version, name, fn)`. Aplicadas em bootstrap (`init_db()`) e registradas em `schema_migrations`.

## Lista atual (até 2026-07-26)

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
| 024 | Create durable company context table | `_create_company_contexts` |
| 025 | Create company context indexes | `_create_company_context_indexes` |
| 026 | Import file-based company contexts | `_import_company_context_seed_files` |
| 027 | Create WhatsApp channel to company bindings | `_create_whatsapp_channel_bindings` |
| 028 | Create WhatsApp channel binding indexes | `_create_whatsapp_channel_binding_indexes` |
| 029 | Create users, memberships, invitations and sessions | `_create_identity_tables` |
| 030 | Create identity and session indexes | `_create_identity_indexes` |
| 031 | Add outbound claims, retries and worker leases | `_create_reliable_job_state` |
| 032 | Create outbound and worker lease indexes | `_create_reliable_job_indexes` |
| 033 | Add Stripe billing state and webhook ledger | `_create_billing_gateway_state` |
| 034 | Create Stripe billing and event indexes | `_create_billing_gateway_indexes` |
| 035 | Enforce tenant and cross-tenant references | `_create_tenant_integrity_guards` |
| 036 | Add reminder claims, retries and dead letters | `_create_reliable_reminder_state` |
| 037 | Create reminder dispatch claim indexes | `_create_reliable_reminder_indexes` |

## Como adicionar uma migração

1. Escrever função `_minha_migration(cursor)` em `db.py`. Use `_ensure_*_column` para adicionar colunas idempotentemente.
2. Adicionar tupla em `MIGRATIONS` com versão sequencial (`038_*`).
3. Executar `python -m app.tools.run_migrations` no release. API/worker usam
   `RUN_MIGRATIONS_ON_STARTUP=false` em topologias multi-réplica/serverless.

## Como inspecionar

```http
GET /admin/system/migrations
```

Retorna lista com `version`, `name`, `applied_at`.

## Idempotência

- Tabelas usam `CREATE TABLE IF NOT EXISTS`.
- Colunas usam helpers `_ensure_postgres_column` / `_ensure_sqlite_column`.
- Reaplicar uma migração já aplicada é safe (registro em `schema_migrations` é checado antes).
- No PostgreSQL, `pg_advisory_xact_lock` serializa API e worker concorrentes.
- No SQLite, `BEGIN IMMEDIATE` adquire o write lock **antes** de ler
  `schema_migrations`; sem isso, dois workers poderiam ler o mesmo ledger vazio e
  disputar o registro da mesma versão.
- A regressão concorrente está em `tests/test_db_migration_concurrency.py`.

### Migração de dados `026`

`026_company_context_seed_import` é a única migração que carrega dados. Ela roda
na mesma transação das demais e:

- Só insere quando a chave (`company_slug`) ainda não tem linha — um contexto mais
  novo no banco nunca é sobrescrito por um seed antigo.
- Valida cada seed antes de inserir; arquivos inválidos são pulados sem quebrar a
  migração.
- Pode ser reexecutada fora do boot com
  `python -m app.tools.bootstrap_company_contexts` (mesma semântica idempotente).

Detalhe em [[40-Data/05-Company Context]].

## Migração SQLite → PostgreSQL

`app/tools/migrate_sqlite_to_postgres.py` copia todas as tabelas de dados do
runtime. `schema_migrations` é recriada por `init_db()` no destino e é a única
exclusão explícita.

```bash
python -m app.tools.migrate_sqlite_to_postgres \
  --sqlite-path project_wapp.db \
  --postgres-url postgresql://user:senha@host:5432/db
```

Garantias verificadas por teste ponta a ponta contra um PostgreSQL real
(`tests/test_sqlite_to_postgres_migration.py`, sem mock):

- `company_contexts`, `whatsapp_channel_bindings` e `contact_company_profiles`
  são migrados.
- Os seis campos operacionais de `appointment_bookings`
  (`scheduling_profile_key`, `customer_profile`, `booking_outcome`,
  `booking_outcome_at`, `booking_outcome_notes`, `cancellation_reason`) são
  preservados. Uma versão anterior da ferramenta copiava a tabela mas descartava
  essas colunas.
- Ids, chaves estrangeiras, timestamps, campos nulos, estado ativo e dono do
  tenant são preservados.
- A ordem é segura para chaves estrangeiras (pai antes de filho).
- As sequences do PostgreSQL são ressincronizadas após a inserção de ids
  explícitos, então o próximo id gerado não colide.
- A unicidade de número WhatsApp ativo continua valendo no destino.
- Tabelas internas do SQLite (`sqlite_*`) não são copiadas.
- `audit_migration_coverage()` introspecta o schema real (`PRAGMA table_info` no
  SQLite e `information_schema.columns` no PostgreSQL) e compara **tabelas e
  colunas**. Uma tabela ou coluna de origem sem mapeamento aborta antes de tocar
  o destino.
- Em fontes legadas, colunas ausentes são omitidas do `INSERT` para que defaults
  seguros do PostgreSQL sejam aplicados. Uma coluna obrigatória sem default
  aborta com erro de compatibilidade.
- JSON persistido passa por sanitização recursiva. Credenciais de provider não
  são copiadas; integrações afetadas ficam inativas e marcadas com
  `_credentials_redacted=true`, exigindo reconfiguração.
- Truncate, inserções e ajuste de sequences ficam na mesma transação; falha em
  qualquer tabela restaura os dados anteriores do destino.
- `migrate()` fecha o pool antes e depois, então migrar vários destinos no mesmo
  processo não reaproveita um pool obsoleto.

O contrato padrão é truncar-e-carregar (`--keep-target-data` desliga), então
reexecutar é seguro e não duplica linhas.

> [!warning]
> Depois de migrar, revise o resumo `sanitization` impresso pela ferramenta.
> Integrações contabilizadas em `integrations_disabled` precisam receber as
> credenciais novamente por variável de ambiente ou cofre antes da ativação.

## Limites

- Sem **down migration** — rollback de schema exige migração forward corretiva
  ou restore ensaiado.
- Sem **Alembic** — o ledger próprio permanece suficiente, mas mudanças
  destrutivas futuras devem usar expand/contract e observabilidade de rollout.

## Próximas leituras

- [[40-Data/02-Schema do Banco]]
- [[60-Operations/06-Runbook Produção]]
