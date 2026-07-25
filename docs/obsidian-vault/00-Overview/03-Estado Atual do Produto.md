---
title: Estado Atual do Produto
type: status
tags: [estado, produto, maturidade]
updated: 2026-04-28
---

# Estado Atual do Produto

Snapshot da maturidade por dimensão, em **2026-04-28**, baseado em código real (não em backlog declarado).

## Maturidade por área

| Área | Status | Evidência (arquivo) | Observação |
|------|--------|---------------------|------------|
| Recepção / orquestração de mensagem | ✅ Pronto | `app/core/orchestrator.py` | Idempotência por `provider_message_id`. |
| Classificação | ✅ Pronto | `app/services/classification_service.py` | `openai` ou heurística mock. |
| Decisão auto-reply / handoff | ✅ Pronto | `app/services/decision_service.py`, `handoff_service.py` | Cobre desconto, estoque, baixa confiança etc. |
| Resposta comercial | ✅ Pronto | `app/services/reply_services.py`, `app/llm/providers/openai_provider.py` | Suporta finetuned model via flag. |
| Memória / pipeline | ✅ Pronto | `memory_service.py`, `pipeline_service.py` | Resumo + estágio + temperatura. |
| Knowledge base incremental | ✅ Pronto | `knowledge_service.py` | Resoluções viram `knowledge_entries`. |
| Outbound + dispatch | ✅ Pronto | `outbound_dispatch_service.py`, `whatsapp_provider.py` | Status pending → sent/failed. |
| Webhook WhatsApp | ✅ Pronto | `app/api/routes.py` | Handshake + HMAC `X-Hub-Signature-256`. |
| Idempotência webhook | ✅ Pronto | `inbound_message_receipts` | Migração 018. |
| Multi-tenant | ✅ Pronto | `companies` + `company_id` em todas tabelas operacionais | Resolução por id, slug ou default. |
| Agenda + bookings | ✅ Pronto | `calendar_service.py`, tabelas 005 | Provider mock + perfis por empresa. |
| Conversa natural agenda | ✅ Pronto | `conversation_workflow_service.py` | Cliente + operador interno. |
| Fila de remanejamento | ✅ Pronto | `appointment_queue_service.py` | Ofertas com janela de resposta. |
| Lembretes (worker) | ✅ Pronto | `app/tools/reminder_worker.py` | Polling configurável. |
| Importação histórica | ✅ Pronto | `training_import_service.py` | CSV/JSON com dry-run. |
| Curadoria / export dataset | ✅ Pronto | `training_export_service.py` | Dedup, qualidade mínima 8.0, split 80/20. |
| Fine-tuning jobs | 🟡 Pipeline pronto, falta job real | `finetuning_service.py` | `ENABLE_FINETUNING_PIPELINE` default false. |
| Calendário externo (Google/CalDAV) | 🟡 Preparado | `calendar_service.py` | Default `mock`, falta plugar credenciais reais. |
| Plataforma web `/platform` | ✅ Pronto | `app/platform/` | SPA + sessão web HTTP-only. |
| Painel Streamlit | ✅ Apoio | `app/admin_app.py`, `app/ui/pages/` | Mantido como contingência. |
| RBAC | ✅ Token estático | `admin_auth_service.py` | viewer / operator / superadmin. |
| Auditoria | ✅ Persistida | `audit_events` + `_record_audit_event()` | Retenção configurável `AUDIT_RETENTION_DAYS`. |
| Observabilidade | ✅ JSON + métricas | `app/core/logging.py`, `/admin/observability/metrics` | Sem tracing distribuído. |
| Rate limiting | ✅ In-memory | `rate_limit_service.py` | Por escopo (auth/admin/incoming/webhook). |
| Migrações | ✅ Versionadas | `app/storage/db.py` | 23 migrações em `MIGRATIONS`. |
| Backup / cleanup | ✅ CLI | `tools/export_runtime_backup.py`, `cleanup_operational_data.py` | Dry-run + execute. |
| Billing / planos | ✅ Implementado | `billing_service.py`, `billing_*` tables | 3 planos com limites e degradação. |
| Realtime (broadcast) | 🟡 Implementado | `realtime_service.py` | Broadcast simples; sem WebSocket dedicado documentado. |
| Identidade humana centralizada | 🔴 Ausente | — | Tokens estáticos. |
| Tracing distribuído | 🔴 Ausente | — | Apenas logs + request_id. |

## Linhas vermelhas conhecidas

- **Token superadmin único** ainda funciona como fallback se `ADMIN_SUPERADMIN_TOKEN` não estiver setado e `ADMIN_API_TOKEN` estiver — coerente com [[50-Security/02-Auth Admin]].
- **Rate limit em memória** não é compartilhado entre instâncias horizontais — limite efetivo por processo. Detalhe em [[50-Security/04-Rate Limiting]].
- **Sanitização de dataset** ainda é básica (markers + redação de `@`) — exige etapa dedicada antes de operar com dados reais. Detalhe em [[50-Security/06-PII e Dataset]].
- **Calendar provider default = mock** — em produção real precisa virar `google_calendar` ou `caldav`.

## Volume de código

| Arquivo | Linhas |
|---------|-------|
| `app/storage/repository.py` | ~4 386 |
| `app/api/admin_routes.py` | ~1 891 |
| `app/storage/db.py` | ~1 731 |
| `app/services/calendar_service.py` | ~1 503 |
| `app/services/conversation_workflow_service.py` | ~1 467 |
| `app/services/system_readiness_service.py` | ~727 |

`repository.py` e `admin_routes.py` concentram operação. Mexer neles **sempre** com revisão lateral.

## Próximas leituras

- [[80-Improvements/01-Limites Conhecidos]]
- [[80-Improvements/05-Backlog de Produto]]
- [[10-Architecture/01-Arquitetura em Camadas]]
