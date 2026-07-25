---
title: Backup e Limpeza
type: data
tags: [dados, backup, cleanup, runbook]
updated: 2026-04-28
---

# Backup e Limpeza Operacional

## Backup lógico

Tool: `app/tools/export_runtime_backup.py`.

```bash
# default → backups/<timestamp>.json
python -m app.tools.export_runtime_backup

# saída customizada
python -m app.tools.export_runtime_backup --output backups/pre-go-live.json
```

Funciona com SQLite e Postgres. Diretório padrão: `OPERATIONS_BACKUP_DIR=backups`. No Docker compose, montado em `/app/backups` (volume `backups_data`).

## Cleanup operacional

Tool: `app/tools/cleanup_operational_data.py`.

```bash
# dry-run (default)
python -m app.tools.cleanup_operational_data --stale-after-days 30

# executar com backup automático antes
python -m app.tools.cleanup_operational_data --execute --stale-after-days 30
```

Aplica:
- Desativa `companies` placeholder ainda ativas.
- Arquiva `pending_questions` antigas em aberto.
- Cancela `outbound_messages` antigas em `pending`.
- Arquiva jobs de fine-tuning antigos com falha.

## Retenção de auditoria

`AUDIT_RETENTION_DAYS=90` (default). `POST /admin/audit-events/cleanup?older_than_days=90` (superadmin) chama `repository.cleanup_audit_events`. Programar via cron externo ou manual.

## Recomendado

Antes do go-live, sempre:

1. Rodar dry-run.
2. Revisar candidatos.
3. Backup.
4. Executar.

Detalhe em [[60-Operations/06-Runbook Produção]].

## PII e backups

Backups contêm `contact_id`, mensagens e contextos brutos. Tratar como dado sensível:
- Não commitar.
- Restringir acesso ao volume `backups_data`.
- Apagar snapshots temporários após janela de suporte/incidente.

Política em [[50-Security/06-PII e Dataset]].

## Próximas leituras

- [[60-Operations/06-Runbook Produção]]
- [[50-Security/06-PII e Dataset]]
