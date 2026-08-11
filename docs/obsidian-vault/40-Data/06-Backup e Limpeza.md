---
title: Backup e Limpeza
type: data
tags: [dados, backup, cleanup, runbook]
updated: 2026-07-26
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

### Cobertura

`BACKUP_TABLES` cobre todas as tabelas de runtime. O formato v2 carrega
`schema_manifest` com as colunas reais de cada tabela e `table_counts`; a
restauração valida ambos antes de executar qualquer `DELETE`.

Inclui a configuração de tenant que antes faltava:

| Tabela | Por que importa |
|--------|-----------------|
| `company_contexts` | Configuração ativa por empresa (payload, `revision`, `schema_version`) |
| `whatsapp_channel_bindings` | Vínculo número↔empresa, com estado ativo/inativo |
| `contact_company_profiles` | Perfil do contato por tenant (o mesmo contato em duas empresas é independente) |

### Ordem e determinismo

As tabelas são exportadas pai-antes-de-filho, e a restauração apaga na ordem
inversa. Toda tabela tem `ORDER BY` explícito. A leitura ocorre em uma única
snapshot (`REPEATABLE READ READ ONLY` no PostgreSQL e transação explícita no
SQLite), evitando misturar estados de instantes diferentes.

### Segredos

Segredos de ambiente não são exportados. Como integrações legadas podem ter
credenciais persistidas em colunas JSON, o exporter também sanitiza
recursivamente chaves como `client_secret`, `refresh_token`, `access_token`,
`api_key`, `password` e `private_key`.

Quando uma credencial é removida de `calendar_integrations.config_json`:

- o restante da configuração é preservado;
- `_credentials_redacted=true` é gravado no JSON exportado;
- a integração é exportada como inativa;
- o resumo `sanitization` informa quantas linhas/campos foram afetados e os ids
  das integrações que exigem reconfiguração, sem revelar valores.

O restore rejeita arquivos que ainda contenham configuração sensível em texto
claro. Isso não prova que texto livre de usuário nunca contenha um segredo; a
garantia é sobre campos estruturados de credencial.

### Restauração

```bash
# exportar
python -m app.tools.export_runtime_backup --output backups/pre-go-live.json

# restaurar no banco apontado por DATABASE_URL
python -m app.tools.export_runtime_backup --restore backups/pre-go-live.json

# restaurar sem apagar as linhas existentes
python -m app.tools.export_runtime_backup --restore backups/x.json --keep-existing-rows

# legado: exige autorização explícita após revisão
python -m app.tools.export_runtime_backup \
  --restore backups/legado.json \
  --allow-legacy

# parcial: só toca as tabelas presentes
python -m app.tools.export_runtime_backup \
  --restore backups/parcial.json \
  --partial
```

Garantias verificadas por teste ponta a ponta (dois tenants, contextos, perfis de
contato e vínculos ativo/inativo):

- Linhas e relacionamentos preservados, inclusive o mesmo `contact_id` em dois
  tenants permanecendo independente.
- Vínculos do WhatsApp mantêm estado ativo e dono.
- A unicidade de número ativo continua valendo **depois** da restauração.
- Restauração é idempotente (o padrão é apagar-e-recarregar).
- Validação, deletes, inserts e ressincronização de sequences acontecem na mesma
  transação. Qualquer falha desfaz tudo.
- Um restore completo exige todas as tabelas, inclusive quando vazias. Arquivo
  incompleto não é interpretado como backup vazio.
- Restore parcial e legado nunca são inferidos: exigem flags explícitas.
- Tabelas e colunas desconhecidas, schemas inconsistentes, contagens divergentes
  e identificadores inválidos são rejeitados antes da mutação.

### Compatibilidade de formato

O formato atual é `backup_format_version=2`. Inclui:

- `schema_manifest`;
- `table_counts`;
- resumo `sanitization`;
- `content_checksum` SHA-256 sobre uma representação canônica do payload.

O checksum detecta corrupção acidental, mas não autentica a origem — não é
assinatura digital. Versão ausente/anterior exige `--allow-legacy`; backup
parcial exige `--partial`. Versão futura é recusada.

### Escrita e armazenamento

O arquivo é escrito em temporário no mesmo diretório, sincronizado com `fsync` e
publicado por `os.replace`, evitando arquivo final parcial. Em POSIX o modo final
é `0600`, inclusive ao substituir destino antigo permissivo.

Mesmo sanitizado, o backup contém PII e dados comerciais. Armazene apenas em
volume/bucket criptografado, com acesso mínimo, retenção e descarte definidos.

Detalhe do contexto em [[40-Data/05-Company Context]].

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
- Restringir acesso ao volume `backups_data`; `0600` local não substitui
  criptografia do volume ou bucket.
- Apagar snapshots temporários após janela de suporte/incidente.

Política em [[50-Security/06-PII e Dataset]].

## Próximas leituras

- [[60-Operations/06-Runbook Produção]]
- [[50-Security/06-PII e Dataset]]
