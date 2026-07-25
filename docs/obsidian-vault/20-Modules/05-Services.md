---
title: Services (catálogo)
type: module
tags: [módulo, services, catálogo]
updated: 2026-04-28
---

# Módulo: Services (catálogo completo)

`app/services/` concentra a lógica de negócio. ~25 arquivos. Cada serviço tem responsabilidade única, mas todos compartilham `RuntimeRepository` e `app.core.config.settings`.

## Por categoria

### Conversação (núcleo do bot)

| Arquivo | Função pública principal | Resumo |
|---------|--------------------------|--------|
| `classification_service.py` | `classify_message(message)` | OpenAI ou heurística. Retorna `{classification, confidence, reason}`. |
| `decision_service.py` | `decide_message_action(classification, confidence)` | Decide auto-reply, prioridade, action. |
| `reply_services.py` | `generate_business_reply(...)` | OpenAI Responses API + fallback determinístico. |
| `style_service.py` | `get_relevant_style_examples`, `infer_style_scenario_type`, `format_style_examples_for_prompt` | Seleciona exemplos aprovados como referência de tom. |
| `handoff_service.py` | `should_create_pending_question(...)`, `normalize_owner_resolution(...)` | Heurística de escalonamento e normalização da resposta humana. |
| `knowledge_service.py` | `build_knowledge_entry_from_resolution`, `should_store_resolution_as_knowledge` | Resoluções viram knowledge incremental. |
| `memory_service.py` | `update_conversation_summary(...)` | Resumo conversacional para continuidade. |
| `pipeline_service.py` | `infer_pipeline_state(...)` | Estágio comercial + lead temperature + next_action. |
| `conversation_workflow_service.py` | `handle_conversation_workflow(...)` | Coordena todos os fluxos conversacionais (agenda, cancel, reschedule, operador). 1467 linhas. |

### Agenda + lembrete

| Arquivo | Função | Resumo |
|---------|--------|--------|
| `calendar_service.py` | `query_calendar_availability`, `create/cancel/reschedule_calendar_booking`, `resolve_scheduling_profile`, `get_calendar_timeline` | 1 503 linhas. Provider mock + integrações Google/CalDAV. |
| `appointment_queue_service.py` | `create_queue_entry`, `respond_to_queue_offer`, `release_booking_slot`, `process_queue_contact_message` | Fila de prioridade e ofertas. |
| `reminder_service.py` | `create_internal_reminder`, `dispatch_due_reminders`, `schedule_booking_customer_reminders`, `list_reminders` | Audience: customer/internal. |

### Outbound

| Arquivo | Função | Resumo |
|---------|--------|--------|
| `outbound_dispatch_service.py` | `dispatch_outbound_message`, `dispatch_pending_outbound_messages` | Multi-canal. |
| `whatsapp_provider.py` | `WhatsAppProvider().send_message` | Cloud API real (Graph). |

### Treino / fine-tuning

| Arquivo | Função | Resumo |
|---------|--------|--------|
| `training_import_service.py` | `import_training_records` | CSV/JSON dry-run + persist. |
| `training_export_service.py` | `export_training_records_as_*`, `export_finetuning_*`, `get_finetuning_readiness_report` | Curadoria + dedup + split + export. |
| `finetuning_service.py` | `FineTuningService().create/list/get` | Cria job na OpenAI e persiste em `finetuning_jobs`. |

### Plataforma e segurança

| Arquivo | Função | Resumo |
|---------|--------|--------|
| `admin_auth_service.py` | `resolve_admin_role_from_token`, `extract_bearer_token`, `get_role_token_map`, `ADMIN_ROLE_HIERARCHY` | RBAC por token. |
| `platform_session_service.py` | `issue_platform_session`, `read_platform_session`, `authenticate_platform_login`, `attach/clear_platform_session_cookie` | Sessão web HMAC. |
| `realtime_service.py` | `broadcast_event` | Broadcast simples de eventos. |

### Operação / infra

| Arquivo | Função | Resumo |
|---------|--------|--------|
| `system_readiness_service.py` | `get_system_profile`, `get_system_readiness`, `get_onboarding_status` | Checks consolidados. 727 linhas. |
| `runtime_guard_service.py` | `validate_runtime_environment`, `get_runtime_blockers`, `runtime_validation_enabled` | Bloqueia subida se há blockers. |
| `rate_limit_service.py` | `evaluate_rate_limit`, `build_rate_limit_headers`, `get_request_client_ip` | In-memory por escopo. |
| `analytics_service.py` | analytics dashboard, trend, reminder effectiveness | Bookings, outcomes, reasons. |
| `billing_service.py` | `get_plans`, `start_trial`, `activate_subscription`, `record_usage`, `check_limit` | 3 planos + degradação. |
| `onboarding_service.py` | `read_onboarding_presets`, `run_onboarding_setup` | Bootstrap operacional por empresa. |

## Padrões observados

1. **Repository singleton por módulo**: `repository = RuntimeRepository()` no top. Coupling implícito.
2. **OpenAI via instanciação local**: cada chamada cria `OpenAIProvider()`. Seguro mas custoso — cliente é leve.
3. **Logs sempre estruturados** com `log_event(logger, level, "evento", **kwargs)`.
4. **Fallback first**: serviços que dependem de OpenAI (classification, reply, workflow) têm fallback determinístico.
5. **Validação de env vars** em pontos-chave: `WhatsAppProvider._validate_configuration()`, `OpenAIProvider.__init__` etc.
6. **Errors específicos**: `WhatsAppProviderError`, `CalendarProviderError`, `OutboundDispatchError`, `BillingError`, `TrainingImportError`, `ReminderServiceError`, `AppointmentQueueError`, `FineTuningError`, `PlatformSessionError`, `CompanyContextError`.

## Próximas leituras

- [[20-Modules/06-LLM Providers]]
- [[20-Modules/07-Storage e Repository]]
- [[10-Architecture/06-Fluxo Agenda]]
