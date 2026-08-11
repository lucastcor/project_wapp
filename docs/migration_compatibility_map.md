# Mapa de compatibilidade de migrations — consolidação da Release 1

## O problema

Duas linhas de desenvolvimento numeraram migrations no mesmo intervalo, com
conteúdo diferente:

| Versão | Linha principal (local) | Branch `onda-1/conexoes-e-tenancy` |
| --- | --- | --- |
| `024` | `company_contexts` | `company_channel_bindings` |
| `029` | `human_identity` | `conversation_import_indexes` |
| `035` | `tenant_integrity` | `message_example_governance` |
| `037` | `reliable_reminder_indexes` | `message_retrieval_trace` |

Se as duas fossem unidas sem renumerar, `schema_migrations` registraria `024`
uma única vez e a segunda migration `024` nunca rodaria — a divergência de
esquema só apareceria em produção, na primeira query contra uma tabela que não
existe.

## Por que renumerar é seguro aqui

A renumeração só é segura se nenhum banco tiver aplicado a numeração antiga.
Verificado antes de renumerar:

- a branch **não** está mesclada em `main` (`git branch --merged origin/main`
  não a lista);
- ela está **um commit** à frente de `22070cd`, a base comum;
- **não há tag** de release nem qualquer registro de deployment que a inclua.

Nenhum banco jamais aplicou `024`–`053` da branch. A renumeração portanto não
exige reparo de dado existente e não há caminho de upgrade a suportar a partir
da numeração antiga.

> Se algum ambiente tiver aplicado a numeração antiga, **não** use este
> release diretamente: seria necessário um script de reconciliação que reescreve
> `schema_migrations`. Confirme com
> `SELECT version FROM schema_migrations ORDER BY version;` antes de migrar.

## Mapa de renumeração

| Branch | Consolidado | Módulo |
| --- | --- | --- |
| `024_company_channel_bindings` | `038_company_channel_bindings` | `migrations_platform` |
| `025_company_context_versions` | `039_company_context_versions` | `migrations_platform` |
| `026_knowledge_tables` | `040_knowledge_tables` | `migrations_platform` |
| `027_knowledge_indexes` | `041_knowledge_indexes` | `migrations_platform` |
| `028_conversation_import_tables` | `042_conversation_import_tables` | `migrations_platform` |
| `029_conversation_import_indexes` | `043_conversation_import_indexes` | `migrations_platform` |
| `030_dataset_tables` | `044_dataset_tables` | `migrations_platform` |
| `031_dataset_indexes` | `045_dataset_indexes` | `migrations_platform` |
| `032_evaluation_tables` | `046_evaluation_tables` | `migrations_platform` |
| `033_evaluation_indexes` | `047_evaluation_indexes` | `migrations_platform` |
| `034_company_models` | `048_company_models` | `migrations_platform` |
| `035_message_example_governance` | `049_message_example_governance` | `migrations_platform` |
| `036_finetuning_job_dataset_link` | `050_finetuning_job_dataset_link` | `migrations_platform` |
| `037_message_retrieval_trace` | `051_message_retrieval_trace` | `migrations_platform` |
| `038_platform_identity` | **descartada** | — |
| `039_tenant_api_credentials` | `052_service_accounts` (reescrita) | `migrations_identity` |
| `040_message_example_lineage` | `053_message_example_lineage` | `migrations_identity` |
| `041_evaluation_run_provenance` | `054_evaluation_run_provenance` | `migrations_identity` |
| `042_knowledge_published_version` | `055_knowledge_published_version` | `migrations_identity` |
| `043_company_model_lifecycle` | `056_company_model_lifecycle` | `migrations_identity` |
| `044_import_lineage_indexes` | `057_import_lineage_indexes` | `migrations_identity` |
| `045_principal_auth_epoch` | `058_identity_auth_epoch` (reescrita) | `migrations_hardening` |
| `046_versioned_knowledge_metadata` | `059_versioned_knowledge_metadata` | `migrations_hardening` |
| `047_evaluation_executor_attestation` | `060_evaluation_executor_attestation` | `migrations_hardening` |
| `048_automation_control_plane` | `061_automation_control_plane` | `migrations_automation` |
| `049_automation_deployments` | `062_automation_deployments` | `migrations_automation` |
| `050_automation_event_plane` | `063_automation_event_plane` | `migrations_automation` |
| `051_automation_execution_plane` | `064_automation_execution_plane` | `migrations_automation` |
| `052_automation_effects_and_gates` | `065_automation_effects_and_gates` | `migrations_automation` |
| `053_automation_dead_letter_indexes` | `066_automation_dead_letter_indexes` | `migrations_automation` |

## As duas migrations que não foram apenas renumeradas

### `038_platform_identity` foi descartada

Criava `platform_principals`, `platform_access_tokens` e
`platform_company_memberships` — um segundo diretório de pessoas, paralelo ao
canônico (`saas_users` + `company_memberships`, migration `029`). Dois
diretórios de identidade humana significam duas superfícies de login, dois
lugares para desativar quem saiu da empresa e duas respostas possíveis para
"quem pode ver esta empresa", divergentes no dia em que uma delas for
esquecida.

### `039_tenant_api_credentials` virou `052_service_accounts`

O problema legítimo que a branch resolvia era acesso **de máquina** (ingestão
de mensagens, workers, integrações). Isso permanece, modelado explicitamente:

- `service_accounts` — identidade não humana, `company_id NOT NULL`
  referenciando `companies`. Não existe service account global: um token
  vazado alcança uma empresa, nunca a plataforma inteira.
- `service_account_tokens` — segredo apenas como hash; `token_prefix` guarda
  os primeiros caracteres em claro para identificação na UI e nos logs.

O `role` usa o mesmo vocabulário de `company_memberships`
(`owner`/`admin`/`operator`/`viewer`), então a autorização de rota não precisa
saber se o chamador é pessoa ou máquina — precisa apenas do par
`(company_id, role)`.

### `045_principal_auth_epoch` virou `058_identity_auth_epoch`

A ideia é boa e vale para o modelo canônico: sem uma época de autenticação,
revogar acesso só tem efeito quando o TTL da sessão expira. Aplicada a
`saas_users`, `user_sessions` e `service_accounts`.

## Garantias mecânicas

`app/storage/db.py` valida a numeração **na importação do módulo**, via
`_assert_migration_versions_are_ordered()`: versão duplicada ou fora de ordem
levanta `RuntimeError` antes de qualquer conexão ser aberta. Uma versão
duplicada é o erro mais caro possível aqui — a segunda migration nunca roda,
porque `schema_migrations` já registrou aquela versão.

Cobertura das ferramentas de portabilidade é testada, não confiada:

- `tests/test_backup_restore.py::test_backup_covers_every_runtime_table`
- `tests/test_sqlite_to_postgres_migration.py::test_migration_covers_every_runtime_table`

Ambos comparam a lista declarada contra o esquema real e falham quando uma
migration nova cria uma tabela ou coluna que as ferramentas não conhecem. Foi
exatamente o que aconteceu nesta consolidação: 28 tabelas e 18 colunas novas
foram capturadas por esses testes, não por revisão manual.

## Aplicação

Migrations são um passo explícito de release, nunca um efeito colateral de
cold start (ver `docs/deployment.md`):

```bash
python3 -c "from app.storage.db import init_db; init_db()"
```

`init_db()` é idempotente e serializa concorrência com
`pg_advisory_xact_lock` no PostgreSQL, então várias instâncias subindo ao
mesmo tempo não aplicam a mesma migration duas vezes.
