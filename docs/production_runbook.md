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


## Plataforma multiempresa

Detalhes de arquitetura em [multitenant_platform.md](multitenant_platform.md);
migração e rollback em [migration_and_rollback.md](migration_and_rollback.md).

### Checklist antes do go-live de uma empresa

1. empresa cadastrada e ativa;
2. company context publicado (`POST /admin/company-config/bootstrap` ou
   `POST /admin/company-config/versions`);
3. vínculo de canal registrado (`POST /admin/channel-bindings`) — sem ele o
   webhook é recusado com `403` em produção;
4. `ALLOW_UNTRUSTED_TENANT_HINTS=false` e
   `ALLOW_NORMALIZED_WHATSAPP_WEBHOOK=false`;
5. `WHATSAPP_APP_SECRET` com valor real;
6. knowledge aprovado para as perguntas mais frequentes;
7. `SHARED_STATE_BACKEND=redis` quando houver mais de um worker.

O readiness cobre os itens 4, 5, 7 e reporta o 3:

```bash
curl -s http://127.0.0.1:8000/admin/system/readiness \
  -H "X-Admin-Token: $ADMIN_SUPERADMIN_TOKEN" | jq '.checks[] | select(.status != "pass")'
```

### Validar o backend Redis compartilhado

O adapter Redis não é exercitado pela suíte padrão (que roda offline). Para
validar com um Redis real:

```bash
docker run -d --name wapp-redis -p 6379:6379 redis:7-alpine

SHARED_STATE_BACKEND=redis REDIS_URL=redis://127.0.0.1:6379/0 \
  python3 -m app.tools.preflight_check

# rate limit compartilhado entre processos
SHARED_STATE_BACKEND=redis REDIS_URL=redis://127.0.0.1:6379/0 \
  python3 -c "
from app.services.shared_state import get_counter_store
store = get_counter_store()
print('shared:', store.is_shared)
print('count:', store.increment('project_wapp:ratelimit:probe', 60))
"
```

### Validar embeddings reais

Também não exercitado pela suíte padrão:

```bash
EMBEDDINGS_PROVIDER=openai ENABLE_VECTOR_SEARCH=true OPENAI_API_KEY=... \
  python3 -c "
from app.llm.embeddings import get_embedding_provider
provider = get_embedding_provider()
print(provider.model, provider.is_semantic, len(provider.embed(['teste'])[0]))
"
```

Reingira os documentos depois de habilitar embeddings, para que os chunks
existentes ganhem vetor:

```bash
curl -X POST "http://127.0.0.1:8000/admin/knowledge/documents?company_slug=SUA-EMPRESA" \
  -H "X-Admin-Token: $ADMIN_OPERATOR_TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"...","content":"...","external_ref":"...","approve":true}'
```

### Fluxo de fine-tuning

```bash
# 1. gerar versão
curl -X POST "http://127.0.0.1:8000/admin/dataset-versions?company_slug=X" -d '{}'

# 2. verificar readiness e vazamento entre splits
curl "http://127.0.0.1:8000/admin/dataset-versions/<ID>/readiness?company_slug=X"
curl "http://127.0.0.1:8000/admin/dataset-versions/<ID>/leakage?company_slug=X"

# 3. congelar
curl -X POST "http://127.0.0.1:8000/admin/dataset-versions/<ID>/freeze?company_slug=X"

# 4. avaliar base e fine-tuned
curl -X POST "http://127.0.0.1:8000/admin/evaluation/runs?company_slug=X" \
  -d '{"approach":"base_rag"}'

# 5. preflight sem efeito externo
curl -X POST "http://127.0.0.1:8000/admin/finetuning/preflight?company_slug=X" \
  -d '{"dataset_version_id":<ID>}'

# 6. criar o job (exige ENABLE_FINETUNING_PIPELINE=true e readiness aprovado)
curl -X POST "http://127.0.0.1:8000/admin/finetuning/jobs?company_slug=X" \
  -d '{"dataset_version_id":<ID>}'
```

### Rollback de modelo

```bash
curl -X POST "http://127.0.0.1:8000/admin/models/rollback?company_slug=X" \
  -H "X-Admin-Token: $ADMIN_SUPERADMIN_TOKEN"
```

---

## Automation Studio

### Habilitar (ordem importa)

```bash
# 1. Gravação de eventos. Nada executa ainda: nenhum fluxo está ativo.
export AUTOMATION_ENGINE_ENABLED=true

# 2. Worker (processo separado). Só depois de haver fluxo publicado.
export AUTOMATION_WORKER_ENABLED=true
python -m app.tools.automation_worker
```

Os dois são `false` por padrão. Nenhuma empresa passa a executar automação por
causa de um deploy.

### Colocar um fluxo no ar

```bash
BASE=http://127.0.0.1:8000/admin/automations
AUTH="-H X-Admin-Token: $ADMIN_TOKEN"

# 1. criar
curl -X POST "$BASE/workflows?company_id=$CID" $AUTH \
  -d '{"workflow_key":"follow-up-orcamento","name":"Follow-up de orçamento"}'

# 2. salvar o rascunho (expected_revision vem do GET do fluxo)
curl -X PUT "$BASE/workflows/$WID/draft?company_id=$CID" $AUTH \
  -d '{"graph": {...}, "expected_revision": 1}'

# 3. validar — o backend é a autoridade
curl -X POST "$BASE/workflows/validate?company_id=$CID" $AUTH -d '{"graph": {...}}'

# 4. simular — nenhum efeito é produzido
curl -X POST "$BASE/workflows/simulate?company_id=$CID" $AUTH \
  -d '{"graph": {...}, "sample_context": {"classification": "orcamento"}}'

# 5. publicar — a versão nasce PAUSADA
curl -X POST "$BASE/workflows/$WID/publish?company_id=$CID" $AUTH -d '{}'

# 6. ativar — decisão separada
curl -X POST "$BASE/workflows/$WID/state?company_id=$CID" $AUTH -d '{"state":"active"}'
```

### Operação diária

```bash
# execuções travadas ou falhas
curl "$BASE/executions?company_id=$CID&status=dead_letter" $AUTH
curl "$BASE/dead-letter?company_id=$CID" $AUTH

# reprocessar (efeitos já concluídos NÃO repetem)
curl -X POST "$BASE/executions/$XID/retry?company_id=$CID" $AUTH

# cancelar (cooperativo: o job em voo termina o passo e não reagenda)
curl -X POST "$BASE/executions/$XID/cancel?company_id=$CID" $AUTH

# fila de aprovações
curl "$BASE/approvals?company_id=$CID" $AUTH

# um ciclo manual do worker (exige superadmin GLOBAL)
curl -X POST "$BASE/worker/run-once" $AUTH
```

### Incidentes

| Sintoma | Diagnóstico | Ação |
|---|---|---|
| Execuções paradas em `queued` | worker parado | subir `automation_worker`; leases vencidos voltam à fila sozinhos |
| Jobs em `running` sem avançar | worker morreu com lease ativo | `recover_stalled_work()` roda a cada ciclo; aguardar `AUTOMATION_JOB_LEASE_SECONDS` |
| Crescimento de dead-letter | erro sistemático num adapter | ver `last_error`; corrigir; `retry` em lote |
| Efeito com `indeterminate_previous_attempt` | worker morreu entre reservar e registrar | **verificar manualmente** se a ação externa ocorreu; o sistema não repete de propósito |
| Mensagem duplicada ao cliente | fluxo enviando no mesmo evento do auto-reply legado | o validador recusa isso na publicação; se ocorreu, há versão publicada sob regra anterior — pausar e republicar |
| Fluxo com efeito indesejado | — | **pausar** (bloqueia entradas, preserva quem espera), não desativar o motor inteiro |

### Desligar tudo

```bash
export AUTOMATION_ENGINE_ENABLED=false
```

O orquestrador continua funcionando normalmente. Eventos e execuções ficam no
banco; o processamento simplesmente para. Nada é apagado.

### Readiness

`GET /admin/system/readiness` inclui as verificações de automação: Postgres
ativo quando há réplicas, worker habilitado, lease/retry válidos, ausência de
versão ativa inválida e shared state adequado.
