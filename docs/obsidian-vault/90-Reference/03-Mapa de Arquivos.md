---
title: Mapa de Arquivos
type: reference
tags: [referência, mapa, arquivos]
updated: 2026-04-28
---

# Mapa de Arquivos

Inventário completo do que existe em `app/`, `tests/`, `docs/` e raiz. Verificado por `find` em 2026-04-28.

## Raiz

| Arquivo | Função |
|---------|--------|
| `README.md` | Guia oficial de instalação/operação. |
| `Dockerfile` | Imagem Python 3.13-slim, user `app`, uvicorn 2 workers. |
| `compose.yaml` | 4 serviços: postgres, api, worker, admin. |
| `requirements.txt` | Deps de produção. |
| `requirements-dev.txt` | Deps de dev. |
| `.env.example` | Modelo (apenas placeholders). |
| `.gitignore`, `.gitattributes` | Configs git. |
| `.dockerignore` | Excluir do build. |
| `project_wapp.db` | SQLite local (não versionar em produção). |
| `project_wapp_documentacao_oficial.docx` | Documentação histórica em Word. |

## `app/`

### Raiz `app/`

- `__init__.py`
- `main.py` — bootstrap FastAPI, middleware, healthchecks, exception handler.
- `admin_app.py` — entry point Streamlit.

### `app/api/`

- `__init__.py`
- `routes.py` — público (mensagens, webhook, /platform).
- `admin_routes.py` — `/admin/*` (~1 891 linhas).
- `admin_security.py` — `require_admin_access`, `build_require_admin_role`.
- `admin_support.py` — métricas Prometheus + helper de auditoria.
- `platform_routes.py` — shell HTML + auth de sessão web.
- `schemas.py` — Pydantic models.

### `app/core/`

- `__init__.py`
- `orchestrator.py` — `_process_contact_message`, `process_incoming_message`, `process_whatsapp_webhook`.
- `company_context.py` — get/save com validação.
- `config.py` — `Settings` + `is_missing_or_placeholder`.
- `logging.py` — JSON, request context, `mask_identifier`, `log_event`.
- `exceptions.py` — exceções base.
- `state.py` — camada fina (legado/redundante).
- `training_config.py` — constantes de curadoria.

### `app/data/`

- `company_context.json` — fallback global.
- `company_contexts/empresa-teste.json` — exemplo.
- `company_contexts/string.json` — placeholder/lixo (ver I-010).

### `app/llm/`

- `__init__.py`
- `base.py` — wrapper mínimo legado.
- `prompts/system_output.py` — vazio (reservado).
- `providers/__init__.py`
- `providers/mock_provider.py` — fake para dev/testes.
- `providers/openai_provider.py` — OpenAI Responses API.

### `app/platform/`

- `index.html` — shell SPA.
- `assets/styles.css` — visual.
- `assets/app.js` — SPA (state, render, eventos).

### `app/services/`

- `__init__.py`
- `admin_auth_service.py`
- `analytics_service.py`
- `appointment_queue_service.py`
- `billing_service.py`
- `calendar_service.py` (~1 503 linhas)
- `classification_service.py`
- `conversation_workflow_service.py` (~1 467 linhas)
- `decision_service.py`
- `finetuning_service.py`
- `handoff_service.py`
- `knowledge_service.py`
- `memory_service.py`
- `onboarding_service.py`
- `outbound_dispatch_service.py`
- `pipeline_service.py`
- `platform_session_service.py`
- `rate_limit_service.py`
- `realtime_service.py`
- `reminder_service.py`
- `reply_services.py`
- `runtime_guard_service.py`
- `style_service.py`
- `system_readiness_service.py` (~727 linhas)
- `training_export_service.py`
- `training_import_service.py`
- `whatsapp_provider.py`

### `app/storage/`

- `__init__.py`
- `db.py` (~1 731 linhas) — DDL, migrations, conexão.
- `repository.py` (~4 386 linhas) — `RuntimeRepository`.
- `memory_store.py` — minimal/legado.

### `app/tools/`

- `__init__` — vazio sem extensão (legado, ver I-009).
- `__init__.py`
- `calendar_mock.py` — disponibilidade mock MVP.
- `cleanup_operational_data.py` — CLI de limpeza.
- `export_runtime_backup.py` — CLI de snapshot JSON.
- `migrate_sqlite_to_postgres.py` — CLI migração.
- `preflight_check.py` — CLI pre-flight.
- `registry.py` — vazio (placeholder).
- `reminder_worker.py` — worker daemon.

### `app/ui/`

- `__init__.py`
- `admin_api_client.py` — cliente HTTP para Streamlit.
- `pages/__init__.py`
- `pages/calendar_page.py`
- `pages/companies_page.py`
- `pages/company_context_page.py`
- `pages/contacts_page.py`
- `pages/finetuning_page.py`
- `pages/knowledge_page.py`
- `pages/message_examples_page.py`
- `pages/outbound_page.py`
- `pages/pending_questions_page.py`
- `pages/system_page.py`

## `tests/`

- `__init__.py`, `conftest.py`
- `test_admin_routes.py`, `test_analytics_service.py`, `test_appointment_queue_service.py`, `test_billing_service.py`, `test_business_scenarios.py`, `test_calendar_service.py`, `test_company_context.py`, `test_conversation_workflow_service.py`, `test_db_backend.py`, `test_finetuning_service.py`, `test_memory_service.py`, `test_onboarding_service.py`, `test_operational_rules.py`, `test_orchestrator.py`, `test_orchestrator_flow.py`, `test_orchestrator_no_auto_reply.py`, `test_outbound_dispatch_service.py`, `test_pipeline_service.py`, `test_pipeline_service_validation.py`, `test_reminder_service.py`, `test_reply_service.py`, `test_routes.py`, `test_system_readiness_service.py`, `test_tenant_isolation.py`, `test_tools.py`, `test_training_export_service.py`, `test_training_import_service.py`.
- `test_data/conversation_scenarios.py` — cenários reutilizáveis.

## `docs/` (originais do repo)

- `architecture.md`
- `api_spec.md`
- `codebase_map.md`
- `frontend_improvements_2026-04-12.md`
- `knowledge_base.md`
- `platform_frontend_guide.md`
- `production_runbook.md`
- `obsidian-vault/` ← **este vault**

## Pontos de atenção

- **Arquivos legados / placeholders**: `app/tools/__init__` (sem extensão), `app/tools/registry.py` (vazio), `app/llm/prompts/system_output.py` (vazio), `app/data/company_contexts/string.json` (placeholder).
- **Arquivos gigantes**: `repository.py`, `admin_routes.py`, `db.py`, `calendar_service.py`, `conversation_workflow_service.py`, `system_readiness_service.py`. Mexer com revisão lateral.
- **Pasta nova**: `docs/obsidian-vault/` (ESTE vault).

## Próximas leituras

- [[10-Architecture/01-Arquitetura em Camadas]]
- [[20-Modules/05-Services]]
- [[80-Improvements/02-Issues Identificados]]
