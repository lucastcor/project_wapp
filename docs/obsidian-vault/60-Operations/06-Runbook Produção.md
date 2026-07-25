---
title: Runbook Produção
type: operations
tags: [operação, runbook, produção]
updated: 2026-04-28
---

# Runbook de Produção

> Mínimo operacional para colocar e manter o Project WAPP em produção. Fonte original: `docs/production_runbook.md` + auditoria atual.

## Go-live

1. **Docker**: ligar daemon ou garantir infra alvo acessível.
2. **`.env.production`**: preencher segredos reais. Conferir cada variável em [[90-Reference/01-Variáveis de Ambiente]].
3. **Coerência DB**:
   - Host: `DATABASE_URL`, `POSTGRES_DATABASE_URL` → `127.0.0.1:5432` ou Postgres gerenciado.
   - Compose: `COMPOSE_DATABASE_URL`, `COMPOSE_POSTGRES_DATABASE_URL` → `@postgres:5432`.
4. **Hardening**:
   - `ENABLE_ADMIN_AUTH=true`.
   - `ENABLE_RATE_LIMITING=true`.
   - `REMINDER_WORKER_ENABLED=true`.
   - `STRICT_RUNTIME_VALIDATION=true`.
   - `ENVIRONMENT=production`.
5. **Canal**:
   - `OUTBOUND_PROVIDER=whatsapp`.
   - `WHATSAPP_API_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`.
   - HTTPS público para o webhook.
6. **Subir**:
   ```bash
   docker compose --env-file .env.production up --build -d
   ```
7. **Pre-flight**:
   ```bash
   docker compose --env-file .env.production exec api \
     python -m app.tools.preflight_check --require-scale-ready
   ```
8. **Validar**: `/health/ready`, login admin, smoke test do webhook real.

Detalhe em [[60-Operations/01-Deploy Docker Compose]].

## Backup lógico

```bash
python -m app.tools.export_runtime_backup
python -m app.tools.export_runtime_backup --output backups/pre-go-live.json
```

Saída em `OPERATIONS_BACKUP_DIR=backups` (default), volume `backups_data` no compose.

## Limpeza segura

Antes de qualquer go-live ou pós-piloto:

```bash
# 1. dry-run
python -m app.tools.cleanup_operational_data --stale-after-days 30
# 2. backup
python -m app.tools.export_runtime_backup
# 3. executar
python -m app.tools.cleanup_operational_data --execute --stale-after-days 30
```

Detalhe em [[40-Data/06-Backup e Limpeza]].

## Migração SQLite → Postgres

```bash
set -a; source .env.production; set +a
python -m app.tools.migrate_sqlite_to_postgres \
  --sqlite-path project_wapp.db \
  --postgres-url "$POSTGRES_DATABASE_URL"
```

## Política mínima de PII

- Não subir backups para Git.
- Restringir acesso ao volume `backups_data`.
- Tratar exports/snapshots como dados sensíveis.
- Apagar snapshots temporários após janela.

Política completa em [[50-Security/06-PII e Dataset]].

## Resposta a incidente

1. Confirmar natureza: credencial, runtime, webhook ou backlog.
2. `python -m app.tools.preflight_check` e salvar saída.
3. Backup imediato.
4. Inspecionar:
   - `audit_events` filtrado por `status=failed` recentes.
   - `outbound_messages.status=failed`.
   - `pending_questions.status=pending` antigas.
   - `inbound_message_receipts.status=failed`.
5. Se dano operacional: congelar mudanças. Restaurar fluxo primário:
   - login admin;
   - recepção de mensagem;
   - dispatch outbound;
   - worker de lembretes;
   - integração de agenda.
6. Pós-incidente: causa raiz + ação permanente + nota nesta vault.

## Operação cotidiana

| Tarefa | Frequência | Comando |
|--------|-----------|---------|
| Cleanup auditoria | mensal | `POST /admin/audit-events/cleanup?older_than_days=90` |
| Backup runtime | semanal | `python -m app.tools.export_runtime_backup` |
| Pre-flight | antes de cada deploy | `python -m app.tools.preflight_check --require-scale-ready` |
| Cleanup operacional | trimestral | `python -m app.tools.cleanup_operational_data --execute` |
| Verificar `pending_questions` | diária | `GET /admin/pending-questions` |
| Verificar `outbound_messages.status=failed` | diária | `GET /admin/outbound-messages?status=failed` |

## Próximas leituras

- [[60-Operations/01-Deploy Docker Compose]]
- [[60-Operations/04-Healthchecks e Readiness]]
- [[50-Security/08-Hardening Operacional]]
