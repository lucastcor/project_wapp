# Production Runbook

## Objetivo
Este runbook consolida o minimo operacional para colocar o Project WAPP em producao com menos improviso: bootstrap, validacao, backup, limpeza segura e resposta inicial a incidente.

## Go-live
1. Ligar o Docker daemon ou garantir que a infraestrutura alvo esteja acessivel.
2. Preencher `.env.production` com segredos reais e valores nao-placeholder.
3. Garantir `DATABASE_URL` e `POSTGRES_DATABASE_URL` apontando para Postgres no host, e `COMPOSE_DATABASE_URL` e `COMPOSE_POSTGRES_DATABASE_URL` apontando para `postgres:5432` dentro do Docker Compose.
4. Habilitar `ENABLE_ADMIN_AUTH=true`, `ENABLE_RATE_LIMITING=true` e `REMINDER_WORKER_ENABLED=true`.
5. Configurar `OUTBOUND_PROVIDER=whatsapp`, `WHATSAPP_API_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN` e `WHATSAPP_APP_SECRET`.
6. Subir a stack endurecida:
   ```bash
   docker compose --env-file .env.production up --build -d
   ```
7. Rodar preflight com exigencia de escala de dentro da API:
   ```bash
   docker compose --env-file .env.production exec api \
     python -m app.tools.preflight_check --require-scale-ready
   ```
8. Validar `/health/ready`, login admin e recebimento real do webhook.

## Backup logico
Gera um snapshot JSON do runtime atual. O comando funciona tanto em SQLite quanto em Postgres:

```bash
python3 -m app.tools.export_runtime_backup
```

Saida customizada:

```bash
python3 -m app.tools.export_runtime_backup --output backups/pre-go-live.json
```

No `compose.yaml`, a API e o worker montam o volume `backups_data` em `/app/backups`.

## Limpeza operacional segura
O fluxo recomendado antes do go-live e sempre:
1. rodar dry-run;
2. revisar candidatos;
3. gerar backup;
4. executar a limpeza.

Dry-run:

```bash
python3 -m app.tools.cleanup_operational_data --stale-after-days 30
```

Execucao com backup automatico:

```bash
python3 -m app.tools.cleanup_operational_data --execute --stale-after-days 30
```

O processo hoje aplica:
- desativacao de empresas placeholder ainda ativas;
- arquivamento de `pending_questions` antigas em aberto;
- cancelamento de `outbound_messages` antigas ainda pendentes.
- arquivamento de jobs de fine-tuning falhados e antigos no historico local.

## Migracao SQLite -> Postgres
Quando existir base em `project_wapp.db`, migrar antes do go-live:

```bash
set -a
source .env.production
set +a

python3 -m app.tools.migrate_sqlite_to_postgres \
  --sqlite-path project_wapp.db \
  --postgres-url "$POSTGRES_DATABASE_URL"
```

Se a migracao for executada de dentro do container da API, use `COMPOSE_POSTGRES_DATABASE_URL` ou deixe a URL explicita apontando para `postgres`.

## Politica minima de PII
- nao subir backups para repositorio Git;
- restringir acesso aos volumes de backup;
- tratar exportacoes e snapshots como dados sensiveis;
- evitar compartilhar `contact_id`, mensagens e contexto bruto fora da operacao;
- apagar snapshots temporarios depois da janela de suporte/incidente.

## Resposta inicial a incidente
1. Confirmar se o problema e de credencial, runtime, webhook ou backlog.
2. Rodar `python3 -m app.tools.preflight_check` e salvar o resultado.
3. Gerar backup logico antes de qualquer alteracao corretiva.
4. Verificar `audit_events`, `outbound_messages`, `pending_questions` e `inbound_message_receipts`.
5. Se houver dano operacional, congelar novas mudancas e priorizar restauracao do fluxo primario:
   - login admin;
   - recepcao de mensagem;
   - dispatch outbound;
   - worker de lembretes;
   - integracao de agenda.
6. Depois do incidente, registrar causa raiz e acao permanente.
