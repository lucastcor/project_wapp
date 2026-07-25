---
title: Estratégia de Testes
type: testing
tags: [testes, qa]
updated: 2026-04-28
---

# Estratégia de Testes

## Stack

- `pytest` como runner único.
- Testes em `tests/`.
- `tests/conftest.py` provê fixtures comuns.
- `tests/test_data/conversation_scenarios.py` mantém cenários conversacionais reutilizáveis.

## Camadas testadas

| Camada | Cobertura |
|--------|-----------|
| Rotas API (público + admin) | `test_routes.py`, `test_admin_routes.py` |
| Orchestrator | `test_orchestrator.py`, `test_orchestrator_flow.py`, `test_orchestrator_no_auto_reply.py` |
| Reply | `test_reply_service.py` |
| Pipeline | `test_pipeline_service.py`, `test_pipeline_service_validation.py` |
| Memória | `test_memory_service.py` |
| Onboarding | `test_onboarding_service.py` |
| Calendário | `test_calendar_service.py` |
| Workflow conversacional | `test_conversation_workflow_service.py` |
| Fila | `test_appointment_queue_service.py` |
| Lembretes | `test_reminder_service.py` |
| Outbound | `test_outbound_dispatch_service.py` |
| Treino import/export | `test_training_import_service.py`, `test_training_export_service.py` |
| Fine-tuning | `test_finetuning_service.py` |
| Pipeline de mensagens | `test_pipeline_service.py` |
| Banco/persistência | `test_db_backend.py` |
| Tenant isolation | `test_tenant_isolation.py` |
| Tools | `test_tools.py` |
| Cenários de negócio (e2e) | `test_business_scenarios.py` |
| Regras operacionais | `test_operational_rules.py` |
| Company context | `test_company_context.py` |
| Readiness | `test_system_readiness_service.py` |
| Analytics | `test_analytics_service.py` |
| Billing | `test_billing_service.py` |

## Princípios

- **Sem mock de banco** — testes usam SQLite efêmero (mais perto do real).
- **OpenAI mockado** — quando `CLASSIFICATION_PROVIDER=mock` ou via fixture.
- **Cenários realistas** — `test_business_scenarios.py` reproduz fluxos de cliente real.
- **Multi-tenant testado** — `test_tenant_isolation.py` confirma escopo.

## Como rodar

```bash
# tudo
pytest

# arquivo específico
pytest tests/test_orchestrator.py

# nome de teste
pytest tests/test_orchestrator.py::test_handles_handoff

# verbose
pytest -vv

# parar no primeiro erro
pytest -x
```

## CI

`.github/` contém workflow GitHub Actions (raiz `.github/workflows/`). Detalhe não auditado nesta nota — ver arquivo direto.

## Próximas leituras

- [[70-Testing/02-Catálogo de Testes]]
- [[80-Improvements/02-Issues Identificados]]
