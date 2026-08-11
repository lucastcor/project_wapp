---
title: Storage e Repository
type: module
tags: [módulo, storage, banco]
updated: 2026-07-25
---

# Módulo: Storage e Repository

`app/storage/`.

## Arquivos

| Arquivo | Linhas | Papel |
|---------|--------|-------|
| `app/storage/db.py` | ~1 990 | Conexão SQLite/Postgres, ciclo de vida de conexão, DDL, migrações versionadas, probes. |
| `app/storage/repository.py` | ~4 800 | `RuntimeRepository` — todos os acessos a dados do runtime. |
| `app/storage/memory_store.py` | — | Legado / mínimo. |

## `db.py`

### Backends suportados

`get_database_backend()`:
- Se `DATABASE_URL` começa com `postgres://` ou `postgresql://` → `postgres`.
- Caso contrário → `sqlite`.

### Conexão

- **Postgres**: pool `psycopg_pool` com `min_size=1`, `max_size=DATABASE_POOL_SIZE`, `connect_timeout=5s`, `statement_timeout=DATABASE_STATEMENT_TIMEOUT_MS` (default 5 000 ms).
- **SQLite**: caminho resolvido via `_resolve_sqlite_path()`.

Wrappers `PostgresConnectionWrapper` e `PostgresCursorWrapper` traduzem queries de SQLite para Postgres on-the-fly:
- `?` → `%s`.
- `INSERT OR IGNORE` → `INSERT ... ON CONFLICT DO NOTHING`.
- `datetime('now', ?)` → `(CURRENT_TIMESTAMP + CAST(%s AS interval))`.
- Captura `lastrowid` adicionando `RETURNING id` automaticamente para tabelas conhecidas (`SQLITE_ID_TABLES`).

### Ciclo de vida da conexão (garantias)

Toda conexão adquirida é devolvida **exatamente uma vez**, e uma transação que
falhou sofre rollback **antes** de voltar ao pool. Três camadas cooperam:

| Camada | Papel |
|--------|-------|
| `PostgresConnectionWrapper` / `SqliteConnectionWrapper` | `close()` idempotente: marca a conexão como liberada **antes** de qualquer limpeza, faz rollback e devolve ao pool uma única vez. Uma conexão já liberada recusa `cursor()` e `commit()` com `RuntimeError`. |
| `db_session()` / `db_transaction()` | Context managers. `db_session` faz rollback e libera; `db_transaction` commita no sucesso e faz rollback na exceção. Padrão para código novo e para as tools. |
| `connection_scope()` | Guarda instalada em **todo** método público de `RuntimeRepository` por `_install_connection_guard`. Libera as conexões abertas durante a chamada aconteça o que acontecer. Como `close()` é idempotente, métodos que já fecham sozinhos não são afetados e os limites de transação continuam iguais. |

Nada depende de `__del__`, coleta de lixo ou fim do processo.

#### Falhas durante a liberação

`close()` nunca levanta exceção — se levantasse, mascararia a exceção da aplicação
que já estava propagando. Cada falha é registrada em `release_error`:

| Falha | Comportamento |
|-------|---------------|
| `rollback()` falha | Registrado; ainda tenta devolver ao pool |
| `putconn()` falha após rollback bem-sucedido | Registrado; a conexão bruta é **fechada** para não ficar presa fora do pool |
| `putconn()` falha após rollback falho | Registrado; mesma disposição |
| Tudo falha, inclusive o fechamento | Registrado; `close()` ainda retorna sem levantar |

Em nenhum caso a mesma conexão bruta é devolvida ao pool duas vezes.

O pool é criado com `open=True` explícito, removendo a dependência do default
implícito que o `psycopg_pool` deprecou. A criação continua preguiçosa: nenhuma
conexão é aberta em tempo de import.

`init_db()` roda dentro de `db_transaction()`: uma migração que falha faz rollback
e libera a conexão — o que no Postgres também solta o advisory lock de migração,
impedindo que um boot quebrado trave os demais processos.

### Filtros opcionais e compatibilidade Postgres

`_optional_equals(column, value)` monta condições parametrizadas para filtros
opcionais. Com `value=None` devolve a condição neutra `1 = 1` e nenhum parâmetro,
em vez do antigo `(? IS NULL OR col = ?)` — que o Postgres rejeitava com
`IndeterminateDatatype` por não conseguir inferir o tipo do placeholder.
Os nomes de coluna são literais do módulo, nunca entrada do chamador; os valores
são sempre ligados como parâmetros.

Cobertura em `tests/test_postgres_compatibility.py`, executada nos dois backends.

### DDL

Tabelas criadas em duas variantes (sqlite e postgres) com `CREATE TABLE IF NOT EXISTS`. Detalhe em [[40-Data/02-Schema do Banco]].

### Migrações

Lista versionada `MIGRATIONS` (37 migrações em 2026-07-26). `_run_migrations()` registra em `schema_migrations` e, no Postgres, serializa processos concorrentes com `pg_advisory_xact_lock`. Detalhe em [[40-Data/03-Migrações]].

### Probes

- `check_runtime_database_connection()` — `SELECT 1` no DB ativo.
- `check_postgres_target_connection()` — probe explícito em `POSTGRES_DATABASE_URL` (mesmo se runtime for sqlite).
- `get_runtime_database_summary()`, `get_postgres_target_summary()` — saídas para readiness.

### URL redaction

`_redact_database_url()` mascara senha em URLs Postgres antes de logar/expor.

## `repository.py`

Classe `RuntimeRepository` — instância stateless. Ponto de entrada para todas as tabelas.

### Domínios cobertos

- Companies / contatos / mensagens / contact_company_profiles.
- Pending questions, knowledge entries, message examples.
- Outbound messages, finetuning jobs, audit events.
- Calendar integrations, appointment bookings, queue entries/offers.
- Conversation flows, customer reminders.
- Inbound message receipts (idempotência).
- Billing plans, subscriptions, usage records.

### Padrões importantes

- Métodos com `company_id=None` aceitam global ou por empresa.
- `resolve_company_id(company_id, company_slug)` é o caminho oficial para tradução.
- `ensure_default_company()` cria/promove a primeira empresa ativa quando não há contexto explícito.
- `begin_inbound_message_processing(provider, provider_message_id, ...)` retorna `started`, `in_progress`, `completed`, `failed`.
- `create_audit_event(...)` é a primitiva de auditoria.

### Operacional

- `get_observability_summary()` — totais para `/admin/observability/summary`.
- `get_operational_hygiene_summary()` — para readiness.
- `cleanup_audit_events(older_than_days)` — para `POST /admin/audit-events/cleanup`.

## Limites do design atual

1. **Monolito de ~4 400 linhas** — tracking em [[80-Improvements/02-Issues Identificados]]. Refatoração natural seria por agregado (Tenant, Conversation, Calendar, Training, Billing, Audit).
2. **Sem migration tool dedicada** — `MIGRATIONS` lista sequencial. Funcional mas frágil para times maiores.
3. **Acesso direto a SQL** — sem ORM. Reduz overhead, mas exige disciplina contra SQL injection (todas as queries usam parâmetros, nunca string interpolation).
4. **Sem connection pool por tenant** — pool global compartilhado.

## Próximas leituras

- [[40-Data/02-Schema do Banco]]
- [[40-Data/03-Migrações]]
- [[40-Data/04-Multi-tenant]]
