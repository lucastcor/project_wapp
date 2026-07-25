---
title: Catálogo de Testes
type: testing
tags: [testes, catálogo]
updated: 2026-04-28
---

# Catálogo de Testes

Mapa arquivo-a-arquivo com o objetivo principal.

| Arquivo | Foco |
|---------|------|
| `tests/conftest.py` | Fixtures comuns (DB, repositório, cliente HTTP). |
| `tests/test_admin_routes.py` | Endpoints administrativos: agenda, contatos, empresa, treino, RBAC. |
| `tests/test_analytics_service.py` | Dashboard, trend, reminder effectiveness. |
| `tests/test_appointment_queue_service.py` | Fila, ofertas, follow-up, interpretação natural. |
| `tests/test_billing_service.py` | Planos, trial, ativação, limites, usage. |
| `tests/test_business_scenarios.py` | Cenários e2e de cliente real (lead → handoff → resposta → follow-up). |
| `tests/test_calendar_service.py` | Disponibilidade, perfis, bookings, sync. |
| `tests/test_company_context.py` | Validação do JSON, pipelines visuais. |
| `tests/test_conversation_workflow_service.py` | Agenda conversacional, cancel, reschedule, operador interno. |
| `tests/test_db_backend.py` | Compatibilidade SQLite ↔ Postgres (com SQLite em testes). |
| `tests/test_finetuning_service.py` | Criação de jobs, persistência, leitura. |
| `tests/test_memory_service.py` | Resumo conversacional. |
| `tests/test_onboarding_service.py` | Presets e setup operacional. |
| `tests/test_operational_rules.py` | Regras de negócio operacionais. |
| `tests/test_orchestrator.py` | Caminho completo do orquestrador. |
| `tests/test_orchestrator_flow.py` | Variações de fluxo. |
| `tests/test_orchestrator_no_auto_reply.py` | Casos onde bot não deve responder. |
| `tests/test_outbound_dispatch_service.py` | Dispatch + erros de provider. |
| `tests/test_pipeline_service.py` | Inferência de pipeline. |
| `tests/test_pipeline_service_validation.py` | Validação adicional do pipeline. |
| `tests/test_reminder_service.py` | Criação e disparo de lembretes. |
| `tests/test_reply_service.py` | Geração de resposta comercial. |
| `tests/test_routes.py` | Rotas públicas, webhook, plataforma. |
| `tests/test_system_readiness_service.py` | Readiness, perfil, onboarding. |
| `tests/test_tenant_isolation.py` | Vazamento cross-tenant nas queries. |
| `tests/test_tools.py` | CLIs (preflight, backup, cleanup). |
| `tests/test_training_export_service.py` | Filtragem, dedup, split, export. |
| `tests/test_training_import_service.py` | CSV/JSON dry-run + persist. |
| `tests/test_data/conversation_scenarios.py` | Dados reutilizáveis em testes conversacionais. |

## Pendências documentadas

- `tests/conftest_py` (sem extensão) **não existe** no checkout atual; era citação histórica do `codebase_map.md`. Confirmado com `ls tests/`. Não é placeholder pendente.
- Cobertura de **WhatsApp provider real** depende de mock HTTP (`requests`) — verificar se há testes específicos para `whatsapp_provider.py`.
- Cobertura de **rate_limit_service** não está listada — possível gap.
- Cobertura de **realtime_service** idem.

Tracking em [[80-Improvements/02-Issues Identificados]].

## Próximas leituras

- [[70-Testing/01-Estratégia de Testes]]
