# Linha de base do fechamento para piloto

Capturado em 2026-08-06T15:01:53Z antes de qualquer alteração desta rodada.
Nenhum arquivo foi descartado, revertido, resetado ou colocado em stash.

## Ambiente

```
claude       2.1.223 (Claude Code)
python       Python 3.13.0
node         v24.14.1
npm          11.11.0
sqlite3      3.45.3
psycopg      3.3.3
docker CLI   Docker version 29.3.1, build c2be9cc
docker daemon NÃO rodando
postgres     NÃO respondendo
redis        NÃO respondendo
```

## Git

```
branch:  main
HEAD:    22070cd Vault
commits novos desde HEAD: 0
stashes: 0
modificados:   50
não rastreados: 120
```

### git diff --stat

```
 .env.example                             |  88 ++++
 .github/workflows/ci.yml                 |  96 +++--
 .gitignore                               |   5 +
 Dockerfile                               |  29 ++
 README.md                                | 163 ++++++--
 app/api/admin_routes.py                  | 305 ++++++++++----
 app/api/admin_security.py                | 190 +++++++--
 app/api/platform_routes.py               | 118 +++++-
 app/api/routes.py                        | 181 +++++++-
 app/core/company_context.py              |  75 +++-
 app/core/config.py                       | 112 +++++
 app/core/orchestrator.py                 |  44 +-
 app/llm/providers/openai_provider.py     |  87 ++--
 app/main.py                              |  19 +-
 app/platform/assets/app.js               |  62 ++-
 app/platform/assets/styles.css           |   8 +
 app/services/calendar_service.py         |  18 +-
 app/services/finetuning_service.py       | 120 +++++-
 app/services/platform_session_service.py |  54 ++-
 app/services/rate_limit_service.py       |  50 +--
 app/services/realtime_service.py         | 227 ++++++++--
 app/services/reminder_service.py         |  18 +-
 app/services/reply_services.py           | 198 ++++++---
 app/services/style_service.py            | 136 +++---
 app/services/system_readiness_service.py | 342 ++++++++++++++++
 app/services/training_export_service.py  | 296 ++++++++-----
 app/services/training_import_service.py  | 101 ++++-
 app/storage/db.py                        |  28 ++
 app/storage/repository.py                | 684 +++++++++++++++++++------------
 app/tools/export_runtime_backup.py       |   9 +-
 app/ui/admin_api_client.py               |  24 +-
 app/ui/pages/finetuning_page.py          |  31 +-
 compose.yaml                             |  76 +++-
 docs/api_spec.md                         | 115 ++++++
 docs/architecture.md                     |  43 ++
 docs/codebase_map.md                     |  51 +++
 docs/production_runbook.md               | 194 +++++++++
 tests/conftest.py                        | 212 ++++++++++
 tests/test_admin_routes.py               |  27 +-
 tests/test_business_scenarios.py         |   3 +-
 tests/test_calendar_service.py           |  20 +-
 tests/test_finetuning_service.py         |  89 +++-
 tests/test_orchestrator.py               |   3 +-
 tests/test_orchestrator_flow.py          |   6 +-
 tests/test_orchestrator_no_auto_reply.py |   3 +-
 tests/test_reminder_service.py           |  12 +-
 tests/test_reply_service.py              |  47 ++-
 tests/test_routes.py                     |  21 +-
 tests/test_training_export_service.py    |   8 +-
 tests/test_training_import_service.py    |  26 +-
 50 files changed, 3984 insertions(+), 890 deletions(-)
```

### Arquivos não rastreados

```
app/api/automation_routes.py
app/api/automation_schemas.py
app/api/company_scope.py
app/api/platform_admin_routes.py
app/api/platform_schemas.py
app/automations/__init__.py
app/automations/catalog.py
app/automations/domain.py
app/automations/effects.py
app/automations/engine.py
app/automations/events.py
app/automations/exceptions.py
app/automations/semantics.py
app/automations/simulation.py
app/automations/validation.py
app/core/credentials.py
app/core/reply_contract.py
app/core/taxonomy.py
app/core/tenancy.py
app/core/vertical_packs.py
app/llm/embeddings.py
app/platform/assets/governance.js
app/services/admin_principal_service.py
app/services/automation_approval_service.py
app/services/automation_event_service.py
app/services/automation_execution_service.py
app/services/automation_trigger_service.py
app/services/automation_workflow_service.py
app/services/company_config_service.py
app/services/conversation_import_service.py
app/services/conversation_parsers.py
app/services/dataset_service.py
app/services/evaluation_executor.py
app/services/evaluation_service.py
app/services/finetuning_readiness_service.py
app/services/knowledge_document_service.py
app/services/knowledge_retrieval_service.py
app/services/model_registry_service.py
app/services/pii_service.py
app/services/shared_state.py
app/services/tenant_credential_service.py
app/services/tenant_resolution_service.py
app/storage/automation_repository.py
app/storage/channel_repository.py
app/storage/company_config_repository.py
app/storage/dataset_repository.py
app/storage/evaluation_repository.py
app/storage/identity_repository.py
app/storage/import_repository.py
app/storage/knowledge_repository.py
app/storage/migrations_automation.py
app/storage/migrations_hardening.py
app/storage/migrations_identity.py
app/storage/migrations_platform.py
app/storage/model_repository.py
app/storage/schema_utils.py
app/storage/sql_support.py
app/tools/automation_worker.py
checked_modules.txt
docs/automation_studio.md
docs/closure-baseline.md
docs/migration_and_rollback.md
docs/multitenant_platform.md
frontend/automation-studio/eslint.config.js
frontend/automation-studio/index.html
frontend/automation-studio/package-lock.json
frontend/automation-studio/package.json
frontend/automation-studio/src/App.tsx
frontend/automation-studio/src/api/client.ts
frontend/automation-studio/src/api/types.ts
frontend/automation-studio/src/components/Approvals.tsx
frontend/automation-studio/src/components/Canvas.tsx
frontend/automation-studio/src/components/Drawer.tsx
frontend/automation-studio/src/components/Editor.tsx
frontend/automation-studio/src/components/Executions.tsx
frontend/automation-studio/src/components/Inspector.tsx
frontend/automation-studio/src/components/Palette.tsx
frontend/automation-studio/src/components/WorkflowList.tsx
frontend/automation-studio/src/components/primitives.tsx
frontend/automation-studio/src/main.tsx
frontend/automation-studio/src/state/graph.ts
frontend/automation-studio/src/styles/studio.css
frontend/automation-studio/tests/client.test.ts
frontend/automation-studio/tests/components.test.tsx
frontend/automation-studio/tests/graph.test.ts
frontend/automation-studio/tests/setup.ts
frontend/automation-studio/tsconfig.json
frontend/automation-studio/vite.config.ts
tests/support/__init__.py
tests/support/attestation.py
tests/support/automation_graphs.py
tests/test_admin_tenant_authorization.py
tests/test_automation_api.py
tests/test_automation_conformance.py
tests/test_automation_domain.py
tests/test_automation_e2e.py
tests/test_automation_engine.py
tests/test_automation_studio_bundle.py
tests/test_checked_modules.py
tests/test_company_config_service.py
tests/test_contract_golden.py
tests/test_conversation_import.py
tests/test_dataset_service.py
tests/test_dedup_knowledge_pseudonymization.py
tests/test_evaluation_and_models.py
tests/test_finetuning_readiness.py
tests/test_import_lineage_and_context.py
tests/test_ingest_credentials.py
tests/test_knowledge_retrieval.py
tests/test_migrations_platform.py
tests/test_no_external_calls.py
tests/test_phase_zero_blockers.py
tests/test_pii_service.py
tests/test_platform_end_to_end.py
tests/test_platform_security.py
tests/test_readiness_platform_checks.py
tests/test_reply_contract_and_style.py
tests/test_sql_interpolation_guard.py
tests/test_taxonomy.py
tests/test_tenant_isolation_knowledge.py
```

---

## PostgreSQL efêmero usado nesta rodada

Instância descartável, em diretório temporário, criada só para validação.
Não toca nenhum banco existente e não altera configuração global.

```bash
PGDATA=/private/tmp/claude-501/-Users-lucasteixeiracorreia-Documents-project-wapp/4fa90067-e23f-4e84-8ba9-273b600b0d3e/scratchpad/pgdata
SOCK=/tmp/wpg   # caminho curto: o socket do PostgreSQL tem limite de 103 bytes

/opt/homebrew/opt/postgresql@14/bin/initdb -D $PGDATA -U wapp --auth=trust -E UTF8
mkdir -p $SOCK
/opt/homebrew/opt/postgresql@14/bin/pg_ctl -D $PGDATA -l /private/tmp/claude-501/-Users-lucasteixeiracorreia-Documents-project-wapp/4fa90067-e23f-4e84-8ba9-273b600b0d3e/scratchpad/pg.log \
  -o "-p 55432 -k $SOCK -c listen_addresses=127.0.0.1" start
/opt/homebrew/opt/postgresql@14/bin/createdb -h 127.0.0.1 -p 55432 -U wapp wapp_test

# a suíte inteira contra PostgreSQL real
WAPP_TEST_DATABASE_URL="postgresql://wapp@127.0.0.1:55432/wapp_test" \
  .venv/bin/python -m pytest -q

# parar
/opt/homebrew/opt/postgresql@14/bin/pg_ctl -D $PGDATA stop
```

Versão: PostgreSQL 14.21 (Homebrew), aarch64-apple-darwin25.
