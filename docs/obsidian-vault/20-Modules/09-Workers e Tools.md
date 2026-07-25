---
title: Workers e Tools (CLI)
type: module
tags: [módulo, worker, cli, tools]
updated: 2026-04-28
---

# Módulo: Workers e Tools

`app/tools/`.

## Arquivos

| Arquivo | Tipo | Papel |
|---------|------|-------|
| `app/tools/preflight_check.py` | CLI | Gate operacional pré-deploy. Lê readiness e falha se há blocker. |
| `app/tools/migrate_sqlite_to_postgres.py` | CLI | Migração 1:1 do `project_wapp.db` para Postgres. |
| `app/tools/export_runtime_backup.py` | CLI | Snapshot JSON de runtime. |
| `app/tools/cleanup_operational_data.py` | CLI | Limpeza com dry-run + execute + backup automático. |
| `app/tools/reminder_worker.py` | Worker daemon | Loop recorrente para lembretes vencidos e fila. |
| `app/tools/calendar_mock.py` | Helper | Disponibilidade mock usada no MVP inicial. |
| `app/tools/registry.py` | Placeholder vazio | Reservado para futuro registry. |
| `app/tools/__init__` | Vazio sem extensão | Legado. Candidato a remoção. |

## CLI: preflight

```bash
python -m app.tools.preflight_check
python -m app.tools.preflight_check --require-scale-ready
```

Retorna:
- `ok` — pode subir.
- `warn` — pode subir mas há warnings.
- `blocked` — não suba.

Em `STRICT_RUNTIME_VALIDATION=true` ou `ENVIRONMENT=production`, blockers fazem o **runtime** falhar cedo (`runtime_guard_service.validate_runtime_environment("api")`). Detalhe em [[60-Operations/04-Healthchecks e Readiness]].

## CLI: backup

```bash
python -m app.tools.export_runtime_backup
python -m app.tools.export_runtime_backup --output backups/pre-go-live.json
```

Saída: JSON com todas as tabelas relevantes. Diretório padrão `OPERATIONS_BACKUP_DIR=backups`. No compose, mountado em `/app/backups` (volume `backups_data`).

## CLI: cleanup

```bash
# dry-run (default)
python -m app.tools.cleanup_operational_data --stale-after-days 30

# executar com backup automático
python -m app.tools.cleanup_operational_data --execute --stale-after-days 30
```

Aplica:
- Desativa empresas placeholder.
- Arquiva `pending_questions` antigas em aberto.
- Cancela `outbound_messages` antigas pendentes.
- Arquiva jobs de fine-tuning antigos com falha.

## CLI: migração SQLite → Postgres

```bash
python -m app.tools.migrate_sqlite_to_postgres \
  --sqlite-path project_wapp.db \
  --postgres-url "$POSTGRES_DATABASE_URL"
```

Migração 1:1 com mapping de tipos. Idempotente quando reexecutado contra alvo limpo (usa `INSERT OR IGNORE` / `ON CONFLICT DO NOTHING`).

## Worker: reminder_worker

```bash
python -m app.tools.reminder_worker
python -m app.tools.reminder_worker --validate-only  # usado pelo healthcheck do compose
```

Loop:
1. Lê `REMINDER_WORKER_POLL_SECONDS` (default 60s).
2. Para cada audience em `REMINDER_WORKER_AUDIENCES` (default `customer,internal`):
   - `reminder_service.dispatch_due_reminders(audience)`.
3. Processa fila: `appointment_queue_service.process_queue_*` (follow-ups e response window expirations).

Habilitado por `REMINDER_WORKER_ENABLED=true` (default false em código, **true** em `.env.example` e compose).

Container dedicado em `compose.yaml:111-161`. Healthcheck via `--validate-only`.

## Próximas leituras

- [[60-Operations/05-Worker de Lembretes]]
- [[60-Operations/06-Runbook Produção]]
