# Migração e rollback — plataforma multiempresa

## 1. Migrations adicionadas

Todas são aditivas, idempotentes e compatíveis com SQLite e Postgres. Nenhuma
remove ou renomeia dado existente. Vivem em
`app/storage/migrations_platform.py` e são anexadas à lista de
`app/storage/db.py`.

| Versão | Descrição |
| --- | --- |
| `024_company_channel_bindings` | vínculo verificável canal ↔ empresa |
| `025_company_context_versions` | company context versionado |
| `026_knowledge_tables` | documentos, versões e chunks |
| `027_knowledge_indexes` | índices de recuperação |
| `028_conversation_import_tables` | staging de importação e candidatos |
| `029_conversation_import_indexes` | índices de importação |
| `030_dataset_tables` | versões de dataset e itens |
| `031_dataset_indexes` | índices de dataset |
| `032_evaluation_tables` | casos, execuções e resultados |
| `033_evaluation_indexes` | índices de avaliação |
| `034_company_models` | registro de modelo por empresa |
| `035_message_example_governance` | colunas de taxonomia e governança |
| `036_finetuning_job_dataset_link` | job ↔ dataset congelado |
| `037_message_retrieval_trace` | rastro de fontes usadas na resposta |

### Colunas aditivas

- `message_examples`: `customer_profile`, `approval_status` (default
  `approved`, o que preserva o comportamento das linhas existentes),
  `source_type`, `source_reference`, `pii_scrubbed`;
- `finetuning_jobs`: `dataset_version_id`, `readiness_json`, `requested_by`;
- `messages`: `retrieval_json`.

### Aplicar

```bash
# host
python3 -c "from app.storage.db import init_db; init_db()"

# container
docker compose --env-file .env.production exec api \
  python -c "from app.storage.db import init_db; init_db()"

# inspeção
curl -s http://127.0.0.1:8000/admin/system/migrations
```

`init_db()` é seguro para reexecução: migrations já aplicadas são puladas e o
DDL usa `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`.

---

## 2. Bootstrap do company context

Os JSONs em `app/data/company_contexts/` continuam válidos como seed.

```bash
curl -X POST "http://127.0.0.1:8000/admin/company-config/bootstrap?company_slug=SUA-EMPRESA" \
  -H "X-Admin-Token: $ADMIN_SUPERADMIN_TOKEN"
```

Idempotente: se já houver versão publicada, ela é devolvida sem escrita.

Migração do knowledge legado para o modelo documental:

```bash
curl -X POST "http://127.0.0.1:8000/admin/knowledge/import-legacy?company_slug=SUA-EMPRESA" \
  -H "X-Admin-Token: $ADMIN_SUPERADMIN_TOKEN"
```

Também idempotente (`external_ref = legacy:<id>`). Só migra conhecimento
global; entradas presas a um contato permanecem no modelo antigo.

---

## 3. Vínculo de canal antes do go-live

Sem binding, o webhook é recusado com `403` em produção.

```bash
curl -X POST "http://127.0.0.1:8000/admin/channel-bindings?company_slug=SUA-EMPRESA" \
  -H "X-Admin-Token: $ADMIN_SUPERADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"whatsapp","external_id":"<PHONE_NUMBER_ID_DO_META>","label":"Número principal"}'
```

---

## 4. Incompatibilidades e compatibilidade

| Contrato | Mudança | Migração |
| --- | --- | --- |
| `POST /admin/finetuning/jobs` | passa a exigir corpo `{"dataset_version_id": N}` | crie e congele uma versão de dataset antes |
| `POST /webhooks/whatsapp` com payload normalizado | bloqueado em produção | envie o payload do provider, ou assine com `WHATSAPP_APP_SECRET` |
| `GET /platform/events` | exige sessão e escopo | a SPA já envia o escopo |
| `repository.get_relevant_knowledge_entries` | `company_id` obrigatório | chamadores internos já ajustados |
| `POST /admin/training-import` | não liga mais `used_for_training` | revise em `/admin/message-examples` ou use o fluxo governado |
| `PUT /admin/company-context` | com empresa resolvida, cria versão | resposta passa a ser a versão criada |

Mudanças de segurança documentadas acima são intencionais: aceitar tenant não
verificado ou payload não autenticado permitia cruzar dados entre empresas.

---

## 5. Rollback

### Rollback de configuração

Republique a versão anterior — o histórico é preservado:

```bash
curl "http://127.0.0.1:8000/admin/company-config/versions?company_slug=SUA-EMPRESA" \
  -H "X-Admin-Token: $ADMIN_SUPERADMIN_TOKEN"

curl -X POST "http://127.0.0.1:8000/admin/company-config/publish?company_slug=SUA-EMPRESA" \
  -H "X-Admin-Token: $ADMIN_SUPERADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"version_id": <ID_DA_VERSAO_ANTERIOR>}'
```

### Rollback de modelo

```bash
curl -X POST "http://127.0.0.1:8000/admin/models/rollback?company_slug=SUA-EMPRESA" \
  -H "X-Admin-Token: $ADMIN_SUPERADMIN_TOKEN"
```

Reativa o modelo anterior; não havendo anterior, cai no modelo base.

### Rollback de aplicação

As migrations são aditivas, então a versão anterior da aplicação continua
funcionando sobre o schema novo: as colunas e tabelas adicionadas são
ignoradas pelo código antigo.

A exceção é `POST /admin/finetuning/jobs`: o código antigo não envia
`dataset_version_id`. Se precisar voltar a aplicação, use o fluxo de
fine-tuning apenas depois de reavançar.

Não há necessidade de `DROP` para rollback. Se quiser descartar dados de
staging de importação, faça-o por empresa e com backup prévio:

```bash
python3 -m app.tools.export_runtime_backup
```

---

## 6. Proteção de backups e exports

- exports de dataset derivam de `dataset_version_items`, que já passaram por
  scrub de PII;
- staging de importação (`imported_messages`) guarda apenas texto anonimizado;
- identificadores de contato aparecem como `anon_<hash>` com salt por tenant;
- `retrieval_json` grava ids e scores, nunca o conteúdo da fonte;
- backups produzidos por `export_runtime_backup` contêm dados operacionais
  reais (contatos, mensagens) e devem ser tratados como dado pessoal: cifrados
  em repouso e com retenção alinhada a `training_data_retention_days`.

---

## 7. Segunda rodada — migrations 038–044

| Versão | Descrição |
| --- | --- |
| `038_platform_identity` | principals, tokens de acesso e memberships por empresa |
| `039_tenant_api_credentials` | credenciais tenant-scoped da API de ingestão |
| `040_message_example_lineage` | lineage estruturado e contexto multi-turno no exemplo |
| `041_evaluation_run_provenance` | modo de execução e proveniência da avaliação |
| `042_knowledge_published_version` | versão publicada separada da versão em edição |
| `043_company_model_lifecycle` | estado do job refletido no candidato de modelo |
| `044_import_lineage_indexes` | índices de lineage e `promotion_state` do candidato |

Todas aditivas e idempotentes. `042` faz backfill:
`current_published_version_id = current_version_id` para documentos já
aprovados, então nada sai do ar na atualização.

### Contratos alterados nesta rodada

| Contrato | Mudança | Migração |
| --- | --- | --- |
| `POST /messages/incoming` | exige credencial de tenant (`X-Tenant-Token` ou `Bearer`) | emitir credencial por empresa antes do deploy |
| rotas `/admin/*` tenant-scoped | principal com membership precisa informar a empresa | criar principals e memberships |
| `GET /admin/channel-bindings` | escopo obrigatório; `external_id` mascarado | usar `reveal_external_id=true` como superadmin quando necessário |
| `POST /admin/evaluation/runs` | sempre `execution_mode=simulation` | avaliação model-backed é procedimento operacional separado |
| `POST /admin/models/{id}/activate` | valida job, modelo, dataset e avaliação | promover só após job concluído e avaliação model-backed |
| `evaluate_finetuning_readiness` | virou `evaluate_pretraining_readiness`; `require_evaluation` ignorado | usar `evaluate_promotion_readiness` para ativação |

### Emissão de credenciais

```bash
# Credencial de ingestão da empresa (token devolvido uma única vez)
curl -X POST "http://127.0.0.1:8000/admin/tenant-credentials?company_slug=SUA-EMPRESA" \
  -H "X-Admin-Token: $ADMIN_SUPERADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"description":"ingestao-producao"}'

# Principal administrativo com membership explícita
curl -X POST "http://127.0.0.1:8000/admin/principals" \
  -H "X-Admin-Token: $ADMIN_SUPERADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"principal_key":"op-empresa-a","display_name":"Operador A",
       "global_role":"operator","company_ids":[1]}'
```

Rotação: emitir a nova credencial, migrar os chamadores, revogar a antiga
(`POST /admin/tenant-credentials/{id}/revoke`). A revogação vale imediatamente.

### Rotação da chave de pseudonimização

`PII_PSEUDONYMIZATION_KEY` entra no HMAC e sua versão entra no prefixo do
pseudônimo (`anon_v1_...`). Girar a chave **muda todos os pseudônimos**: os
agrupamentos antigos e novos deixam de casar. Procedimento:

1. incrementar `PII_PSEUDONYMIZATION_KEY_VERSION`;
2. trocar `PII_PSEUDONYMIZATION_KEY`;
3. reimportar os lotes que ainda alimentam dataset ativo;
4. reconstruir as `dataset_versions` não congeladas.

Versões congeladas permanecem válidas: elas já materializaram o split.

---

## Migrations 045–053

Aditivas, idempotentes e compatíveis com SQLite e Postgres. Nenhuma altera uma
migration já aplicada.

### Endurecimento (045–047)

| Versão | O que faz | Retrofit |
|---|---|---|
| `045_principal_auth_epoch` | `auth_epoch` em `platform_principals` | principais existentes recebem época 1; nada é invalidado retroativamente |
| `046_versioned_knowledge_metadata` | move `title`, `document_type`, `priority`, `valid_from`, `valid_until` para `knowledge_document_versions` | cada versão herda o metadado que o documento carregava — única interpretação possível de dados anteriores à separação |
| `047_evaluation_executor_attestation` | `executor_kind`, `provider`, `attestation_json` em `evaluation_runs` | runs antigos ficam sem atestação e **não** passam mais no gate de promoção, o que é o comportamento correto |

> **Atenção na 047:** um modelo promovido antes desta migration continua ativo,
> mas uma nova promoção exigirá uma avaliação atestada. Isso é intencional: a
> proveniência antiga não era verificável.

### Automation Studio (048–053)

| Versão | Tabelas |
|---|---|
| `048_automation_control_plane` | `automation_workflows`, `automation_workflow_drafts`, `automation_workflow_versions` |
| `049_automation_deployments` | `automation_deployments` |
| `050_automation_event_plane` | `automation_events`, `automation_outbox` |
| `051_automation_execution_plane` | `automation_executions`, `automation_step_executions`, `automation_jobs` |
| `052_automation_effects_and_gates` | `automation_effects`, `automation_approval_gates` |
| `053_automation_dead_letter_indexes` | índices de dead-letter |

Todas criam tabelas novas. Nenhum dado existente é tocado.

### Rollback

As migrations não têm `down`. Para reverter o Automation Studio:

1. `AUTOMATION_ENGINE_ENABLED=false` — para o processamento;
2. as tabelas ficam no banco, sem custo além do espaço;
3. remover as tabelas só é necessário se o schema precisar encolher, e aí é
   uma operação manual deliberada.

Reverter a 046 exigiria copiar o metadado da versão publicada de volta para o
documento. As colunas do documento continuam existindo e são atualizadas em
`approve_document`, então o espelho está sempre correto — a reversão é
possível sem perda.

### Verificação

```bash
# banco limpo
rm -f check.db && DATABASE_URL=sqlite:///check.db \
  python -c "from app.storage.db import init_db; init_db()"

# reexecução (idempotência)
DATABASE_URL=sqlite:///check.db \
  python -c "from app.storage.db import init_db; init_db()"

# suíte de migrations
pytest tests/test_migrations_platform.py -q
```
